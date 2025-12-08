# coding=utf-8
# Copyright (c) 2025, HUAWEI CORPORATION.  All rights reserved.
import os
import json
from dataclasses import dataclass, field, asdict
from typing import Any
from pathlib import Path

from motor.common.utils.logger import get_logger, LoggingConfig, reconfigure_logging
from motor.config.standby import StandbyConfig


logger = get_logger(__name__)


@dataclass
class ApiConfig:
    """API configuration class"""

    # controller API configuration
    controller_api_host: str = '127.0.0.1'
    controller_api_port: int = 8000

    # coordinator API configuration
    coordinator_api_dns: str = '127.0.0.1'
    coordinator_api_port: int = 1026


@dataclass
class TlsConfig:
    """TLS configuration class"""

    # TLS enable/disable
    enable_tls: bool = False

    # certificate paths
    ca_cert_path: str = 'security/controller/cert/ca.crt'
    cert_path: str = 'security/controller/cert/server.crt'
    key_path: str = 'security/controller/keys/server.key'


@dataclass
class InstanceConfig:
    """Instance management configuration class"""

    # instance assembler configuration
    instance_assemble_timeout: int = 600  # 600 seconds
    instance_assembler_check_internal: int = 1  # 1 second
    instance_assembler_cmd_send_internal: int = 1  # 1 second

    # instance manager configuration
    instance_manager_check_internal: int = 1  # 1 second
    instance_heartbeat_timeout: int = 5  # 5 seconds
    instance_expired_timeout: int = 300  # 300 seconds

    # other instance configuration
    send_cmd_retry_times: int = 3


@dataclass
class EventPusherConfig:
    """Event configuration class"""

    # event consumer configuration
    event_consumer_sleep_interval: float = 1.0  # 1 second

    # coordinator heartbeat configuration
    coordinator_heartbeat_interval: float = 0.5  # 0.5 seconds


@dataclass
class FaultToleranceConfig:
    """Fault tolerance configuration class"""

    # fault tolerance enable/disable
    enable_fault_tolerance: bool = True

    # strategy center configuration
    strategy_center_check_internal: int = 1  # 1 second

    # scale and recovery configuration
    enable_scale_p2d: bool = True  # Enable/disable scale p2d strategy
    enable_lingqu_network_recover: bool = True  # Enable/disable lingqu network recovery strategy


@dataclass
class EtcdConfig:
    """ETCD configuration class"""

    # ETCD connection configuration
    etcd_host: str = 'localhost'
    etcd_port: int = 2379
    etcd_timeout: int = 5

    # ETCD certificate configuration
    etcd_ca_cert: str | None = None
    etcd_cert_key: str | None = None
    etcd_cert_cert: str | None = None

    # ETCD persistence configuration
    enable_etcd_persistence: bool = False  # Enable/disable ETCD persistence and restoration


@dataclass
class ControllerConfig:
    """Controller configuration class with validation, reload and error handling support"""

    # Configuration sections
    logging_config: LoggingConfig = field(default_factory=LoggingConfig)
    api_config: ApiConfig = field(default_factory=ApiConfig)
    tls_config: TlsConfig = field(default_factory=TlsConfig)
    instance_config: InstanceConfig = field(default_factory=InstanceConfig)
    event_config: EventPusherConfig = field(default_factory=EventPusherConfig)
    fault_tolerance_config: FaultToleranceConfig = field(default_factory=FaultToleranceConfig)
    standby_config: StandbyConfig = field(default_factory=StandbyConfig)
    etcd_config: EtcdConfig = field(default_factory=EtcdConfig)

    # internal fields
    config_path: str | None = field(default=None, init=False)
    last_modified: float | None = field(default=None, init=False)

    def __post_init__(self):
        """Validate configuration after initialization"""
        self.validate_config()

    @classmethod
    def from_json(cls, json_path: str) -> 'ControllerConfig':
        """Load configuration from JSON file"""
        config_path = Path(json_path)

        cfg = {}
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"Configuration file {json_path} format error: {e}") from e
            except Exception as e:
                raise ValueError(f"Unable to read configuration file {json_path}: {e}") from e

        # Create configuration instance with default values
        try:
            config = cls()

            # Helper function to update config object from dict
            def update_config_from_dict(config_obj, config_dict):
                """Update configuration object fields from dictionary, only for existing keys"""
                for key, value in config_dict.items():
                    if hasattr(config_obj, key):
                        setattr(config_obj, key, value)

            # Update configuration sections if they exist in JSON
            if 'logging_config' in cfg:
                update_config_from_dict(config.logging_config, cfg['logging_config'])

            if 'api_config' in cfg:
                update_config_from_dict(config.api_config, cfg['api_config'])

            if 'tls_config' in cfg:
                update_config_from_dict(config.tls_config, cfg['tls_config'])

            if 'instance_config' in cfg:
                update_config_from_dict(config.instance_config, cfg['instance_config'])

            if 'event_config' in cfg:
                update_config_from_dict(config.event_config, cfg['event_config'])

            if 'fault_tolerance_config' in cfg:
                update_config_from_dict(config.fault_tolerance_config, cfg['fault_tolerance_config'])

            if 'standby_config' in cfg:
                update_config_from_dict(config.standby_config, cfg['standby_config'])

            if 'etcd_config' in cfg:
                update_config_from_dict(config.etcd_config, cfg['etcd_config'])

            # Set internal fields
            config.config_path = str(config_path)
            if config_path.exists():
                config.last_modified = config_path.stat().st_mtime

            # Configure logging for this module with the loaded configuration
            from motor.common.utils.logger import set_logging_config_for_module
            set_logging_config_for_module(
                'motor.config.controller',
                log_config=config.logging_config
            )

            # Now it's safe to log after logging configuration is set
            logger.info(f"Loading configuration file: {config_path}")
            if config_path.exists():
                logger.info(f"Successfully loaded configuration file: {config_path}")
            else:
                logger.warning(f"Configuration file does not exist, using default configuration: {config_path}")
            logger.info("Configuration loading completed")

            return config

        except Exception as e:
            logger.error(f"Failed to create configuration instance: {e}")
            raise

    def validate_config(self) -> None:
        """Validate the validity of configuration values"""
        errors = []

        # Validate logging configuration
        valid_log_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR']
        if self.logging_config.log_level.upper() not in valid_log_levels:
            errors.append(f"log_level must be one of: {', '.join(valid_log_levels)}")

        if self.logging_config.log_max_line_length <= 0:
            errors.append("log_max_line_length must be greater than 0")

        # Validate API configuration
        if not (1 <= self.api_config.controller_api_port <= 65535):
            errors.append("controller_api_port must be in range 1-65535")

        # Validate instance configuration
        if self.instance_config.instance_assemble_timeout <= 0:
            errors.append("instance_assemble_timeout must be greater than 0")

        if self.instance_config.instance_heartbeat_timeout <= 0:
            errors.append("instance_heartbeat_timeout must be greater than 0")

        if self.instance_config.instance_expired_timeout <= 0:
            errors.append("instance_expired_timeout must be greater than 0")

        if self.instance_config.instance_assembler_check_internal <= 0:
            errors.append("instance_assembler_check_internal must be greater than 0")

        if self.instance_config.instance_manager_check_internal <= 0:
            errors.append("instance_manager_check_internal must be greater than 0")

        if self.instance_config.send_cmd_retry_times < 0:
            errors.append("send_cmd_retry_times cannot be negative")

        # Validate event configuration
        if self.event_config.event_consumer_sleep_interval <= 0:
            errors.append("event_consumer_sleep_interval must be greater than 0")

        if self.event_config.coordinator_heartbeat_interval <= 0:
            errors.append("coordinator_heartbeat_interval must be greater than 0")

        # Validate fault tolerance configuration
        if self.fault_tolerance_config.strategy_center_check_internal <= 0:
            errors.append("strategy_center_check_internal must be greater than 0")

        # Validate standby configuration
        if self.standby_config.master_standby_check_interval <= 0:
            errors.append("master_standby_check_interval must be greater than 0")

        # Validate ETCD configuration
        if not (1 <= self.etcd_config.etcd_port <= 65535):
            errors.append("etcd_port must be in range 1-65535")

        if self.etcd_config.etcd_timeout <= 0:
            errors.append("etcd_timeout must be greater than 0")

        if errors:
            error_msg = "Configuration validation failed:\n" + "\n".join(f"  - {error}" for error in errors)
            logger.error(error_msg)
            raise ValueError(error_msg)

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
                'motor.config.controller',
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
        return f"""
                Controller Configuration Summary:
                  API Service: {self.api_config.controller_api_host}:{self.api_config.controller_api_port}
                  Coordinator API Service: {self.api_config.coordinator_api_dns}:{self.api_config.coordinator_api_port}
                  Instance Assembly Timeout: {self.instance_config.instance_assemble_timeout} seconds
                  Instance Heartbeat Timeout: {self.instance_config.instance_heartbeat_timeout} seconds
                  Instance Expired Timeout: {self.instance_config.instance_expired_timeout} seconds
                  Fault Tolerance: {'Enabled' if self.fault_tolerance_config.enable_fault_tolerance else 'Disabled'}
                  Configuration Path: {self.config_path or 'Not set'}
                """


# Global configuration instance
CONFIG_PATH_OVERRIDE = None


def set_config_path(config_path: str) -> None:
    """Set the configuration file path override"""
    global CONFIG_PATH_OVERRIDE
    CONFIG_PATH_OVERRIDE = config_path
    logger.info(f"Configuration path override set to: {config_path}")


def find_config_file():
    """Intelligently find configuration file, prioritize development environment configuration file"""
    # If path override is set, use it
    if CONFIG_PATH_OVERRIDE:
        return CONFIG_PATH_OVERRIDE
    
    # First try configuration file in current package directory
    package_config = os.path.join(os.path.dirname(__file__), 'controller_config.json')
    if os.path.exists(package_config):
        return package_config
    
    # If not in package, try configuration file in project root directory
    # Find project root directory by searching upward
    current_dir = os.path.dirname(__file__)
    while current_dir != os.path.dirname(current_dir):  # Until root directory
        project_config = os.path.join(current_dir, 'motor', 'config', 'controller_config.json')
        if os.path.exists(project_config):
            return project_config
        current_dir = os.path.dirname(current_dir)
    
    # Finally return package path (even if file does not exist)
    return package_config


def get_config_path():
    """Get the current configuration file path"""
    return find_config_file()
