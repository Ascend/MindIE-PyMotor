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
"""
Cluster fault codes, types and utilities for fault tolerance management.

This module defines cluster fault level enumerations, fault types, and provides mapping utilities
to convert fault type strings to corresponding fault levels for cluster fault handling.
"""
from enum import Enum
from pydantic import BaseModel, Field


class SpecialFaultCode(int, Enum):
    NODE_REBOOT = 0x0000001


class NodeStatus(str, Enum):
    READY = "READY"
    NOT_READY = "NOT_READY"


class FaultType(str, Enum):
    """Fault type enumeration"""
    CARD_UNHEALTHY = "CardUnhealthy"  # Card fault
    CARD_NETWORK_UNHEALTHY = "CardNetworkUnhealthy"  # chip network fault
    NODE_UNHEALTHY = "NodeUnhealthy"  # Node fault


class OriginFaultLevel(str, Enum):
    """Original fault level enumeration for mapping fault type strings"""
    NOT_HANDLE_FAULT = "NotHandleFault"
    RESTART_REQUEST = "RestartRequest"
    RESTART_BUSINESS = "RestartBusiness"
    FREE_RESTART_NPU = "FreeRestartNPU"
    RESTART_NPU = "RestartNPU"
    SEPARATE_NPU = "SeparateNPU"
    PRE_SEPARATE_NPU = "PreSeparateNPU"


class FaultLevel(int, Enum):
    """Fault level enumeration with severity levels from 0 to 6.

    Higher values indicate more severe faults requiring more aggressive
    recovery strategies.
    """
    HEALTHY = 0  # Healthy state, no faults
    L1 = 1       # Level 1 faults that don't require handling
    L2 = 2       # Level 2 faults that can be self-healed
    L3 = 3       # Level 3 faults that cannot be handled automatically
    L4 = 4       # Level 4 faults requiring severe isolation actions
    L5 = 5       # Level 5 faults requiring NPU restart
    L6 = 6       # Level 6 faults requiring NPU separation


class FaultInfo(BaseModel):
    """Fault information model"""
    fault_type: FaultType = Field(..., description="Fault type")
    npu_name: str = Field(default="", description="Faulty chip name, empty for node faults")
    fault_code: int = Field(default=0x0, description="Fault code")
    fault_level: FaultLevel = Field(default=FaultLevel.L1, description="Fault level, L1, L2, L3, L4, L5, L6")
    origin_fault_level: OriginFaultLevel = Field(
        default=OriginFaultLevel.NOT_HANDLE_FAULT,
        description="Original fault level, NotHandleFault, RestartRequest......"
    )


def map_fault_type(fault_type_str: str) -> FaultType:
    """Map fault type string to FaultType enum.

    Maps fault type strings from configuration to corresponding FaultType
    enumeration values based on predefined mapping rules.

    Args:
        fault_type_str: Fault type string from configuration data

    Returns:
        FaultType: Mapped fault type enum value, defaults to NODE_UNHEALTHY for unknown types

    Mapping rules:
    - CardUnhealthy -> FaultType.CARD_UNHEALTHY
    - CardNetworkUnhealthy -> FaultType.CARD_NETWORK_UNHEALTHY
    - Others -> FaultType.NODE_UNHEALTHY
    """
    # Mapping table for fault type strings to fault types
    fault_type_mapping = {
        "CardUnhealthy": FaultType.CARD_UNHEALTHY,
        "CardNetworkUnhealthy": FaultType.CARD_NETWORK_UNHEALTHY,
    }

    return fault_type_mapping.get(fault_type_str, FaultType.NODE_UNHEALTHY)


def map_fault_level(fault_level_str: str) -> FaultLevel:
    """Map fault level string to FaultLevel enum.

    Maps fault type strings from configuration to corresponding FaultLevel
    enumeration values based on predefined mapping rules.

    Args:
        fault_level_str: Fault level string from configuration data

    Returns:
        FaultLevel: Mapped fault level enum value, defaults to HEALTHY for unknown types

    Mapping rules:
    - L1: OriginFaultLevel.NOT_HANDLE_FAULT
    - L2: OriginFaultLevel.RESTART_REQUEST
    - L3: OriginFaultLevel.RESTART_BUSINESS
    - L4: OriginFaultLevel.FREE_RESTART_NPU
    - L5: OriginFaultLevel.RESTART_NPU
    - L6: OriginFaultLevel.SEPARATE_NPU, OriginFaultLevel.PRE_SEPARATE_NPU
    """
    # Mapping table for fault type strings to fault levels
    fault_level_mapping = {
        OriginFaultLevel.NOT_HANDLE_FAULT: FaultLevel.L1,
        OriginFaultLevel.RESTART_REQUEST: FaultLevel.L2,
        OriginFaultLevel.RESTART_BUSINESS: FaultLevel.L3,
        OriginFaultLevel.FREE_RESTART_NPU: FaultLevel.L4,
        OriginFaultLevel.RESTART_NPU: FaultLevel.L5,
        OriginFaultLevel.SEPARATE_NPU: FaultLevel.L6,
        OriginFaultLevel.PRE_SEPARATE_NPU: FaultLevel.L6,
    }

    return fault_level_mapping.get(fault_level_str, FaultLevel.HEALTHY)