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

from motor.engine_server.config.base import IConfig
from motor.engine_server.core.base_core import IServerCore


class ServerCoreFactory:
    _ENGINE_CORE_MAP: dict[str, str] = {
        "vllm": "motor.engine_server.core.vllm.vllm_core.VLLMServerCore"
    }

    def create_server_core(self, config: IConfig) -> IServerCore:
        config_type = config.get_server_config().engine_type
        core_class_path = self._ENGINE_CORE_MAP.get(config_type)

        if not core_class_path:
            supported_types = list(self._ENGINE_CORE_MAP.keys())
            raise ValueError(
                f"Unsupported engine type: {config_type}. "
                f"Supported types are: {supported_types}."
            )

        try:
            module_path, class_name = core_class_path.rsplit('.', 1)
            module = importlib.import_module(module_path)
            core_class = getattr(module, class_name)

            return core_class(config)
        except (ImportError, AttributeError) as e:
            raise ValueError(f"Failed to load core class for {config_type}") from e
