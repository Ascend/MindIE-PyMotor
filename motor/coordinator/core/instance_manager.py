#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import threading
from enum import Enum

from motor.common.utils.http_client import AsyncSafeHTTPSClient
from motor.common.utils.logger import get_logger
from motor.common.resources.instance import Instance, PDRole, Workload, Endpoint
from motor.common.resources.http_msg_spec import EventType
from motor.common.utils.singleton import ThreadSafeSingleton
from motor.config.coordinator import CoordinatorConfig, DeployMode


logger = get_logger(__name__)
    

class UpdateInstanceMode(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"

    def __repr__(self) -> str:
        return str.__repr__(self.value)


class InstanceManager(ThreadSafeSingleton):
    def __init__(self, config: CoordinatorConfig | None = None):
        # If the instance manager is already initialized, return.
        if hasattr(self, '_initialized'):
            return

        if config is None:
            config = CoordinatorConfig()
        self._scheduler_config = config.scheduler_config
        self._infer_tls_config = config.infer_tls_config
        self._deploy_config = config.deploy_config
        self._config_lock = threading.RLock()

        self._lock = threading.Lock()
        self._available_pool: dict[int, Instance] = {}
        self._unavailable_pool: dict[int, Instance] = {}

        # Available pools for different PD roles
        self._prefill_pool: dict[int, Instance] = {}
        self._decode_pool: dict[int, Instance] = {}
        self._hybrid_pool: dict[int, Instance] = {}

        self._available_role_pools = {
            PDRole.ROLE_P: self._prefill_pool,
            PDRole.ROLE_D: self._decode_pool,
            PDRole.ROLE_U: self._hybrid_pool
        }

        self._initialized = True
        logger.info("InstanceManager started.")

    def is_available(self) -> bool:
        with self._config_lock:
            scheduler_config = self._scheduler_config
        if not scheduler_config:
            logger.error("Scheduler config not found in coordinator config, while checking availability")
            return False
        deploy_mode = scheduler_config.deploy_mode

        # no need to lock here, asynchrony is acceptable
        if deploy_mode in (
            DeployMode.CDP_SEPARATE,
            DeployMode.CPCD_SEPARATE,
            DeployMode.PD_SEPARATE,
        ):
            return len(self._prefill_pool) > 0 and len(self._decode_pool) > 0
        elif deploy_mode == DeployMode.SINGLE_NODE:
            return len(self._hybrid_pool) > 0
        else:
            logger.error(f"Unknown deploy mode: {deploy_mode}, while checking availability")
            return False

    def stop(self) -> None:
        """
        Stop instance_manager, delete all info.

        :returns:
        """
        with self._lock:
            self._available_pool = {}
            self._unavailable_pool = {}
            self._prefill_pool = {}
            self._decode_pool = {}
            self._hybrid_pool = {}
            self._available_role_pools = {
                PDRole.ROLE_P: self._prefill_pool,
                PDRole.ROLE_D: self._decode_pool,
                PDRole.ROLE_U: self._hybrid_pool
            }
        logger.info("InstanceManager stopped.")

    def update_config(self, config: CoordinatorConfig) -> None:
        """Update configuration for the instance manager"""
        with self._config_lock:
            self._scheduler_config = config.scheduler_config
            self._infer_tls_config = config.infer_tls_config
            self._deploy_config = config.deploy_config
        logger.info("InstanceManager configuration updated")

    def get_instances_num(self) -> tuple[int, int]:
        with self._lock:
            return self._deploy_config.p_instances_num, self._deploy_config.d_instances_num

    def get_available_instances(self, role: PDRole) -> dict[int, Instance]:
        # no need to lock here, asynchrony is acceptable
        instance_pool = self._available_role_pools.get(role)
        if instance_pool is None:
            logger.error(f"Unknown role: {role}, while getting available instances")
            return {}
        return instance_pool.copy()
    
    def get_all_instances(self) -> tuple[dict[int, Instance], dict[int, Instance]]:
        with self._lock:
            return self._available_pool.copy(), self._unavailable_pool.copy()

    def update_instance_workload(self, instance_id: int, endpoint: Endpoint,
                                 workload_change: Workload) -> None:
        with self._lock:
            instance = self._available_pool.get(instance_id)
            if instance is None:
                logger.warning(f"Instance ID {instance_id} not found in available instance pool, "
                               f"while updating workload")
                return
            
            instance.gathered_workload += workload_change
            endpoint.workload += workload_change
            logger.debug(f"Updated workload for instance ID {instance_id}, endpoint ID {endpoint.id}: "
                         f"Instance workload: {instance.gathered_workload}, Endpoint workload: {endpoint.workload}")

    def delete_unavailable_instance(self, instance_id: int) -> None:
        with self._lock:
            if instance_id not in self._unavailable_pool:
                logger.warning(f"Instance ID {instance_id} not found in unavailable instance pool yet, cannot delete")
                return
            
            del self._unavailable_pool[instance_id]
            logger.info(f"Deleted unavailable instance with ID {instance_id} successfully")
    
    def update_instance_state(self, instance_id: int, update_mode: UpdateInstanceMode) -> None:
        if update_mode == UpdateInstanceMode.AVAILABLE:
            with self._lock:
                if instance_id not in self._unavailable_pool:
                    logger.warning(f"Instance ID {instance_id} not found in unavailable instance pool, "
                                   f"cannot update to available")
                    return
                
                instance = self._unavailable_pool[instance_id]
                del self._unavailable_pool[instance_id]

                if not self._add_instance_to_available_pool(instance):
                    logger.error(f"Failed to add instance ID {instance_id} to available pool, "
                                 f"while updating to available")
                    return
                
                logger.info(f"Instance ID {instance_id} updated to available successfully")

        elif update_mode == UpdateInstanceMode.UNAVAILABLE:
            with self._lock:
                instance = self._available_pool.get(instance_id)
                if instance is None:
                    logger.warning(f"Instance ID {instance_id} not found in available instance pool, "
                                   f"cannot update to unavailable")
                    return
                
                if not self._delete_instance_from_available_pool(instance_id):
                    logger.warning(f"Failed to delete instance ID {instance_id} from available pool, "
                                   f"while updating to unavailable")
                    return

                self._unavailable_pool[instance_id] = instance
                logger.info(f"Instance ID {instance_id} updated to unavailable successfully")

    def refresh_instances(self, event_type: EventType, instances: list[Instance]) -> None:
        with self._lock:
            logger.info(f"Refresh instances with event type: {event_type}, number of instances: {len(instances)}")
            if event_type == EventType.ADD:
                self._add_instances(instances)
            elif event_type == EventType.DEL:
                self._delete_instances(instances)
            elif event_type == EventType.SET:
                self._set_instances(instances)
            else:
                logger.error(f"Unknown event type: {event_type}, cannot refresh instances")

    def _find_available_pool(self, instance_id: int) -> dict[int, Instance] | None:
        # This is a private method that should only be called within locked contexts
        instance = self._available_pool.get(instance_id)
        if instance is None:
            return None
        return self._available_role_pools.get(instance.role)

    def _add_instances(self, instances: list[Instance]) -> None:
        # This is a private method that should only be called within locked contexts
        for instance in instances:
            if instance.id in self._unavailable_pool:
                logger.warning("Instance ID %d (role: %s, job_name: %s) already exists in unavailable pool, "
                               "cannot add instance again",
                               instance.id, instance.role, instance.job_name)
                continue
            if not self._add_instance_to_available_pool(instance):
                logger.warning("Failed to add instance ID %d (role: %s, job_name: %s) "
                               "to available pool, while adding instance",
                               instance.id, instance.role, instance.job_name)
                continue

            # Initialize workload info
            instance.gathered_workload = Workload()
            for endpoint in instance.endpoints.values():
                for ep in endpoint.values():
                    ep.workload = Workload()

            logger.info("Added instance ID %d (role: %s, job_name: %s) to available pool successfully",
                        instance.id, instance.role, instance.job_name)

    def _delete_instances(self, instances: list[Instance]) -> None:
        # This is a private method that should only be called within locked contexts
        for instance in instances:
            if instance.id in self._unavailable_pool:
                del self._unavailable_pool[instance.id]
                logger.info("Deleted instance ID %d (role: %s, job_name: %s) from unavailable pool successfully",
                            instance.id, instance.role, instance.job_name)
                continue

            if not self._delete_instance_from_available_pool(instance.id):
                logger.warning("Instance ID %d (role: %s, job_name: %s) not found in instance pool, "
                               "cannot delete instance",
                               instance.id, instance.role, instance.job_name)

            logger.info("Deleted instance ID %d (role: %s, job_name: %s) from available pool successfully",
                        instance.id, instance.role, instance.job_name)
    
    def _set_instances(self, instances: list[Instance]) -> None:
        # This is a private method that should only be called within locked contexts
        if any(len(pool) > 0 for pool in self._available_role_pools.values()) or len(self._unavailable_pool) > 0:
            logger.error("Cannot set instance pools when there are existing instances in pools")
            return

        for instance in instances:
            self._add_instance_to_available_pool(instance)
            logger.info("Added instance ID %d (role: %s, job_name: %s) to available pool successfully",
                        instance.id, instance.role, instance.job_name)

    def _add_instance_to_available_pool(self, instance: Instance) -> bool:
        # This is a private method that should only be called within locked contexts
        update_pool = self._available_role_pools.get(instance.role)
        if update_pool is None:
            logger.error("Unknown role for instance ID %d (role: %s, job_name: %s), cannot add instance",
                         instance.id, instance.role, instance.job_name)
            return False
        if instance.id in update_pool:
            logger.warning("Instance ID %d (role: %s, job_name: %s) already exists in available pool, "
                           "cannot add instance again",
                           instance.id, instance.role, instance.job_name)
            return False

        for endpoint in instance.endpoints.values():
            for ep in endpoint.values():
                client = AsyncSafeHTTPSClient.create_client(address=f"{ep.ip}:{ep.business_port}",
                                    tls_config=self._infer_tls_config)
                ep.set_client(client)
        update_pool[instance.id] = instance
        self._available_pool[instance.id] = instance

        logger.debug("Instance ID %d (role: %s, job_name: %s) added to available pool successfully",
                     instance.id, instance.role, instance.job_name)
        return True

    def _delete_instance_from_available_pool(self, instance_id: int) -> bool:
        # This is a private method that should only be called within locked contexts
        update_pool = self._find_available_pool(instance_id)
        if update_pool is None:
            logger.warning(f"Instance ID {instance_id} not found in available instance pool yet, cannot delete")
            return False

        self._release_instance_resource(update_pool[instance_id])
        del update_pool[instance_id]
        del self._available_pool[instance_id]
        logger.debug(f"Instance ID {instance_id} deleted from available pool successfully")
        return True
    
    def _release_instance_resource(self, instance: Instance):
        try:
            for endpoint in instance.endpoints.values():
                for ep in endpoint.values():
                    ep.close_client()
        except Exception as e:
            logger.warning(f"Failed to release instance resource for instance {instance}: {e}")