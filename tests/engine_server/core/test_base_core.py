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
import sys
from abc import ABC
from unittest.mock import Mock, patch, MagicMock

# Mock prometheus and related modules BEFORE importing other modules
mock_prometheus = MagicMock()
sys.modules['prometheus_client'] = mock_prometheus
sys.modules['prometheus_client.Counter'] = MagicMock()
sys.modules['prometheus_client.Gauge'] = MagicMock()
sys.modules['prometheus_client.Histogram'] = MagicMock()
sys.modules['prometheus_client.Summary'] = MagicMock()
sys.modules['prometheus_client.REGISTRY'] = MagicMock()

# Mock the missing prometheus_fastapi_instrumentator module
mock_instrumentator = MagicMock()
sys.modules['prometheus_fastapi_instrumentator'] = mock_instrumentator
sys.modules['prometheus_fastapi_instrumentator.Instrumentator'] = MagicMock()

# Now import the modules under test
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
    """Mock core dependencies (Endpoint, HttpServer, ProcManager)"""
    with patch("motor.engine_server.core.base_core.Endpoint") as mock_endpoint_cls, \
            patch("motor.engine_server.core.base_core.HttpServer") as mock_http_server_cls, \
            patch("motor.engine_server.core.base_core.ProcManager") as mock_proc_manager_cls:
        # Create mock instances
        mock_endpoint = Mock()
        mock_endpoint_cls.return_value = mock_endpoint

        mock_http_server = Mock()
        mock_http_server_cls.return_value = mock_http_server

        mock_proc_manager = Mock()
        mock_proc_manager_cls.return_value = mock_proc_manager

        yield {
            "mock_endpoint_cls": mock_endpoint_cls,
            "mock_endpoint": mock_endpoint,
            "mock_http_server_cls": mock_http_server_cls,
            "mock_http_server": mock_http_server,
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

    # Verify http_server is None initially
    assert base_server_core.http_server is None
    assert base_server_core._http_server_settings is None

    # Verify Endpoint is initialized with config
    deps["mock_endpoint_cls"].assert_called_once_with(mock_config)
    assert base_server_core.endpoint == deps["mock_endpoint"]

    # Verify ProcManager is initialized with current process ID
    deps["mock_proc_manager_cls"].assert_called_once()
    assert base_server_core.proc_manager == deps["mock_proc_manager"]


def test_initialize_method(base_server_core):
    """test BaseServerCore.initialize() should execute without errors (no-op implementation)"""
    # Verify no exception is raised
    base_server_core.initialize()


def test_run_method(base_server_core, mock_dependencies):
    """test BaseServerCore.run() should call run() on Endpoint when executed"""
    deps = mock_dependencies

    base_server_core.run()

    # Verify _is_running flag is set
    assert base_server_core._is_running is True
    
    # Verify Endpoint.run() is called
    deps["mock_endpoint"].run.assert_called_once()


def test_join_method(base_server_core, mock_dependencies):
    """test BaseServerCore.join() should call join() on ProcManager when executed"""
    deps = mock_dependencies

    base_server_core.join()

    # Verify ProcManager.join() is called
    deps["mock_proc_manager"].join.assert_called_once()


def test_shutdown_method(base_server_core, mock_dependencies):
    """test BaseServerCore.shutdown() should call shutdown() on Endpoint, HttpServer and ProcManager when executed"""
    deps = mock_dependencies
    
    # Set http_server to something to test its shutdown
    base_server_core.http_server = deps["mock_http_server"]
    
    base_server_core.shutdown()

    # Verify _is_running flag is cleared
    assert base_server_core._is_running is False
    
    # Verify HttpServer.shutdown() is called
    deps["mock_http_server"].shutdown.assert_called_once()
    # Verify HttpServer is set to None
    assert base_server_core.http_server is None
    
    # Verify Endpoint.shutdown() is called
    deps["mock_endpoint"].shutdown.assert_called_once()
    
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
    with patch("motor.engine_server.core.base_core.Endpoint"), \
            patch("motor.engine_server.core.base_core.ProcManager"):
        server_core = BaseServerCore(config=minimal_config)
        assert server_core.config == minimal_config


@patch("motor.engine_server.core.base_core.Endpoint")
@patch("motor.engine_server.core.base_core.ProcManager")
def test_endpoint_initialization_failure(mock_proc_manager_cls, mock_endpoint_cls):
    """test BaseServerCore should propagate exceptions from Endpoint initialization when they occur"""
    # Make Endpoint initialization raise exception
    mock_endpoint_cls.side_effect = Exception("Endpoint initialization failed")

    mock_config = Mock(spec=IConfig)
    mock_config.get_server_config.return_value = Mock()

    # Verify exception is raised
    with pytest.raises(Exception, match="Endpoint initialization failed"):
        BaseServerCore(config=mock_config)


def test_http_server_settings(base_server_core, mock_dependencies):
    """test BaseServerCore.http_server_settings should create and run HttpServer when set"""
    deps = mock_dependencies
    
    # Test initial state
    assert base_server_core.http_server is None
    assert base_server_core._http_server_settings is None
    
    # Set _is_running to True
    base_server_core._is_running = True
    
    # Test setting http_server_settings
    test_settings = {"test_param": "test_value"}
    base_server_core.http_server_settings = test_settings
    
    # Verify _http_server_settings is set
    assert base_server_core._http_server_settings == test_settings
    
    # Verify HttpServer is created
    deps["mock_http_server_cls"].assert_called_once_with(
        config=base_server_core.config,
        init_params=test_settings,
    )
    
    # Verify http_server is set
    assert base_server_core.http_server == deps["mock_http_server"]
    
    # Verify HttpServer.run() is called because _is_running is True
    deps["mock_http_server"].run.assert_called_once()
    
    # Reset mocks
    deps["mock_http_server_cls"].reset_mock()
    deps["mock_http_server"].reset_mock()
    
    # Test setting http_server_settings again - should not create new HttpServer
    new_settings = {"test_param": "new_value"}
    base_server_core.http_server_settings = new_settings
    
    # Verify _http_server_settings is updated
    assert base_server_core._http_server_settings == new_settings
    
    # Verify HttpServer is not created again
    deps["mock_http_server_cls"].assert_not_called()
    deps["mock_http_server"].run.assert_not_called()
    