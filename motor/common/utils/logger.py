# coding=utf-8
# Copyright (c) 2025, HUAWEI CORPORATION.  All rights reserved.

import logging
import os
import json
import sys
from dataclasses import dataclass
from pathlib import Path


# Set to track modules that have requested loggers
_logged_modules = set()


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
            config = LoggingConfig()
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
    # Record this module as having requested a logger
    _logged_modules.add(name)

    # Get configuration for this specific module (use default config initially)
    config = LoggingConfig()

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

    # Always ensure root logger has proper handlers configured
    _ensure_root_logger_configured(config, log_file)

    # Set logger level - logs will propagate to root logger
    logger.setLevel(level)

    return logger


def _ensure_root_logger_configured(config: LoggingConfig, log_file: str | None) -> None:
    """Ensure root logger has the proper handlers configured."""
    root_logger = logging.getLogger()

    # If root logger already has handlers, assume it's properly configured
    if root_logger.handlers:
        return

    # Configure root logger with handlers
    level = getattr(logging, config.log_level.upper(), logging.INFO)
    root_logger.setLevel(level)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    formatter = MaxLengthFormatter(
        config.log_format,
        max_length=config.log_max_line_length,
        datefmt=config.log_date_format
    )
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

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
                root_logger.addHandler(file_handler)
            except Exception:
                # If file logging fails, continue with console only
                pass


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

    # Ensure root logger is properly configured with new settings
    root_logger = logging.getLogger()

    # Remove existing handlers and reconfigure
    for handler in root_logger.handlers[:]:  # Create a copy of the list to avoid modification issues
        root_logger.removeHandler(handler)

    # Reconfigure root logger with new settings
    _ensure_root_logger_configured(log_config, log_config.log_file)

    # Update all existing logger levels (they propagate to root logger for formatting)
    for name in logging.root.manager.loggerDict:
        logger_obj = logging.getLogger(name)
        logger_obj.setLevel(new_level)

    # Log the reconfiguration (using root logger to avoid import issues)
    logging.info(f"Logging reconfigured with level: {log_config.log_level}")