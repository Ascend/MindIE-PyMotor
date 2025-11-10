#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import os
import json
import logging
from typing import Dict, Any, Optional
from enum import Enum
from motor.utils.singleton import ThreadSafeSingleton


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
            logging.warning(f"Invalid deploy mode: {value}")
            return None


class AlgorithmMode(Enum):
    LOAD_BALANCE = "load_balance"
    CACHE_AFFINITY = "cache_affinity"
    ROUND_ROBIN = "round_robin"


class HttpConfig:
    def __init__(self):
        self.connection_pool_max_conn = 10000
        self.server_thread_num = 1
        self.client_thread_num = 1
        self.http_timeout_seconds = 10
        self.keep_alive_seconds = 180
        self.predict_ip = ""
        self.predict_port = ""
        self.manage_ip = ""
        self.manage_port = ""
        self.alarm_port = ""
        self.server_name = ""
        self.user_agent = ""
        self.allow_all_zero_ip_listening = False


class MetricsConfig:
    def __init__(self):
        self.enable = False
        self.trigger_size = 100


class PrometheusMetricsConfig:
    def __init__(self):
        self.reuse_time = 3


class ExceptionConfig:
    def __init__(self):
        self.max_retry = 5
        self.retry_delay = 0.2
        self.schedule_timeout = 60
        self.first_token_timeout = 60
        self.infer_timeout = 300
        self.tokenizer_timeout = 300


class RequestLimit:
    def __init__(self):
        self.conn_max_reqs = 10000
        self.single_node_max_reqs = 1000
        self.max_reqs = 10000
        self.body_limit = 10485760  # 10MB
        self.req_congestion_alarm_threshold = 0.85
        self.req_congestion_clear_threshold = 0.75


class TlsItems:
    def __init__(self):
        self.tls_enable = False
        self.items = {
            "ca_cert": "",
            "tls_cert": "",
            "tls_key": "",
            "tls_passwd": "",
            "tls_crl": "",
            "kmcKsfMaster": "",
            "kmcKsfStandby": ""
        }
        self.check_files = True


class HealthCheckConfig:
    def __init__(self):
        self.dummy_request_interval: float = 5.0
        self.max_consecutive_failures: int = 3
        self.dummy_request_timeout: float = 10.0
        self.controller_base_url: str = "http://localhost:10000"
        self.dummy_request_endpoint: str = '/v1/completions'         
        self.dummy_request_body: dict = {
            'model': 'test-model', 
            'prompt': 'Health check. Please respond with OK only.',
            'max_tokens': 3,
            'temperature': 0.1,
            'top_p': 0.9,
            'stream': False,
        }
        
        self.alarm_endpoint: str = '/v1/alarm/coordinator'
        self.alarm_timeout: float = 5.
        self.terminate_instance_endpoint: str = '/controller/terminate_instance'
        
        self.thread_join_timeout: float = 5.0
        self.error_retry_interval: float = 1.0


class TimeoutConfig:
    def __init__(self):
        self.request_timeout = 30
        self.connection_timeout = 10
        self.read_timeout = 15
        self.write_timeout = 15
        self.keep_alive_timeout = 60


class APIKeyConfig:
    def __init__(self):
        self.enabled = True
        self.valid_keys = set()
        self.header_name = "Authorization"
        self.key_prefix = "Bearer "
        self.skip_paths = set(["/", "/startup", "/readiness", "/liveness", "/health", "/metrics", 
                               "/v1/instances/refresh",
                               "/docs", "/redoc", "/openapi.json", "/favicon.ico"])


class RateLimitConfig:
    def __init__(self):
        self.enabled = True
        self.max_requests = 1000
        self.window_size = 60
        self.scope = "global"
        self.skip_paths = [
            "/health", "/readiness", "/metrics",
            "/docs", "/redoc", "/openapi.json",
            "/favicon.ico", "/startup"
        ]
        self.error_message = "too many requests, please try again later"
        self.error_status_code = 429


class CoordinatorConfig(ThreadSafeSingleton):

    DEFAULT_HOST = "0.0.0.0"
    DEFAULT_MGMT_PORT = 9998
    DEFAULT_INFERENCE_PORT = 9999
    ENABLED_KEY = "enabled"
    VALID_KEY = "valid_keys"
    HEADER_NAME_KEY = "header_name"
    PREFIX_KEY = "key_prefix"
    SKIP_PATHS_KEY = "skip_paths"
    MAX_REQUESTS_KEY = "max_requests"
    WINDOW_SIZE_KEY = "window_size"
    SCOPE_KEY = "scope"
    ERROR_MESSAGE_KEY = "error_message"
    ERROR_STATUS_CODE_KEY = "error_status_code"

    def __init__(self):
        # Prevent re-initialization in singleton
        if hasattr(self, '_initialized'):
            return
            
        self._initialized = False
        self.default_config_file_path = os.path.join(
            os.path.dirname(__file__), 'coordinator_config.json'
        )
        self.config_file_path_env = "MINDIE_MS_COORDINATOR_CONFIG_FILE_PATH"
        self.check_mounted_files = True
        
        # Configuration objects
        self.config = {}
        self.http_config = HttpConfig()
        self.metrics_config = MetricsConfig()
        self.prometheus_metrics_config = PrometheusMetricsConfig()
        self.exception_config = ExceptionConfig()
        self.req_limit = RequestLimit()
        self.health_check_config = HealthCheckConfig()
        self.scheduler_config = {}
        self.controller_server_tls = TlsItems()
        self.request_server_tls = TlsItems()
        self.mindie_client_tls = TlsItems()
        self.mindie_mgmt_tls = TlsItems()
        self.alarm_client_tls = TlsItems()
        self.health_check_config = HealthCheckConfig()
        self.timeout_config = TimeoutConfig()
        self.api_key_config = APIKeyConfig()
        self.rate_limit_config = RateLimitConfig()
        
        # Runtime flags and settings
        self.is_master = False
        self.is_abnormal = False
        self.backup_enable = False
        self.str_token_rate = 4.2
        self.combined_mode = False
        self.combined_host = self.DEFAULT_HOST
        self.combined_port = self.DEFAULT_INFERENCE_PORT
        self.mgmt_host = self.DEFAULT_HOST
        self.mgmt_port = self.DEFAULT_MGMT_PORT
        self.inference_host = self.DEFAULT_HOST
        self.inference_port = self.DEFAULT_INFERENCE_PORT

        
        # Auto-initialize on creation
        self._initialize()

    def _initialize(self) -> None:
        try:
            config_file = os.getenv(self.config_file_path_env, self.default_config_file_path)
            self.check_mounted_files = self._get_check_files()
            
            if not os.path.exists(config_file):
                logging.error(f"Configuration file not found: {config_file}")
                raise FileNotFoundError(f"Configuration file not found: {config_file}")
            
            with open(config_file, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
            
            if not self._validate_config():
                raise ValueError("Configuration validation failed")
            
            self._load_all_configs()
            self._initialized = True
            logging.info("Coordinator configuration initialized successfully")
            
        except Exception as e:
            logging.error(f"Failed to initialize coordinator configuration: {e}")
            raise

    def _get_check_files(self) -> bool:
        """Get file permission check setting from environment."""
        check_files_env = os.getenv("MINDIE_CHECK_INPUTFILES_PERMISSION", "1")
        return check_files_env != "0"

    def _validate_config(self) -> bool:
        """Validate overall configuration structure."""
        try:
            scheduler_config = self.config.get("digs_scheduler_config", {})
            return self._is_scheduler_config_valid(scheduler_config)
        except Exception as e:
            logging.error(f"Configuration validation error: {e}")
            return False

    def _load_all_configs(self) -> None:
        """Load all configuration sections."""
        config_loaders = [
            self._load_http_config,
            self._load_metrics_config,
            self._load_prometheus_metrics_config,
            self._load_exception_config,
            self._load_request_limit,
            self._load_tls_config,
            self._load_scheduler_config,
            self._load_str_token_rate,
            self._load_health_check_config,
            self._load_combined_config,
            self._load_server_config,
            self._load_timeout_config,
            self._load_api_key_config,
            self._load_rate_limit_config
        ]
        
        for loader in config_loaders:
            try:
                loader()
            except Exception as e:
                logging.error(f"Failed to load configuration section {loader.__name__}: {e}")
                raise

    def _load_http_config(self) -> None:
        """Load HTTP configuration section."""
        config = self.config.get("http_config", {})
        
        http_mappings = {
            "predict_ip": (str, ""),
            "predict_port": (str, ""),
            "manage_ip": (str, ""),
            "manage_port": (str, ""),
            "alarm_port": (str, ""),
            "server_thread_num": (int, 1),
            "client_thread_num": (int, 1),
            "http_timeout_seconds": (int, 10),
            "keep_alive_seconds": (int, 180),
            "server_name": (str, ""),
            "user_agent": (str, ""),
            "allow_all_zero_ip_listening": (bool, False)
        }
        
        for field, (field_type, default) in http_mappings.items():
            value = config.get(field, default)
            if isinstance(value, field_type):
                setattr(self.http_config, field, value)
            else:
                raise ValueError(f"Invalid type for HTTP config field '{field}': expected {field_type}")

    def _load_metrics_config(self) -> None:
        """Load metrics configuration section."""
        config = self.config.get("metrics_config", {})
        
        metrics_mappings = {
            "enable": (bool, False),
            "trigger_size": (int, 100)
        }
        
        for field, (field_type, default) in metrics_mappings.items():
            value = config.get(field, default)
            if isinstance(value, field_type):
                setattr(self.metrics_config, field, value)
            else:
                raise ValueError(f"Invalid type for Metrics config field '{field}': expected {field_type}")

    def _load_prometheus_metrics_config(self) -> None:
        """Load Prometheus metrics configuration section."""
        config = self.config.get("prometheus_metrics_config", {})
        
        value = config.get("reuse_time", 3)
        if isinstance(value, int):
            self.prometheus_metrics_config.reuse_time = value
        else:
            raise ValueError("Invalid type for Prometheus metrics config field 'reuse_time': expected int")

    def _load_exception_config(self) -> None:
        """Load exception handling configuration section."""
        config = self.config.get("exception_config", {})
        
        exception_mappings = {
            "max_retry": (int, 5),
            "retry_delay": (float, 0.2),
            "schedule_timeout": (int, 60),
            "first_token_timeout": (int, 60),
            "infer_timeout": (int, 300),
            "tokenizer_timeout": (int, 300)
        }
        
        for field, (field_type, default) in exception_mappings.items():
            value = config.get(field, default)
            if isinstance(value, field_type):
                setattr(self.exception_config, field, value)
            else:
                raise ValueError(f"Invalid type for Exception config field '{field}': expected {field_type}")

    def _load_request_limit(self) -> None:
        """Load request limiting configuration section."""
        config = self.config.get("request_limit", {})
        
        request_limit_mappings = {
            "single_node_max_requests": ("single_node_max_reqs", int, 1000),
            self.MAX_REQUESTS_KEY: ("max_reqs", int, 10000),
            "body_limit": ("body_limit", int, 10485760)
        }
        
        for config_field, (attr_field, field_type, default) in request_limit_mappings.items():
            value = config.get(config_field, default)
            if isinstance(value, field_type):
                setattr(self.req_limit, attr_field, value)
            else:
                raise ValueError(f"Invalid type for Request Limit config field '{config_field}': expected {field_type}")
        
        # Handle environment variable overrides
        self._handle_request_limit_env()

    def _handle_request_limit_env(self) -> None:
        """Handle environment variable overrides for request limits."""
        env_mappings = {
            "MINDIE_MS_COORDINATOR_CONFIG_SINGLE_NODE_MAX_REQ": "single_node_max_reqs",
            "MINDIE_MS_COORDINATOR_CONFIG_MAX_REQ": "max_reqs"
        }
        
        for env_var, attr_name in env_mappings.items():
            env_value = os.getenv(env_var)
            if env_value:
                try:
                    setattr(self.req_limit, attr_name, int(env_value))
                except ValueError as e:
                    logging.warning(f"Invalid environment variable {env_var}: {e}")

    def _load_tls_config(self) -> None:
        """Load TLS configuration section."""
        config = self.config.get("tls_config", {})
        
        tls_components = [
            ("controller_server_tls_enable", "controller_server_tls_items", self.controller_server_tls),
            ("request_server_tls_enable", "request_server_tls_items", self.request_server_tls),
            ("mindie_client_tls_enable", "mindie_client_tls_items", self.mindie_client_tls),
            ("mindie_management_tls_enable", "mindie_management_tls_items", self.mindie_mgmt_tls),
            ("alarm_client_tls_enable", "alarm_client_tls_items", self.alarm_client_tls)
        ]
        
        for enable_field, items_field, tls_obj in tls_components:
            if config.get(enable_field, False):
                items = config.get(items_field, {})
                if self._validate_tls_items(items):
                    tls_obj.tls_enable = True
                    tls_obj.items = items
                    tls_obj.check_files = self.check_mounted_files
                else:
                    raise ValueError(f"Invalid TLS configuration for {enable_field}")

    def _validate_tls_items(self, items: Dict[str, str]) -> bool:
        """Validate TLS configuration items."""
        required_items = [
            "ca_cert", "tls_cert", "tls_key", "tls_passwd", 
            "kmcKsfMaster", "kmcKsfStandby"
        ]
        
        return all(item in items for item in required_items)

    def _load_scheduler_config(self) -> None:
        """Load scheduler configuration section."""
        config = self.config.get("digs_scheduler_config", {})
        if not self._is_scheduler_config_valid(config):
            raise ValueError("Invalid scheduler configuration")
        self.scheduler_config = dict(config.items())

    def _is_scheduler_config_valid(self, config: Dict[str, Any]) -> bool:
        """Validate scheduler configuration structure and values."""
        required_fields = [
            ("deploy_mode", str),
            ("scheduler_type", str),
            ("algorithm_type", str)
        ]
        
        # Check required fields existence and type
        for field, field_type in required_fields:
            if field not in config or not isinstance(config[field], field_type):
                logging.error(f"Missing or invalid scheduler configuration field: {field}")
                return False
        
        # Validate enum values
        deploy_mode = DeployMode.from_string(config["deploy_mode"])
        if not deploy_mode:
            return False
            
        if config["scheduler_type"] not in ["default_scheduler", "digs_scheduler"]:
            logging.error(f"Invalid scheduler_type: {config['scheduler_type']}")
            return False
            
        algorithm_values = [mode.value for mode in AlgorithmMode]
        if config["algorithm_type"] not in algorithm_values:
            logging.error(f"Invalid algorithm_type: {config['algorithm_type']}")
            return False
            
        return True

    def _load_str_token_rate(self) -> None:
        """Load string token rate configuration."""
        str_token_rate = self.config.get("string_token_rate", 4.2)
        
        if not isinstance(str_token_rate, (int, float)):
            raise ValueError("string_token_rate must be a number")
            
        if not 1.0 <= float(str_token_rate) <= 100.0:
            raise ValueError("string_token_rate must be in the range [1.0, 100.0]")
            
        self.str_token_rate = float(str_token_rate)

    def _load_health_check_config(self) -> None:
        """Load health check configuration section."""
        config = self.config.get("health_check_config", {})
        
        health_check_mappings = {
            "dummy_request_interval": (float, 5.0),
            "max_consecutive_failures": (int, 3),
            "dummy_request_timeout": (float, 10.0),
            "controller_base_url": (str, "http://localhost:10000")
        }
        
        for field, (field_type, default) in health_check_mappings.items():
            value = config.get(field, default)
            if isinstance(value, field_type):
                setattr(self.health_check_config, field, value)
            else:
                logging.warning(f"Invalid type for Health Check config field '{field}', using default")

    def _load_combined_config(self) -> None:
        """Load combined mode configuration section."""
        combined_mode = self.config.get("combined_mode", False)
        if isinstance(combined_mode, bool):
            self.combined_mode = combined_mode
        else:
            raise ValueError("combined_mode must be a boolean")

        combined_host = self.config.get("combined_host", "0.0.0.0")
        if isinstance(combined_host, str):
            self.combined_host = combined_host
        else:
            raise ValueError("combined_host must be a string")

        combined_port = self.config.get("combined_port", 9999)
        if isinstance(combined_port, int) and 1 <= combined_port <= 65535:
            self.combined_port = combined_port
        else:
            raise ValueError("combined_port must be an integer between 1 and 65535")

    def _load_server_config(self) -> None:
        """Load server configuration section."""
        mgmt_host = self.config.get("mgmt_host", "0.0.0.0")
        if isinstance(mgmt_host, str):
            self.mgmt_host = mgmt_host
        else:
            raise ValueError("mgmt_host must be a string")

        mgmt_port = self.config.get("mgmt_port", 9998)
        if isinstance(mgmt_port, int) and 1 <= mgmt_port <= 65535:
            self.mgmt_port = mgmt_port
        else:
            raise ValueError("mgmt_port must be an integer between 1 and 65535")

        inference_host = self.config.get("inference_host", "0.0.0.0")
        if isinstance(inference_host, str):
            self.inference_host = inference_host
        else:
            raise ValueError("inference_host must be a string")

        inference_port = self.config.get("inference_port", 9999)
        if isinstance(inference_port, int) and 1 <= inference_port <= 65535:
            self.inference_port = inference_port
        else:
            raise ValueError("inference_port must be an integer between 1 and 65535")

    def _load_timeout_config(self) -> None:
        """Load timeout configuration section."""
        config = self.config.get("timeout_config", {})
        
        if config:
            timeout_fields = {
                "request_timeout": int,
                "connection_timeout": int,
                "read_timeout": int,
                "write_timeout": int,
                "keep_alive_timeout": int
            }

            for field, field_type in timeout_fields.items():
                if field not in config:
                    continue
                
                if not isinstance(config[field], field_type):
                    logging.error(f"Invalid timeout configuration parameter: {field}, expected {field_type.__name__}")
                    raise ValueError(f"Invalid timeout configuration parameter: {field}")
                
                setattr(self.timeout_config, field, config[field])

        env_mappings = [
            ("TIMEOUT_REQUEST", "request_timeout", 30),
            ("TIMEOUT_CONNECTION", "connection_timeout", 10),
            ("TIMEOUT_READ", "read_timeout", 15),
            ("TIMEOUT_WRITE", "write_timeout", 15),
            ("TIMEOUT_KEEP_ALIVE", "keep_alive_timeout", 60)
        ]
        
        for env_var, attr_name, _ in env_mappings:
            env_value = os.getenv(env_var)
            if env_value is None:
                continue
                
            try:
                setattr(self.timeout_config, attr_name, int(env_value))
            except ValueError:
                logging.error(f"Invalid {env_var} value: {env_value}")

    def _load_api_key_config(self) -> None:
        """Load API key configuration section."""
        config = self.config.get("api_key_config", {})
        
        if config:
            if self.ENABLED_KEY in config:
                if isinstance(config[self.ENABLED_KEY], bool):
                    self.api_key_config.enabled = config[self.ENABLED_KEY]
                else:
                    logging.error("api_key_config.enabled must be a boolean")
                    raise ValueError("api_key_config.enabled must be a boolean")
            
            if self.VALID_KEY in config:
                if isinstance(config[self.VALID_KEY], list):
                    self.api_key_config.valid_keys = set(config[self.VALID_KEY])
                else:
                    logging.error("api_key_config.valid_keys must be a list")
                    raise ValueError("api_key_config.valid_keys must be a list")
            
            if self.HEADER_NAME_KEY in config:
                if isinstance(config[self.HEADER_NAME_KEY], str):
                    self.api_key_config.header_name = config[self.HEADER_NAME_KEY]
                else:
                    logging.error("api_key_config.header_name must be a string")
                    raise ValueError("api_key_config.header_name must be a string")
            
            if self.PREFIX_KEY in config:
                if isinstance(config[self.PREFIX_KEY], str):
                    self.api_key_config.key_prefix = config[self.PREFIX_KEY]
                else:
                    logging.error("api_key_config.key_prefix must be a string")
                    raise ValueError("api_key_config.key_prefix must be a string")
            
            if self.SKIP_PATHS_KEY in config:
                if isinstance(config[self.SKIP_PATHS_KEY], list):
                    self.api_key_config.skip_paths = set(config[self.SKIP_PATHS_KEY])
                else:
                    logging.error("api_key_config.skip_paths must be a list")
                    raise ValueError("api_key_config.skip_paths must be a list")

        # Handle environment variable overrides
        if os.getenv("API_KEY_ENABLED") is not None:
            self.api_key_config.enabled = os.getenv("API_KEY_ENABLED", "true").lower() in ("true", "1", "yes")
        
        if os.getenv("API_KEY_VALID_KEYS") is not None:
            valid_keys_str = os.getenv("API_KEY_VALID_KEYS", "")
            if valid_keys_str:
                self.api_key_config.valid_keys = \
                    set([key.strip() for key in valid_keys_str.split(",") if key.strip()])
        
        if os.getenv("API_KEY_SKIP_PATHS") is not None:
            skip_paths_str = os.getenv("API_KEY_SKIP_PATHS", "")
            if skip_paths_str:
                self.api_key_config.skip_paths = \
                    set([path.strip() for path in skip_paths_str.split(",") if path.strip()])

        if self.api_key_config.enabled and not self.api_key_config.valid_keys:
            logging.warning("API Key validation enabled but no valid keys configured!")

    def _load_rate_limit_config(self) -> None:
        """Load rate limiting configuration section."""
        config = self.config.get("rate_limit_config", {})
        
        if config:
            if self.ENABLED_KEY in config:
                if isinstance(config[self.ENABLED_KEY], bool):
                    self.rate_limit_config.enabled = config[self.ENABLED_KEY]
                else:
                    logging.error("rate_limit_config.enabled must be a boolean")
                    raise ValueError("rate_limit_config.enabled must be a boolean")
            
            if self.MAX_REQUESTS_KEY in config:
                if isinstance(config[self.MAX_REQUESTS_KEY], int):
                    self.rate_limit_config.max_requests = config[self.MAX_REQUESTS_KEY]
                else:
                    logging.error("rate_limit_config.max_requests must be an integer")
                    raise ValueError("rate_limit_config.max_requests must be an integer")
            
            if self.WINDOW_SIZE_KEY in config:
                if isinstance(config[self.WINDOW_SIZE_KEY], int):
                    self.rate_limit_config.window_size = config[self.WINDOW_SIZE_KEY]
                else:
                    logging.error("rate_limit_config.window_size must be an integer")
                    raise ValueError("rate_limit_config.window_size must be an integer")
            
            if self.SCOPE_KEY in config:
                if isinstance(config[self.SCOPE_KEY], str):
                    self.rate_limit_config.scope = config[self.SCOPE_KEY]
                else:
                    logging.error("rate_limit_config.scope must be a string")
                    raise ValueError("rate_limit_config.scope must be a string")
            
            if self.SKIP_PATHS_KEY in config:
                if isinstance(config[self.SKIP_PATHS_KEY], list):
                    self.rate_limit_config.skip_paths = config[self.SKIP_PATHS_KEY]
                else:
                    logging.error("rate_limit_config.skip_paths must be a list")
                    raise ValueError("rate_limit_config.skip_paths must be a list")
            
            if self.ERROR_MESSAGE_KEY in config:
                if isinstance(config[self.ERROR_MESSAGE_KEY], str):
                    self.rate_limit_config.error_message = config[self.ERROR_MESSAGE_KEY]
                else:
                    logging.error("rate_limit_config.error_message must be a string")
                    raise ValueError("rate_limit_config.error_message must be a string")
            
            if self.ERROR_STATUS_CODE_KEY in config:
                if isinstance(config[self.ERROR_STATUS_CODE_KEY], int):
                    self.rate_limit_config.error_status_code = config[self.ERROR_STATUS_CODE_KEY]
                else:
                    logging.error("rate_limit_config.error_status_code must be an integer")
                    raise ValueError("rate_limit_config.error_status_code must be an integer")

        # Handle environment variable overrides
        if os.getenv("RATE_LIMIT_ENABLED") is not None:
            self.rate_limit_config.enabled = os.getenv("RATE_LIMIT_ENABLED", "true").lower() in ("true", "1", "yes")
        
        rate_limit_value = os.getenv("RATE_LIMIT_MAX_REQUESTS")
        if rate_limit_value is not None:
            rate_limit_value = rate_limit_value.strip()
            if rate_limit_value != "":
                try:
                    self.rate_limit_config.max_requests = int(rate_limit_value)
                except ValueError:
                    logging.error(f"Invalid RATE_LIMIT_MAX_REQUESTS value: {rate_limit_value}")

        rate_limit_window_size = os.getenv("RATE_LIMIT_WINDOW_SIZE")
        if rate_limit_window_size is not None:
            try:
                self.rate_limit_config.window_size = int(rate_limit_window_size)
            except ValueError:
                logging.error(f"Invalid RATE_LIMIT_WINDOW_SIZE value: {os.getenv('RATE_LIMIT_WINDOW_SIZE')}")
        
        rate_limit_scope = os.getenv("RATE_LIMIT_SCOPE")
        if rate_limit_scope is not None:
            self.rate_limit_config.scope = rate_limit_scope
        
        if os.getenv("RATE_LIMIT_SKIP_PATHS") is not None:
            skip_paths_str = os.getenv("RATE_LIMIT_SKIP_PATHS", "")
            if skip_paths_str:
                self.rate_limit_config.skip_paths = [path.strip() for path in skip_paths_str.split(",") if path.strip()]
