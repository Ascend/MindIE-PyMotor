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
import threading
from unittest.mock import Mock, MagicMock, patch
import sys

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

    with patch('motor.engine_server.core.endpoint.logger', mock_logger):
        try:
            yield
        finally:
            for module_name, original_module in original_modules.items():
                if original_module is not None:
                    sys.modules[module_name] = original_module
                else:
                    if module_name in sys.modules:
                        del sys.modules[module_name]


from motor.engine_server.config.base import ServerConfig
from motor.engine_server.core.service import Service
from motor.engine_server.core.endpoint import Endpoint
from motor.engine_server.core.endpoint import METRICS_SERVICE, HEALTH_SERVICE


@pytest.fixture(scope="function")
def endpoint():
    mock_server_config = Mock(spec=ServerConfig)
    mock_server_config.server_host = "127.0.0.1"
    mock_server_config.server_port = 8000
    mock_metrics_service = Mock(spec=Service)
    mock_health_service = Mock(spec=Service)
    mock_services = {
        METRICS_SERVICE: mock_metrics_service,
        HEALTH_SERVICE: mock_health_service
    }

    ep = Endpoint(
        server_config=mock_server_config,
        services=mock_services
    )
    ep._mock_metrics = mock_metrics_service
    ep._mock_health = mock_health_service

    yield ep
    if ep._server_thread.is_alive():
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
    assert endpoint.metrics_service == endpoint._mock_metrics
    assert endpoint.health_service == endpoint._mock_health
    assert hasattr(endpoint, "app")
    assert isinstance(endpoint._stop_event, threading.Event)
    assert isinstance(endpoint._server_thread, threading.Thread)
    assert endpoint._server_thread.name == "endpoint_server_thread"

    status_route = _get_route_by_path(endpoint, "/status")
    metrics_route = _get_route_by_path(endpoint, "/metrics")
    assert status_route is not None
    assert metrics_route is not None


def test_status_health_service_none(endpoint):
    """test /status endpoint should return {"status": "initial"} when health service returns empty data"""
    endpoint._mock_health.get_data.return_value = {}
    mock_response = Mock()
    status_route = _get_route_by_path(endpoint, "/status")

    result = status_route.endpoint(response=mock_response)
    assert mock_response.status_code == 200
    assert result == {"status": constants.INIT_STATUS}


def test_status_server_core_init(endpoint):
    """test /status endpoint should return {"status": "initial"} when server_core_status is init"""
    endpoint._mock_health.get_data.return_value = {
        "latest_health": {"core_status": constants.INIT_STATUS, "status": "success"}
    }
    mock_response = Mock()
    status_route = _get_route_by_path(endpoint, "/status")

    result = status_route.endpoint(response=mock_response)
    assert mock_response.status_code == 200
    assert result == {"status": constants.INIT_STATUS}


def test_status_abnormal(endpoint):
    """test /status endpoint should return {"status": "abnormal"} when collect_status is failed or server_core_status is abnormal"""
    mock_response = Mock()
    status_route = _get_route_by_path(endpoint, "/status")

    endpoint._mock_health.get_data.return_value = {
        "latest_health": {"core_status": "normal", "status": "failed"}
    }
    result1 = status_route.endpoint(response=mock_response)
    assert result1 == {"status": "abnormal"}

    endpoint._mock_health.get_data.return_value = {
        "latest_health": {"core_status": "abnormal", "status": "success"}
    }
    result2 = status_route.endpoint(response=mock_response)
    assert result2 == {"status": "abnormal"}


def test_status_normal(endpoint):
    """test /status endpoint should return {"status": "normal"} when server_core_status is normal and collection succeeds"""
    endpoint._mock_health.get_data.return_value = {
        "latest_health": {"core_status": "normal", "status": "success"}
    }
    mock_response = Mock()
    status_route = _get_route_by_path(endpoint, "/status")

    result = status_route.endpoint(response=mock_response)
    assert mock_response.status_code == 200
    assert result == {"status": "normal"}


def test_metrics_service_none(endpoint):
    """test /metrics endpoint should return empty body when metrics service returns empty data"""
    endpoint._mock_metrics.get_data.return_value = {}
    mock_response = Mock()
    metrics_route = _get_route_by_path(endpoint, "/metrics")

    result = metrics_route.endpoint(response=mock_response)
    assert result.status_code == 200
    assert result.body == b""
    assert result.media_type == "text/plain"


def test_metrics_server_core_init(endpoint):
    """test /metrics endpoint should return empty body when server_core_status is init"""
    endpoint._mock_metrics.get_data.return_value = {
        "latest_metrics": {"core_status": constants.INIT_STATUS, "status": "success"}
    }
    mock_response = Mock()
    metrics_route = _get_route_by_path(endpoint, "/metrics")

    result = metrics_route.endpoint(response=mock_response)
    assert result.status_code == 200
    assert result.body == b""


def test_metrics_collect_success(endpoint):
    """test /metrics endpoint should return collected data when metrics collection succeeds"""
    endpoint._mock_metrics.get_data.return_value = {
        "latest_metrics": {
            "core_status": "normal",
            "status": "success",
            "data": "cpu_usage 0.5\nmemory_usage 0.8"
        }
    }
    mock_response = Mock()
    metrics_route = _get_route_by_path(endpoint, "/metrics")

    result = metrics_route.endpoint(response=mock_response)
    assert result.status_code == 200
    assert result.body == b"cpu_usage 0.5\nmemory_usage 0.8"
    assert result.media_type == "text/plain"


def test_metrics_collect_failed(endpoint):
    """test /metrics endpoint should return empty body when metrics collection fails"""
    endpoint._mock_metrics.get_data.return_value = {
        "latest_metrics": {
            "core_status": "normal",
            "status": "failed",
            "data": "some_data"
        }
    }
    mock_response = Mock()
    metrics_route = _get_route_by_path(endpoint, "/metrics")

    result = metrics_route.endpoint(response=mock_response)
    assert result.status_code == 200
    assert result.body == b""


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


if __name__ == "__main__":
    pytest.main(["-v", __file__])
