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

import enum
from pydantic import Field


from motor.common.alarm.event import Event
from motor.common.alarm.enums import (
    EventType,
    ServiceAffectedType,
    Severity
)


class ServerExceptionReason(enum.IntEnum):
    HEARTBEAT_TIMEOUT = 1
    ENDPOINT_ABNORMAL = 2


class ServerExceptionEvent(Event):
    """Server exception event alarm (e.g., heartbeat timeout)."""

    event_type: EventType = Field(default=EventType.STATE_CHANGE)
    alarm_id: str = Field(default="0xFC001003")
    alarm_name: str = Field(default="Server Exception Alarm")
    severity: Severity = Field(default=Severity.MAJOR)
    probable_cause: str = Field(default="1:Heartbeat timeout;2:endpoint abnormal;")
    service_affected_type: ServiceAffectedType = Field(default=ServiceAffectedType.YES)


    def __init__(self, reason_id: ServerExceptionReason, endpoint_ip: str, endpoint_ids: list):
        super().__init__()
        self.reason_id = reason_id.value
        self.update_time()
        endpoint_ids_str = ",".join(map(str, endpoint_ids))
        service_location = f"service name=Controller, endpoint ip={endpoint_ip}, endpoint ids=[{endpoint_ids_str}]"
        self.location = service_location
        self.moi = service_location
        self.additional_information = service_location
        
