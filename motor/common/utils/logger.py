# coding=utf-8
# Copyright (c) 2025, HUAWEI CORPORATION.  All rights reserved.

import logging
import os
import json
import sys
from dataclasses import dataclass
from pathlib import Path


# Module-specific logging configurations
_module_logging_configs = {}


@dataclass
class LoggingConfig:
    """Logging configuration class used by various components"""
    log_level: str = 'INFO'  # Logging level: DEBUG, INFO, WARNING, ERROR
    log_max_line_length: int = 8192
    log_file: str | None = None  # Optional log file path
    log_format: str = '%(asctime)s  [%(levelname)s][%(name)s][%(filename)s:%(lineno)d]  %(message)s'
    log_date_format: str = '%Y-%m-%d %H:%M:%S'


class MaxLengthFormatter(logging.Formatter):
    """
    Formatter that limits log message length to prevent performance issues.
    """

    def __init__(
        self,
        fmt=None,
        max_length=None,
        datefmt=None,
        style='%'
    ):
        # If max_length is not provided, get it from config
        if max_length is None:
            config = get_logging_config()
            max_length = config.log_max_line_length
        super().__init__(fmt=fmt, datefmt=datefmt, style=style)
        self.max_length = max_length

    def format(self, record):
        msg = super().format(record)
        # Escape special characters and limit length
        msg = repr(msg)[1:-1]  # Remove quotes added by repr()
        if len(msg) > self.max_length:
            return msg[:self.max_length] + '...'
        return msg


class ApiAccessFilter(logging.Filter):
    """Suppress uvicorn access logs for specified APIs unless level >= configured level."""

    def __init__(self, api_filters: dict[str, int] = None):
        """
        Args:
            api_filters: dict mapping API paths to minimum log levels.
                        e.g., {"/heartbeat": logging.ERROR, "/register": logging.WARNING}
        """
        super().__init__()
        self.api_filters = api_filters or {}

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        if record.name == "uvicorn.access":
            for path, min_level in self.api_filters.items():
                if path in message:
                    return record.levelno >= min_level
        return True


def set_logging_config_for_module(
    module_name: str,
    log_config: LoggingConfig
) -> None:
    """
    Set logging configuration for a specific module.

    Args:
        module_name: Module name (e.g., 'motor.config.controller')
        log_config: LoggingConfig object
    """
    _module_logging_configs[module_name] = log_config


def get_logging_config(module_name: str | None = None):
    """
    Get logging configuration for a specific module.
    Falls back to default values if no module-specific config is found.

    Args:
        module_name: Module name to get config for. If None, uses caller module.
    """
    if module_name is None:
        import inspect
        frame = inspect.currentframe()
        if frame and frame.f_back:
            module_name = frame.f_back.f_globals.get('__name__', '')

    # Try module-specific config first
    if module_name and module_name in _module_logging_configs:
        return _module_logging_configs[module_name]

    # Fall back to default values
    return LoggingConfig()


def _load_logging_config_from_file(config_path: str) -> LoggingConfig:
    """
    Load logging configuration directly from a JSON config file.

    Args:
        config_path: Path to the config file

    Returns:
        LoggingConfig object with loaded settings, or default config if loading fails
    """
    config_file = Path(config_path)
    logging_config = LoggingConfig()  # Default config

    if config_file.exists():
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            if 'logging_config' in cfg:
                # Update logging config from JSON
                for key, value in cfg['logging_config'].items():
                    if hasattr(logging_config, key):
                        setattr(logging_config, key, value)
        except Exception:
            # If reading fails, use default config
            pass

    return logging_config


def load_config_for_module(module_name: str) -> bool:
    """
    Load logging configuration for a specific module from its config file.

    Args:
        module_name: Module name (e.g., 'motor.controller')

    Returns:
        True if config was loaded successfully, False otherwise
    """
    logging_config = None

    try:
        if module_name.startswith('motor.controller') or module_name == 'motor.config.controller':
            from motor.config.controller import get_config_path

            config_path = get_config_path()
            logging_config = _load_logging_config_from_file(config_path)
        elif module_name.startswith('motor.coordinator') or module_name == 'motor.config.coordinator':
            # Coordinator modules: load config from CoordinatorConfig
            try:
                from motor.config.coordinator import CoordinatorConfig
                logging_config = CoordinatorConfig().logging_config
            except Exception:
                return False
        elif module_name.startswith('motor.node_manager') or module_name == 'motor.config.node_manager':
            from motor.common.utils.env import Env

            config_path = os.path.join(Env.config_path or "", "config", "node_manager_config.json")
            logging_config = _load_logging_config_from_file(config_path)

        # Apply configuration if we have a valid config
        if logging_config is not None:
            set_logging_config_for_module(module_name, logging_config)
            return True

        return False

    except Exception:
        return False


def get_logger(
    name: str = __name__,
    log_file: str | None = None,
    level: int | None = None
):
    """
    Get or create a logger with enhanced capabilities.

    Args:
        name: Logger name (usually __name__)
        log_file: Optional file path for file logging (overrides config)
        level: Optional logging level (overrides config)

    Returns:
        Configured logger instance
    """
    # Try to load config for this module if not already loaded
    if name not in _module_logging_configs:
        load_config_for_module(name)

    # Get configuration for this specific module
    config = get_logging_config(name)

    # Use provided parameters or fall back to config
    if level is None:
        level = getattr(logging, config.log_level.upper(), logging.INFO)
    if log_file is None:
        log_file = config.log_file

    log_name = name
    if name.startswith("motor."):
        parts = name.split('.')
        if len(parts) >= 2:
            log_name = parts[1]

    logger = logging.getLogger(log_name)
    if not logger.handlers:
        logger.setLevel(level)

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        formatter = MaxLengthFormatter(
            config.log_format,
            max_length=config.log_max_line_length,
            datefmt=config.log_date_format
        )
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # File handler (optional)
        if log_file:
            # Ensure log directory exists
            log_dir = os.path.dirname(log_file)
            if log_dir and not os.path.exists(log_dir):
                try:
                    Path(log_dir).mkdir(parents=True, exist_ok=True)
                except Exception:
                    # If directory creation fails, skip file logging
                    pass
            else:
                try:
                    file_handler = logging.FileHandler(log_file, encoding='utf-8')
                    file_handler.setLevel(level)
                    file_handler.setFormatter(formatter)
                    logger.addHandler(file_handler)
                except Exception:
                    # If file logging fails, continue with console only
                    pass

    return logger


def reconfigure_logging(log_config: LoggingConfig) -> None:
    """
    Reconfigure all loggers with new logging configuration.
    This function is used by config classes to update logging after config reload.

    Args:
        log_config: LoggingConfig object with new configuration
    """

    # Get new logging level
    new_level = getattr(logging, log_config.log_level.upper(), logging.INFO)

    # Check if we're running in pytest (to avoid breaking caplog)
    is_pytest = os.environ.get('PYTEST_CURRENT_TEST') is not None

    if is_pytest:
        # In test environment, skip logging reconfiguration to avoid breaking pytest caplog
        return

    # In production, update root logger level
    root_logger = logging.getLogger()
    root_logger.setLevel(new_level)

    # Update all existing handlers on root logger
    for handler in root_logger.handlers:
        handler.setLevel(new_level)

        # If it's a formatter with max_length, update the formatter
        if hasattr(handler, 'formatter') and isinstance(handler.formatter, MaxLengthFormatter):
            new_formatter = MaxLengthFormatter(
                log_config.log_format,
                max_length=log_config.log_max_line_length,
                datefmt=log_config.log_date_format
            )
            handler.setFormatter(new_formatter)

    # Update specific loggers that might have been created
    # This covers loggers created by get_logger() calls
    for name in logging.root.manager.loggerDict:
        logger_obj = logging.getLogger(name)
        if logger_obj.handlers:  # Only update loggers with handlers
            logger_obj.setLevel(new_level)
            for handler in logger_obj.handlers:
                handler.setLevel(new_level)

    # Log the reconfiguration (using root logger to avoid import issues)
    logging.info(f"Logging reconfigured with level: {log_config.log_level}")