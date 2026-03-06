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

from typing import Dict, Type

from motor.engine_server.config.base import IConfig
from motor.engine_server.core.base_collector import Collector
from motor.engine_server.core.vllm.vllm_collector import VLLMCollector


class CollectorFactory:
    def __init__(self):
        self._collector_map: Dict[str, Type[Collector]] = {
            "vllm": VLLMCollector
        }

    def create_collector(self, config: IConfig) -> Collector:
        config_type = config.get_server_config().engine_type
        collector_class = self._collector_map.get(config_type)

        if not collector_class:
            raise ValueError(f"No collector found for config type {config_type}")

        return collector_class(config)
