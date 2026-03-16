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

import os
import json
from typing import Any
from enum import Enum
from dataclasses import dataclass, field, asdict
from pathlib import Path

from motor.common.resources.instance import ParallelConfig, PDRole
from motor.config.tls_config import TLSConfig
from motor.common.utils.env import Env
from motor.common.utils.patch_check import safe_open
from motor.common.utils.logger import get_logger, reconfigure_logging
from motor.config.config_utils import (
    ConfigKey,
    save_config_to_json,
    _update_tls_config,
    MGMT_TLS_CONFIG,
)
from motor.config.log_config import LoggingConfig

FILE_ENCODING = "utf-8"

PP = "pp_size"
TP = "tp_size"
DP = "dp_size"
BASIC_CONFIG_KEY = "basic_config"
MODEL_CONFIG_KEY = "model_config"
PREFILL_PARALLEL_CONFIG_KEY = "prefill_parallel_config"
DECODE_PARALLEL_CONFIG_KEY = "decode_parallel_config"
MOTOR_NODE_MANAGER_CONFIG_KEY = "motor_nodemanger_config"
MOTOR_ENGINE_PREFILL_CONFIG_KEY = "motor_engine_prefill_config"
MOTOR_ENGINE_DECODE_CONFIG_KEY = "motor_engine_decode_config"
ENGINE_CONFIG_KEY = "engine_config"
KV_TRANSFER_CONFIG_KEY = "kv_transfer_config"
KV_CONNECTOR_KEY = "kv_connector"
MULTICONNECTOR = "MultiConnector"
KV_CONNECTOR_EXTRA_CONFIG_KEY = "kv_connector_extra_config"
CONNECTORS_KEY = "connectors"
KV_PORT_KEY = "kv_port"
LOOPUP_RPC_PORT_KEY = "lookup_rpc_port"
SERVER_LIST = "server_list"
DEVICE = 'device'
HARDWARE_TYPE_KEY = "hardware_type"
MODEL_NAME_KEY = "model_name"

logger = get_logger(__name__)


class HardwareType(str, Enum):
    TYPE_800I_A2 = "800I-A2"
    TYPE_800I_A3 = "800I-A3"

    def __repr__(self) -> str:
        return str.__repr__(self.value)


@dataclass
class BasicConfig:
    """Basic configuration class"""

    # Job configuration
    job_name: str = Env.job_name
    role: PDRole = PDRole.ROLE_U
    model_name: str = ""
    hardware_type: HardwareType = HardwareType.TYPE_800I_A3

    # Heartbeat sending configuration
    heartbeat_interval_seconds: int = 1

    # Device information
    device_num: int = 0
    # Parallel configuration
    parallel_config: ParallelConfig = field(default_factory=ParallelConfig)


@dataclass
class APIConfig:
    """API configuration class"""
    # http config
    pod_ip: str | None = field(default_factory=lambda: Env.pod_ip or "127.0.0.1")
    node_manager_port: int = 1026


@dataclass
class EndpointConfig:

    # EngineServer's number
    endpoint_num: int = 0

    # EngineServer's Port configuration
    base_port: int = 10000
    mgmt_ports: list[str] = field(default_factory=list)
    service_ports: list[str] = field(default_factory=list)


@dataclass
class SingleContainerNodemanagerConfig:
    single_container_flag: bool = False
    node_manager_port_offset: int | None = None
    base_port_offset: int | None = None
    device_offset: int | None = None
    device_num: int | None = None
    kv_port: int | None = None
    lookup_rpc_port: int | None = None
    dp_rpc_port: int | None = None

    @classmethod
    def from_json(cls, user_config_data: dict[str, Any]) -> 'SingleContainerNodemanagerConfig':
        config = cls()
        deploy_mode = user_config_data.get('motor_coordinator_config', {}).get(
                "scheduler_config", {}).get('deploy_mode', '')
        if not deploy_mode == 'pd_disaggregation_single_container' or not Env.role or Env.index is None:
            return config

        config.single_container_flag = True
        p_instances_num = user_config_data['motor_deploy_config']['p_instances_num']
        d_instances_num = user_config_data['motor_deploy_config']['d_instances_num']
        prefill_parallel_config = \
                user_config_data[MOTOR_ENGINE_PREFILL_CONFIG_KEY][MODEL_CONFIG_KEY][PREFILL_PARALLEL_CONFIG_KEY]
        decode_parallel_config = \
                user_config_data[MOTOR_ENGINE_DECODE_CONFIG_KEY][MODEL_CONFIG_KEY][DECODE_PARALLEL_CONFIG_KEY]
        p_dp_size = prefill_parallel_config[DP]
        p_tp_size = prefill_parallel_config[TP]
        p_pp_size = prefill_parallel_config[PP]
        d_dp_size = decode_parallel_config[DP]
        d_tp_size = decode_parallel_config[PP]
        d_pp_size = decode_parallel_config[TP]

        index = int(Env.index)
        if Env.role == 'prefill':
            config.node_manager_port_offset = index
            config.base_port_offset = index * d_dp_size * 2
            config.device_offset = index * p_dp_size * p_tp_size * p_pp_size
            config.device_num = p_dp_size * p_tp_size * p_pp_size
            kv_port_offset = config.device_offset
            lookup_rpc_port_offset = index
            dp_rpc_port_offset = index
        else:
            config.node_manager_port_offset = p_instances_num * p_dp_size + index
            config.base_port_offset = (p_instances_num * p_dp_size + index * d_dp_size) * 2
            config.device_offset = p_instances_num * p_dp_size * p_tp_size * p_pp_size + \
                    index * d_dp_size * d_tp_size * d_pp_size
            config.device_num = d_dp_size * d_tp_size * d_pp_size
            kv_port_offset = config.device_offset
            lookup_rpc_port_offset = p_instances_num + index
            dp_rpc_port_offset = p_instances_num + index

        kv_config = user_config_data[MOTOR_ENGINE_PREFILL_CONFIG_KEY][ENGINE_CONFIG_KEY].get(KV_TRANSFER_CONFIG_KEY, {})
        if kv_config:
            if kv_config[KV_CONNECTOR_KEY] == MULTICONNECTOR:
                connectors = kv_config[KV_CONNECTOR_EXTRA_CONFIG_KEY][CONNECTORS_KEY]
                config.kv_port = int(connectors[0][KV_PORT_KEY]) + kv_port_offset
                config.lookup_rpc_port = int(connectors[1][LOOPUP_RPC_PORT_KEY]) + lookup_rpc_port_offset
            else:
                config.kv_port = int(kv_config[KV_PORT_KEY]) + kv_port_offset

        config.dp_rpc_port = int(prefill_parallel_config["dp_rpc_port"]) + dp_rpc_port_offset

        return config


@dataclass
class NodeManagerConfig:
    """
    Global configuration singleton for node manager.
    Loads basic config and HCCL config file.
    """

    # Configuration sections
    api_config: APIConfig = field(default_factory=APIConfig)
    mgmt_tls_config: TLSConfig = field(default_factory=TLSConfig)
    endpoint_config: EndpointConfig = field(default_factory=EndpointConfig)
    basic_config: BasicConfig = field(default_factory=BasicConfig)
    logging_config: LoggingConfig = field(default_factory=LoggingConfig)
    single_container_config: SingleContainerNodemanagerConfig = field(default_factory=SingleContainerNodemanagerConfig)

    # Internal fields
    config_path: str = field(init=False)
    hccl_path: str = field(init=False)
    last_modified: float | None = field(default=None, init=False)

    def __post_init__(self):
        """Validate configuration after initialization"""
        # Set internal paths with defaults only if not already set (e.g., by from_json)
        if not hasattr(self, 'config_path') or self.config_path is None:
            self.config_path = Env.user_config_path

        if not hasattr(self, 'hccl_path') or self.hccl_path is None:
            # Handle HCCL path: if it's a directory, append hccl.json; otherwise use as-is
            hccl_base_path = Env.hccl_path or (Env.config_path or os.getcwd())
            hccl_path_obj = Path(hccl_base_path)
            if hccl_path_obj.is_dir():
                self.hccl_path = str(hccl_path_obj / "hccl.json")
            else:
                self.hccl_path = hccl_base_path

        # Set last modified time if config file exists
        try:
            if os.path.exists(self.config_path):
                self.last_modified = os.path.getmtime(self.config_path)
        except (OSError, IOError):
            # Ignore errors when checking file modification time
            pass

        self.validate_config()

    @classmethod
    def from_json(cls, config_path: str | None = None, hccl_path: str | None = None) -> 'NodeManagerConfig':
        """Load configuration from config and HCCL files"""

        if config_path is None:
            config_path = Env.user_config_path

        config_path_obj = Path(config_path)
        logger.info("Loading configuration files: config=%s", config_path_obj)

        if hccl_path is None:
            # Try to find HCCL file in the same directory
            config_dir = config_path_obj.parent
            hccl_path = str(config_dir / "hccl.json")
        else:
            # If hccl_path is a directory, append the default hccl filename
            hccl_path_obj = Path(hccl_path)
            if hccl_path_obj.is_dir():
                hccl_path = str(hccl_path_obj / "hccl.json")

        hccl_path_obj = Path(hccl_path)
        logger.info("HCCL file: %s", hccl_path_obj)

        # Create configuration instance with default values
        try:
            config = cls()

            # Set the resolved paths
            config.config_path = config_path
            config.hccl_path = hccl_path

            # Load config JSON
            config_data = {}
            if os.path.exists(str(config_path_obj)):
                try:
                    with safe_open(str(config_path_obj), "r") as f:
                        raw = json.load(f)
                    logger.info("Successfully loaded config file: %s", config_path_obj)

                    config.single_container_config = SingleContainerNodemanagerConfig.from_json(raw)

                    if isinstance(raw, dict) and MOTOR_NODE_MANAGER_CONFIG_KEY in raw:
                        user_cfg = raw
                        config_data = user_cfg.get(MOTOR_NODE_MANAGER_CONFIG_KEY, {})
                        if BASIC_CONFIG_KEY not in config_data:
                            config_data[BASIC_CONFIG_KEY] = {}
                        config_data[BASIC_CONFIG_KEY][MODEL_NAME_KEY] = \
                            user_cfg[MOTOR_ENGINE_PREFILL_CONFIG_KEY][MODEL_CONFIG_KEY][MODEL_NAME_KEY]
                        config_data[BASIC_CONFIG_KEY][HARDWARE_TYPE_KEY] = \
                            user_cfg["motor_deploy_config"][HARDWARE_TYPE_KEY]
                        if Env.role == "prefill":
                            config_data[BASIC_CONFIG_KEY]["parallel_config"] = \
                                user_cfg[MOTOR_ENGINE_PREFILL_CONFIG_KEY][MODEL_CONFIG_KEY][PREFILL_PARALLEL_CONFIG_KEY]
                        elif Env.role == "decode":
                            config_data[BASIC_CONFIG_KEY]["parallel_config"] = \
                                user_cfg[MOTOR_ENGINE_DECODE_CONFIG_KEY][MODEL_CONFIG_KEY][DECODE_PARALLEL_CONFIG_KEY]
                        tls_configs = [MGMT_TLS_CONFIG]
                        _update_tls_config(tls_configs, config_data, user_cfg)
                    else:
                        config_data = raw

                    # Update configuration from loaded data
                    cls._update_from_config_data(config, config_data)
                except Exception as e:
                    logger.error("Failed to read config file: %s", e)
                    raise ValueError(f"Unable to read config file {config_path}: {e}") from e
            else:
                logger.warning("Config file does not exist, using default configuration: %s", config_path_obj)


            # Load HCCL JSON
            hccl_data = {}
            if os.path.exists(str(hccl_path_obj)):
                try:
                    with safe_open(str(hccl_path_obj), "r") as f:
                        hccl_data = json.load(f)
                    logger.info("Successfully loaded HCCL file: %s", hccl_path_obj)
                    if config.single_container_config.single_container_flag:
                        device_offset = config.single_container_config.device_offset
                        device_num = config.single_container_config.device_num
                        if SERVER_LIST in hccl_data and len(hccl_data[SERVER_LIST]) > 0 and \
                                DEVICE in hccl_data[SERVER_LIST][0]:
                            hccl_data[SERVER_LIST][0][DEVICE] = \
                                hccl_data[SERVER_LIST][0][DEVICE][device_offset: device_offset + device_num]
                    # Update configuration from loaded data
                    cls._update_from_hccl_data(config, hccl_data)
                    # Generate endpoint ports only if we have HCCL data
                    cls._generate_endpoint_ports(config)
                except Exception as e:
                    logger.error("Failed to read HCCL file: %s", e)
                    raise ValueError(f"Unable to read HCCL file {hccl_path}: {e}") from e
            else:
                logger.warning("HCCL file does not exist, using default configuration: %s", hccl_path_obj)
                # No HCCL data, set default endpoint configuration
                config.endpoint_config.endpoint_num = 0
                config.endpoint_config.service_ports = []
                config.endpoint_config.mgmt_ports = []

            config.validate_config()

            # Set last modified time
            if config_path_obj.exists():
                config.last_modified = config_path_obj.stat().st_mtime

            logger.info("Configuration loading completed")
            return config

        except Exception as e:
            logger.error("Failed to create configuration instance: %s", e)
            raise

    @classmethod
    def _update_from_config_data(cls, config: 'NodeManagerConfig', cfg: dict[str, Any]):
        """Update configuration from config JSON data"""
        # Helper function to update config object from dict
        def update_config_from_dict(config_obj, config_dict):
            """Update configuration object fields from dictionary, only for existing keys"""
            for key, value in config_dict.items():
                if hasattr(config_obj, key):
                    setattr(config_obj, key, value)

        # Update configuration sections if they exist in JSON
        if "logging_config" in cfg:
            update_config_from_dict(config.logging_config, cfg["logging_config"])

        if "api_config" in cfg:
            update_config_from_dict(config.api_config, cfg["api_config"])

        if 'mgmt_tls_config' in cfg:
            update_config_from_dict(config.mgmt_tls_config, cfg['mgmt_tls_config'])

        if "endpoint_config" in cfg:
            update_config_from_dict(config.endpoint_config, cfg["endpoint_config"])

        if BASIC_CONFIG_KEY in cfg:
            basic_cfg = cfg[BASIC_CONFIG_KEY]
            update_config_from_dict(config.basic_config, basic_cfg)
            # Handle parallel_config specially
            if "parallel_config" in basic_cfg:
                pc = basic_cfg["parallel_config"]
                if isinstance(pc, dict):
                    config.basic_config.parallel_config = ParallelConfig(**pc)

        if config.single_container_config.single_container_flag:
            config.api_config.node_manager_port += config.single_container_config.node_manager_port_offset
            config.endpoint_config.base_port += config.single_container_config.base_port_offset

        # Set role from environment
        try:
            role = Env.role
            config.basic_config.role = PDRole(role)
            logger.info("Role from environment: %s", role)
            logger.info("Role from config: %s", config.basic_config.role)
        except ValueError as e:
            raise ValueError("Invalid role value from environment") from e

    @classmethod
    def _update_from_hccl_data(cls, config: 'NodeManagerConfig', data: dict[str, Any]):
        """Update configuration from HCCL JSON data"""
        # Extract server and device information
        server = (data.get("server_list") or [None])[0]
        if server:
            # Update API config with server information
            config.api_config.pod_ip = server.get("container_ip")

            # Extract device count
            devices = server.get("device") or []
            device_count = len(devices)
            config.basic_config.device_num = device_count

    @classmethod
    def _generate_endpoint_ports(cls, config: 'NodeManagerConfig'):
        """
        Calculate endpoint number based on tensor parallel & pipeline parallel config.
        Example: tp=2, pp=4 => 8 devices per pod
        """
        dp = config.basic_config.parallel_config.dp_size
        devices_per_dp = config.basic_config.parallel_config.tp_size * config.basic_config.parallel_config.pp_size

        if config.basic_config.device_num < devices_per_dp or dp < 1:
            raise ValueError(
                f"Device count ({config.basic_config.device_num}) must bigger than "
                f"or equal to devices per dp ({devices_per_dp}) "
                f"and dp must be bigger than 0"
            )

        config.endpoint_config.endpoint_num = min(dp, config.basic_config.device_num // devices_per_dp)
        config.endpoint_config.service_ports = [
            str(config.endpoint_config.base_port + i * 2)
            for i in range(config.endpoint_config.endpoint_num)
        ]
        config.endpoint_config.mgmt_ports = [
            str(config.endpoint_config.base_port + i * 2 + 1)
            for i in range(config.endpoint_config.endpoint_num)
        ]

        logger.info(
            "Generate endpoint ports successfully: endpoint_num: %d, mgmt_ports: %s, service_ports: %s.",
            config.endpoint_config.endpoint_num,
            config.endpoint_config.mgmt_ports,
            config.endpoint_config.service_ports,
        )

    def validate_config(self) -> None:
        """Validate the validity of configuration values"""
        errors = []

        # Validate API configuration
        if self.api_config.node_manager_port <= 0 or self.api_config.node_manager_port > 65535:
            errors.append("node_manager_port must be in range 1-65535")

        # Validate network configuration
        if self.endpoint_config.base_port < 0 or self.endpoint_config.base_port > 65535:
            errors.append("base_port must be in range 0-65535")

        if self.endpoint_config.endpoint_num < 0:
            errors.append("endpoint_num cannot be negative")

        # Validate device configuration
        if self.basic_config.heartbeat_interval_seconds <= 0:
            errors.append("heartbeat_interval_seconds must be greater than 0")

        # Validate logging configuration
        valid_log_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR']
        if self.logging_config.log_level.upper() not in valid_log_levels:
            errors.append(f"log_level must be one of: {', '.join(valid_log_levels)}")

        if self.logging_config.log_max_line_length <= 0:
            errors.append("log_max_line_length must be greater than 0")

        if errors:
            error_msg = "Configuration validation failed:\n" + "\n".join(f"  - {error}" for error in errors)
            logger.error(error_msg)
            raise ValueError(error_msg)

    def reload(self) -> bool:
        """Reload configuration from files"""
        if not self.config_path or not os.path.exists(self.config_path):
            logger.warning("Configuration file path does not exist, cannot reload")
            return False

        try:
            # Check if config file has been modified
            current_mtime = os.path.getmtime(self.config_path)
            if self.last_modified and current_mtime <= self.last_modified:
                logger.debug("Configuration file not modified, skipping reload")
                return True

            logger.info("Configuration file change detected, reloading...")
            new_config = NodeManagerConfig.from_json(self.config_path, self.hccl_path)

            # Update current configuration
            for field_name in self.__dataclass_fields__:
                if field_name not in ['config_path', 'hccl_path', 'last_modified']:
                    setattr(self, field_name, getattr(new_config, field_name))

            self.last_modified = current_mtime

            reconfigure_logging(self.logging_config)

            logger.info("NodeManager configuration reload successful")
            return True

        except Exception as e:
            logger.error("Failed to reload NodeManager configuration: %s", e)
            return False

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to dictionary with grouped structure"""

        # Use dataclasses.asdict to automatically serialize all config objects
        config_dict = asdict(self)

        # Handle BaseModel objects that can't be serialized by asdict
        if hasattr(self.basic_config.parallel_config, 'model_dump'):
            config_dict['basic_config']['parallel_config'] = self.basic_config.parallel_config.model_dump()

        # Remove internal fields that shouldn't be in the output
        config_dict.pop('config_path', None)
        config_dict.pop('hccl_path', None)
        config_dict.pop('last_modified', None)

        return config_dict

    def save_to_json(self, config_path: str | None = None, hccl_path: str | None = None) -> bool:
        """Save configuration to JSON files"""
        save_config_path = config_path or self.config_path
        save_hccl_path = hccl_path or self.hccl_path

        if not save_config_path or not save_hccl_path:
            logger.error("Save paths not specified")
            return False

        try:
            # Save config file
            config_dict = self.to_dict()
            save_config_to_json(
                save_config_path,
                ConfigKey.MOTOR_NODEMANAGER,
                config_dict,
                file_encoding=FILE_ENCODING,
                component_name="node manager",
            )
            logger.info("Configuration saved to: %s", save_config_path)

            # Note: HCCL file saving is not implemented as it's typically read-only
            # from the cluster management system

            return True
        except Exception as e:
            logger.error("Failed to save configuration: %s", e)
            return False

    def get_config_summary(self) -> str:
        """Get configuration summary information"""
        separator = "=" * 80
        title = " " * 22 + "NodeManager Configuration Summary"
        return (
            f"{separator}\n"
            f"{title}\n"
            f"{separator}\n"
            "  Logging Configuration:\n"
            f"    ├─ Log Level:           {self.logging_config.log_level}\n"
            f"    ├─ Log File:            {self.logging_config.host_log_dir}\n"
            f"    └─ Log Max Line Length: {self.logging_config.log_max_line_length}\n"
            "\n"
            "  Network Configuration:\n"
            f"    ├─ Node Manager Port:   {self.api_config.node_manager_port}\n"
            f"    ├─ Pod IP:              {self.api_config.pod_ip}\n"
            f"    └─ TLS:                 {'Enabled' if self.mgmt_tls_config.enable_tls else 'Disabled'}\n"
            "\n"
            "  Basic Configuration:\n"
            f"    ├─ Job Name:            {self.basic_config.job_name}\n"
            f"    ├─ Role:                {self.basic_config.role}\n"
            f"    ├─ Model:               {self.basic_config.model_name}\n"
            f"    ├─ Device Count:        {self.basic_config.device_num}\n"
            f"    ├─ Endpoint Count:      {self.endpoint_config.endpoint_num}\n"
            f"    └─ Hardware Type:       {self.basic_config.hardware_type}\n"
            "\n"
            "  Parallel Configuration:\n"
            f"    ├─ TP Size:          TP={self.basic_config.parallel_config.tp_size}\n"
            f"    ├─ PP Size:          PP={self.basic_config.parallel_config.pp_size}\n"
            f"    ├─ DP Size:          DP={self.basic_config.parallel_config.dp_size}\n"
            f"    ├─ EP Size:          EP={self.basic_config.parallel_config.ep_size}\n"
            f"    ├─ SP Size:          SP={self.basic_config.parallel_config.sp_size}\n"
            f"    └─ World Size:       World Size={self.basic_config.parallel_config.world_size}\n"
            f"{separator}"
        )
