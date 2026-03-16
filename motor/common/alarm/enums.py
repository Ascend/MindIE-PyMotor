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

from typing import Dict
import enum
from motor.common.utils.singleton import ThreadSafeSingleton


class Category(enum.IntEnum):
    ALARM = 1
    CLEAR = 2
    EVENT = 3
    LEVEL_CHANGE = 4
    CONFIRM = 5
    UNCONFIRM = 6
    OTHER_CHANGE = 7


class Cleared(enum.IntEnum):
    NO = 0
    YES = 1


class ClearCategory(enum.IntEnum):
    AUTO = 1
    MANUAL = 2


class Severity(enum.IntEnum):
    CRITICAL = 1
    MAJOR = 2
    MINOR = 3
    WARNING = 4


class EventType(enum.IntEnum):
    COMMUNICATION = 1
    EQUIPMENT = 2
    PROCESSING_ERROR = 3
    QUALITY_OF_SERVICE = 4
    ENVIRONMENTAL = 5
    INTEGRITY_VIOLATION = 6
    OPERATIONAL = 7
    PHYSICAL_VIOLATION = 8
    SECURITY_VIOLATION = 9
    TIME_DOMAIN = 10
    PROPERTY_CHANGE = 11
    OBJECT_CREATION = 12
    OBJECT_DELETION = 13
    RELATION_CHANGE = 14
    STATE_CHANGE = 15
    ROUTE_CHANGE = 16
    PROTECTION_SWITCH = 17
    EXCEED_LIMIT = 18
    FILE_TRANSFER_STATUS = 19
    BACKUP_STATUS = 20
    HEARTBEAT = 21


class ScaleInReason(enum.IntEnum):
    INSTANCE_REDUCTION = 1


class ServiceAffectedType(enum.IntEnum):
    NO = 0
    YES = 1