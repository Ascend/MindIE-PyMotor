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
from motor.common.alarm.enums import EventType, ServiceAffectedType, Severity

INSTANCE_EXCEPTION_ALARM_ID = "0xFC001002"


class InstanceExceptionReason(enum.IntEnum):
    INSTANCE_EXCEPTION = 1


class InstanceExceptionAlarm(Alarm):
    """InstanceExceptionAlarm class for motor instance exceptions."""

    event_type: EventType = Field(default=EventType.STATE_CHANGE)
    alarm_id: str = Field(default=INSTANCE_EXCEPTION_ALARM_ID)
    alarm_name: str = Field(default="Model Instance Exception Alarm")
    severity: Severity = Field(default=Severity.CRITICAL)
    probable_cause: str = Field(
        default="1:Hardware and software failures caused instance anomalies"
    )
    service_affected_type: ServiceAffectedType = Field(default=ServiceAffectedType.YES)

    def __init__(self, instance_id: str, reason_id: InstanceExceptionReason, is_cleared: bool = False):
        super().__init__()
        self.instance_id = instance_id
        self.reason_id = reason_id.value
        if is_cleared:
            self.clear()
        self.update_time()
        pod_ip = os.getenv("POD_IP", "")
        service_location = (f"servicename = controller,inst_type=p_inst_type or d_inst_type,"
            f"inst_id={instance_id}, service ip={pod_ip}")
        self.location = service_location
        self.moi = service_location
        self.additional_information = service_location