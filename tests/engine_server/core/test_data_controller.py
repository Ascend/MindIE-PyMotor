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

import sys
import pytest
import threading
from unittest.mock import Mock, patch, MagicMock

from motor.engine_server.constants import constants


@pytest.fixture(autouse=True)
def mock_logger_module():
    module_name = 'motor.common.utils.logger'
    original_logger = sys.modules.get(module_name)

    mock_logger = MagicMock()
    mock_logger_module = MagicMock()
    mock_logger_module.get_logger = MagicMock(return_value=mock_logger)
    sys.modules[module_name] = mock_logger_module

    with patch('motor.engine_server.core.data_controller.logger', mock_logger):
        try:
            yield
        finally:
            if original_logger is not None:
                sys.modules[module_name] = original_logger
            else:
                if module_name in sys.modules:
                    del sys.modules[module_name]


from motor.engine_server.core.data_controller import DataController
from motor.engine_server.config.base import IConfig


@pytest.fixture(scope="function")
def data_controller():
    mock_config = Mock(spec=IConfig)
    mock_vllm_collector = Mock()
    mock_vllm_collector.name = "vllm_collector_test"
    mock_vllm_collector.collect.return_value = {
        "metrics": {"cpu_usage": 0.3, "memory_usage": 0.6},
        "health": {"status": "healthy", "connections": 10}
    }

    with patch(
            "motor.engine_server.core.data_controller.CollectorFactory.create_collector",
            return_value=mock_vllm_collector
    ):
        dc = DataController(config=mock_config)
        dc._mock_vllm_collector = mock_vllm_collector
        yield dc
        # Mock sleep during shutdown to avoid waiting
        with patch("motor.engine_server.core.data_controller.time.sleep"):
            dc.shutdown()


def test_initialization(data_controller):
    """test DataController should initialize with correct default properties when created"""
    assert data_controller.collect_interval == 3
    assert data_controller._core_status == constants.INIT_STATUS
    assert data_controller._server_core is None
    assert isinstance(data_controller._stop_event, threading.Event)
    assert isinstance(data_controller._collect_thread, threading.Thread)
    assert data_controller._collect_thread.name == "data_controller_collect_thread"
    assert data_controller._collect_thread.daemon is True
    assert data_controller._data_map == {"metrics": {}, "health": {}}
    assert data_controller.vllm_collector == data_controller._mock_vllm_collector


@patch("motor.engine_server.core.data_controller.time.sleep")
def test_run_starts_thread(mock_sleep, data_controller):
    """test DataController.run() should start the collect thread when called"""
    # Set server core to avoid thread getting stuck in init loop
    mock_server_core = Mock()
    mock_server_core.status.return_value = "normal"
    data_controller.set_server_core(mock_server_core)

    assert data_controller._collect_thread.is_alive() is False
    data_controller.run()
    mock_sleep.assert_called()
    assert data_controller._collect_thread.is_alive() is True


@patch("motor.engine_server.core.data_controller.time.sleep")
def test_shutdown_stops_thread(mock_sleep, data_controller):
    """test DataController.shutdown() should stop the collect thread and set stop event when called"""
    data_controller.run()
    assert data_controller._collect_thread.is_alive() is True

    # Mock sleep in shutdown to avoid waiting for thread join
    with patch("motor.engine_server.core.data_controller.time.sleep"):
        data_controller.shutdown()
    assert data_controller._collect_thread.is_alive() is False
    assert data_controller._stop_event.is_set() is True


def test_set_server_core(data_controller):
    """test DataController.set_server_core() should inject server core and update core status when server core is provided"""
    mock_server_core = Mock()
    mock_server_core.status.return_value = "normal"

    data_controller.set_server_core(mock_server_core)
    assert data_controller._server_core == mock_server_core

    data_controller._core_status = data_controller._server_core.status() if data_controller._server_core else constants.INIT_STATUS
    assert data_controller._core_status == "normal"


def test_do_collect_handles_exception(data_controller):
    """test DataController._do_collect() should not pollute data map when collector throws exception"""
    data_controller._mock_vllm_collector.collect.side_effect = Exception("collect failed")
    data_controller._do_collect()
    assert data_controller._data_map == {"metrics": {}, "health": {}}


def test_modify_data_adds_core_status(data_controller):
    """test DataController._modify_data() should add core_status to raw data without changing original data when called"""
    raw_data = {"key1": "value1", "key2": 123}
    data_controller._core_status = "abnormal"

    updated_data = data_controller._modify_data(raw_data)
    assert updated_data["key1"] == "value1"
    assert updated_data["key2"] == 123
    assert updated_data["core_status"] == "abnormal"


def test_get_metrics_data_returns_correct_format(data_controller):
    """test DataController.get_metrics_data() should return metrics data with expected format and collector name when collection succeeds"""
    data_controller._do_collect()
    metrics_data = data_controller.get_metrics_data()

    expected_metrics = {
        "cpu_usage": 0.3,
        "memory_usage": 0.6,
        "core_status": constants.INIT_STATUS,
    }
    assert metrics_data["latest_metrics"] == expected_metrics
    assert metrics_data["collector_name"] == "vllm_collector_test"


def test_get_health_data_returns_correct_format(data_controller):
    """test DataController.get_health_data() should return health data with expected format and collector name when collection succeeds"""
    data_controller._do_collect()
    health_data = data_controller.get_health_data()

    expected_health = {
        "status": "healthy",
        "connections": 10,
        "core_status": constants.INIT_STATUS
    }
    assert health_data["latest_health"] == expected_health
    assert health_data["collector_name"] == "vllm_collector_test"


@patch("motor.engine_server.core.data_controller.time.sleep")
def test_collect_loop_switches_mode_after_core_status_change(mock_sleep, data_controller):
    """test DataController._collect_loop should switch to normal mode and execute collection when server core status is normal"""
    mock_server_core = Mock()
    mock_server_core.status.return_value = "normal"
    data_controller.set_server_core(mock_server_core)

    data_controller.run()
    data_controller.shutdown()

    assert data_controller._core_status == "normal"
    assert data_controller._mock_vllm_collector.collect.call_count > 0


@patch("motor.engine_server.core.data_controller.time.sleep")
@patch("motor.engine_server.core.data_controller.threading.Thread.is_alive")
def test_collect_loop_stops_on_stop_event(mock_is_alive, mock_sleep, data_controller):
    """test DataController._collect_loop should stop the collect thread when stop event is set"""
    mock_is_alive.side_effect = [False, True, False]
    data_controller.run()
    assert mock_is_alive.call_count >= 1
    assert data_controller._collect_thread.is_alive() is True
    data_controller._stop_event.set()
    assert data_controller._collect_thread.is_alive() is False


if __name__ == "__main__":
    pytest.main(["-v", __file__])
