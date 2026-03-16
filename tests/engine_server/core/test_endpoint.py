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
import threading
from unittest.mock import Mock, MagicMock, patch, AsyncMock
import sys

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

# Also mock attach_metrics_router if it's imported from somewhere that depends on prometheus
sys.modules['motor.engine_server.core.metrics'] = MagicMock()
sys.modules['motor.engine_server.core.metrics.attach_metrics_router'] = MagicMock()

from motor.engine_server.constants import constants


@pytest.fixture(autouse=True)
def mock_modules():
    original_modules = {
        'motor.engine_server.utils.logger': sys.modules.get('motor.engine_server.utils.logger'),
        'fastapi.FastAPI': sys.modules.get('fastapi.FastAPI'),
        'fastapi.Response': sys.modules.get('fastapi.Response'),
        'uvicorn.Server': sys.modules.get('uvicorn.Server'),
        'uvicorn.Config': sys.modules.get('uvicorn.Config')
    }

    mock_logger = MagicMock()
    mock_logger_module = MagicMock()
    mock_logger_module.get_logger = MagicMock(return_value=mock_logger)
    sys.modules['motor.common.utils.logger'] = mock_logger_module

    mock_fastapi = Mock()
    mock_fastapi.routes = []

    def mock_get(path):
        def decorator(func):
            mock_fastapi.routes.append(Mock(path=path, endpoint=func))
            return func

        return decorator

    mock_fastapi.get = mock_get
    sys.modules['fastapi.FastAPI'] = lambda **kwargs: mock_fastapi

    class MockResponse:
        def __init__(self, body=b"", media_type="text/plain", status_code=200):
            self.body = body
            self.media_type = media_type
            self.status_code = status_code

    sys.modules['fastapi.Response'] = MockResponse

    mock_uvicorn_server = Mock()
    mock_uvicorn_server.run = Mock()
    sys.modules['uvicorn.Server'] = lambda config: mock_uvicorn_server
    sys.modules['uvicorn.Config'] = Mock()

    with patch('motor.engine_server.core.endpoint.logger', mock_logger), \
            patch('motor.engine_server.utils.aicore.get_aicore_usage', return_value=5):
        try:
            yield
        finally:
            for module_name, original_module in original_modules.items():
                if original_module is not None:
                    sys.modules[module_name] = original_module
                else:
                    if module_name in sys.modules:
                        del sys.modules[module_name]


from motor.engine_server.config.base import IConfig
from motor.engine_server.core.endpoint import Endpoint


@pytest.fixture(scope="function")
def endpoint():
    mock_config = Mock(spec=IConfig)
    mock_server_config = Mock()
    mock_server_config.server_host = "127.0.0.1"
    mock_server_config.server_port = 8000

    # Mock deploy_config and health_check_config
    mock_deploy_config = Mock()
    mock_health_check_config = Mock()
    mock_health_check_config.npu_usage_threshold = 10
    mock_health_check_config.enable_virtual_inference = True
    mock_deploy_config.health_check_config = mock_health_check_config
    mock_deploy_config.mgmt_tls_config = None
    mock_deploy_config.infer_tls_config = None
    mock_server_config.deploy_config = mock_deploy_config

    mock_config.get_server_config.return_value = mock_server_config

    # Mock both HealthCollector and attach_metrics_router to avoid prometheus dependencies
    with patch("motor.engine_server.core.endpoint.HealthCollector") as mock_health_collector_cls, \
            patch("motor.engine_server.core.endpoint.attach_metrics_router") as mock_attach_metrics:
        mock_health_collector = Mock()
        mock_health_collector_cls.return_value = mock_health_collector

        # Make is_healthy an async method
        mock_health_collector.is_healthy = AsyncMock(return_value=True)  # Default healthy

        ep = Endpoint(config=mock_config)
        ep._mock_health_collector = mock_health_collector

        yield ep
        if hasattr(ep, '_server_thread') and ep._server_thread.is_alive():
            ep.shutdown()


def _get_route_by_path(endpoint, path):
    for route in endpoint.app.routes:
        if route.path == path:
            return route
    raise ValueError(f"Route {path} not found")


def test_initialization(endpoint):
    """test Endpoint should initialize with correct properties and routes when created"""
    assert endpoint.host == "127.0.0.1"
    assert endpoint.port == 8000
    assert hasattr(endpoint, "app")
    assert isinstance(endpoint._stop_event, threading.Event)
    assert isinstance(endpoint._server_thread, threading.Thread)
    assert endpoint._server_thread.name == "endpoint_server_thread"
    assert hasattr(endpoint, "health_collector")
    assert endpoint.health_collector == endpoint._mock_health_collector

    # Check status route
    status_route = _get_route_by_path(endpoint, "/status")
    assert status_route is not None


def test_status_normal(endpoint):
    """test /status endpoint should return {"status": "normal"} when health_collector returns True"""
    import asyncio

    # Mock is_healthy to return True using AsyncMock
    endpoint._mock_health_collector.is_healthy = AsyncMock(return_value=True)
    mock_response = Mock()
    status_route = _get_route_by_path(endpoint, "/status")

    # Run async endpoint function
    result = asyncio.run(status_route.endpoint(response=mock_response))
    assert mock_response.status_code == 200
    assert result == {"status": constants.NORMAL_STATUS}


def test_status_abnormal(endpoint):
    """test /status endpoint should return {"status": "abnormal"} when health_collector returns False"""
    import asyncio

    # Mock is_healthy to return False using AsyncMock
    endpoint._mock_health_collector.is_healthy = AsyncMock(return_value=False)
    mock_response = Mock()
    status_route = _get_route_by_path(endpoint, "/status")

    # Run async endpoint function
    result = asyncio.run(status_route.endpoint(response=mock_response))
    assert mock_response.status_code == 200
    assert result == {"status": constants.ABNORMAL_STATUS}


def test_status_initial(endpoint):
    """test /status endpoint should return {"status": "initial"} when health_collector raises exception"""
    import asyncio

    # Mock is_healthy to raise exception
    endpoint._mock_health_collector.is_healthy.side_effect = Exception("Health check failed")
    mock_response = Mock()
    status_route = _get_route_by_path(endpoint, "/status")

    # Run async endpoint function
    result = asyncio.run(status_route.endpoint(response=mock_response))
    assert mock_response.status_code == 200
    assert result == {"status": constants.INIT_STATUS}


def test_run_server(endpoint):
    """test Endpoint.run() should start the server thread when called"""
    endpoint._server_thread.start = Mock()
    endpoint.run()
    endpoint._server_thread.start.assert_called_once()


@patch("motor.engine_server.core.endpoint.threading.Thread.join")
def test_shutdown_server(mock_join, endpoint):
    """test Endpoint.shutdown() should set stop event and server exit flag when called with unstarted thread"""
    endpoint._server_thread.is_alive = Mock(return_value=False)
    mock_server = Mock()
    endpoint._server = mock_server
    endpoint.shutdown()
    assert mock_server.should_exit is True
    assert endpoint._stop_event.is_set()
    mock_join.assert_not_called()


def test_set_server_core(endpoint):
    """test Endpoint.set_server_core() should set server_core attribute"""
    mock_server_core = Mock()
    endpoint.set_server_core(mock_server_core)
    assert endpoint._server_core == mock_server_core


if __name__ == "__main__":
    pytest.main(["-v", __file__])
