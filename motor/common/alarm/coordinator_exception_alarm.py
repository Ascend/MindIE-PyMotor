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

import os
import enum

from pydantic import Field

from motor.common.alarm.alarm import Alarm
from motor.common.alarm.enums import (
    EventType,
    ServiceAffectedType,
    Severity,
)

COORDINATOR_EXCEPTION_ALARM_ID = "0xFC001004"


class CoordinatorExceptionReason(enum.IntEnum):
    INSTANCE_MISSING = 1
    COORDINATOR_EXCEPTION = 2


class CoordinatorExceptionAlarm(Alarm):
    """Coordinator exception alarm."""

    event_type: EventType = Field(default=EventType.QUALITY_OF_SERVICE)
    alarm_id: str = Field(default=COORDINATOR_EXCEPTION_ALARM_ID)
    alarm_name: str = Field(default="Coordinator Service Exception Alarm")
    severity: Severity = Field(default=Severity.CRITICAL)
    probable_cause: str = Field(
        default=(
            "1: No available P or D instances;"
            "2: Coordinator's own status is abnormal"
        )
    )
    service_affected_type: ServiceAffectedType = Field(default=ServiceAffectedType.YES)
    
    def __init__(self, reason_id: CoordinatorExceptionReason, is_cleared: bool = False):
        super().__init__()
        self.reason_id = reason_id.value
        if is_cleared:
            self.clear()
        self.update_time()

        pod_ip = os.getenv("POD_IP", "")
        service_location = f"service name=Coordinator, service ip={pod_ip}"
        self.location = service_location
        self.moi = service_location
        self.additional_information = service_location
