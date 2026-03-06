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

from typing import Dict, Any
from abc import ABC, abstractmethod


class Service(ABC):
    @abstractmethod
    def get_data(self) -> Dict[str, Any]:
        pass


class BaseService(Service):
    def __init__(self, name: str):
        self.name = name

    def get_data(self) -> Dict[str, Any]:
        pass


class MetricsService(BaseService):
    def __init__(self, data_controller):
        super().__init__(name="metrics_service")
        self.data_controller = data_controller

    def get_data(self) -> Dict[str, Any]:
        return self.data_controller.get_metrics_data()


class HealthService(BaseService):
    def __init__(self, data_controller):
        super().__init__(name="health_service")
        self.data_controller = data_controller

    def get_data(self) -> Dict[str, Any]:
        return self.data_controller.get_health_data()
