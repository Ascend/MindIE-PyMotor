# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

from dataclasses import dataclass, field
from typing import Any
import json
from pathlib import Path

from motor.config.tls_config import TLSConfig
from motor.config.config_utils import _update_engine_server_tls_config

MOTOR_ENGINE_PREFILL_CONFIG_KEY = "motor_engine_prefill_config"
MOTOR_ENGINE_DECODE_CONFIG_KEY = "motor_engine_decode_config"
MODEL_CONFIG_KEY = "model_config"
PREFILL_PARALLEL_CONFIG_KEY = "prefill_parallel_config"
DECODE_PARALLEL_CONFIG_KEY = "decode_parallel_config"


@dataclass
class ParallelConfig:
    """Configuration for parallel processing (both prefill and decode)"""
    dp_size: int
    tp_size: int
    pp_size: int
    enable_ep: bool
    dp_rpc_port: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParallelConfig":
        return cls(**data)


@dataclass
class ModelConfig:
    """Configuration for the model itself"""
    model_name: str
    model_path: str
    npu_mem_utils: float
    prefill_parallel_config: ParallelConfig
    decode_parallel_config: ParallelConfig

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelConfig":
        return cls(
            model_name=data["model_name"],
            model_path=data["model_path"],
            npu_mem_utils=data["npu_mem_utils"],
            prefill_parallel_config=ParallelConfig.from_dict(data[PREFILL_PARALLEL_CONFIG_KEY]),
            decode_parallel_config=ParallelConfig.from_dict(data[DECODE_PARALLEL_CONFIG_KEY])
        )


@dataclass
class EngineConfig:
    """Configuration for the engine with dynamic key-value pairs"""
    configs: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EngineConfig":
        """Parse EngineConfig from a dictionary (stores dynamic key-value pairs directly)"""
        return cls(configs=data)

    def get(self, key: str, default: Any = None) -> Any:
        return self.configs.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.configs[key] = value


@dataclass
class HealthCheckConfig:
    """Configuration for health check"""
    health_collector_timeout: int = 2
    npu_usage_threshold: int = 10
    enable_virtual_inference: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HealthCheckConfig":
        return cls(**data)


@dataclass
class DeployConfig:
    """Root configuration class representing the entire JSON structure"""
    engine_type: str
    model_config: ModelConfig
    engine_config: EngineConfig
    mgmt_tls_config: TLSConfig | None
    infer_tls_config: TLSConfig | None
    health_check_config: HealthCheckConfig = field(default_factory=HealthCheckConfig)

    @classmethod
    def load(cls, file_path: str | Path, role: str | None = None) -> "DeployConfig":
        """
        Load configuration from a JSON file and parse into a DeployConfig instance

        :param file_path: Path to the JSON file
        :return: Parsed DeployConfig instance
        """
        with open(file_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
        data = raw_data
        if isinstance(raw_data, dict) and (
                MOTOR_ENGINE_PREFILL_CONFIG_KEY in raw_data
                or MOTOR_ENGINE_DECODE_CONFIG_KEY in raw_data
        ):
            key = (
                MOTOR_ENGINE_DECODE_CONFIG_KEY
                if role == "decode"
                else MOTOR_ENGINE_PREFILL_CONFIG_KEY
            )
            data = raw_data.get(key, {})
            _update_engine_server_tls_config(data, raw_data)

            model_cfg = data.get(MODEL_CONFIG_KEY, {})
            prefill_cfg = raw_data.get(MOTOR_ENGINE_PREFILL_CONFIG_KEY, {}).get(MODEL_CONFIG_KEY, {})
            decode_cfg = raw_data.get(MOTOR_ENGINE_DECODE_CONFIG_KEY, {}).get(MODEL_CONFIG_KEY, {})

            if PREFILL_PARALLEL_CONFIG_KEY not in model_cfg and PREFILL_PARALLEL_CONFIG_KEY in prefill_cfg:
                model_cfg[PREFILL_PARALLEL_CONFIG_KEY] = prefill_cfg[PREFILL_PARALLEL_CONFIG_KEY]
            if DECODE_PARALLEL_CONFIG_KEY not in model_cfg and DECODE_PARALLEL_CONFIG_KEY in decode_cfg:
                model_cfg[DECODE_PARALLEL_CONFIG_KEY] = decode_cfg[DECODE_PARALLEL_CONFIG_KEY]

        mgmt_tls_config = data.get("mgmt_tls_config")
        infer_tls_config = data.get("infer_tls_config")

        return cls(
            engine_type=data["engine_type"],
            model_config=ModelConfig.from_dict(data["model_config"]),
            engine_config=EngineConfig.from_dict(data["engine_config"]),
            mgmt_tls_config=TLSConfig.from_dict(mgmt_tls_config) if mgmt_tls_config else None,
            infer_tls_config=TLSConfig.from_dict(infer_tls_config) if infer_tls_config else None,
            health_check_config=HealthCheckConfig.from_dict(data.get("health_check_config", {}))
        )

    def get_parallel_config(self, role: str = "union") -> ParallelConfig:
        """
        Get the parallel configuration based on the role.

        :param role: Role for parallel config:
            - "union" or "prefill": Get prefill_parallel_config
            - "decode": Get decode_parallel_config
        :return: Corresponding ParallelConfig instance
        """
        if role in ("union", "prefill"):
            return self.model_config.prefill_parallel_config
        elif role == "decode":
            return self.model_config.decode_parallel_config
        else:
            raise ValueError(f"Unsupported role: {role}. Allowed values: 'union', 'prefill', 'decode'")
