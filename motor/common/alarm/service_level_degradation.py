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

from pydantic import Field

from motor.common.alarm.alarm import Alarm
from motor.common.alarm.enums import (
    ClearCategory,
    Cleared,
    EventType,
    ServiceAffectedType,
    Severity,
    Category,
)
from motor.common.alarm.record import Record


class ServiceLevelDegradation(Record):
    """Service level degradation alarm (e.g., scale-in)."""

    category: Category = Field(default=Category.LEVEL_CHANGE)
    cleared: Cleared = Field(default=Cleared.NO)
    clear_category: ClearCategory = Field(default=ClearCategory.AUTO)
    location: str = Field(default="")
    moi: str = Field(default="")
    additional_information: str = Field(default="")
    event_type: EventType = Field(default=EventType.STATE_CHANGE)
    alarm_id: str = Field(default="0xFC001001")
    alarm_name: str = Field(default="Service Level Degradation Alarm")
    severity: Severity = Field(default=Severity.MAJOR)
    probable_cause: str = Field(
        default="1:Hardware or software failures, resulting in a reduction of instances"
    )
    reason_id: int = Field(default=0)
    service_affected_type: ServiceAffectedType = Field(default=ServiceAffectedType.YES)
