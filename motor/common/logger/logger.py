# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import logging
import multiprocessing
import os
import sys
from pathlib import Path

from motor.common.logger.formatter import ColoredFormatter, NewLineFormatter
from motor.common.logger.logger_handler import CompressedRotatingFileHandler
from motor.config.log_config import LoggingConfig


# Set to track modules that have requested loggers
_logged_modules = set()

# Get hostname from en
hostname = os.getenv('HOSTNAME', 'unknown')
env_log_dir = os.getenv('MOTOR_LOG_PATH')

_MODULE_LOGGER_NAME = "common.logger"

# motor.engine_server.* uses a single logger bucket; other top-level packages use two levels.
_SINGLE_BUCKET_COMPONENTS = frozenset({"engine_server"})


class ProcessContextFilter(logging.Filter):
    """Inject process name into LogRecord for format placeholders."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.processName = multiprocessing.current_process().name
        return True


# Backward-compatible alias
ProcessNameFilter = ProcessContextFilter


class MaxLengthFormatter(logging.Formatter):
    """Wrap a formatter and cap output line length."""

    def __init__(self, inner: logging.Formatter, max_length: int):
        super().__init__()
        self.inner = inner
        self.max_length = max_length

    def format(self, record: logging.LogRecord) -> str:
        msg = self.inner.format(record)
        msg = repr(msg)[1:-1]
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


def _module_logger() -> logging.Logger:
    return logging.getLogger(_MODULE_LOGGER_NAME)


def _resolve_logger_name(name: str) -> str:
    if not name.startswith("motor."):
        return name
    parts = name.split('.')
    if len(parts) < 2:
        return name
    component = parts[1]
    if component in _SINGLE_BUCKET_COMPONENTS:
        return component
    if len(parts) >= 3:
        return f"{parts[1]}.{parts[2]}"
    return component


def _use_color() -> bool:
    if os.environ.get('NO_COLOR'):
        return False
    color_flag = os.environ.get('MOTOR_LOGGING_COLOR')
    if color_flag == '0':
        return False
    if color_flag == '1':
        return True
    return hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()


def _build_formatter(config: LoggingConfig, *, color: bool) -> MaxLengthFormatter:
    use_relpath = config.log_level.upper() == 'DEBUG'
    base_cls = ColoredFormatter if color else NewLineFormatter
    inner = base_cls(
        config.log_format,
        datefmt=config.log_date_format,
        use_relpath=use_relpath,
    )
    return MaxLengthFormatter(inner, config.log_max_line_length)


def get_logger(
    name: str = __name__,
    level: int | None = None
):
    """
    Get or create a logger with enhanced capabilities.

    Args:
        name: Logger name (usually __name__)
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

    log_name = _resolve_logger_name(name)
    root_logger = logging.getLogger()
    logger = logging.getLogger(log_name)

    # Only configure root when it has no handlers (e.g. first use in process).
    # If root already has handlers, it was likely set by reconfigure_logging() (e.g. DEBUG);
    # calling _ensure_root_logger_configured with default LoggingConfig() would overwrite
    # level back to INFO and hide DEBUG logs in API server workers.
    if not root_logger.handlers:
        _ensure_root_logger_configured(config, env_log_dir)
        logger.setLevel(level)
    else:
        # Leave root and its handlers unchanged; set this logger to DEBUG so it does not
        # filter messages—root/handlers will apply the actual level from reconfigure_logging.
        logger.setLevel(logging.DEBUG)

    return logger


def _ensure_root_logger_configured(config: LoggingConfig, log_dir: str | None) -> None:
    """Ensure root logger has the proper handlers configured."""
    root_logger = logging.getLogger()
    level = getattr(logging, config.log_level.upper(), logging.INFO)

    # If root logger already has handlers, update their level and return
    # Fix: update level of existing handlers as well
    if root_logger.handlers:
        root_logger.setLevel(level)
        for handler in root_logger.handlers:
            handler.setLevel(level)
        return

    # Configure root logger with handlers
    root_logger.setLevel(level)

    process_filter = ProcessContextFilter()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.addFilter(process_filter)
    console_handler.setFormatter(_build_formatter(config, color=_use_color()))
    root_logger.addHandler(console_handler)

    file_formatter = _build_formatter(config, color=False)

    # File handler (optional)
    if log_dir:
        _module_logger().info(
            "Internal logs of pod will be saved to %s, will mounted to host %s",
            log_dir,
            config.host_log_dir,
        )

        # Get log_dir from pod name prefix, remove random suffix
        parts = hostname.split('-')
        if len(parts) <= 2:
            module_log_dir = os.path.join(log_dir, hostname)
        else:
            module_log_dir = os.path.join(log_dir, '-'.join(parts[:-2]))

        if not os.path.exists(module_log_dir):
            try:
                Path(module_log_dir).mkdir(parents=True, exist_ok=True)
            except Exception:
                _module_logger().error("Failed to create log directory: %s", module_log_dir)
        if os.path.exists(module_log_dir):
            try:
                log_file = os.path.join(module_log_dir, f"{hostname}_{os.getpid()}.log")

                rotate_handler = CompressedRotatingFileHandler(
                    filename=log_file,
                    maxBytes=config.log_rotation_size * 1024 * 1024,
                    backupCount=config.log_rotation_count,
                    compress=config.log_compress,
                    compress_level=config.log_compress_level,
                    max_total_size=config.log_max_total_size * 1024 * 1024,
                    cleanup_interval=config.log_cleanup_interval
                )
                rotate_handler.addFilter(process_filter)
                rotate_handler.setFormatter(file_formatter)
                root_logger.addHandler(rotate_handler)
            except Exception:
                _module_logger().error("Failed to configure log handler")


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
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Reconfigure root logger with new settings
    _ensure_root_logger_configured(log_config, env_log_dir)

    # Update level of all handlers (ensure handler level matches config)
    for handler in root_logger.handlers:
        handler.setLevel(new_level)

    # Update all existing logger levels (they propagate to root logger for formatting)
    for name in logging.root.manager.loggerDict:
        logger_obj = logging.getLogger(name)
        logger_obj.setLevel(new_level)

    _module_logger().info("Logging reconfigured with level: %s", log_config.log_level)
