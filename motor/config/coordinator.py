# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import os
import json
import re
import ipaddress
from typing import Optional, Any
from enum import Enum
from dataclasses import dataclass, field, asdict
from pathlib import Path

from motor.common.utils.logger import LoggingConfig, reconfigure_logging, get_logger
from motor.common.utils.env import Env
from motor.config.etcd import EtcdConfig
from motor.config.standby import StandbyConfig, LOCK_SLASH

logger = get_logger(__name__)


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


def _default_dummy_request_body() -> dict:
    return {
        'model': 'test-model',
        'prompt': 'Health check. Please respond with OK only.',
        'message': "[{'role': 'user', 'content': 'hi'}]",
        'max_tokens': 3,
        'temperature': 0.1,
        'top_p': 0.9,
        'stream': False,
    }


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

    @classmethod
    def from_string(cls, value: str) -> Optional['SchedulerType']:
        """Convert string to SchedulerType enum."""
        try:
            return cls[value.upper()]
        except (KeyError, AttributeError):
            logger.warning(f"Invalid deploy mode: {value}")
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
    infer_timeout: int = 600  # 10 minutes


@dataclass
class TLSConfig:
    enable_tls: bool = False
    items: dict[str, str] = field(default_factory=_default_tls_items)
    check_files: bool = True


@dataclass
class HealthCheckConfig:
    dummy_request_interval: float = 5.0
    max_consecutive_failures: int = 3
    dummy_request_timeout: float = 10.0
    controller_api_dns: str = "mindie-ms-controller-service.mindie.svc.cluster.local"
    controller_api_port: int = 1026
    dummy_request_endpoint: str = '/v1/completions'
    dummy_request_body: dict = field(default_factory=_default_dummy_request_body)
    alarm_endpoint: str = '/v1/alarm/coordinator'
    alarm_timeout: float = 5.0
    terminate_instance_endpoint: str = '/controller/terminate_instance'
    thread_join_timeout: float = 5.0
    error_retry_interval: float = 1.0


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


@dataclass
class HttpConfig:
    """HTTP configuration class"""

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
class CoordinatorConfig:
    """Coordinator configuration class with validation, reload and error handling support"""

    logging_config: LoggingConfig = field(default_factory=LoggingConfig)
    prometheus_metrics_config: PrometheusMetricsConfig = field(default_factory=PrometheusMetricsConfig)
    exception_config: ExceptionConfig = field(default_factory=ExceptionConfig)
    scheduler_config: SchedulerConfig = field(default_factory=SchedulerConfig)
    tls_config: TLSConfig = field(default_factory=TLSConfig)
    health_check_config: HealthCheckConfig = field(default_factory=HealthCheckConfig)
    timeout_config: TimeoutConfig = field(default_factory=TimeoutConfig)
    api_key_config: APIKeyConfig = field(default_factory=APIKeyConfig)
    rate_limit_config: RateLimitConfig = field(default_factory=RateLimitConfig)
    standby_config: StandbyConfig = field(default_factory=StandbyConfig)
    etcd_config: EtcdConfig = field(default_factory=EtcdConfig)
    http_config: HttpConfig = field(default_factory=HttpConfig)
    aigw_model: dict[str, Any] | None = None

    # internal fields
    config_path: str | None = field(default=None, init=False)
    last_modified: float | None = field(default=None, init=False)

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
            json_path = os.getenv("MOTOR_COORDINATOR_CONFIG_PATH")

        config_path = Path(json_path) if json_path else None

        cfg = {}
        try:
            if config_path and config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:  # Only parse if file is not empty
                        cfg = json.loads(content)
        except json.JSONDecodeError as e:
            # If JSON parsing fails, use default configuration
            logger.warning(f"Configuration file {json_path} format error: {e}, using default configuration")
        except Exception as e:
            # If any other error occurs, use default configuration
            logger.warning(f"Unable to read configuration file {json_path}: {e}, using default configuration")

        try:
            config = cls()

            def update_config_from_dict(config_obj, config_dict, special_handlers=None):
                """Update configuration object fields from dictionary, only for existing keys"""
                for key, value in config_dict.items():
                    if special_handlers and key in special_handlers:
                        special_handlers[key](config_obj, key, value)
                    elif hasattr(config_obj, key):
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

            # Update configuration sections if they exist in JSON
            config_mappings = [
                ('logging_config', config.logging_config, None),
                ('prometheus_metrics_config', config.prometheus_metrics_config, None),
                ('exception_config', config.exception_config, None),
                ('scheduler_config', config.scheduler_config, scheduler_handlers),
                ('health_check_config', config.health_check_config, None),
                ('timeout_config', config.timeout_config, None),
                ('api_key_config', config.api_key_config, None),
                ('rate_limit_config', config.rate_limit_config, None),
                ('standby_config', config.standby_config, None),
                ('etcd_config', config.etcd_config, None),
                ('http_config', config.http_config, None),
            ]

            for section_name, config_obj, special_handlers in config_mappings:
                if section_name in cfg:
                    update_config_from_dict(config_obj, cfg[section_name], special_handlers)

            # Handle TLS config separately due to its special structure
            if 'tls_config' in cfg:
                tls_config = cfg['tls_config']
                if 'request_server_tls_enable' in tls_config and tls_config['request_server_tls_enable']:
                    config.tls_config.enable_tls = True
                    if 'request_server_tls_items' in tls_config:
                        config.tls_config.items.update(tls_config['request_server_tls_items'])
                    config.tls_config.check_files = config.check_mounted_files

                if 'etcd_client_tls_enable' in tls_config and tls_config['etcd_client_tls_enable']:
                    config.etcd_client_tls.enable_tls = True
                    if 'etcd_client_tls_items' in tls_config:
                        config.etcd_client_tls.items.update(tls_config['etcd_client_tls_items'])
                    config.etcd_client_tls.check_files = config.check_mounted_files

            if 'aigw' in cfg:
                config.aigw_model = dict(cfg['aigw'])

            # Set internal fields
            if config_path:
                config.config_path = str(config_path)
                if config_path.exists():
                    config.last_modified = config_path.stat().st_mtime
            else:
                config.config_path = None

            # Configure logging for this module with the loaded configuration
            from motor.common.utils.logger import set_logging_config_for_module
            set_logging_config_for_module(
                'motor.config.coordinator',
                log_config=config.logging_config
            )

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

        # Validate health check configuration
        self._validate_positive_number(self.health_check_config.dummy_request_interval,
                                       "dummy_request_interval")
        self._validate_positive_number(self.health_check_config.max_consecutive_failures,
                                       "max_consecutive_failures")
        self._validate_positive_number(self.health_check_config.dummy_request_timeout,
                                       "dummy_request_timeout")
        self._validate_port_range(self.health_check_config.controller_api_port, "controller_api_port")

        # Validate DNS hostname
        self._validate_ip_or_hostname(self.health_check_config.controller_api_dns, "controller_api_dns")

        # Validate endpoint paths
        self._validate_endpoint_path(self.health_check_config.dummy_request_endpoint,
                                     "dummy_request_endpoint")
        self._validate_endpoint_path(self.health_check_config.alarm_endpoint, "alarm_endpoint")
        self._validate_endpoint_path(self.health_check_config.terminate_instance_endpoint,
                                     "terminate_instance_endpoint")

        self._validate_positive_number(self.health_check_config.alarm_timeout, "alarm_timeout")
        self._validate_positive_number(self.health_check_config.thread_join_timeout, "thread_join_timeout")
        self._validate_positive_number(self.health_check_config.error_retry_interval, "error_retry_interval")

        # Validate HTTP configuration
        self._validate_port_range(self.http_config.coordinator_api_infer_port, "coordinator_api_infer_port")
        self._validate_port_range(self.http_config.coordinator_api_mgmt_port, "coordinator_api_mgmt_port")

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

            # Update current configuration
            for field_name in self.__dataclass_fields__:
                if not field_name.startswith('_'):
                    setattr(self, field_name, getattr(new_config, field_name))

            self.last_modified = current_mtime

            # Reconfigure logging for this module with new settings
            from motor.common.utils.logger import set_logging_config_for_module
            set_logging_config_for_module(
                'motor.config.coordinator',
                log_config=self.logging_config
            )

            # Reconfigure logging with new settings
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
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(config_dict, f, indent=2, ensure_ascii=False)
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
            "  Logging Configuration:\n"
            f"    ├─ Log Level:           {self.logging_config.log_level}\n"
            f"    └─ Log Max Line Length: {self.logging_config.log_max_line_length}\n"
            "\n"
            "  Network Configuration:\n"
            f"    ├─ HTTP Pod IP:         {self.http_config.coordinator_api_host}\n"
            f"    ├─ Inference Port:      {self.http_config.coordinator_api_infer_port}\n"
            f"    ├─ Management Port:     {self.http_config.coordinator_api_mgmt_port}\n"
            f"    └─ Combined Mode:       {'Enabled' if self.http_config.combined_mode else 'Disabled'}\n"
            "\n"
            "  Scheduler Configuration:\n"
            f"    ├─ Deploy Mode:         {self.scheduler_config.deploy_mode.value}\n"
            f"    └─ Scheduler Type:      {self.scheduler_config.scheduler_type.value}\n"
            "\n"
            "  Security:\n"
            f"    ├─ TLS:                 {'Enabled' if self.tls_config.enable_tls else 'Disabled'}\n"
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
