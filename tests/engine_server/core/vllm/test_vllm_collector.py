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
from unittest.mock import Mock, patch, MagicMock
import sys
from requests.exceptions import ConnectionError, Timeout, HTTPError, RequestException


@pytest.fixture
def mock_logger_module():
    module_name = 'motor.common.utils.logger'
    original_logger = sys.modules.get(module_name)

    mock_logger = MagicMock()
    mock_logger_module = MagicMock()
    mock_logger_module.get_logger = MagicMock(return_value=mock_logger)
    sys.modules[module_name] = mock_logger_module

    try:
        yield
    finally:
        if original_logger is not None:
            sys.modules[module_name] = original_logger
        else:
            if module_name in sys.modules:
                del sys.modules[module_name]


from motor.engine_server.core.vllm.vllm_collector import VLLMCollector
from motor.engine_server.config.base import IConfig


@pytest.fixture(scope="function")
def vllm_collector(mock_logger_module):
    # Clear module from cache if it exists to ensure fresh import with mocks
    if 'motor.engine_server.core.vllm.vllm_collector' in sys.modules:
        del sys.modules['motor.engine_server.core.vllm.vllm_collector']
    
    # Reimport with fresh mocks
    from motor.engine_server.core.vllm.vllm_collector import VLLMCollector
    from motor.engine_server.config.base import IConfig
    from motor.config.tls_config import TLSConfig
    
    with patch("motor.engine_server.core.vllm.vllm_collector.logger") as mock_logger:
        mock_config = Mock(spec=IConfig)
        mock_server_config = Mock()
        mock_server_config.engine_type = "vllm"
        mock_deploy_config = Mock()
        mock_tls_config = TLSConfig(enable_tls=False)
        mock_deploy_config.infer_tls_config = mock_tls_config
        mock_server_config.deploy_config = mock_deploy_config
        mock_config.get_server_config.return_value = mock_server_config
        mock_args = Mock()
        mock_args.host = "127.0.0.1"
        mock_args.port = 8000
        mock_config.get_args.return_value = mock_args
        collector = VLLMCollector(config=mock_config)
        collector._mock_logger = mock_logger
        collector._expected_metrics_url = "http://127.0.0.1:8000/metrics"
        collector._expected_health_url = "http://127.0.0.1:8000/health"
        collector._mock_config = mock_config
        yield collector


def test_initialization(vllm_collector, mock_logger_module):
    """test VLLMCollector should initialize with correct properties and log info when created"""
    expected_name = f"{vllm_collector._mock_config.get_server_config().engine_type}_metrics_and_health_collector"
    assert vllm_collector.name == expected_name
    assert vllm_collector.host == "127.0.0.1"
    assert vllm_collector.port == 8000
    assert vllm_collector.collect_interval == 3
    assert vllm_collector.timeout == 2
    vllm_collector._mock_logger.info.assert_called_once_with(
        "VLLMCollector initialized: collect_interval=3s"
    )


@patch("motor.engine_server.core.vllm.vllm_collector.VLLMCollector._do_collect_health")
@patch("motor.engine_server.core.vllm.vllm_collector.VLLMCollector._do_collect_metrics")
@patch("motor.engine_server.core.vllm.vllm_collector.time.time")
def test_collect_returns_combined_data(mock_time, mock_do_metrics, mock_do_health, vllm_collector, mock_logger_module):
    """test VLLMCollector._collect() should return combined metrics and health data with timestamp when called"""
    mock_time.return_value = 1718000000.0
    expected_timestamp = int(mock_time.return_value * 1000)
    mock_metrics_data = {"status": "success", "data": "metrics_data"}
    mock_health_data = {"status": "success", "data": None}
    mock_do_metrics.return_value = mock_metrics_data
    mock_do_health.return_value = mock_health_data
    result = vllm_collector._collect()

    assert result["timestamp"] == expected_timestamp
    assert result["collector_name"] == vllm_collector.name
    assert result["metrics"] == mock_metrics_data
    assert result["health"] == mock_health_data


@patch("motor.engine_server.core.vllm.vllm_collector.SafeHTTPSClient")
def test_do_collect_metrics_success(mock_client_class, vllm_collector, mock_logger_module):
    """test VLLMCollector._do_collect_metrics() should return success data when request succeeds"""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.text = "prometheus_metrics_data"
    mock_client.do_get.return_value = mock_response
    mock_client_class.return_value = mock_client
    
    result = vllm_collector._do_collect_metrics()
    
    assert result["status"] == "success"
    assert result["api_url"] == vllm_collector._expected_metrics_url
    assert result["data"] == "prometheus_metrics_data"
    assert result["http_status_code"] == 200
    assert "collect_time" in result


@patch("motor.engine_server.core.vllm.vllm_collector.SafeHTTPSClient")
def test_do_collect_metrics_connection_error(mock_client_class, vllm_collector, mock_logger_module):
    """test VLLMCollector._do_collect_metrics() should return failed result with error log when connection fails"""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.do_get.side_effect = ConnectionError("Connection refused")
    mock_client_class.return_value = mock_client
    
    result = vllm_collector._do_collect_metrics()
    
    assert result["status"] == "failed"
    assert result["api_url"] == vllm_collector._expected_metrics_url
    assert "Connection refused" in result["error"]
    assert result["data"] is None
    assert result["http_status_code"] is None
    vllm_collector._mock_logger.error.assert_called_once()


@patch("motor.engine_server.core.vllm.vllm_collector.SafeHTTPSClient")
def test_do_collect_metrics_timeout(mock_client_class, vllm_collector, mock_logger_module):
    """test VLLMCollector._do_collect_metrics() should return failed result when request times out"""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.do_get.side_effect = Timeout("Request timed out")
    mock_client_class.return_value = mock_client
    
    result = vllm_collector._do_collect_metrics()
    
    assert result["status"] == "failed"
    assert "timed out" in result["error"]
    assert result["http_status_code"] is None


@patch("motor.engine_server.core.vllm.vllm_collector.SafeHTTPSClient")
def test_do_collect_metrics_http_error(mock_client_class, vllm_collector, mock_logger_module):
    """test VLLMCollector._do_collect_metrics() should return failed result with status code when HTTP error occurs"""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_response = Mock()
    mock_response.status_code = 404
    mock_client.do_get.side_effect = HTTPError("404 Not Found", response=mock_response)
    mock_client_class.return_value = mock_client
    
    result = vllm_collector._do_collect_metrics()
    
    assert result["status"] == "failed"
    assert result["http_status_code"] == 404
    assert "404 Not Found" in result["error"]


@patch("motor.engine_server.core.vllm.vllm_collector.SafeHTTPSClient")
def test_do_collect_metrics_generic_request_error(mock_client_class, vllm_collector, mock_logger_module):
    """test VLLMCollector._do_collect_metrics() should return failed result for generic request errors"""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.do_get.side_effect = RequestException("Unknown error")
    mock_client_class.return_value = mock_client
    
    result = vllm_collector._do_collect_metrics()
    
    assert result["status"] == "failed"
    assert "Unknown error" in result["error"]
    assert result["http_status_code"] is None


@patch("motor.engine_server.core.vllm.vllm_collector.SafeHTTPSClient")
def test_do_collect_health_success(mock_client_class, vllm_collector, mock_logger_module):
    """test VLLMCollector._do_collect_health() should return success result when health check succeeds"""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_response = Mock()
    mock_response.status_code = 200
    mock_client.do_get.return_value = mock_response
    mock_client_class.return_value = mock_client
    
    result = vllm_collector._do_collect_health()
    
    assert result["status"] == "success"
    assert result["http_status_code"] == 200
    assert result["data"] is None


@patch("motor.engine_server.core.vllm.vllm_collector.SafeHTTPSClient")
def test_do_collect_health_non_200_status(mock_client_class, vllm_collector, mock_logger_module):
    """test VLLMCollector._do_collect_health() should return failed result with error log when status is non-200"""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_response = Mock()
    mock_response.status_code = 503
    mock_client.do_get.return_value = mock_response
    mock_client_class.return_value = mock_client
    
    result = vllm_collector._do_collect_health()
    
    assert result["status"] == "failed"
    assert result["http_status_code"] == 503
    assert "503" in result["error"]
    vllm_collector._mock_logger.error.assert_called_once()


@patch("motor.engine_server.core.vllm.vllm_collector.SafeHTTPSClient")
def test_do_collect_health_connection_error(mock_client_class, vllm_collector, mock_logger_module):
    """test VLLMCollector._do_collect_health() should return failed result when connection fails"""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.do_get.side_effect = ConnectionError("Cannot connect")
    mock_client_class.return_value = mock_client
    
    result = vllm_collector._do_collect_health()
    
    assert result["status"] == "failed"
    assert "Cannot connect" in result["error"]


@patch("motor.engine_server.core.vllm.vllm_collector.time.time")
def test_build_error_result(mock_time, vllm_collector, mock_logger_module):
    """test VLLMCollector._build_error_result() should return error data with correct format when called with status code"""
    mock_time.return_value = 1718000000.0
    expected_collect_time = int(mock_time.return_value * 1000)
    result = VLLMCollector._build_error_result(
        error_msg="Test error",
        url="http://test.url",
        http_status_code=400
    )
    assert result["api_url"] == "http://test.url"
    assert result["status"] == "failed"
    assert result["error"] == "Test error"
    assert result["data"] is None
    assert result["http_status_code"] == 400
    assert result["collect_time"] == expected_collect_time


@patch("motor.engine_server.core.vllm.vllm_collector.time.time")
def test_build_error_result_no_http_status(mock_time, vllm_collector, mock_logger_module):
    """test VLLMCollector._build_error_result() should return error data without status code when not provided"""
    mock_time.return_value = 1718000000.0
    result = VLLMCollector._build_error_result(
        error_msg="No status error",
        url="http://test.url",
        http_status_code=None
    )
    assert result["http_status_code"] is None
    assert "collect_time" in result


if __name__ == "__main__":
    pytest.main(["-v", __file__])
