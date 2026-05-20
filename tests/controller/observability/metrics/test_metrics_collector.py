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
from unittest.mock import patch

from motor.controller.observability.metrics.metrics_collector import MetricsCollector


@pytest.fixture
def metrics_collector():
    """Fixture to create a MetricsCollector instance"""
    return MetricsCollector()


@patch("motor.controller.observability.metrics.metrics_collector.CoordinatorApiClient.get_metrics")
def test_get_full_metrics(mock_get_metrics, metrics_collector):
    mock_get_metrics.return_value = "# HELP test test_metric\ntest_metric 1.0"

    result = metrics_collector.get_full_metrics()

    assert result == "# HELP test test_metric\ntest_metric 1.0"
    mock_get_metrics.assert_called_once_with(metrics_type="full")


@patch("motor.controller.observability.metrics.metrics_collector.CoordinatorApiClient.get_metrics")
def test_get_full_metrics_api_failure(mock_get_metrics, metrics_collector):
    mock_get_metrics.return_value = None

    result = metrics_collector.get_full_metrics()

    assert result == ""
    mock_get_metrics.assert_called_once_with(metrics_type="full")


@patch("motor.controller.observability.metrics.metrics_collector.CoordinatorApiClient.get_metrics")
def test_get_instance_metrics_prometheus(mock_get_metrics, metrics_collector):
    mock_get_metrics.return_value = (
        '# HELP test test_metric\n'
        '# TYPE test_metric gauge\n'
        'test_metric{instance_id="1",role="prefill"} 5.0'
    )

    result = metrics_collector.get_instance_metrics_prometheus()

    assert "instance_id" in result
    assert "prefill" in result
    mock_get_metrics.assert_called_once_with(metrics_type="instance")


@patch("motor.controller.observability.metrics.metrics_collector.CoordinatorApiClient.get_metrics")
def test_get_instance_metrics_prometheus_api_failure(mock_get_metrics, metrics_collector):
    mock_get_metrics.return_value = None

    result = metrics_collector.get_instance_metrics_prometheus()

    assert result == ""
    mock_get_metrics.assert_called_once_with(metrics_type="instance")


@patch("motor.controller.observability.metrics.metrics_collector.CoordinatorApiClient.get_metrics")
def test_get_role_metrics_all(mock_get_metrics, metrics_collector):
    joined = (
        "# HELP test test_metric\ntest_metric{role=\"prefill\"} 5.0\n"
        "# HELP test test_metric\ntest_metric{role=\"decode\"} 3.0"
    )
    mock_get_metrics.return_value = joined

    result = metrics_collector.get_role_metrics()

    assert "prefill" in result
    assert "decode" in result
    mock_get_metrics.assert_called_once_with(metrics_type="role", role=None)


@patch("motor.controller.observability.metrics.metrics_collector.CoordinatorApiClient.get_metrics")
def test_get_role_metrics_specific_role(mock_get_metrics, metrics_collector):
    mock_get_metrics.return_value = (
        '# HELP test test_metric\n'
        '# TYPE test_metric gauge\n'
        'test_metric{role="prefill"} 5.0'
    )

    result = metrics_collector.get_role_metrics(role="prefill")

    assert "prefill" in result
    mock_get_metrics.assert_called_once_with(metrics_type="role", role="prefill")


@patch("motor.controller.observability.metrics.metrics_collector.CoordinatorApiClient.get_metrics")
def test_get_role_metrics_api_failure(mock_get_metrics, metrics_collector):
    mock_get_metrics.return_value = None

    result = metrics_collector.get_role_metrics()

    assert result == ""
    mock_get_metrics.assert_called_once_with(metrics_type="role", role=None)
