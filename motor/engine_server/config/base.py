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

import argparse
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from motor.engine_server.config import config_loader
from motor.engine_server.config.config_loader import DeployConfig
from motor.engine_server.utils.ip import ip_valid_check, port_valid_check
from motor.engine_server.utils.validators import FileValidator
from motor.engine_server.constants import constants

supported_engine = ["vllm"]
supported_role = ["prefill", "decode", "union"]


@dataclass
class ServerConfig:
    engine_type: str = "vllm"
    server_host: str = "127.0.0.1"
    role: str = "union"
    kv_port: int | None = None
    lookup_rpc_port: int | None = None
    dp_rpc_port: int | None = None
    server_port: int = 9001
    engine_port: int = 8000
    instance_id: int = 0
    dp_rank: int = 0
    config_path: Optional[str] = None
    deploy_config: DeployConfig = None

    @classmethod
    def parse_cli_args(cls) -> argparse.Namespace:
        parser = argparse.ArgumentParser(description="EngineServer - Universal Inference Engine Service")
        parser.add_argument("--host",
                            help="EngineServer endpoint host")
        parser.add_argument("--role",
                            help="PD separate role, prefill/decode/union")
        parser.add_argument("--kv-port", type=int,
                            help="kv port")
        parser.add_argument("--lookup-rpc-port", type=int,
                            help="lookup rpc port")
        parser.add_argument("--dp-rpc-port", type=int,
                            help="dp rpc port")
        parser.add_argument("--port", type=int,
                            help="EngineServer business interface port")
        parser.add_argument("--mgmt-port", type=int, dest="server_port",
                            help="EngineServer management interface port")
        parser.add_argument("--instance-id", type=int, default=0,
                            help="Engine instance id")
        parser.add_argument("--dp-rank", type=int, default=0,
                            help="DP parallel rank")
        parser.add_argument("--config-path",
                            help="Path to engine-specific configuration file (JSON format)")
        return parser.parse_args()

    @classmethod
    def init_engine_server_config(cls) -> 'ServerConfig':
        cli_args = cls.parse_cli_args()
        server_config = cls(
            server_host=cli_args.host,
            role=cli_args.role,
            kv_port=cli_args.kv_port,
            lookup_rpc_port=cli_args.lookup_rpc_port,
            dp_rpc_port=cli_args.dp_rpc_port,
            server_port=cli_args.server_port,
            engine_port=cli_args.port,
            instance_id=cli_args.instance_id,
            config_path=cli_args.config_path,
            dp_rank=cli_args.dp_rank,
        )
        server_config.validate()
        server_config.load_deploy_config()
        return server_config

    def validate(self):
        if self.role not in supported_role:
            raise ValueError(f"role {self.role} is not supported.")
        if self.instance_id < 0:
            raise ValueError(f"instance_id {self.instance_id} illegal.")
        ip_valid_check(self.server_host)
        port_valid_check(int(self.server_port))
        port_valid_check(int(self.engine_port))
        if self.dp_rank < 0 or self.dp_rank > 65535:
            raise ValueError(f"{self.dp_rank} is not supported.")
        if not os.path.exists(self.config_path):
            raise ValueError(f"config file {self.config_path} does not exist")
        if not FileValidator(self.config_path) \
                .check_not_soft_link().check_file_size().check().is_valid():
            raise ValueError(f"{self.config_path} is not a valid file path.")

    def load_deploy_config(self):
        self.deploy_config = config_loader.DeployConfig.load(self.config_path, role=self.role)
        kv_config = self.deploy_config.engine_config.get(constants.KV_TRANSFER_CONFIG, {})
        if kv_config:
            if kv_config[constants.KV_CONNECTOR] == constants.MULTI_CONNECTOR:
                connectors = kv_config[constants.KV_CONNECTOR_EXTRA_CONFIG][constants.CONNECTORS]
                if self.kv_port is not None:
                    connectors[0][constants.KV_PORT] = str(self.kv_port)
                if self.lookup_rpc_port is not None:
                    connectors[1][constants.LOOKUP_RPC_PORT] = str(self.lookup_rpc_port)
            else:
                if self.kv_port is not None:
                    kv_config[constants.KV_PORT] = str(self.kv_port)
        if self.role == "prefill" and self.dp_rpc_port is not None:
            self.deploy_config.model_config.prefill_parallel_config.dp_rpc_port = self.dp_rpc_port
        if self.role == "decode" and self.dp_rpc_port is not None:
            self.deploy_config.model_config.decode_parallel_config.dp_rpc_port = self.dp_rpc_port
        self.engine_type = str(self.deploy_config.engine_type)
        if self.engine_type not in supported_engine:
            raise ValueError(f"engine type {self.engine_type} is not supported.")
        self.update_engine_config()

    def update_engine_config(self):
        split_str = "*:"
        kv_events_config = self.deploy_config.engine_config.get("kv-events-config", None)
        if kv_events_config is None:
            return
        endpoint = kv_events_config.get("endpoint", None)
        if endpoint is None:
            return
        endpoint_info = endpoint.split(split_str)
        if endpoint_info.__len__() != 2:
            return
        kv_events_config["endpoint"] = endpoint_info[0] + split_str + str(int(endpoint_info[1]) + self.dp_rank)

        replay_endpoint = kv_events_config.get("replay_endpoint", None)
        if replay_endpoint is None:
            return
        replay_endpoint_info = replay_endpoint.split(split_str)
        if replay_endpoint_info.__len__() != 2:
            return
        kv_events_config["replay_endpoint"] = replay_endpoint_info[0] + split_str + str(int(replay_endpoint_info[1]) +
                                                                                    self.dp_rank)

        self.deploy_config.engine_config.set("kv-events-config", kv_events_config)


class IConfig(ABC):
    @abstractmethod
    def __init__(self, server_config: ServerConfig):
        pass

    @abstractmethod
    def initialize(self):
        pass

    @abstractmethod
    def validate(self):
        pass

    @abstractmethod
    def convert(self):
        pass

    @abstractmethod
    def get_args(self) -> Optional[argparse.Namespace]:
        pass

    @abstractmethod
    def get_server_config(self) -> Optional[ServerConfig]:
        pass


@dataclass
class BaseConfig(IConfig):
    server_config: ServerConfig

    def initialize(self):
        pass

    def validate(self):
        pass

    def convert(self):
        pass

    def get_args(self) -> Optional[argparse.Namespace]:
        return None

    def get_server_config(self) -> Optional[ServerConfig]:
        return self.server_config
