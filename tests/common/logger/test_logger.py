# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

import logging

import pytest

from motor.common.logger.formatter import ColoredFormatter, NewLineFormatter
from motor.common.logger.logger import _resolve_logger_name
from motor.config.log_config import LoggingConfig


class TestResolveLoggerName:
    def test_toplevel_components_use_first_level(self):
        assert _resolve_logger_name("motor.engine_server.core.vllm_engine") == "engine_server"
        assert _resolve_logger_name("motor.engine_server.cli.main") == "engine_server"
        assert _resolve_logger_name("motor.node_manager.api_client.controller_api_client") == "node_manager"
        assert _resolve_logger_name("motor.config.controller") == "config"
        assert _resolve_logger_name("motor.config.coordinator") == "config"

    def test_secondlevel_components_use_second_level(self):
        assert _resolve_logger_name("motor.controller.fault_tolerance.k8s.resource_monitor") == "fault_tolerance"
        assert _resolve_logger_name("motor.coordinator.api_server.management_server") == "api_server"
        assert _resolve_logger_name("motor.common.etcd.etcd_client") == "etcd"

    def test_non_motor_name_unchanged(self):
        assert _resolve_logger_name("uvicorn.error") == "uvicorn.error"


class TestLogFormatter:
    @pytest.fixture
    def record(self):
        record = logging.LogRecord(
            name="engine_server",
            level=logging.INFO,
            pathname="/app/motor/engine_server/cli/main.py",
            lineno=31,
            msg="successfully parsed vllm engine configuration",
            args=(),
            exc_info=None,
        )
        record.filename = "main.py"
        record.processName = "MainProcess"
        record.process = 412
        return record

    def test_newline_formatter_output(self, record):
        config = LoggingConfig()
        formatter = NewLineFormatter(config.log_format, datefmt=config.log_date_format)
        output = formatter.format(record)
        assert output.startswith("(MainProcess pid=412) INFO ")
        assert "[engine_server][main.py:31]" in output
        assert output.endswith("successfully parsed vllm engine configuration")

    def test_colored_formatter_adds_ansi(self, record):
        config = LoggingConfig()
        formatter = ColoredFormatter(config.log_format, datefmt=config.log_date_format)
        output = formatter.format(record)
        assert "\033[32mINFO\033[0m" in output
        assert "\033[90m" in output

    def test_default_date_format(self):
        assert LoggingConfig().log_date_format == "%m-%d %H:%M:%S"

    def test_default_log_format_has_process_and_module(self):
        fmt = LoggingConfig().log_format
        assert "%(processName)s pid=%(process)d)" in fmt
        assert "[%(name)s][%(fileinfo)s:%(lineno)d]" in fmt
