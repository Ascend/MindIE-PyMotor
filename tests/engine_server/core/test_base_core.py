#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import pytest
from abc import ABC
from unittest.mock import Mock, patch
from motor.engine_server.core.base_core import IServerCore, BaseServerCore
from motor.engine_server.config.base import IConfig


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
    """Mock core dependencies (DataController, Services, Endpoint)"""
    with patch("motor.engine_server.core.base_core.DataController") as mock_dc_cls, \
            patch("motor.engine_server.core.base_core.MetricsService") as mock_metrics_cls, \
            patch("motor.engine_server.core.base_core.HealthService") as mock_health_cls, \
            patch("motor.engine_server.core.base_core.Endpoint") as mock_endpoint_cls:
        # Create mock instances
        mock_dc = Mock()
        mock_dc_cls.return_value = mock_dc

        mock_metrics = Mock()
        mock_metrics_cls.return_value = mock_metrics

        mock_health = Mock()
        mock_health_cls.return_value = mock_health

        mock_endpoint = Mock()
        mock_endpoint_cls.return_value = mock_endpoint

        yield {
            "mock_dc_cls": mock_dc_cls,
            "mock_dc": mock_dc,
            "mock_metrics_cls": mock_metrics_cls,
            "mock_metrics": mock_metrics,
            "mock_health_cls": mock_health_cls,
            "mock_health": mock_health,
            "mock_endpoint_cls": mock_endpoint_cls,
            "mock_endpoint": mock_endpoint
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
    with pytest.raises(TypeError, match="Can't instantiate abstract class IServerCore with abstract methods"):
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
    assert base_server_core.metrics_service == deps["mock_metrics"]

    # Verify HealthService is initialized with DataController
    deps["mock_health_cls"].assert_called_once_with(deps["mock_dc"])
    assert base_server_core.health_service == deps["mock_health"]

    # Verify Endpoint is initialized with server config and services
    expected_services = [deps["mock_metrics"], deps["mock_health"]]
    deps["mock_endpoint_cls"].assert_called_once_with(mock_config.get_server_config.return_value, expected_services)
    assert base_server_core.endpoint == deps["mock_endpoint"]



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


def test_join_method(base_server_core):
    """test BaseServerCore.join() should execute without errors (no-op implementation)"""
    # Verify no exception is raised
    base_server_core.join()


def test_shutdown_method(base_server_core, mock_dependencies):
    """test BaseServerCore.shutdown() should call shutdown() on Endpoint and DataController when executed"""
    deps = mock_dependencies

    base_server_core.shutdown()

    # Verify Endpoint.shutdown() is called first
    deps["mock_endpoint"].shutdown.assert_called_once()
    # Verify DataController.shutdown() is called
    deps["mock_dc"].shutdown.assert_called_once()


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
