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

from motor.engine_server.config.base import IConfig


class Collector(ABC):
    def __init__(self, config: IConfig):
        pass

    @abstractmethod
    def collect(self) -> Dict[str, Any]:
        pass


class BaseCollector(Collector):
    def __init__(self, config: IConfig):
        super().__init__(config)
        self.name = f"{config.get_server_config().engine_type}_metrics_and_health_collector"

    def collect(self) -> Dict[str, Any]:
        return self._collect()

    def _collect(self) -> Dict[str, Any]:
        pass
