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

from motor.common.alarm.enums import Category, Cleared, ClearCategory
from .record import Record


class Alarm(Record):
    """Alarm class for motor alarms."""

    category: Category = Field(default=Category.ALARM)

    def __init__(self):
        super().__init__()

    def clear(self) -> None:
        self.cleared = Cleared.YES
        self.category = Category.CLEAR