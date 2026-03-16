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

"""
Controller core module - contains the core components for instance management and observation.
"""

__all__ = [
    # Observer pattern
    "Observer",
    "ObserverEvent",

    # Instance management
    "InstanceManager",
    "InstanceAssembler",
    "RegisterStatus",

    # Event handling
    "EventPusher",
    "Event",
]

from .observer import Observer, ObserverEvent
from .instance_manager import InstanceManager
from .instance_assembler import InstanceAssembler, RegisterStatus
from .event_pusher import EventPusher, Event
