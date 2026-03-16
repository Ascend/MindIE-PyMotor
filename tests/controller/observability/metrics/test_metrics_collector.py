# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import threading
import time
from unittest.mock import patch, Mock
import pytest

from motor.common.utils.logger import get_logger
from motor.controller.api_client.coordinator_api_client import CoordinatorApiClient
from motor.config.controller import ControllerConfig
from motor.controller.observability.metrics.metrics_collector import MetricsCollector

# Define test metrics data
TEST_METRICS_DATA = """# HELP vllm:request_success_total Count of successfully processed requests.
# TYPE vllm:request_success_total counter
vllm:request_success_total{engine="0",finished_reason="stop",model_name="/job/model/Qwen2.5-0.5B-Instruct"} 1.0
vllm:request_success_total{engine="0",finished_reason="length",model_name="/job/model/Qwen2.5-0.5B-Instruct"} 2.0
vllm:request_success_total{engine="0",finished_reason="abort",model_name="/job/model/Qwen2.5-0.5B-Instruct"} 0.0"""

@pytest.fixture
def metrics_collector():
    """Fixture to create a MetricsCollector instance"""
    config = ControllerConfig()
    config.observability_config.metrics_ttl = 2  # Set a short TTL for testing
    collector = MetricsCollector(config)
    return collector


@patch("motor.controller.observability.metrics.metrics_collector.time.monotonic")
@patch("motor.controller.observability.metrics.metrics_collector.CoordinatorApiClient.get_full_metrics")
def test_get_full_metrics_normal_case(mock_get_metrics, mock_monotonic, metrics_collector):
    """
    Test normal case: First-time metrics fetch
    """
    # Prepare test data
    mock_get_metrics.return_value = TEST_METRICS_DATA
    
    # Mock time
    mock_monotonic.return_value = 1000.0
    
    # Execute
    result = metrics_collector.get_full_metrics()
    
    # Verify
    assert result == TEST_METRICS_DATA
    mock_get_metrics.assert_called_once()
    
    # Verify cache
    with metrics_collector._lock:
        assert metrics_collector._last_metrics == TEST_METRICS_DATA
        assert metrics_collector._last_fetch_time == 1000.0


@patch("motor.controller.observability.metrics.metrics_collector.time.monotonic")
@patch("motor.controller.observability.metrics.metrics_collector.CoordinatorApiClient.get_full_metrics")
def test_get_full_metrics_cache_hit(mock_get_metrics, mock_monotonic, metrics_collector):
    """
    Test cache hit: Return cached data within TTL
    """
    # First call, set cache
    mock_get_metrics.return_value = TEST_METRICS_DATA
    mock_monotonic.return_value = 1000.0
    result1 = metrics_collector.get_full_metrics()
    
    assert result1 == TEST_METRICS_DATA
    mock_get_metrics.assert_called_once()
    
    # Second call, within TTL, should hit cache
    mock_get_metrics.reset_mock()
    mock_monotonic.return_value = 1001.9  # Still within TTL
    result2 = metrics_collector.get_full_metrics()
    
    # Verify: Should return cache, and API is not called again
    assert result2 == TEST_METRICS_DATA
    mock_get_metrics.assert_not_called()


@patch("motor.controller.observability.metrics.metrics_collector.time.monotonic")
@patch("motor.controller.observability.metrics.metrics_collector.CoordinatorApiClient.get_full_metrics")
def test_get_full_metrics_cache_expired(mock_get_metrics, mock_monotonic, metrics_collector):
    """
    Test cache expiration: Refetch after TTL
    """
    # Prepare new metrics data
    NEW_METRICS_DATA = TEST_METRICS_DATA + """
vllm:request_success_total{engine="1",finished_reason="stop",model_name="/job/model/Qwen2.5-0.5B-Instruct"} 5.0"""
    
    # First call, set cache
    mock_get_metrics.return_value = TEST_METRICS_DATA
    mock_monotonic.return_value = 1000.0
    result1 = metrics_collector.get_full_metrics()
    
    # Second call, TTL expired, should refetch
    mock_get_metrics.reset_mock()
    mock_get_metrics.return_value = NEW_METRICS_DATA
    mock_monotonic.return_value = 1002.1  # Exceeds 2-second TTL
    result2 = metrics_collector.get_full_metrics()
    
    # Verify: Should return new metrics
    assert result2 == NEW_METRICS_DATA
    mock_get_metrics.assert_called_once()
    
    # Verify cache is updated
    with metrics_collector._lock:
        assert metrics_collector._last_metrics == NEW_METRICS_DATA
        assert metrics_collector._last_fetch_time == 1002.1


@patch("motor.controller.observability.metrics.metrics_collector.time.monotonic")
@patch("motor.controller.observability.metrics.metrics_collector.CoordinatorApiClient.get_full_metrics")
def test_get_full_metrics_api_failure_with_cache(mock_get_metrics, mock_monotonic, metrics_collector):
    """
    Test API failure with existing cache
    """
    # First successful call, set cache
    mock_get_metrics.return_value = TEST_METRICS_DATA
    mock_monotonic.return_value = 1000.0
    result1 = metrics_collector.get_full_metrics()
    
    assert result1 == TEST_METRICS_DATA
    
    # Second call, cache expired but API call fails
    mock_get_metrics.reset_mock()
    mock_get_metrics.return_value = None  # API call fails
    mock_monotonic.return_value = 1002.5  # Exceeds TTL
    
    result2 = metrics_collector.get_full_metrics()
    
    # Verify: Should return old cache
    assert result2 == TEST_METRICS_DATA
    mock_get_metrics.assert_called_once()
    
    # Verify fetch time is not updated
    with metrics_collector._lock:
        assert metrics_collector._last_fetch_time == 1000.0


@patch("motor.controller.observability.metrics.metrics_collector.time.monotonic")
@patch("motor.controller.observability.metrics.metrics_collector.CoordinatorApiClient.get_full_metrics")
def test_get_full_metrics_api_failure_without_cache(mock_get_metrics, mock_monotonic, metrics_collector):
    """
    Test API failure without cache
    """
    # First call fails immediately
    mock_get_metrics.return_value = None
    mock_monotonic.return_value = 1000.0
    
    result = metrics_collector.get_full_metrics()
    
    # Verify: Should return empty string
    assert result == ""
    mock_get_metrics.assert_called_once()
    
    # Verify cache is empty
    with metrics_collector._lock:
        assert metrics_collector._last_metrics is None
        assert metrics_collector._last_fetch_time == 0.0


@patch("motor.controller.observability.metrics.metrics_collector.time.monotonic")
@patch("motor.controller.observability.metrics.metrics_collector.CoordinatorApiClient.get_full_metrics")
def test_get_full_metrics_concurrent_access(mock_get_metrics, mock_monotonic, metrics_collector):
    """
    Test concurrent access: Multiple threads accessing simultaneously
    """
    import threading
    
    # Record API call count
    call_count = 0
    
    def call_api():
        nonlocal call_count
        call_count += 1
        return TEST_METRICS_DATA
    
    mock_get_metrics.side_effect = call_api
    
    # Set time points to simulate cache expiration
    time_points = iter([1000.0, 1001.0, 1001.1, 1001.2])
    mock_monotonic.side_effect = lambda: next(time_points)
    
    # Create multiple threads for concurrent calls
    results = []
    threads = []
    
    def worker():
        result = metrics_collector.get_full_metrics()
        results.append(result)
    
    for _ in range(3):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()
    
    for t in threads:
        t.join()
    
    # Verify: API should be called only once (due to lock)
    assert call_count == 1
    assert len(results) == 3
    assert all(r == TEST_METRICS_DATA for r in results)


@patch("motor.controller.observability.metrics.metrics_collector.time.monotonic")
@patch("motor.controller.observability.metrics.metrics_collector.CoordinatorApiClient.get_full_metrics")
def test_get_full_metrics_empty_string_response(mock_get_metrics, mock_monotonic, metrics_collector):
    """
    Test API returning empty string
    """
    # API returns empty string
    mock_get_metrics.return_value = ""
    mock_monotonic.return_value = 1000.0
    
    result = metrics_collector.get_full_metrics()
    
    # Verify: Should return empty string
    assert result == ""
    mock_get_metrics.assert_called_once()
    
    # Verify cache is set to empty string
    with metrics_collector._lock:
        assert metrics_collector._last_metrics == ""


def test_metrics_collector_default_config():
    """
    Test creating MetricsCollector with default config
    """
    collector = MetricsCollector()  # No config passed, uses default
    
    # Verify default config
    assert collector._cache_ttl_sec == collector.config.observability_config.metrics_ttl
    assert isinstance(collector._lock, type(threading.RLock()))
    assert collector._last_metrics is None
    assert collector._last_fetch_time == 0.0


@patch("motor.controller.observability.metrics.metrics_collector.time.monotonic")
@patch("motor.controller.observability.metrics.metrics_collector.CoordinatorApiClient.get_full_metrics")
def test_get_full_metrics_edge_case_exact_ttl(mock_get_metrics, mock_monotonic, metrics_collector):
    """
    Test edge case: Exactly reaching TTL
    """
    # First call
    mock_get_metrics.return_value = TEST_METRICS_DATA
    mock_monotonic.return_value = 1000.0
    result1 = metrics_collector.get_full_metrics()
    
    assert result1 == TEST_METRICS_DATA
    
    # Exactly at TTL (exactly 2 seconds), should refetch
    mock_get_metrics.reset_mock()
    NEW_METRICS_DATA = TEST_METRICS_DATA + """
# Add new metric
vllm:request_latency_bucket{le="0.1"} 10.0"""
    mock_get_metrics.return_value = NEW_METRICS_DATA
    mock_monotonic.return_value = 1002.0  # Exactly 2 seconds
    
    result2 = metrics_collector.get_full_metrics()
    
    # Verify: Should refetch
    assert result2 == NEW_METRICS_DATA
    mock_get_metrics.assert_called_once()