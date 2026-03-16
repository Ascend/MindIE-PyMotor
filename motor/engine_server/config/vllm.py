# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import argparse
import sys
import json
from typing import Any
from dataclasses import dataclass, field

from vllm.entrypoints.openai.cli_args import make_arg_parser, validate_parsed_serve_args

from motor.engine_server.config.base import BaseConfig, ServerConfig
from motor.common.utils.logger import get_logger
from motor.engine_server.utils.ranktable import get_data_parallel_address
from motor.engine_server.constants import constants

logger = get_logger("engine_server")


def _add_argument_to_list(arg_list: list, key: str, value: Any):
    if isinstance(value, bool):
        if value:
            arg_list.append(f"--{key}")
    elif isinstance(value, list):
        if value:
            arg_list.append(f"--{key}")
            for item in value:
                arg_list.append(str(item))
    elif isinstance(value, dict):
        arg_list.append(f"--{key}")
        arg_list.append(json.dumps(value))
    else:
        arg_list.append(f"--{key}")
        arg_list.append(str(value))


def _get_default_mapping() -> dict[str, str]:
    return {
        'model_path': 'model',
        'model_name': 'served_model_name',
        'npu_mem_utils': 'gpu_memory_utilization',
        'dp_rank': 'data_parallel_rank',
        'dp_size': 'data_parallel_size',
        'tp_size': 'tensor_parallel_size',
        'pp_size': 'pipeline_parallel_size',
        'enable_ep': 'enable_expert_parallel',
    }


@dataclass
class VLLMConfig(BaseConfig):
    args: argparse.Namespace | None = None
    data_parallel_address: str | None = None
    data_parallel_rpc_port: int | None = None
    kv_transfer_config: str | None = None
    mapping: dict[str, str] | None = field(default_factory=_get_default_mapping)

    def initialize(self):
        super().initialize()
        if self.server_config.deploy_config.get_parallel_config(self.server_config.role).dp_size > 1:
            self.data_parallel_address = get_data_parallel_address()
            self.data_parallel_rpc_port = self.server_config.deploy_config. \
                get_parallel_config(self.server_config.role).dp_rpc_port
        if self.server_config.role == constants.PREFILL_ROLE or self.server_config.role == constants.DECODE_ROLE:
            self._process_kv_transfer_config()

    def validate(self):
        super().validate()
        if self.args is not None:
            validate_parsed_serve_args(self.args)

    def convert(self):
        super().convert()
        arg_list = self._get_param_list()
        logger.info(f'engine server parsed arg_list: {arg_list}')

        sys.argv = ["serve"] + arg_list

        try:
            from vllm.utils import FlexibleArgumentParser
        except ImportError:
            from vllm.utils.argparse_utils import FlexibleArgumentParser
        parser = FlexibleArgumentParser(description="vLLM parser")
        parser = make_arg_parser(parser)
        self.args = parser.parse_args()

    def get_args(self) -> argparse.Namespace:
        return self.args

    def get_server_config(self) -> ServerConfig:
        return self.server_config

    def _process_kv_transfer_config(self):
        role = self.server_config.role
        if role == constants.UNION_ROLE:
            return

        kv_config = self.server_config.deploy_config.engine_config.get(constants.KV_TRANSFER_CONFIG)
        if kv_config is None:
            raise ValueError(f"{constants.KV_TRANSFER_CONFIG} is None in engine_config")
        try:
            if kv_config[constants.KV_CONNECTOR] == constants.MULTI_CONNECTOR:
                self._process_multi_connector(kv_config)
            else:
                self._process_mooncake_connector(kv_config, add_engine_id=True)

            self.kv_transfer_config = json.dumps(kv_config)
        except Exception as e:
            logger.error(f"Failed to process kv_transfer_config: {e}")
            raise ValueError(f"Failed to process kv_transfer_config: {e}") from e

    def _process_multi_connector(self, kv_config):
        role = self.server_config.role
        if role == constants.PREFILL_ROLE:
            kv_config[constants.KV_ROLE] = constants.KV_PRODUCER
        elif role == constants.DECODE_ROLE:
            kv_config[constants.KV_ROLE] = constants.KV_CONSUMER
        kv_config[constants.ENGINE_ID] = str(self.server_config.instance_id)
        if constants.KV_CONNECTOR_EXTRA_CONFIG not in kv_config:
            raise ValueError("KV connector extra config missing from multi connector")
        connectors = kv_config[constants.KV_CONNECTOR_EXTRA_CONFIG][constants.CONNECTORS]
        if len(connectors) < 2:
            raise ValueError("KV connector extra config at least have 2 connectors")
        self._process_mooncake_connector(connectors[0], add_engine_id=False)
        self._process_store_connector(connectors[1])

    def _process_mooncake_connector(self, kv_config, add_engine_id: bool = True):
        role = self.server_config.role
        if role == constants.PREFILL_ROLE:
            kv_config[constants.KV_ROLE] = constants.KV_PRODUCER
        elif role == constants.DECODE_ROLE:
            kv_config[constants.KV_ROLE] = constants.KV_CONSUMER
        if add_engine_id:
            kv_config[constants.ENGINE_ID] = str(self.server_config.instance_id)

        prefill_parallel = self.server_config.deploy_config.get_parallel_config(constants.KV_PREFILL)
        decode_parallel = self.server_config.deploy_config.get_parallel_config(constants.KV_DECODE)

        if constants.KV_CONNECTOR_EXTRA_CONFIG not in kv_config:
            kv_config[constants.KV_CONNECTOR_EXTRA_CONFIG] = {}

        kv_config[constants.KV_CONNECTOR_EXTRA_CONFIG][constants.KV_PREFILL] = {
            constants.DP_SIZE: prefill_parallel.dp_size,
            constants.TP_SIZE: prefill_parallel.tp_size
        }
        kv_config[constants.KV_CONNECTOR_EXTRA_CONFIG][constants.KV_DECODE] = {
            constants.DP_SIZE: decode_parallel.dp_size,
            constants.TP_SIZE: decode_parallel.tp_size
        }

    def _process_store_connector(self, kv_config):
        role = self.server_config.role
        if role == constants.PREFILL_ROLE:
            kv_config[constants.KV_ROLE] = constants.KV_PRODUCER
        elif role == constants.DECODE_ROLE:
            kv_config[constants.KV_ROLE] = constants.KV_CONSUMER

        if kv_config[constants.KV_CONNECTOR] == constants.MOON_CAKE_STORE_V1:
            kv_config[constants.KV_CONNECTOR_EXTRA_CONFIG][constants.MOON_CAKE_RPC_PORT] \
                = str(self.server_config.instance_id)
        elif kv_config[constants.KV_CONNECTOR] == constants.ASCEND_STORE_CONNECTOR:
            kv_config[constants.LOOKUP_RPC_PORT] = str(self.server_config.instance_id)
        else:
            raise ValueError(f"{constants.KV_CONNECTOR} is not supported")

    def _flatten_config(self) -> dict[str, Any]:
        """
        Flatten deploy_config into a simple key-value dictionary with the following rules:
        1. Include all key-value pairs from engine_config
        2. For other fields, only include those defined in self.mapping
        3. Use the value from mapping as the final key name
        4. If there's a conflict between engine_config and model_config, model_config takes precedence
        """
        flattened = {}

        deploy_config = self.server_config.deploy_config

        flattened.update(deploy_config.engine_config.configs)

        model_config = deploy_config.model_config
        for server_key, vllm_key in self.mapping.items():
            if hasattr(model_config, server_key):
                value = getattr(model_config, server_key)
                if value is not None:
                    flattened[vllm_key] = value

        parallel_config = deploy_config.get_parallel_config(self.server_config.role)
        for server_key, vllm_key in self.mapping.items():
            if hasattr(parallel_config, server_key):
                value = getattr(parallel_config, server_key)
                if value is not None:
                    flattened[vllm_key] = value

        flattened.update({"host": self.server_config.server_host, "port": self.server_config.engine_port})
        if self.data_parallel_address is not None:
            flattened["data_parallel_address"] = self.data_parallel_address
            flattened["data_parallel_rpc_port"] = self.data_parallel_rpc_port
            flattened["data_parallel_rank"] = self.server_config.dp_rank
        if self.kv_transfer_config is not None:
            flattened["kv_transfer_config"] = self.kv_transfer_config

        return flattened

    def _get_param_list(self) -> list[str]:
        processed_args = []

        flattened_config = self._flatten_config()

        for key, value in flattened_config.items():
            formatted_key = key.replace('_', '-')
            _add_argument_to_list(processed_args, formatted_key, value)

        return processed_args
