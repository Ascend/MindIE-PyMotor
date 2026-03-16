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
import re
import ipaddress
from typing import Optional, Any
from enum import Enum
from dataclasses import dataclass, field, asdict, is_dataclass
from pathlib import Path

from motor.common.utils.logger import reconfigure_logging, get_logger
from motor.common.utils.env import Env
from motor.common.utils.key_encryption import set_default_key_encryption_by_name
from motor.config.etcd import EtcdConfig
from motor.config.log_config import LoggingConfig
from motor.config.standby import StandbyConfig, LOCK_SLASH
from motor.config.tls_config import TLSConfig
from motor.config.config_utils import (
    ConfigKey,
    save_config_to_json,
    _update_tls_config,
    _update_instances_num,
    _update_prefill_kv_event_config,
    MGMT_TLS_CONFIG,
    INFER_TLS_CONFIG,
    ETCD_TLS_CONFIG,
)

FILE_ENCODING = "utf-8"

AIGW = "aigw"
MODEL_CONFIG = "model_config"
MODEL_NAME = "model_name"
ENGINE_CONFIG = "engine_config"
MAX_MODEL_LEN = "max_model_len"
AIGW_ID = "id"
AIGW_OBJECT = "object"
AIGW_OWNED_BY = "owned_by"
AIGW_OBJECT_MODEL = "model"
AIGW_OWNED_BY_MOTOR = "motor"
AIGW_P_MAX_SEQLEN = "p_max_seqlen"
AIGW_D_MAX_SEQLEN = "d_max_seqlen"
SLO_TTFT = "slo_ttft"
SLO_TPOT = "slo_tpot"

logger = get_logger(__name__)

# Role shm and heartbeat (Coordinator Daemon liveness).
# Not configurable; use these constants so Daemon and Mgmt stay in sync.
ROLE_SHM_NAME = "coordinator_standby_role"
ROLE_SHM_SIZE = 9  # 1 byte role (byte0) + 8 bytes heartbeat (bytes 1-8, little-endian uint64)
ROLE_SHM_MASTER = 1  # byte0 value when this node is master
ROLE_SHM_STANDBY = 0  # byte0 value when standby or unknown
ROLE_HEARTBEAT_INTERVAL_SEC = 2.0
ROLE_HEARTBEAT_STALE_SEC = 5.0


def _default_skip_paths() -> set[str]:
    return {
        "/", "/startup", "/readiness", "/liveness", "/metrics",
        "/instances/refresh", "/docs", "/redoc", "/openapi.json", "/favicon.ico"
    }


def _default_rate_limit_skip_paths() -> list[str]:
    return [
        "/liveness", "/readiness", "/metrics",
        "/docs", "/redoc", "/openapi.json",
        "/favicon.ico", "/startup"
    ]


def _default_tls_items() -> dict[str, str]:
    return {
        "ca_cert": "",
        "tls_cert": "",
        "tls_key": "",
        "tls_passwd": "",
        "tls_crl": "",
        "kmcKsfMaster": "",
        "kmcKsfStandby": ""
    }


class DeployMode(Enum):
    SINGLE_NODE = "single_node"
    PD_SEPARATE = "pd_separate"
    CDP_SEPARATE = "cdp_separate"
    CPCD_SEPARATE = "cpcd_separate"
    PD_DISAGGREGATION_SINGLE_CONTAINER = "pd_disaggregation_single_container"

    @classmethod
    def from_string(cls, value: str) -> Optional['DeployMode']:
        """Convert string to DeployMode enum."""
        try:
            return cls[value.upper()]
        except (KeyError, AttributeError):
            logger.warning(f"Invalid deploy mode: {value}")
            return None


class SchedulerType(Enum):
    LOAD_BALANCE = "load_balance"
    ROUND_ROBIN = "round_robin"
    KV_CACHE_AFFINITY = "kv_cache_affinity"

    @classmethod
    def from_string(cls, value: str) -> Optional['SchedulerType']:
        """Convert string to SchedulerType enum."""
        try:
            return cls[value.upper()]
        except (KeyError, AttributeError):
            logger.warning(f"Invalid scheduler type: {value}")
            return None


@dataclass
class SchedulerConfig:
    deploy_mode: DeployMode = field(default=DeployMode.PD_SEPARATE)
    scheduler_type: SchedulerType = field(default=SchedulerType.LOAD_BALANCE)


@dataclass
class PrometheusMetricsConfig:
    """Prometheus metrics configuration class"""

    reuse_time: int = 3


@dataclass
class ExceptionConfig:
    """Exception handling configuration class"""

    max_retry: int = 5
    retry_delay: float = 0.2
    first_token_timeout: int = 600  # 10 minutes
    infer_timeout: int = 3600  # 60 minutes


@dataclass
class TimeoutConfig:
    request_timeout: int = 30
    connection_timeout: int = 10
    read_timeout: int = 15
    write_timeout: int = 15
    keep_alive_timeout: int = 60


@dataclass
class APIKeyConfig:
    enable_api_key: bool = False
    valid_keys: set[str] = field(default_factory=set)
    header_name: str = "Authorization"
    key_prefix: str = "Bearer "
    skip_paths: set[str] = field(default_factory=_default_skip_paths)
    encryption_algorithm: str = "PBKDF2_SHA256"  # Encryption algorithm to use

    def __post_init__(self):
        """Initialize encryption algorithm after dataclass creation"""
        self._setup_encryption()

    def _setup_encryption(self):
        """Setup the encryption algorithm based on configuration"""

        try:
            set_default_key_encryption_by_name(self.encryption_algorithm)
            logger.info(f"Using encryption algorithm: {self.encryption_algorithm}")
        except ValueError as e:
            logger.error(f"Invalid encryption algorithm: {e}")
            raise ValueError(f"Invalid encryption algorithm '{self.encryption_algorithm}': {e}") from e


@dataclass
class InferenceWorkersConfig:
    num_workers: int = 4  # Number of inference API worker processes; >1 = multiprocess
    # Base port for worker metaserver; 0=disabled. When >0 and num_workers>1 and CDP/PD separation,
    # worker i listens on (host, worker_metaserver_base_port + i).
    worker_metaserver_base_port: int = 12000


@dataclass
class SchedulerProcessConfig:
    """Scheduler process configuration (default only; not user-configurable in first version)."""
    ipc_dir: str = ""  # Base dir for IPC sockets; empty => /tmp. Used to avoid hardcoded /tmp in containers.
    timeout: float = 5.0  # Client request timeout (seconds)
    reconnect_interval: float = 5.0  # Client reconnect interval (seconds)

    @property
    def frontend_address(self) -> str:
        """IPC address for ROUTER (API Server ↔ Scheduler). Derived from ipc_dir."""
        base = (self.ipc_dir or "/tmp").rstrip("/")
        return f"ipc://{base}/scheduler_frontend"

    @property
    def instance_pub_address(self) -> str:
        """IPC address for instance-change PUB. Derived from ipc_dir."""
        base = (self.ipc_dir or "/tmp").rstrip("/")
        return f"ipc://{base}/scheduler_instance_pub"


# First version: single default instance used by all scheduler process / client code.
DEFAULT_SCHEDULER_PROCESS_CONFIG = SchedulerProcessConfig()


@dataclass
class HttpConfig:
    """HTTP configuration class (host/ports, combined mode)."""

    combined_mode: bool = False
    coordinator_api_host: str = field(default_factory=lambda: Env.pod_ip or "127.0.0.1")
    coordinator_api_infer_port: int = 1025
    coordinator_api_mgmt_port: int = 1026


@dataclass
class RateLimitConfig:
    """Rate limiting configuration class"""

    enable_rate_limit: bool = False
    max_requests: int = 1000
    window_size: int = 60
    scope: str = "global"
    skip_paths: list[str] = field(default_factory=_default_rate_limit_skip_paths)
    error_message: str = "too many requests, please try again later"
    error_status_code: int = 429


@dataclass
class ApiConfig:
    """API configuration class"""

    # controller API configuration
    coordinator_api_host: str = field(default_factory=lambda: Env.pod_ip or '127.0.0.1')
    coordinator_api_dns: str = field(default_factory=lambda: Env.coordinator_service or '127.0.0.1')
    coordinator_api_port: int = 1026


@dataclass
class DeployConfig:
    """Deploy configuration class"""

    p_instances_num: int = 1
    d_instances_num: int = 1


@dataclass
class TracerConfig:
    """Tracer configuration class"""
    endpoint: str = ""
    root_sampling_rate: float = 1.0
    remote_parent_sampled: float = 1.0
    remote_parent_not_sampled: float = 1.0
    local_parent_sampled: float = 1.0
    local_parent_not_sampled: float = 1.0


@dataclass
class PrefillKvEventConfig:
    """
    Prefill kv event configuration class
    If the value of conductor_service is empty, the kv conductor is disabled.
    """

    conductor_service: str = field(default_factory=lambda: Env.conductor_service or "")
    http_server_port: int = 13333
    block_size: int = 128
    endpoint: str = ""
    replay_endpoint: str = ""
    engine_type: str = "vLLM"
    model_path: str = ""


@dataclass
class CoordinatorConfig:
    """Coordinator configuration class with validation, reload and error handling support"""

    logging_config: LoggingConfig = field(default_factory=LoggingConfig)
    prometheus_metrics_config: PrometheusMetricsConfig = field(default_factory=PrometheusMetricsConfig)
    exception_config: ExceptionConfig = field(default_factory=ExceptionConfig)
    scheduler_config: SchedulerConfig = field(default_factory=SchedulerConfig)
    inference_workers_config: InferenceWorkersConfig = field(default_factory=InferenceWorkersConfig)
    infer_tls_config: TLSConfig = field(default_factory=TLSConfig)
    mgmt_tls_config: TLSConfig = field(default_factory=TLSConfig)
    etcd_tls_config: TLSConfig = field(default_factory=TLSConfig)
    timeout_config: TimeoutConfig = field(default_factory=TimeoutConfig)
    api_key_config: APIKeyConfig = field(default_factory=APIKeyConfig)
    rate_limit_config: RateLimitConfig = field(default_factory=RateLimitConfig)
    standby_config: StandbyConfig = field(default_factory=StandbyConfig)

    etcd_config: EtcdConfig = field(default_factory=EtcdConfig)
    http_config: HttpConfig = field(default_factory=HttpConfig)
    aigw_model: dict[str, Any] | None = None
    api_config: ApiConfig = field(default_factory=ApiConfig)
    deploy_config: DeployConfig = field(default_factory=DeployConfig)
    tracer_config: TracerConfig = field(default_factory=TracerConfig)
    prefill_kv_event_config: PrefillKvEventConfig = field(default_factory=PrefillKvEventConfig)

    # internal fields
    config_path: str | None = field(default=None, init=False)
    last_modified: float | None = field(default=None, init=False)
    _errors: list[str] = field(default_factory=list, init=False)
    # Set by Worker process at runtime (D direct to Worker metaserver)
    worker_index: Optional[int] = field(default=None, repr=False)
    worker_metaserver_port: Optional[int] = field(default=None, repr=False)

    def __post_init__(self):
        """Validate configuration after initialization"""
        # Refresh master lock key with coordinator prefix
        if self.standby_config.master_lock_key == "/master_lock":
            self.standby_config.master_lock_key = LOCK_SLASH + "coordinator" + self.standby_config.master_lock_key
        self.validate_config()

    @classmethod
    def from_json(cls, json_path: str = None) -> 'CoordinatorConfig':
        """Load configuration from JSON file"""
        if json_path is None:
            # Read from environment variable
            json_path = Env.user_config_path

        config_path = Path(json_path) if json_path else None

        cfg = {}
        user_config_data = None
        try:
            if config_path and config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:  # Only parse if file is not empty
                        raw = json.loads(content)
                        if isinstance(raw, dict) and "motor_coordinator_config" in raw:
                            user_config_data = raw
                            cfg = raw.get("motor_coordinator_config", {})
                        else:
                            cfg = raw
                        tls_configs = [MGMT_TLS_CONFIG, INFER_TLS_CONFIG, ETCD_TLS_CONFIG]
                        _update_tls_config(tls_configs, cfg, raw)   
                        _update_instances_num(cfg, raw)
                        _update_prefill_kv_event_config(cfg, raw)
        except json.JSONDecodeError as e:
            # If JSON parsing fails, use default configuration
            logger.warning(f"Configuration file {json_path} format error: {e}, using default configuration")
        except Exception as e:
            # If any other error occurs, use default configuration
            logger.warning(f"Unable to read configuration file {json_path}: {e}, using default configuration")

        try:
            config = cls()

            def update_config_from_dict(config_obj, config_dict, special_handlers=None):
                """Update configuration object fields from dictionary, only for existing keys.
                Nested dicts are merged into existing dataclass attributes (e.g. inference_workers_config).
                """
                for key, value in config_dict.items():
                    if special_handlers and key in special_handlers:
                        special_handlers[key](config_obj, key, value)
                    elif hasattr(config_obj, key):
                        existing = getattr(config_obj, key)
                        if is_dataclass(existing) and isinstance(value, dict):
                            update_config_from_dict(existing, value, special_handlers)
                        else:
                            setattr(config_obj, key, value)

            def set_enum_field(obj, key, value, enum_class):
                """Set enum field value from string"""
                if isinstance(value, str):
                    enum_value = enum_class.from_string(value)
                    if enum_value is not None:
                        setattr(obj, key, enum_value)

            scheduler_handlers = {
                'deploy_mode': lambda obj, key, value: set_enum_field(obj, key, value, DeployMode),
                'scheduler_type': lambda obj, key, value: set_enum_field(obj, key, value, SchedulerType)
            }

            # Enrich AIGW fields from user_config if present
            if user_config_data and AIGW in cfg:
                try:
                    prefill = user_config_data[ConfigKey.MOTOR_ENGINE_PREFILL.value]
                    decode = user_config_data[ConfigKey.MOTOR_ENGINE_DECODE.value]
                    cfg[AIGW][AIGW_ID] = prefill[MODEL_CONFIG][MODEL_NAME]
                    cfg[AIGW][AIGW_OBJECT] = AIGW_OBJECT_MODEL
                    cfg[AIGW][AIGW_OWNED_BY] = AIGW_OWNED_BY_MOTOR
                    cfg[AIGW][AIGW_P_MAX_SEQLEN] = prefill[ENGINE_CONFIG][MAX_MODEL_LEN]
                    cfg[AIGW][AIGW_D_MAX_SEQLEN] = decode[ENGINE_CONFIG][MAX_MODEL_LEN]
                    cfg[AIGW].setdefault(SLO_TTFT, 1000)
                    cfg[AIGW].setdefault(SLO_TPOT, 50)
                except Exception as e:
                    logger.warning(f"Failed to enrich aigw from user_config: {e}")

            # Update configuration sections if they exist in JSON
            config_mappings = [
                ('logging_config', config.logging_config, None),
                ('prometheus_metrics_config', config.prometheus_metrics_config, None),
                ('exception_config', config.exception_config, None),
                ('scheduler_config', config.scheduler_config, scheduler_handlers),
                ('inference_workers_config', config.inference_workers_config, None),
                ('timeout_config', config.timeout_config, None),
                ('api_key_config', config.api_key_config, None),
                ('rate_limit_config', config.rate_limit_config, None),
                ('standby_config', config.standby_config, None),
                ('etcd_config', config.etcd_config, None),
                ('http_config', config.http_config, None),
                ('infer_tls_config', config.infer_tls_config, None),
                ('mgmt_tls_config', config.mgmt_tls_config, None),
                ('etcd_tls_config', config.etcd_tls_config, None),
                ('api_config', config.api_config, None),
                ('deploy_config', config.deploy_config, None),
                ('tracer_config', config.tracer_config, None),
                ('prefill_kv_event_config', config.prefill_kv_event_config, None),
            ]

            for section_name, config_obj, special_handlers in config_mappings:
                if section_name in cfg:
                    update_config_from_dict(config_obj, cfg[section_name], special_handlers)

            if 'aigw' in cfg:
                config.aigw_model = dict(cfg['aigw'])

            # Set internal fields
            if config_path:
                config.config_path = str(config_path)
                if config_path.exists():
                    config.last_modified = config_path.stat().st_mtime
            else:
                config.config_path = None

            # Re-validate configuration after applying values from JSON
            config.validate_config()

            reconfigure_logging(config.logging_config)

            # Now it's safe to log after logging configuration is set
            if config_path:
                logger.info(f"Loading configuration file: {config_path}")
                if config_path.exists():
                    logger.info(f"Successfully loaded configuration file: {config_path}")
                else:
                    logger.warning(f"Configuration file does not exist, using default configuration: {config_path}")
            else:
                logger.info("No configuration file specified, using default configuration")
            logger.info("Configuration loading completed")

            return config

        except Exception as e:
            logger.error(f"Failed to create configuration instance: {e}")
            raise

    def validate_config(self) -> None:
        """Validate the validity of configuration values"""
        self._errors = []

        # Validate logging configuration
        valid_log_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR']
        if self.logging_config.log_level.upper() not in valid_log_levels:
            self._errors.append(f"log_level must be one of: {', '.join(valid_log_levels)}")

        self._validate_positive_number(self.logging_config.log_max_line_length, "log_max_line_length")

        # Validate timeout configuration
        self._validate_positive_number(self.timeout_config.request_timeout, "request_timeout")
        self._validate_positive_number(self.timeout_config.connection_timeout, "connection_timeout")
        self._validate_positive_number(self.timeout_config.read_timeout, "read_timeout")
        self._validate_positive_number(self.timeout_config.write_timeout, "write_timeout")
        self._validate_positive_number(self.timeout_config.keep_alive_timeout, "keep_alive_timeout")

        # Validate exception configuration
        self._validate_positive_number(self.exception_config.max_retry, "max_retry", allow_zero=True)
        self._validate_positive_number(self.exception_config.retry_delay, "retry_delay")
        self._validate_positive_number(self.exception_config.first_token_timeout, "first_token_timeout")
        self._validate_positive_number(self.exception_config.infer_timeout, "infer_timeout")

        # Validate tracer_config configuration
        self._validate_positive_number(self.tracer_config.root_sampling_rate, "root_sampling_rate")
        self._validate_positive_number(self.tracer_config.remote_parent_sampled, "remote_parent_sampled")
        self._validate_positive_number(self.tracer_config.remote_parent_not_sampled, "remote_parent_not_sampled")
        self._validate_positive_number(self.tracer_config.local_parent_sampled, "local_parent_sampled")
        self._validate_positive_number(self.tracer_config.local_parent_not_sampled, "local_parent_not_sampled")

        # Validate HTTP configuration
        self._validate_port_range(self.http_config.coordinator_api_infer_port, "coordinator_api_infer_port")
        self._validate_port_range(self.http_config.coordinator_api_mgmt_port, "coordinator_api_mgmt_port")
        if self.inference_workers_config.worker_metaserver_base_port != 0:
            self._validate_port_range(
                self.inference_workers_config.worker_metaserver_base_port,
                "worker_metaserver_base_port",
            )
        # Multiprocess: num_workers >= 1; when Worker metaserver enabled,
        # base_port + (num_workers-1) must be valid port
        self._validate_positive_number(
            self.inference_workers_config.num_workers,
            "num_workers",
        )
        iwc = self.inference_workers_config
        if iwc.num_workers > 1 and iwc.worker_metaserver_base_port > 0:
            last_port = iwc.worker_metaserver_base_port + iwc.num_workers - 1
            if last_port > 65535:
                self._errors.append(
                    f"worker_metaserver_base_port({iwc.worker_metaserver_base_port}) + "
                    f"num_workers({iwc.num_workers}) - 1 = {last_port} exceeds max port 65535"
                )

        # Validate host address
        self._validate_ip_or_hostname(self.http_config.coordinator_api_host, "coordinator_api_host")

        # Validate rate limit configuration
        self._validate_positive_number(self.rate_limit_config.max_requests, "max_requests")
        self._validate_positive_number(self.rate_limit_config.window_size, "window_size")

        if not (100 <= self.rate_limit_config.error_status_code <= 599):
            self._errors.append("error_status_code must be in range 100-599")

        # Validate Prometheus metrics configuration
        self._validate_positive_number(self.prometheus_metrics_config.reuse_time, "reuse_time")

        # Validate standby configuration
        self._validate_positive_number(self.standby_config.master_standby_check_interval,
                                       "master_standby_check_interval")
        self._validate_positive_number(self.standby_config.master_lock_ttl, "master_lock_ttl")
        self._validate_positive_number(self.standby_config.master_lock_retry_interval,
                                       "master_lock_retry_interval")
        self._validate_positive_number(self.standby_config.master_lock_max_failures,
                                       "master_lock_max_failures",
                                       allow_zero=True)

        # Validate master lock key path
        self._validate_endpoint_path(self.standby_config.master_lock_key, "master_lock_key")

        # Validate ETCD configuration
        self._validate_port_range(self.etcd_config.etcd_port, "etcd_port")
        self._validate_positive_number(self.etcd_config.etcd_timeout, "etcd_timeout")
        self._validate_ip_or_hostname(self.etcd_config.etcd_host, "etcd_host")

        # Note: TLS certificate file validation is handled by the TLS configuration's check_files flag
        # and is performed during TLS handshake, not during configuration validation

        # Validate API key configuration
        if self.api_key_config.enable_api_key:
            if not self.api_key_config.valid_keys:
                self._errors.append("valid_keys cannot be empty when api_key authentication is enabled")
            if not self.api_key_config.header_name:
                self._errors.append("header_name cannot be empty when api_key authentication is enabled")
            if not self.api_key_config.key_prefix:
                self._errors.append("key_prefix cannot be empty when api_key authentication is enabled")

        if self._errors:
            error_msg = "Configuration validation failed:\n" + "\n".join(f"  - {error}" for error in self._errors)
            logger.error(error_msg)
            raise ValueError(error_msg)

    def get_aigw_models(self) -> Optional[dict[str, Any]]:
        """Return configured AIGW model."""
        return self.aigw_model

    def reload(self) -> bool:
        """Reload configuration file"""
        if not self.config_path or not os.path.exists(self.config_path):
            logger.warning("Configuration file path does not exist, cannot reload")
            return False

        try:
            # Check if file has been modified
            current_mtime = os.path.getmtime(self.config_path)
            if self.last_modified and current_mtime <= self.last_modified:
                logger.debug("Configuration file not modified, skipping reload")
                return True

            logger.info("Configuration file change detected, reloading...")
            new_config = self.from_json(self.config_path)

            # Update current configuration (skip runtime-only fields not from config file)
            _reload_skip = frozenset({"worker_index", "worker_metaserver_port"})
            for field_name in self.__dataclass_fields__:
                if not field_name.startswith('_') and field_name not in _reload_skip:
                    setattr(self, field_name, getattr(new_config, field_name))

            self.last_modified = current_mtime

            reconfigure_logging(self.logging_config)

            logger.info("Configuration reload successful")
            return True

        except Exception as e:
            logger.error(f"Configuration reload failed: {e}")
            return False

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to dictionary with grouped structure"""

        # Use dataclasses.asdict to automatically serialize all config objects
        config_dict = asdict(self)

        # Remove internal fields that shouldn't be in the output
        config_dict.pop('config_path', None)
        config_dict.pop('last_modified', None)

        # Convert enums to their string values for JSON serialization
        if 'scheduler_config' in config_dict:
            scheduler_config = config_dict['scheduler_config']
            if 'deploy_mode' in scheduler_config and isinstance(scheduler_config['deploy_mode'], DeployMode):
                scheduler_config['deploy_mode'] = scheduler_config['deploy_mode'].value
            if 'scheduler_type' in scheduler_config and isinstance(scheduler_config['scheduler_type'], SchedulerType):
                scheduler_config['scheduler_type'] = scheduler_config['scheduler_type'].value
        # Convert sets to lists for JSON serialization
        if 'api_key_config' in config_dict:
            api_key_config = config_dict['api_key_config']
            if 'valid_keys' in api_key_config and isinstance(api_key_config['valid_keys'], set):
                api_key_config['valid_keys'] = list(api_key_config['valid_keys'])
            if 'skip_paths' in api_key_config and isinstance(api_key_config['skip_paths'], set):
                api_key_config['skip_paths'] = list(api_key_config['skip_paths'])

        return config_dict

    def save_to_json(self, json_path: str | None = None) -> bool:
        """Save configuration to JSON file"""
        save_path = json_path or self.config_path
        if not save_path:
            logger.error("Save path not specified")
            return False

        try:
            config_dict = self.to_dict()
            save_config_to_json(
                save_path,
                ConfigKey.MOTOR_COORDINATOR,
                config_dict,
                file_encoding=FILE_ENCODING,
                component_name="coordinator",
            )
            logger.info(f"Configuration saved to: {save_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save configuration: {e}")
            return False

    def get_config_summary(self) -> str:
        """Get configuration summary information"""
        separator = "=" * 80
        title = " " * 20 + "Coordinator Configuration Summary"
        etcd_host = self.etcd_config.etcd_host
        etcd_port = self.etcd_config.etcd_port
        etcd_timeout = self.etcd_config.etcd_timeout
        master_standby_check_interval = self.standby_config.master_standby_check_interval
        master_lock_ttl = self.standby_config.master_lock_ttl
        master_lock_key = self.standby_config.master_lock_key
        return (
            f"{separator}\n"
            f"{title}\n"
            f"{separator}\n"
            "  Deploy Configuration:\n"
            f"    ├─ p_instances_num:     {self.deploy_config.p_instances_num}\n"
            f"    └─ d_instances_num:     {self.deploy_config.d_instances_num}\n"
            "  Logging Configuration:\n"
            f"    ├─ Log Level:           {self.logging_config.log_level}\n"
            f"    ├─ Log File:            {self.logging_config.host_log_dir}\n"
            f"    └─ Log Max Line Length: {self.logging_config.log_max_line_length}\n"
            "\n"
            "  Network Configuration:\n"
            f"    ├─ HTTP Pod IP:         {self.http_config.coordinator_api_host}\n"
            f"    ├─ Inference Port:      {self.http_config.coordinator_api_infer_port}\n"
            f"    ├─ Management Port:     {self.http_config.coordinator_api_mgmt_port}\n"
            f"    └─ Combined Mode:       {'Enabled' if self.http_config.combined_mode else 'Disabled'}\n"
            "\n"
            "  Scheduler Configuration:\n"
            f"    ├─ Deploy Mode:               {self.scheduler_config.deploy_mode.value}\n"
            f"    └─ Scheduler Type:            {self.scheduler_config.scheduler_type.value}\n"
            "\n"
            "  Multiprocess (Inference Workers):\n"
            f"    ├─ Num Workers:               {self.inference_workers_config.num_workers}\n"
            f"    └─ Worker Metaserver Base:    "
            f"{self.inference_workers_config.worker_metaserver_base_port or 'disabled'}\n"
            "\n"
            "  Security:\n"
            f"    ├─ Infer TLS:           {'Enabled' if self.infer_tls_config.enable_tls else 'Disabled'}\n"
            f"    ├─ Management TLS:      {'Enabled' if self.mgmt_tls_config.enable_tls else 'Disabled'}\n"
            f"    ├─ Etcd TLS:            {'Enabled' if self.etcd_tls_config.enable_tls else 'Disabled'}\n"
            f"    ├─ API Key Auth:        {'Enabled' if self.api_key_config.enable_api_key else 'Disabled'}\n"
            f"    └─ Rate Limiting:       {'Enabled' if self.rate_limit_config.enable_rate_limit else 'Disabled'}\n"
            "\n"
            "  High Availability:\n"
            f"    ├─ ETCD:\n"
            f"    │   ├─ Persistence:       {'Enabled' if self.etcd_config.enable_etcd_persistence else 'Disabled'}\n"
            f"    │   ├─ Host:              {etcd_host}\n"
            f"    │   ├─ Port:              {etcd_port}\n"
            f"    │   └─ Timeout:           {etcd_timeout} seconds\n"
            f"    └─ Master/Standby:      {'Enabled' if self.standby_config.enable_master_standby else 'Disabled'}\n"
            f"        ├─ Check Interval:   {master_standby_check_interval} seconds\n"
            f"        ├─ Lock TTL:         {master_lock_ttl} seconds\n"
            f"        └─ Lock Key:         {master_lock_key}\n"
            "\n"
            "  Configuration:\n"
            f"    └─ Config Path:         {self.config_path or 'Not set'}\n"
            f"{separator}"
        )

    def _validate_positive_number(
        self,
        value: float | int,
        field_name: str,
        allow_zero: bool = False
    ) -> None:
        """Validate that a number is positive (optionally allow zero)"""
        if allow_zero and value < 0:
            self._errors.append(f"{field_name} cannot be negative")
        elif not allow_zero and value <= 0:
            self._errors.append(f"{field_name} must be greater than 0")

    def _validate_port_range(self, port: int, field_name: str) -> None:
        """Validate that a port number is in valid range (1-65535)"""
        if not (1 <= port <= 65535):
            self._errors.append(f"{field_name} must be in range 1-65535")

    def _validate_ip_or_hostname(self, value: str, field_name: str) -> None:
        """Validate that a string is a valid IP address or hostname"""
        if not value or not isinstance(value, str):
            self._errors.append(f"{field_name} cannot be empty")
            return

        # Try to parse as IP address first
        try:
            ipaddress.ip_address(value)
            return
        except ValueError:
            pass

        # If not IP, validate as hostname (basic validation)
        if not re.match(
            r'^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*$',
            value
        ):
            self._errors.append(f"{field_name} must be a valid IP address or hostname")

    def _validate_endpoint_path(self, path: str, field_name: str) -> None:
        """Validate that an endpoint path starts with '/' and is not empty"""
        if not path or not isinstance(path, str):
            self._errors.append(f"{field_name} cannot be empty")
        elif not path.startswith('/'):
            self._errors.append(f"{field_name} must start with '/'")