# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

from __future__ import annotations

from typing import Iterable

from motor.common.resources.instance import Instance, PDRole
from motor.common.resources.endpoint import Endpoint, Workload, WorkloadAction
from motor.coordinator.domain import InstanceProvider
from motor.coordinator.scheduler.policy.base import BaseSchedulingPolicy
from motor.common.utils.logger import get_logger

logger = get_logger(__name__)


class LoadBalancePolicy(BaseSchedulingPolicy):
    """
    Load Balance Scheduler Policy implementation.
    Selects instances and endpoints based on their current workload.
    Implements select_and_endpoint and update_workload required by SchedulingFacade (forwarded via Scheduler).
    """
    def __init__(self, instance_provider: InstanceProvider):
        super().__init__(instance_provider=instance_provider)
        self._instance_provider = instance_provider
        # Removed req_workload_dict - workload state is now managed by API Server's RequestManager
        logger.info("LoadBalancePolicy started.")

    @staticmethod
    def select_instance_from_list(
        instances: list[Instance] | Iterable[Instance],
        role: PDRole = None,
        start_index: int = 0,
    ) -> Instance | None:
        """
        Select one instance with minimum workload from list/iterable (shared by Policy and Client).
        Single pass, always picks globally lowest; start_index only affects order (tie-break).
        When start_index==0 can pass values() view to avoid list alloc; when !=0 materialize to list.

        Args:
            instances: Instance list or iterable (e.g. InstanceManager.get_available_instances(role).values())
            role: Optional, for workload score
            start_index: Traversal start offset (start_index + i) % n, for multi API Server tie-break, default 0

        Returns:
            Selected instance, or None (empty or all failed)
        """
        min_workload = float('inf')
        selected_instance = None

        if start_index != 0:
            if not isinstance(instances, (list, tuple)):
                instances = list(instances)
            if not instances:
                return None
            n = len(instances)
            for i in range(n):
                idx = (start_index + i) % n
                instance = instances[idx]
                try:
                    workload_score = instance.gathered_workload.calculate_workload_score(role=instance.role)
                    if workload_score < min_workload:
                        min_workload = workload_score
                        selected_instance = instance
                except Exception as e:
                    logger.warning("Failed to calculate workload score for instance %s: %s", instance.id, e)
                    continue
            return selected_instance

        # start_index == 0: single pass, no materialize, save list allocation
        for instance in instances:
            try:
                workload_score = instance.gathered_workload.calculate_workload_score(role=instance.role)
                if workload_score < min_workload:
                    min_workload = workload_score
                    selected_instance = instance
            except Exception as e:
                logger.warning("Failed to calculate workload score for instance %s: %s", instance.id, e)
                continue
        return selected_instance

    @staticmethod
    def select_endpoint_from_instance(instance: Instance) -> Endpoint | None:
        """
        Select one endpoint with minimum workload from instance (shared by Policy and Client).

        Args:
            instance: Instance to select endpoint from

        Returns:
            Selected Endpoint, or None if none available
        """
        if not instance:
            logger.warning("No instance provided for endpoint selection")
            return None

        all_endpoints = instance.get_all_endpoints()
        if not all_endpoints:
            logger.warning(f"No endpoints available in instance {instance.id}")
            return None

        min_workload = float('inf')
        selected_endpoint = None
        for endpoint in all_endpoints:
            try:
                workload_score = endpoint.workload.calculate_workload_score(role=instance.role)
                if workload_score < min_workload:
                    min_workload = workload_score
                    selected_endpoint = endpoint
            except Exception as e:
                logger.warning("Failed to calculate workload score for endpoint %s: %s", endpoint.id, e)
                continue
        return selected_endpoint

    async def update_workload(self, instance_id: int, endpoint_id: int, req_id: str,
                              workload_action: WorkloadAction, workload_change: Workload) -> bool:
        """
        Update workload information for load-aware scheduling (by id only).

        Args:
            instance_id: Instance ID
            endpoint_id: Endpoint ID
            req_id: Request identifier (optional, only for logging)
            workload_action: Workload action type
            workload_change: Workload change value (calculated and passed by API Server)

        Returns:
            True if workload was updated successfully, False otherwise
        """
        if hasattr(self._instance_provider, "update_instance_workload"):
            await self._instance_provider.update_instance_workload(
                instance_id, endpoint_id, workload_change
            )
        else:
            raise RuntimeError(
                "InstanceProvider must support update_instance_workload for LoadBalancePolicy"
            )

        if req_id:
            logger.debug(
                f"Request {req_id} updated workload: instance_id={instance_id}, "
                f"endpoint_id={endpoint_id}, action={workload_action.value}, "
                f"change={workload_change}"
            )
        else:
            logger.debug(
                f"Updated workload: instance_id={instance_id}, "
                f"endpoint_id={endpoint_id}, action={workload_action.value}, "
                f"change={workload_change}"
            )

        return True

    def _select_instance(self, role: PDRole = None) -> Instance | None:
        """
        Select an instance with the least workload.
        """
        active_instances = self._instance_provider.get_available_instances(role)
        if not active_instances:
            logger.warning("No active instances available for scheduling")
            return None
        return LoadBalancePolicy.select_instance_from_list(active_instances.values(), role)

    def _select_endpoint(self, instance: Instance) -> Endpoint | None:
        """
        Select an endpoint with the least workload from the given instance.
        """
        return LoadBalancePolicy.select_endpoint_from_instance(instance)
