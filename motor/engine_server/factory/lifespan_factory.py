# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import importlib
from typing import Any

from motor.engine_server.config.base import IConfig
from motor.common.utils.logger import get_logger

logger = get_logger("engine_server")


class LifespanFactory:
    _LIFESPAN_CREATOR_MAP: dict[str, str] = {
        "vllm": "motor.engine_server.core.vllm.vllm_httpserver_init.create_vllm_lifespan",
    }

    def get_lifespan(self, config: IConfig, init_params: dict) -> Any:
        engine_type = config.get_server_config().engine_type
        lifespan_creator_path = self._LIFESPAN_CREATOR_MAP.get(engine_type)

        if not lifespan_creator_path:
            supported_types = list(self._LIFESPAN_CREATOR_MAP.keys())
            raise ValueError(
                f"Unsupported engine type: {engine_type}. "
                f"Supported types are: {supported_types}."
            )

        try:
            module_path, func_name = lifespan_creator_path.rsplit(".", 1)
            module = importlib.import_module(module_path)
            lifespan_creator = getattr(module, func_name)
            return lifespan_creator(config, init_params)
        except (ImportError, AttributeError) as e:
            raise ValueError(f"Failed to load lifespan creator for {engine_type}") from e
