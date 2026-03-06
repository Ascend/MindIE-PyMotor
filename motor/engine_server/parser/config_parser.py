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

from typing import Dict, Optional
import importlib

from motor.engine_server.config.base import IConfig, ServerConfig
from motor.common.utils.logger import get_logger

logger = get_logger("engine_server")


class ConfigParser:
    _ENGINE_CONFIG_MAP: Dict[str, str] = {
        "vllm": "motor.engine_server.config.vllm.VLLMConfig"
    }

    def __init__(self, server_config: ServerConfig):
        self.server_config = server_config

    def parse(self) -> IConfig:
        engine_type = self.server_config.engine_type
        config_class_path = self._ENGINE_CONFIG_MAP.get(engine_type)

        if not config_class_path:
            supported_types = list(self._ENGINE_CONFIG_MAP.keys())
            raise ValueError(
                f"Unsupported engine type: {engine_type}. "
                f"Supported types are: {supported_types}."
            )

        try:
            module_path, class_name = config_class_path.rsplit('.', 1)
            module = importlib.import_module(module_path)
            config_class = getattr(module, class_name)
            
            config_instance = config_class(server_config=self.server_config)
            config_instance.initialize()
            config_instance.convert()
            config_instance.validate()
            return config_instance
        except (ImportError, AttributeError) as e:
            raise ValueError(f"Failed to load config class for {engine_type}") from e