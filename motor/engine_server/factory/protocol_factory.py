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

from motor.common.utils.logger import get_logger

logger = get_logger("engine_server")


class ProtocolFactory:
    _PROTOCOL_MAP: dict[str, dict[str, str]] = {
        "vllm":
            {
                "ChatCompletionRequest": "vllm.entrypoints.openai.chat_completion.protocol.ChatCompletionRequest",
                "CompletionRequest": "vllm.entrypoints.openai.completion.protocol.CompletionRequest",
            },
    }

    @staticmethod
    def _import_class_from_string(path_str: str):
        module_name, class_identifier = path_str.rsplit('.', 1)
        imported_module = importlib.import_module(module_name)
        target_class = getattr(imported_module, class_identifier)
        return target_class

    def load_protocol_classes(self, engine_type: str):
        protocol_paths = self._PROTOCOL_MAP.get(engine_type)

        if not protocol_paths:
            supported_types = list(self._PROTOCOL_MAP.keys())
            raise ValueError(
                f"Unsupported engine type: {engine_type}. "
                f"Supported types are: {supported_types}."
            )

        try:
            chat_completion_request = self._import_class_from_string(protocol_paths["ChatCompletionRequest"])
            completion_request = self._import_class_from_string(protocol_paths["CompletionRequest"])
            return chat_completion_request, completion_request
        except Exception as e:
            raise ValueError(f"Failed to load protocol classes for {engine_type}") from e
