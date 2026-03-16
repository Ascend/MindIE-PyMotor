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

from motor.common.alarm.event import Event
from motor.common.alarm.enums import (
    EventType,
    ServiceAffectedType,
    Severity,
)


class ControllerToSlaveReason(enum.IntEnum):
    MASTER_CONTROLLER_EXCEPTION = 1


class ControllerToSlaveEvent(Event):
    """Controller to slave event."""

    event_type: EventType = Field(default=EventType.STATE_CHANGE)
    severity: Severity = Field(default=Severity.WARNING)
    alarm_id: str = Field(default="0xFC001000")
    alarm_name: str = Field(default="Controller Master To Slave Alarm")
    probable_cause: str = Field(
        default="1:failures caused the original main controller to malfunction"
    )
    service_affected_type: ServiceAffectedType = Field(default=ServiceAffectedType.NO)

    def __init__(self, reason_id: ControllerToSlaveReason):
        super().__init__()
        self.reason_id = reason_id.value
        self.update_time()

        pod_ip = os.getenv("POD_IP", "")
        service_location = f"service name=Controller, service ip={pod_ip}"
        self.location = service_location
        self.moi = service_location
        self.additional_information = service_location
