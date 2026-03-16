# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

"""
Scheduling facade abstraction: select instance + allocate, update workload.
Router depends only on this interface, not on Scheduler/AsyncSchedulerClient concrete types.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Tuple

from pydantic import BaseModel

from motor.common.resources.endpoint import Endpoint, Workload, WorkloadAction
from motor.common.resources.instance import Instance, PDRole
from motor.coordinator.models.request import RequestInfo


class InstanceReadiness(str, Enum):
    """
    Instance readiness state for deploy mode (e.g. PD separate).
    Callers can distinguish "both P and D", "only P", "only D", "none" for routing/readiness.
    """
    REQUIRED_MET = "required_met"    # PD: both P and D; SINGLE_NODE: has hybrid
    ONLY_PREFILL = "only_prefill"    # PD mode: only prefill instances
    ONLY_DECODE = "only_decode"      # PD mode: only decode instances
    NONE = "none"                    # No required instances
    UNKNOWN = "unknown"              # Unknown deploy mode

    def is_ready(self) -> bool:
        """True if required instances are present for the deploy mode."""
        return self == InstanceReadiness.REQUIRED_MET


class ScheduledResource(BaseModel):
    """
    Represents a scheduled resource with an instance and endpoint.
    Output type of scheduling allocation.
    """
    instance: Instance | None = None
    endpoint: Endpoint | None = None


@dataclass(frozen=True)
class UpdateWorkloadParams:
    """
    Parameters for update_workload (G.FNM.03: encapsulate many related args).
    """
    instance_id: int
    endpoint_id: int
    role: PDRole | str
    req_id: str
    workload_action: WorkloadAction
    workload_change: Workload


class SchedulingFacade(Protocol):
    """
    Scheduling + workload update facade protocol.
    Implemented by Scheduler (in-process) and AsyncSchedulerClient (standalone process); used by BaseRouter for DI.
    Allocation workload is determined by the implementation (e.g. RR uses zero, LoadBalance uses demand).
    """

    async def select_and_allocate(
        self,
        role: PDRole,
        req_info: RequestInfo
    ) -> Tuple[Instance, Endpoint, Workload] | None:
        """
        Atomic: select instance + one workload allocation (ALLOCATION).
        Returns (instance, endpoint, allocation_workload). Caller records allocation_workload for release.
        """
        ...

    async def update_workload(self, params: UpdateWorkloadParams) -> bool:
        """Update workload (ALLOCATION / RELEASE_KV / RELEASE_TOKENS)."""
        ...

    async def has_required_instances(self) -> InstanceReadiness:
        """
        Check by deploy mode; returns detailed state (REQUIRED_MET, ONLY_PREFILL, ONLY_DECODE, NONE, UNKNOWN).
        Use .is_ready() for boolean, or compare to enum for routing/readiness.
        """
        ...
