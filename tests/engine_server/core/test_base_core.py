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

import pytest
from abc import ABC
from unittest.mock import Mock, patch
from motor.engine_server.core.base_core import IServerCore, BaseServerCore
from motor.engine_server.config.base import IConfig
from motor.engine_server.core.endpoint import METRICS_SERVICE, HEALTH_SERVICE


@pytest.fixture
def mock_config():
    """Create mocked IConfig object"""
    mock_cfg = Mock(spec=IConfig)
    # Mock server config for Endpoint initialization
    mock_server_config = Mock()
    mock_cfg.get_server_config.return_value = mock_server_config
    return mock_cfg


@pytest.fixture
def mock_dependencies():
    """Mock core dependencies (DataController, Services, Endpoint, ProcManager)"""
    with patch("motor.engine_server.core.base_core.DataController") as mock_dc_cls, \
            patch("motor.engine_server.core.base_core.MetricsService") as mock_metrics_cls, \
            patch("motor.engine_server.core.base_core.HealthService") as mock_health_cls, \
            patch("motor.engine_server.core.base_core.Endpoint") as mock_endpoint_cls, \
            patch("motor.engine_server.core.base_core.ProcManager") as mock_proc_manager_cls:
        # Create mock instances
        mock_dc = Mock()
        mock_dc_cls.return_value = mock_dc

        mock_metrics = Mock()
        mock_metrics_cls.return_value = mock_metrics

        mock_health = Mock()
        mock_health_cls.return_value = mock_health

        mock_endpoint = Mock()
        mock_endpoint_cls.return_value = mock_endpoint

        mock_proc_manager = Mock()
        mock_proc_manager_cls.return_value = mock_proc_manager

        yield {
            "mock_dc_cls": mock_dc_cls,
            "mock_dc": mock_dc,
            "mock_metrics_cls": mock_metrics_cls,
            "mock_metrics": mock_metrics,
            "mock_health_cls": mock_health_cls,
            "mock_health": mock_health,
            "mock_endpoint_cls": mock_endpoint_cls,
            "mock_endpoint": mock_endpoint,
            "mock_proc_manager_cls": mock_proc_manager_cls,
            "mock_proc_manager": mock_proc_manager
        }


@pytest.fixture
def base_server_core(mock_config, mock_dependencies):
    """Create BaseServerCore instance with mocked dependencies"""
    return BaseServerCore(config=mock_config)


def test_is_server_core_is_abstract():
    """test IServerCore should be an abstract class that cannot be instantiated directly"""
    # Verify IServerCore is abstract
    assert issubclass(IServerCore, ABC)

    # Verify instantiation raises TypeError
    with pytest.raises(TypeError, match=r"Can't instantiate abstract class IServerCore.*abstract methods"):
        IServerCore(config=Mock(spec=IConfig))


def test_base_server_core_initialization(base_server_core, mock_config, mock_dependencies):
    """test BaseServerCore should initialize all dependencies correctly when created"""
    deps = mock_dependencies

    # Verify config is set
    assert base_server_core.config == mock_config

    # Verify DataController is initialized with config
    deps["mock_dc_cls"].assert_called_once_with(mock_config)
    assert base_server_core.data_controller == deps["mock_dc"]

    # Verify MetricsService is initialized with DataController
    deps["mock_metrics_cls"].assert_called_once_with(deps["mock_dc"])
    assert base_server_core.services[METRICS_SERVICE] == deps["mock_metrics"]

    # Verify HealthService is initialized with DataController
    deps["mock_health_cls"].assert_called_once_with(deps["mock_dc"])
    assert base_server_core.services[HEALTH_SERVICE] == deps["mock_health"]

    # Verify Endpoint is initialized with server config and services
    expected_services = {
        METRICS_SERVICE: deps["mock_metrics"],
        HEALTH_SERVICE: deps["mock_health"]
    }
    deps["mock_endpoint_cls"].assert_called_once_with(
        mock_config.get_server_config.return_value,
        expected_services
    )
    assert base_server_core.endpoint == deps["mock_endpoint"]

    # Verify ProcManager is initialized with current process ID
    deps["mock_proc_manager_cls"].assert_called_once()
    assert base_server_core.proc_manager == deps["mock_proc_manager"]


def test_initialize_method(base_server_core):
    """test BaseServerCore.initialize() should execute without errors (no-op implementation)"""
    # Verify no exception is raised
    base_server_core.initialize()


def test_run_method(base_server_core, mock_dependencies):
    """test BaseServerCore.run() should call run() on DataController and Endpoint when executed"""
    deps = mock_dependencies

    base_server_core.run()

    # Verify DataController.run() is called
    deps["mock_dc"].run.assert_called_once()
    # Verify Endpoint.run() is called
    deps["mock_endpoint"].run.assert_called_once()


def test_join_method(base_server_core, mock_dependencies):
    """test BaseServerCore.join() should call join() on ProcManager when executed"""
    deps = mock_dependencies

    base_server_core.join()

    # Verify ProcManager.join() is called
    deps["mock_proc_manager"].join.assert_called_once()


def test_shutdown_method(base_server_core, mock_dependencies):
    """test BaseServerCore.shutdown() should call shutdown() on Endpoint, DataController and ProcManager when executed"""
    deps = mock_dependencies

    base_server_core.shutdown()

    # Verify Endpoint.shutdown() is called first
    deps["mock_endpoint"].shutdown.assert_called_once()
    # Verify DataController.shutdown() is called
    deps["mock_dc"].shutdown.assert_called_once()
    # Verify ProcManager.shutdown() is called
    deps["mock_proc_manager"].shutdown.assert_called_once()


def test_status_method(base_server_core):
    """test BaseServerCore.status() should return None (no-op implementation)"""
    result = base_server_core.status()
    assert result is None


def test_base_server_core_with_empty_config():
    """test BaseServerCore should handle config with minimal required methods when initialized"""
    # Create a config with only required method (get_server_config)
    minimal_config = Mock(spec=["get_server_config"])
    minimal_config.get_server_config.return_value = Mock()

    # Verify initialization doesn't raise error
    with patch("motor.engine_server.core.base_core.DataController"), \
            patch("motor.engine_server.core.base_core.MetricsService"), \
            patch("motor.engine_server.core.base_core.HealthService"), \
            patch("motor.engine_server.core.base_core.Endpoint"):
        server_core = BaseServerCore(config=minimal_config)
        assert server_core.config == minimal_config


@patch("motor.engine_server.core.base_core.DataController")
def test_data_controller_initialization_failure(mock_dc_cls):
    """test BaseServerCore should propagate exceptions from DataController initialization when they occur"""
    # Make DataController initialization raise exception
    mock_dc_cls.side_effect = Exception("DC initialization failed")

    mock_config = Mock(spec=IConfig)
    mock_config.get_server_config.return_value = Mock()

    # Verify exception is raised
    with pytest.raises(Exception, match="DC initialization failed"):
        BaseServerCore(config=mock_config)
