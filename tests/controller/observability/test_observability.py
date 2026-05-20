# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

from unittest.mock import patch
import pytest

from motor.common.utils.singleton import ThreadSafeSingleton
from motor.config.controller import ControllerConfig
from motor.controller.observability.observability import Observability


_FULL_METRICS = "# HELP test_metric Test metric\n# TYPE test_metric gauge\ntest_metric 1.0\n"
_ROLE_METRICS = {
    "prefill": "# HELP pref metric\n# TYPE pref gauge\npref 1.0\n",
    "decode": "# HELP dec metric\n# TYPE dec gauge\ndec 2.0\n",
}


def _cleanup_singletons():
    """Clean up singleton instances to ensure test isolation."""
    singletons_to_cleanup = [Observability]
    for singleton_cls in singletons_to_cleanup:
        if singleton_cls in ThreadSafeSingleton._instances:
            try:
                instance = ThreadSafeSingleton._instances[singleton_cls]
                if hasattr(instance, "stop"):
                    instance.stop()
            except Exception:
                pass
            del ThreadSafeSingleton._instances[singleton_cls]


@pytest.fixture(autouse=True)
def cleanup_singletons():
    """Auto cleanup singletons before and after each test."""
    _cleanup_singletons()
    yield
    _cleanup_singletons()


@pytest.fixture
def observability():
    config = ControllerConfig()
    return Observability(config)


@patch(
    "motor.controller.observability.observability.CoordinatorApiClient.get_metrics"
)
def test_get_metrics_full_default(mock_get_metrics, observability):
    mock_get_metrics.return_value = _FULL_METRICS
    result = observability.get_metrics()
    assert result == _FULL_METRICS
    mock_get_metrics.assert_called_once_with(metrics_type="full", role=None)


@patch(
    "motor.controller.observability.observability.CoordinatorApiClient.get_metrics"
)
def test_get_metrics_full_explicit(mock_get_metrics, observability):
    mock_get_metrics.return_value = _FULL_METRICS
    result = observability.get_metrics(metrics_type="full")
    assert result == _FULL_METRICS
    mock_get_metrics.assert_called_once_with(metrics_type="full", role=None)


@patch(
    "motor.controller.observability.observability.CoordinatorApiClient.get_metrics"
)
def test_get_metrics_instance(mock_get_metrics, observability):
    mock_get_metrics.return_value = (
        '# HELP a\n# TYPE a gauge\na{instance_id="1",role="prefill"} 1.0\n'
    )
    result = observability.get_metrics(metrics_type="instance")
    assert result.startswith("# HELP")
    mock_get_metrics.assert_called_once_with(metrics_type="instance", role=None)


@patch(
    "motor.controller.observability.observability.CoordinatorApiClient.get_metrics"
)
def test_get_metrics_role_all(mock_get_metrics, observability):
    joined = "\n".join(_ROLE_METRICS.values())
    mock_get_metrics.return_value = joined
    result = observability.get_metrics(metrics_type="role")
    assert result == joined
    assert "# HELP pref metric" in result
    assert "# HELP dec metric" in result
    mock_get_metrics.assert_called_once_with(metrics_type="role", role=None)


@patch(
    "motor.controller.observability.observability.CoordinatorApiClient.get_metrics"
)
def test_get_metrics_role_filtered(mock_get_metrics, observability):
    mock_get_metrics.return_value = _ROLE_METRICS["prefill"]
    result = observability.get_metrics(metrics_type="role", role="prefill")
    assert result == _ROLE_METRICS["prefill"]
    mock_get_metrics.assert_called_once_with(metrics_type="role", role="prefill")


@patch(
    "motor.controller.observability.observability.CoordinatorApiClient.get_metrics"
)
def test_get_metrics_role_not_found(mock_get_metrics, observability):
    mock_get_metrics.return_value = ""
    result = observability.get_metrics(metrics_type="role", role="nonexistent")
    assert result == ""
    mock_get_metrics.assert_called_once_with(metrics_type="role", role="nonexistent")


@patch(
    "motor.controller.observability.observability.CoordinatorApiClient.get_metrics"
)
def test_get_metrics_api_returns_none(mock_get_metrics, observability):
    mock_get_metrics.return_value = None
    result = observability.get_metrics()
    assert result == ""
    mock_get_metrics.assert_called_once_with(metrics_type="full", role=None)


@patch(
    "motor.controller.observability.observability.CoordinatorApiClient.get_metrics"
)
def test_get_metrics_exception(mock_get_metrics, observability):
    mock_get_metrics.side_effect = RuntimeError("boom")
    result = observability.get_metrics()
    assert result == ""
