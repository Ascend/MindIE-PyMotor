# coding=utf-8
# Copyright (c) 2025, HUAWEI CORPORATION.  All rights reserved.
import os
import json

from dataclasses import dataclass, field
from motor.utils.logger import get_logger
from typing import Any
from pathlib import Path

logger = get_logger(__name__)

@dataclass
class ControllerConfig:
    """Controller configuration class with validation, reload and error handling support"""
    
    # instance assembler relative config
    instance_assemble_timeout: int = 600  # 600 seconds
    instance_assembler_check_internal: int = 1 # 1 second
    instance_assembler_cmd_send_internal: int = 1 # 1 second
    max_link_number: int = 768
    send_cmd_retry_times: int = 3

    # instance manager relative config
    instance_manager_check_internal: int = 1  # 1 second
    instance_heartbeat_timeout: int = 5  # 5 second
    instance_expired_timeout: int = 300  # 300 seconds

    # controller api relative config
    controller_api_host: str = '127.0.0.1'
    controller_api_port: int = 8000
    coordinator_api_dns: str = '127.0.0.1'
    coordinator_api_port: int = 8000
    enable_tls: bool = False
    cert_path: str = 'security/controller/cert/server.crt'
    key_path: str = 'security/controller/keys/server.key'

    # fault tolerance feature
    enable_fault_tolerance: bool = True
    strategy_center_check_internal: int = 1  # 1 second
    
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
        logger.info(f"Loading configuration file: {config_path}")
        
        cfg = {}
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                logger.info(f"Successfully loaded configuration file: {config_path}")
            except json.JSONDecodeError as e:
                logger.error(f"JSON parsing error: {e}")
                raise ValueError(f"Configuration file {json_path} format error: {e}") from e
            except Exception as e:
                logger.error(f"Failed to read configuration file: {e}")
                raise ValueError(f"Unable to read configuration file {json_path}: {e}") from e
        else:
            logger.warning(f"Configuration file does not exist, using default configuration: {config_path}")
        
        # Create configuration instance
        try:
            config = cls(
                controller_api_host=cfg.get('controller_api_host', cls.controller_api_host),
                controller_api_port=cfg.get('controller_api_port', cls.controller_api_port),
                coordinator_api_dns=cfg.get('coordinator_api_dns', cls.coordinator_api_dns),
                coordinator_api_port=cfg.get('coordinator_api_port', cls.coordinator_api_port),
                enable_tls=cfg.get('enable_tls', cls.enable_tls),
                cert_path=cfg.get('cert_path', cls.cert_path),
                key_path=cfg.get('key_path', cls.key_path),
                instance_assemble_timeout=cfg.get('instance_assemble_timeout', 
                                                  cls.instance_assemble_timeout),
                instance_assembler_check_internal=cfg.get('instance_assembler_check_internal', 
                                                          cls.instance_assembler_check_internal),
                instance_assembler_cmd_send_internal=cfg.get('instance_assembler_cmd_send_internal', 
                                                             cls.instance_assembler_cmd_send_internal),
                max_link_number=cfg.get('max_link_number', cls.max_link_number),
                send_cmd_retry_times=cfg.get('send_cmd_retry_times', cls.send_cmd_retry_times),
                instance_manager_check_internal=cfg.get('instance_manager_check_internal', 
                                                        cls.instance_manager_check_internal),
                instance_heartbeat_timeout=cfg.get('instance_heartbeat_timeout', 
                                                   cls.instance_heartbeat_timeout),
                instance_expired_timeout=cfg.get('instance_expired_timeout', 
                                                 cls.instance_expired_timeout),
                enable_fault_tolerance=cfg.get('enable_fault_tolerance', 
                                               cls.enable_fault_tolerance),
                strategy_center_check_internal=cfg.get('strategy_center_check_internal', 
                                                       cls.strategy_center_check_internal),
            )
            
            # Set internal fields
            config.config_path = str(config_path)
            if config_path.exists():
                config.last_modified = config_path.stat().st_mtime
            
            logger.info("Configuration loading completed")
            return config
            
        except Exception as e:
            logger.error(f"Failed to create configuration instance: {e}")
            raise

    def validate_config(self) -> None:
        """Validate the validity of configuration values"""
        errors = []
        
        # Validate timeout values
        if self.instance_assemble_timeout <= 0:
            errors.append("instance_assemble_timeout must be greater than 0")
        
        if self.instance_heartbeat_timeout <= 0:
            errors.append("instance_heartbeat_timeout must be greater than 0")
            
        if self.instance_expired_timeout <= 0:
            errors.append("instance_expired_timeout must be greater than 0")
        
        # Validate check intervals
        if self.instance_assembler_check_internal <= 0:
            errors.append("instance_assembler_check_internal must be greater than 0")
            
        if self.instance_manager_check_internal <= 0:
            errors.append("instance_manager_check_internal must be greater than 0")
            
        if self.strategy_center_check_internal <= 0:
            errors.append("strategy_center_check_internal must be greater than 0")
        
        # Validate port
        if not (1 <= self.controller_api_port <= 65535):
            errors.append("controller_api_port must be in range 1-65535")
        
        # Validate retry times
        if self.send_cmd_retry_times < 0:
            errors.append("send_cmd_retry_times cannot be negative")
        
        # Validate maximum link number
        if self.max_link_number <= 0:
            errors.append("max_link_number must be greater than 0")
        
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
            logger.info("Configuration reload successful")
            return True
            
        except Exception as e:
            logger.error(f"Configuration reload failed: {e}")
            return False

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to dictionary"""
        result = {}
        for field_name in self.__dataclass_fields__.keys():
            if not field_name.startswith('_'):
                result[field_name] = getattr(self, field_name)
        return result

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
                  API Service: {self.controller_api_host}:{self.controller_api_port}
                  Coordinator API Service: {self.coordinator_api_dns}:{self.coordinator_api_port}
                  Instance Assembly Timeout: {self.instance_assemble_timeout} seconds
                  Instance Heartbeat Timeout: {self.instance_heartbeat_timeout} seconds
                  Instance Expired Timeout: {self.instance_expired_timeout} seconds
                  Max Link Number: {self.max_link_number}
                  Fault Tolerance: {'Enabled' if self.enable_fault_tolerance else 'Disabled'}
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
