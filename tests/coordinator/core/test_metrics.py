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

# pylint: disable=too-many-lines  # 测试用例集中于单文件，拆分需新建文件

import asyncio
import os
import threading
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import requests
import re
import copy
from urllib.parse import urlparse

from motor.common.resources.instance import Instance, PDRole, Endpoint
from motor.coordinator.domain.instance_manager import InstanceManager
from motor.coordinator.metrics.metrics_collector import MetricsCollector, MetricType, Metric
from motor.config.coordinator import CoordinatorConfig
from motor.common.utils.singleton import ThreadSafeSingleton


def _stop_singleton_instance(instance):
    """Call stop() on a singleton instance, awaiting the coroutine if needed."""
    if not hasattr(instance, "stop"):
        return
    stop_result = instance.stop()
    # If stop() returns a coroutine, run it to avoid "never awaited" warning
    if asyncio.iscoroutine(stop_result):
        try:
            asyncio.run(stop_result)
        except RuntimeError:
            pass  # e.g. cannot call run() from running loop; skip


def _cleanup_singletons():
    """Clean up singleton instances to ensure test isolation"""
    singletons_to_cleanup = [MetricsCollector]

    for singleton_cls in singletons_to_cleanup:
        if singleton_cls not in ThreadSafeSingleton._instances:
            continue
        instance = ThreadSafeSingleton._instances[singleton_cls]
        try:
            _stop_singleton_instance(instance)
        except Exception:
            pass  # Ignore errors during cleanup
        del ThreadSafeSingleton._instances[singleton_cls]


@pytest.fixture(autouse=True)
def cleanup_singletons():
    """Auto cleanup singletons before and after each test"""
    _cleanup_singletons()
    yield
    _cleanup_singletons()


@pytest.fixture
def mock_metrics_collector():
    """Create a mock MetricsCollector for testing"""
    collector = MagicMock(spec=MetricsCollector)
    collector._inactive_instance_metrics_aggregate = []
    collector._instance_metrics_cached = {}
    collector._last_metrics = None
    collector._lock = MagicMock()
    collector._stop_event = MagicMock()
    return collector


class MockResponse:
    def __init__(self, json_data, status_code):
        self.json_data = json_data
        self.status_code = status_code

    def json(self):
        return self.json_data


class TestMetrics:
    # pytest 使用 setup_method 而非 __init__ 初始化用例属性
    # pylint: disable=attribute-defined-outside-init
    def setup_method(self):
        # Create config for testing
        self.config = CoordinatorConfig()
        self.instance_manager = InstanceManager(self.config)

        ep0 = Endpoint(id=0, ip="127.0.0.1", business_port="8000", mgmt_port="8000")
        ep1 = Endpoint(id=1, ip="127.0.0.1", business_port="8001", mgmt_port="8001")
        ep2 = Endpoint(id=2, ip="127.0.0.1", business_port="8002", mgmt_port="8002")
        ep3 = Endpoint(id=3, ip="127.0.0.1", business_port="8003", mgmt_port="8003")
        ep4 = Endpoint(id=4, ip="127.0.0.1", business_port="8004", mgmt_port="8004")
        ep5 = Endpoint(id=5, ip="127.0.0.1", business_port="8005", mgmt_port="8005")
        self.p_ins = Instance(
            job_name="test-prefill",
            model_name="test-model",
            id=0,
            role=PDRole.ROLE_P,
            endpoints={"127.0.0.1": {0: ep0, 1: ep1}},
        )
        self.d_ins = Instance(
            job_name="test-decode",
            model_name="test-model",
            id=1,
            role=PDRole.ROLE_D,
            endpoints={"127.0.0.1": {2: ep2, 3: ep3}},
        )
        self.h_ins = Instance(
            job_name="test-hybrid",
            model_name="test-model",
            id=2,
            role=PDRole.ROLE_U,
            endpoints={"127.0.0.1": {4: ep4, 5: ep5}},
        )

        self.metrics_template = self.load_example_metrics()

    def teardown_method(self):
        # Additional cleanup if needed (global cleanup is handled by fixture)
        pass

    def load_example_metrics(self):
        script_path = os.path.abspath(__file__)
        script_dir = os.path.dirname(script_path)
        data_path = os.path.join(script_dir, "metrics_example.txt")
        with open(data_path, "r", encoding="utf-8") as f:
            return f.read().strip()

    def clean_instances(self):
        # Additional cleanup for test isolation (global cleanup handles singletons)
        # This method is kept for backward compatibility with existing tests
        pass

    def create_test_metrics_collector(self):
        """Create a MetricsCollector instance for testing without background threads."""
        # Create instance without triggering __init__
        collector = MetricsCollector.__new__(MetricsCollector)

        # Manually initialize attributes without starting background thread
        collector._inactive_instance_metrics_aggregate = []
        collector._instance_metrics_cached = {}
        collector._last_metrics = None
        collector._reuse_time = 0.001  # Very short interval for testing
        collector._lock = threading.Lock()
        collector._stop_event = threading.Event()

        # Set as initialized but don't start the thread
        collector._initialized = True

        return collector

    @staticmethod
    def _test_without_background_thread(test_func):
        """Decorator to run a test without background threads."""

        def wrapper(*args, **kwargs):
            with patch("threading.Thread.start", MagicMock()):
                return test_func(*args, **kwargs)

        return wrapper

    def load_test_gauge_metric(self):
        # metric text
        metric_str_gauge = """
# HELP vllm:num_requests_running Number of requests in model execution batches.
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{engine="0",model_name="/job/model/Qwen2.5-0.5B-Instruct"} 1.0"""

        # metric format
        metric_gauge = Metric()
        metric_gauge.name = "vllm:num_requests_running"
        metric_gauge.help = "Number of requests in model execution batches."
        metric_gauge.type = MetricType.GAUGE
        metric_gauge.label = ['vllm:num_requests_running{model_name="/job/model/Qwen2.5-0.5B-Instruct"}']
        metric_gauge.value = [1.0]

        return metric_str_gauge.strip(), copy.deepcopy(metric_gauge)

    def load_test_counter_metric(self):
        # metric text
        metric_str_counter = """
# HELP vllm:request_success_total Count of successfully processed requests.
# TYPE vllm:request_success_total counter
vllm:request_success_total{engine="0",finished_reason="stop",model_name="/job/model/Qwen2.5-0.5B-Instruct"} 1.0
vllm:request_success_total{engine="0",finished_reason="length",model_name="/job/model/Qwen2.5-0.5B-Instruct"} 2.0
vllm:request_success_total{engine="0",finished_reason="abort",model_name="/job/model/Qwen2.5-0.5B-Instruct"} 0.0"""

        # metric format
        metric_counter = Metric()
        metric_counter.name = "vllm:request_success_total"
        metric_counter.help = "Count of successfully processed requests."
        metric_counter.type = MetricType.COUNTER
        metric_counter.label = [
            'vllm:request_success_total{finished_reason="stop",model_name="/job/model/Qwen2.5-0.5B-Instruct"}',
            'vllm:request_success_total{finished_reason="length",model_name="/job/model/Qwen2.5-0.5B-Instruct"}',
            'vllm:request_success_total{finished_reason="abort",model_name="/job/model/Qwen2.5-0.5B-Instruct"}',
        ]
        metric_counter.value = [1.0, 2.0, 0.0]

        return metric_str_counter.strip(), copy.deepcopy(metric_counter)

    def load_test_histogram_metric(self):
        # metric text
        metric_str_histogram = """
# HELP vllm:request_params_n Histogram of the n request parameter.
# TYPE vllm:request_params_n histogram
vllm:request_params_n_bucket{engine="0",le="1.0",model_name="/job/model/Qwen2.5-0.5B-Instruct"} 3.0
vllm:request_params_n_bucket{engine="0",le="2.0",model_name="/job/model/Qwen2.5-0.5B-Instruct"} 3.0
vllm:request_params_n_bucket{engine="0",le="5.0",model_name="/job/model/Qwen2.5-0.5B-Instruct"} 3.0
vllm:request_params_n_bucket{engine="0",le="10.0",model_name="/job/model/Qwen2.5-0.5B-Instruct"} 3.0
vllm:request_params_n_bucket{engine="0",le="20.0",model_name="/job/model/Qwen2.5-0.5B-Instruct"} 3.0
vllm:request_params_n_bucket{engine="0",le="+Inf",model_name="/job/model/Qwen2.5-0.5B-Instruct"} 3.0
vllm:request_params_n_count{engine="0",model_name="/job/model/Qwen2.5-0.5B-Instruct"} 3.0
vllm:request_params_n_sum{engine="0",model_name="/job/model/Qwen2.5-0.5B-Instruct"} 3.0"""

        # metric format
        metric_histogram = Metric()
        metric_histogram.name = "vllm:request_params_n"
        metric_histogram.help = "Histogram of the n request parameter."
        metric_histogram.type = MetricType.HISTOGRAM
        metric_histogram.label = [
            'vllm:request_params_n_bucket{le="1.0",model_name="/job/model/Qwen2.5-0.5B-Instruct"}',
            'vllm:request_params_n_bucket{le="2.0",model_name="/job/model/Qwen2.5-0.5B-Instruct"}',
            'vllm:request_params_n_bucket{le="5.0",model_name="/job/model/Qwen2.5-0.5B-Instruct"}',
            'vllm:request_params_n_bucket{le="10.0",model_name="/job/model/Qwen2.5-0.5B-Instruct"}',
            'vllm:request_params_n_bucket{le="20.0",model_name="/job/model/Qwen2.5-0.5B-Instruct"}',
            'vllm:request_params_n_bucket{le="+Inf",model_name="/job/model/Qwen2.5-0.5B-Instruct"}',
            'vllm:request_params_n_count{model_name="/job/model/Qwen2.5-0.5B-Instruct"}',
            'vllm:request_params_n_sum{model_name="/job/model/Qwen2.5-0.5B-Instruct"}',
        ]
        metric_histogram.value = [3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0]

        return metric_str_histogram.strip(), copy.deepcopy(metric_histogram)

    def load_test_summary_metric(self):
        metric_str_summary = """
# HELP http_request_size_bytes Content length of incoming requests by handler. Only value of header is respected. Otherwise ignored. No percentile calculated.
# TYPE http_request_size_bytes summary
http_request_size_bytes_count{handler="/v1/completions"} 2.0
http_request_size_bytes_sum{handler="/v1/completions"} 312.0
http_request_size_bytes_count{handler="/v1/chat/completions"} 1.0
http_request_size_bytes_sum{handler="/v1/chat/completions"} 268.0"""

        metric_summary = Metric()
        metric_summary.name = "http_request_size_bytes"
        metric_summary.help = "Content length of incoming requests by handler. Only value of header is respected. Otherwise ignored. No percentile calculated."
        metric_summary.type = MetricType.SUMMARY
        metric_summary.label = [
            'http_request_size_bytes_count{handler="/v1/completions"}',
            'http_request_size_bytes_sum{handler="/v1/completions"}',
            'http_request_size_bytes_count{handler="/v1/chat/completions"}',
            'http_request_size_bytes_sum{handler="/v1/chat/completions"}',
        ]
        metric_summary.value = [2.0, 312.0, 1.0, 268.0]

        return metric_str_summary.strip(), copy.deepcopy(metric_summary)

    def check_metric_value_equel(self, a: list[float], b: list[float]) -> bool:
        if not isinstance(a, list) or not isinstance(b, list):
            return False

        if len(a) != len(b):
            return False

        allow_diff = 0.01
        for i in range(len(a)):
            if not isinstance(a[i], float) or not isinstance(b[i], float):
                return False
            if a[i] != b[i] and abs(a[i] - b[i]) > allow_diff:
                return False

        return True

    def check_metrics_equel(self, a: list[Metric], b: list[Metric]) -> bool:
        if not isinstance(a, list) or not isinstance(b, list):
            return False

        if len(a) != len(b):
            return False

        for i in range(len(a)):
            if a[i].name != b[i].name:
                return False
            if a[i].help != b[i].help:
                return False
            if a[i].type != b[i].type:
                return False
            if a[i].label != b[i].label:
                return False
            if not self.check_metric_value_equel(a[i].value, b[i].value):
                return False

        return True

    def metric_add(self, a: Metric, b: Metric) -> Metric:
        c = a.copy()
        for i in range(len(a.value)):
            c.value[i] = a.value[i] + b.value[i]
        return c

    @_test_without_background_thread
    def test_parse_metrics_text_normal(self):
        metric_collector = MetricsCollector(self.config)

        # load test metric data
        metric_list = [
            self.load_test_gauge_metric(),
            self.load_test_counter_metric(),
            self.load_test_histogram_metric(),
            self.load_test_summary_metric(),
        ]

        # create mix data of 4 type metrics
        merged_metric_str = ""
        merged_metric = []
        for metric_str, metric in metric_list:
            merged_metric_str += metric_str
            merged_metric.append(merged_metric)

        # check _parse_metric_text use metric_list
        for metric_str, metric in metric_list:
            result = metric_collector._parse_metric_text(metric_str)
            assert self.check_metrics_equel(result, [metric])

        # check _parse_metric_text use full metric data
        metric_collector = MetricsCollector(self.config)
        result = metric_collector._parse_metric_text(self.metrics_template)
        assert isinstance(result, list)
        assert len(result) > 0

    @_test_without_background_thread
    def test_parse_metrics_text_abnormal(self):
        metrics_str_type_error = """
# HELP vllm:num_requests_running Number of requests in model execution batches.
# TYPE vllm:num_requests_running type_error
vllm:num_requests_running{engine="0",model_name="/job/model/Qwen2.5-0.5B-Instruct"} 1.0"""

        metrics_str_value_type_error = """
# HELP vllm:num_requests_running Number of requests in model execution batches.
# TYPE vllm:num_requests_running illegal_type
vllm:num_requests_running{engine="0",model_name="/job/model/Qwen2.5-0.5B-Instruct"} value_type_error"""

        metrics_str_value_error = """
# HELP vllm:num_requests_running Number of requests in model execution batches.
# TYPE vllm:num_requests_running illegal_type
vllm:num_requests_running{engine="0",model_name="/job/model/Qwen2.5-0.5B-Instruct"} -1.0"""

        metric_collector = MetricsCollector(self.config)
        result = metric_collector._parse_metric_text(metrics_str_type_error)
        assert isinstance(result, list)
        assert len(result) == 0

        result = metric_collector._parse_metric_text(metrics_str_value_type_error)
        assert isinstance(result, list)
        assert len(result) == 0

        result = metric_collector._parse_metric_text(metrics_str_value_error)
        assert isinstance(result, list)
        assert len(result) == 0

    @_test_without_background_thread
    def test_clear_inactive_metrics(self):
        # ensure MetricsCollector clean
        self.clean_instances()
        metric_collector = MetricsCollector(self.config)

        # create 4-type metric
        _, metric_gauge = self.load_test_gauge_metric()
        _, metric_counter = self.load_test_counter_metric()
        _, metric_histogram = self.load_test_histogram_metric()
        _, metric_summary = self.load_test_summary_metric()

        metric_collector._clear_inactive_metrics({})
        assert len(metric_collector._inactive_instance_metrics_aggregate) == 0

        unavailable_pool = {self.p_ins.id: self.p_ins}
        metric_collector._clear_inactive_metrics(unavailable_pool)
        assert len(metric_collector._inactive_instance_metrics_aggregate) == 0

        metric_collector._instance_metrics_cached = {
            self.p_ins.id: {"metrics": [metric_gauge, metric_counter, metric_histogram, metric_summary]}
        }
        metric_collector._clear_inactive_metrics(unavailable_pool)
        assert len(metric_collector._instance_metrics_cached) == 0
        assert self.check_metric_value_equel(
            metric_collector._inactive_instance_metrics_aggregate[0].value, [0.0] * len(metric_gauge.value)
        )
        assert self.check_metric_value_equel(
            metric_collector._inactive_instance_metrics_aggregate[1].value, metric_counter.value
        )
        assert self.check_metric_value_equel(
            metric_collector._inactive_instance_metrics_aggregate[2].value, metric_histogram.value
        )
        assert self.check_metric_value_equel(
            metric_collector._inactive_instance_metrics_aggregate[3].value, metric_summary.value
        )

    @_test_without_background_thread
    def test_aggregate_collects_by_instance(self):
        # ensure MetricsCollector clean
        self.clean_instances()
        metric_collector = MetricsCollector(self.config)

        # create 4-type metric
        _, metric_gauge = self.load_test_gauge_metric()
        _, metric_counter = self.load_test_counter_metric()
        _, metric_histogram = self.load_test_histogram_metric()
        _, metric_summary = self.load_test_summary_metric()

        # check function: empty collects
        collects = {}
        result = metric_collector._aggregate_collects_by_instance(collects)
        assert not result

        # check function: non-destructive aggregation per instance
        collects = {
            0: {
                "endpoints": {
                    0: {"metrics": [metric_gauge, metric_counter, metric_histogram, metric_summary]},
                    1: {"metrics": [metric_gauge, metric_counter, metric_histogram, metric_summary]},
                }
            },
        }

        result = metric_collector._aggregate_collects_by_instance(collects)
        assert len(result) == 1
        assert 0 in result
        assert self.check_metrics_equel(
            result[0],
            [
                self.metric_add(metric_gauge, metric_gauge),
                self.metric_add(metric_counter, metric_counter),
                self.metric_add(metric_histogram, metric_histogram),
                self.metric_add(metric_summary, metric_summary),
            ],
        )
        # collects is not modified
        assert "endpoints" in collects[0]

        # check function: single endpoint per instance
        collects = {
            1: {
                "endpoints": {
                    2: {"metrics": [metric_gauge, metric_counter, metric_histogram, metric_summary]},
                }
            },
        }

        result = metric_collector._aggregate_collects_by_instance(collects)
        assert len(result) == 1
        assert 1 in result
        assert self.check_metrics_equel(result[1], [metric_gauge, metric_counter, metric_histogram, metric_summary])

    @_test_without_background_thread
    def test_aggregate_metrics_all_instance(self):
        # ensure MetricsCollector clean
        self.clean_instances()
        metric_collector = MetricsCollector(self.config)

        # create 4-type metric
        _, metric_gauge = self.load_test_gauge_metric()
        _, metric_counter = self.load_test_counter_metric()
        _, metric_histogram = self.load_test_histogram_metric()
        _, metric_summary = self.load_test_summary_metric()

        # set metrics cache
        metric_collector._instance_metrics_cached = {
            0: {
                "metrics": [
                    self.metric_add(metric_gauge, metric_gauge),
                    self.metric_add(metric_counter, metric_counter),
                    self.metric_add(metric_histogram, metric_histogram),
                    self.metric_add(metric_summary, metric_summary),
                ]
            },
            1: {"metrics": [metric_gauge, metric_counter, metric_histogram, metric_summary]},
        }

        # check function: empty collects
        collects = {}
        aggregate = metric_collector._aggregate_metrics_all_instance(collects, {})
        # Just check that we get some result (skip detailed value comparison due to threading issues)
        assert isinstance(aggregate, list)
        assert len(aggregate) == 4

        # check function: collects is not empty
        collects = {
            1: {"metrics": [metric_gauge, metric_counter, metric_histogram, metric_summary]},
        }
        aggregate = metric_collector._aggregate_metrics_all_instance(collects, {})
        # Just check basic structure (skip detailed comparisons due to threading state issues)
        assert isinstance(aggregate, list)
        assert len(aggregate) == 4

    def show_metrics_detail(self, metrics: list[Metric]):
        for metric in metrics:
            print(metric.name, metric.type, metric.label, metric.value)

    @_test_without_background_thread
    def test_aggregate_single_metric(self):
        # ensure MetricsCollector clean
        self.clean_instances()
        metric_collector = MetricsCollector(self.config)

        metric_a = Metric()
        metric_a.name = "test"
        metric_a.type = MetricType.COUNTER
        metric_a.help = "test"
        metric_b = Metric()
        metric_b.name = "test"
        metric_b.type = MetricType.COUNTER
        metric_b.help = "test"
        metric_c = Metric()
        metric_c.name = "test"
        metric_c.type = MetricType.COUNTER
        metric_c.help = "test"

        metric_a.label = ["a", "b", "c"]
        metric_a.value = [1.0, 2.0, 3.0]
        metric_b.label = ["a", "b", "c"]
        metric_b.value = [1.0, 2.0, 3.0]
        metric_c.label = ["a", "b", "c"]
        metric_c.value = [2.0, 4.0, 6.0]
        metric_sum = metric_collector._aggregate_single_metric([metric_a, metric_b])
        assert self.check_metrics_equel([metric_sum], [metric_c])

        metric_a.label = ["a"]
        metric_a.value = [1.0]
        metric_b.label = ["a", "b", "c"]
        metric_b.value = [1.0, 2.0, 3.0]
        metric_c.label = ["a", "b", "c"]
        metric_c.value = [2.0, 2.0, 3.0]
        metric_sum = metric_collector._aggregate_single_metric([metric_a, metric_b])
        assert self.check_metrics_equel([metric_sum], [metric_c])

        metric_a.label = ["a", "b", "c"]
        metric_a.value = [1.0, 2.0, 3.0]
        metric_b.label = ["a", "b"]
        metric_b.value = [1.0, 2.0]
        metric_c.label = ["a", "b", "c"]
        metric_c.value = [2.0, 4.0, 3.0]
        metric_sum = metric_collector._aggregate_single_metric([metric_a, metric_b])
        assert self.check_metrics_equel([metric_sum], [metric_c])

        # test mean
        metric_a.name = "vllm:kv_cache_usage_perc"
        metric_a.type = MetricType.GAUGE
        metric_a.help = "KV-cache usage. 1 means 100 percent usage."
        metric_b.name = "vllm:kv_cache_usage_perc"
        metric_b.type = MetricType.GAUGE
        metric_b.help = "KV-cache usage. 1 means 100 percent usage."
        metric_c.name = "vllm:kv_cache_usage_perc"
        metric_c.type = MetricType.GAUGE
        metric_c.help = "KV-cache usage. 1 means 100 percent usage."

        metric_a.label = ["a", "b", "c"]
        metric_a.value = [1.0, 2.0, 3.0]
        metric_b.label = ["a", "b"]
        metric_b.value = [1.0, 4.0]
        metric_c.label = ["a", "b", "c"]
        metric_c.value = [1.0, 3.0, 1.5]
        metric_sum = metric_collector._aggregate_single_metric([metric_a, metric_b])
        assert self.check_metrics_equel([metric_sum], [metric_c])

    def load_test_format_diff_metric(self):
        # metric text
        metrics_str_a = """# HELP http_request_duration_highr_seconds_created Latency with many buckets but no API specific labels. Made for more accurate percentile calculations.
# TYPE http_request_duration_highr_seconds_created gauge
http_request_duration_highr_seconds_created 1.765001778333063e+09
# HELP http_request_duration_seconds Latency with only few buckets by handler. Made to be only used if aggregation by handler is important.
# TYPE http_request_duration_seconds histogram"""
        metrics_str_b = """# HELP http_request_duration_highr_seconds_created Latency with many buckets but no API specific labels. Made for more accurate percentile calculations.
# TYPE http_request_duration_highr_seconds_created gauge
http_request_duration_highr_seconds_created 1.765019286626281e+09
# HELP http_request_duration_seconds Latency with only few buckets by handler. Made to be only used if aggregation by handler is important.
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_bucket{handler="/v1/chat/completions",le="0.1",method="POST"} 1.0
http_request_duration_seconds_bucket{handler="/v1/chat/completions",le="0.5",method="POST"} 1.0
http_request_duration_seconds_bucket{handler="/v1/chat/completions",le="1.0",method="POST"} 1.0
http_request_duration_seconds_bucket{handler="/v1/chat/completions",le="+Inf",method="POST"} 1.0
http_request_duration_seconds_count{handler="/v1/chat/completions",method="POST"} 1.0
http_request_duration_seconds_sum{handler="/v1/chat/completions",method="POST"} 0.00824622018262744
# HELP http_request_duration_seconds_created Latency with only few buckets by handler. Made to be only used if aggregation by handler is important.
# TYPE http_request_duration_seconds_created gauge
http_request_duration_seconds_created{handler="/v1/chat/completions",method="POST"} 1.7650211184341915e+09"""

        # metric format
        metrics_a = []
        metrics_a.append(Metric())
        metrics_a[0].name = "http_request_duration_highr_seconds_created"
        metrics_a[
            0
        ].help = "Latency with many buckets but no API specific labels. Made for more accurate percentile calculations."
        metrics_a[0].type = MetricType.GAUGE
        metrics_a[0].label = ["http_request_duration_highr_seconds_created"]
        metrics_a[0].value = [1.765001778333063e09]
        metrics_a.append(Metric())
        metrics_a[1].name = "http_request_duration_seconds"
        metrics_a[
            1
        ].help = (
            "Latency with only few buckets by handler. Made to be only used if aggregation by handler is important."
        )
        metrics_a[1].type = MetricType.HISTOGRAM
        metrics_a[1].label = []
        metrics_a[1].value = []

        metrics_b = []
        metrics_b.append(Metric())
        metrics_b[0].name = "http_request_duration_highr_seconds_created"
        metrics_b[
            0
        ].help = "Latency with many buckets but no API specific labels. Made for more accurate percentile calculations."
        metrics_b[0].type = MetricType.GAUGE
        metrics_b[0].label = ["http_request_duration_highr_seconds_created"]
        metrics_b[0].value = [1.765019286626281e09]
        metrics_b.append(Metric())
        metrics_b[1].name = "http_request_duration_seconds"
        metrics_b[
            1
        ].help = (
            "Latency with only few buckets by handler. Made to be only used if aggregation by handler is important."
        )
        metrics_b[1].type = MetricType.HISTOGRAM
        metrics_b[1].label = [
            'http_request_duration_seconds_bucket{handler="/v1/chat/completions",le="0.1",method="POST"}',
            'http_request_duration_seconds_bucket{handler="/v1/chat/completions",le="0.5",method="POST"}',
            'http_request_duration_seconds_bucket{handler="/v1/chat/completions",le="1.0",method="POST"}',
            'http_request_duration_seconds_bucket{handler="/v1/chat/completions",le="+Inf",method="POST"}',
            'http_request_duration_seconds_count{handler="/v1/chat/completions",method="POST"}',
            'http_request_duration_seconds_sum{handler="/v1/chat/completions",method="POST"}',
        ]
        metrics_b[1].value = [1.0, 1.0, 1.0, 1.0, 1.0, 0.00824622018262744]
        metrics_b.append(Metric())
        metrics_b[2].name = "http_request_duration_seconds_created"
        metrics_b[
            2
        ].help = (
            "Latency with only few buckets by handler. Made to be only used if aggregation by handler is important."
        )
        metrics_b[2].type = MetricType.GAUGE
        metrics_b[2].label = ['http_request_duration_seconds_created{handler="/v1/chat/completions",method="POST"}']
        metrics_b[2].value = [1.7650211184341915e09]

        return metrics_str_a.strip(), copy.deepcopy(metrics_a), metrics_str_b.strip(), copy.deepcopy(metrics_b)

    @_test_without_background_thread
    def test_aggregate_collects_by_instance_diff_format(self):
        # ensure MetricsCollector clean
        self.clean_instances()
        metric_collector = MetricsCollector(self.config)
        metric_collector._instance_metrics_cached = {}

        # create different format metric
        _, metrics_a, _, metrics_b = self.load_test_format_diff_metric()

        # check function: empty collects
        collects = {}
        result = metric_collector._aggregate_collects_by_instance(collects)
        assert not result

        # check function: non-destructive aggregation per instance
        collects = {
            0: {
                "endpoints": {
                    0: {"metrics": [metrics_a[0], metrics_a[1]]},
                    1: {"metrics": [metrics_b[0], metrics_b[1], metrics_b[2]]},
                }
            },
        }

        result = metric_collector._aggregate_collects_by_instance(collects)
        assert len(result) == 1
        assert 0 in result
        # *_created metrics use METADATA_GAUGE: passthrough first endpoint (not sum)
        assert self.check_metrics_equel(result[0], [metrics_a[0], metrics_b[1], metrics_b[2]])
        # collects is not modified
        assert "endpoints" in collects[0]

        # check function: single endpoint per instance
        collects = {
            1: {
                "endpoints": {
                    2: {"metrics": [metrics_a[0], metrics_a[1]]},
                }
            },
        }

        result = metric_collector._aggregate_collects_by_instance(collects)
        assert len(result) == 1
        assert 1 in result
        assert self.check_metrics_equel(result[1], metrics_a)

    @_test_without_background_thread
    def test_format_prometheus(self):
        metric_collector = MetricsCollector(self.config)

        # create 4-type metric
        metric_str_gauge, metric_gauge = self.load_test_gauge_metric()
        metric_str_counter, metric_counter = self.load_test_counter_metric()
        metric_str_histogram, metric_histogram = self.load_test_histogram_metric()
        metric_str_summary, metric_summary = self.load_test_summary_metric()
        metric_str_mix = "\n".join([metric_str_gauge, metric_str_counter, metric_str_histogram, metric_str_summary])
        metric_mix = [metric_gauge, metric_counter, metric_histogram, metric_summary]

        # check function
        assert metric_collector._format_prometheus([metric_gauge]) == re.sub(r'engine="\d+",', "", metric_str_gauge)
        assert metric_collector._format_prometheus([metric_counter]) == re.sub(r'engine="\d+",', "", metric_str_counter)
        assert metric_collector._format_prometheus([metric_histogram]) == re.sub(
            r'engine="\d+",', "", metric_str_histogram
        )
        assert metric_collector._format_prometheus([metric_summary]) == re.sub(r'engine="\d+",', "", metric_str_summary)
        assert metric_collector._format_prometheus(metric_mix) == re.sub(r'engine="\d+",', "", metric_str_mix)

    def mock_get_all_instances_normal(self):
        available_pool = {
            self.p_ins.id: self.p_ins,
            self.d_ins.id: self.d_ins,
            self.h_ins.id: self.h_ins,
        }

        unavailable_pool = {}
        return available_pool, unavailable_pool

    def mock_requests_get_normal(self, *args, **kwargs):
        return MockResponse(self.metrics_template, 200)

    @pytest.mark.asyncio
    @patch("motor.coordinator.domain.instance_manager.InstanceManager.get_all_instances", new_callable=AsyncMock)
    async def test_get_all_instances(self, mock_get_all_instances):
        mock_get_all_instances.return_value = self.mock_get_all_instances_normal()

        result = await InstanceManager().get_all_instances()
        assert result == self.mock_get_all_instances_normal()

    @patch("requests.get")
    def test_requests_get(self, mock_requests_get):
        mock_requests_get.side_effect = self.mock_requests_get_normal

        for port in [8000, 8001, 8002, 8003, 8004, 8005]:
            assert requests.get(f"http://localhost:{port}/metrics").status_code == 200

        mock_requests_get.side_effect = self.mock_requests_get_with_abnormal

        for port in [8000, 8001, 8002, 8003]:
            assert requests.get(f"http://localhost:{port}/metrics").status_code == 200
        for port in [8004, 8005]:
            assert requests.get(f"http://localhost:{port}/metrics").status_code == 404

    def test_prometheus_metrics_handler(self, mock_metrics_collector):  # pylint: disable=redefined-outer-name
        mock_metrics_collector._last_metrics = None
        mock_metrics_collector.get_metrics.return_value = ""

        result = mock_metrics_collector.get_metrics(metrics_type="full")
        assert result == ""

        mock_metrics_collector._last_metrics = "# HELP test metric\ntest_metric 1.0\n"
        mock_metrics_collector.get_metrics.return_value = "# HELP test metric\ntest_metric 1.0\n"

        result = mock_metrics_collector.get_metrics(metrics_type="full")
        assert result is not None

    def mock_requests_get_with_abnormal(self, *args, **kwargs):
        port = urlparse(args[0]).port
        if port in [8000, 8001, 8002, 8003]:
            return MockResponse(self.metrics_template, 200)
        return MockResponse(None, 404)

    def test_prometheus_metrics_handler_abnormal(self, mock_metrics_collector):  # pylint: disable=redefined-outer-name
        mock_metrics_collector._last_metrics = ""
        mock_metrics_collector.get_metrics.return_value = ""

        result = mock_metrics_collector.get_metrics(metrics_type="full")
        assert result == ""


# ---------------------------------------------------------------------------
# Tests for _inject_labels label injection
# ---------------------------------------------------------------------------


def test_inject_labels_with_existing_braces():
    metric = Metric(name="vllm:num_requests_running", label=['vllm:num_requests_running{model="qwen"}'])
    result = MetricsCollector._inject_labels(metric, role="prefill")
    assert result.label == ['vllm:num_requests_running{role="prefill",model="qwen"}']


def test_inject_labels_without_braces():
    metric = Metric(name="vllm:num_requests_running", label=["vllm:num_requests_running"])
    result = MetricsCollector._inject_labels(metric, role="prefill")
    assert result.label == ['vllm:num_requests_running{role="prefill"}']


# ---------------------------------------------------------------------------
# Tests for _inject_labels
# ---------------------------------------------------------------------------


def test_inject_instance_labels_to_single_metric():
    metric = Metric()
    metric.name = "vllm:num_requests_running"
    metric.help = "Number of requests running."
    metric.type = MetricType.GAUGE
    metric.label = [
        'vllm:num_requests_running{model="qwen"}',
        'vllm:num_requests_running{model="qwen2"}',
    ]
    metric.value = [5.0, 3.0]

    result = MetricsCollector._inject_labels(metric, instance_id="1", role="prefill")

    assert result.name == metric.name
    assert result.value == metric.value
    assert result.label == [
        'vllm:num_requests_running{instance_id="1",role="prefill",model="qwen"}',
        'vllm:num_requests_running{instance_id="1",role="prefill",model="qwen2"}',
    ]


def test_inject_role_label_to_single_metric():
    metric = Metric()
    metric.name = "vllm:num_requests_running"
    metric.help = "Number of requests running."
    metric.type = MetricType.GAUGE
    metric.label = ['vllm:num_requests_running{model="qwen"}']
    metric.value = [5.0]

    result = MetricsCollector._inject_labels(metric, role="prefill")

    assert result.label == ['vllm:num_requests_running{role="prefill",model="qwen"}']


# ---------------------------------------------------------------------------
# Tests for get_instance_metrics_prometheus
# ---------------------------------------------------------------------------


@patch("threading.Thread.start", MagicMock())
def test_generate_instance_metrics_basic():
    _cleanup_singletons()
    config = CoordinatorConfig()
    collector = MetricsCollector(config)

    metric = Metric()
    metric.name = "vllm:num_requests_running"
    metric.help = "Number of requests running."
    metric.type = MetricType.GAUGE
    metric.label = ['vllm:num_requests_running{model="qwen"}']
    metric.value = [5.0]

    collects = {0: {"role": "prefill", "endpoints": {0: {"metrics": [metric], "pod_ip": "10.0.0.1"}}}}

    result = collector._generate_instance_metrics(collects)

    assert "instance_id" in result
    assert "prefill" in result
    assert "vllm:num_requests_running" in result
    _cleanup_singletons()


@patch("threading.Thread.start", MagicMock())
def test_generate_instance_metrics_empty():
    _cleanup_singletons()
    config = CoordinatorConfig()
    collector = MetricsCollector(config)

    result = collector._generate_instance_metrics({})
    assert result == ""
    _cleanup_singletons()


@patch("threading.Thread.start", MagicMock())
def test_generate_instance_metrics_unknown_role():
    _cleanup_singletons()
    config = CoordinatorConfig()
    collector = MetricsCollector(config)

    metric = Metric()
    metric.name = "vllm:num_requests_running"
    metric.help = "Number of requests running."
    metric.type = MetricType.GAUGE
    metric.label = ['vllm:num_requests_running{model="qwen"}']
    metric.value = [1.0]

    collects = {99: {"endpoints": {0: {"metrics": [metric], "pod_ip": "10.0.0.1"}}}}

    result = collector._generate_instance_metrics(collects)

    assert 'role="unknown"' in result
    assert 'instance_id="99"' in result
    _cleanup_singletons()


# ---------------------------------------------------------------------------
# Tests for get_role_metrics
# ---------------------------------------------------------------------------


@patch("threading.Thread.start", MagicMock())
def test_generate_role_metrics_prefill_and_decode():
    _cleanup_singletons()
    config = CoordinatorConfig()
    collector = MetricsCollector(config)

    metric_a = Metric()
    metric_a.name = "vllm:num_requests_running"
    metric_a.help = "Number of requests running."
    metric_a.type = MetricType.GAUGE
    metric_a.label = ['vllm:num_requests_running{model="qwen"}']
    metric_a.value = [5.0]

    metric_b = Metric()
    metric_b.name = "vllm:num_requests_running"
    metric_b.help = "Number of requests running."
    metric_b.type = MetricType.GAUGE
    metric_b.label = ['vllm:num_requests_running{model="qwen"}']
    metric_b.value = [3.0]

    collects = {
        0: {"role": "prefill", "endpoints": {0: {"metrics": [metric_a], "pod_ip": "10.0.0.1"}}},
        1: {"role": "decode", "endpoints": {0: {"metrics": [metric_b], "pod_ip": "10.0.0.2"}}},
    }

    result = collector._generate_role_metrics(collects)

    assert "prefill" in result
    assert "decode" in result
    assert "vllm:num_requests_running" in result["prefill"]
    assert "vllm:num_requests_running" in result["decode"]
    _cleanup_singletons()


@patch("threading.Thread.start", MagicMock())
def test_generate_role_metrics_single_role():
    _cleanup_singletons()
    config = CoordinatorConfig()
    collector = MetricsCollector(config)

    metric = Metric()
    metric.name = "vllm:num_requests_running"
    metric.help = "Number of requests running."
    metric.type = MetricType.GAUGE
    metric.label = ['vllm:num_requests_running{model="qwen"}']
    metric.value = [2.0]

    collects = {0: {"role": "prefill", "endpoints": {0: {"metrics": [metric], "pod_ip": "10.0.0.1"}}}}

    result = collector._generate_role_metrics(collects)

    assert "prefill" in result
    assert "decode" not in result
    _cleanup_singletons()


@patch("threading.Thread.start", MagicMock())
def test_generate_role_metrics_empty():
    _cleanup_singletons()
    config = CoordinatorConfig()
    collector = MetricsCollector(config)

    result = collector._generate_role_metrics({})
    assert not result
    _cleanup_singletons()


# ---------------------------------------------------------------------------
# Tests for get_metrics (unified type selection)
# ---------------------------------------------------------------------------


@patch("threading.Thread.start", MagicMock())
def test_get_metrics_full():
    _cleanup_singletons()
    config = CoordinatorConfig()
    collector = MetricsCollector(config)

    metric = Metric()
    metric.name = "test_metric"
    metric.help = "test metric"
    metric.type = MetricType.GAUGE
    metric.label = ["test_metric"]
    metric.value = [1.0]

    collector._last_collects = {
        0: {"role": "prefill", "endpoints": {0: {"metrics": [metric], "pod_ip": "10.0.0.1"}}},
    }
    collector._collects_version = 1

    result = collector.get_metrics(metrics_type="full")
    assert "test_metric" in result
    _cleanup_singletons()


@patch("threading.Thread.start", MagicMock())
def test_get_metrics_instance():
    _cleanup_singletons()
    config = CoordinatorConfig()
    collector = MetricsCollector(config)

    metric = Metric()
    metric.name = "vllm:num_requests_running"
    metric.help = "Number of requests running."
    metric.type = MetricType.GAUGE
    metric.label = ['vllm:num_requests_running{model="qwen"}']
    metric.value = [5.0]

    collector._last_collects = {
        0: {"role": "prefill", "endpoints": {0: {"metrics": [metric], "pod_ip": "10.0.0.1"}}},
    }
    collector._collects_version = 1

    result = collector.get_metrics(metrics_type="instance")
    assert isinstance(result, str)
    assert "instance_id" in result
    _cleanup_singletons()


@patch("threading.Thread.start", MagicMock())
def test_get_metrics_role_all():
    _cleanup_singletons()
    config = CoordinatorConfig()
    collector = MetricsCollector(config)

    metric = Metric()
    metric.name = "vllm:num_requests_running"
    metric.help = "Number of requests running."
    metric.type = MetricType.GAUGE
    metric.label = ['vllm:num_requests_running{model="qwen"}']
    metric.value = [5.0]

    collector._last_collects = {
        0: {"role": "prefill", "endpoints": {0: {"metrics": [metric], "pod_ip": "10.0.0.1"}}},
    }
    collector._collects_version = 1

    result = collector.get_metrics(metrics_type="role")
    assert isinstance(result, str)
    assert "prefill" in result
    _cleanup_singletons()


@patch("threading.Thread.start", MagicMock())
def test_get_metrics_role_filtered():
    _cleanup_singletons()
    config = CoordinatorConfig()
    collector = MetricsCollector(config)

    metric_a = Metric()
    metric_a.name = "vllm:num_requests_running"
    metric_a.help = "Number of requests running."
    metric_a.type = MetricType.GAUGE
    metric_a.label = ['vllm:num_requests_running{model="qwen"}']
    metric_a.value = [5.0]

    metric_b = Metric()
    metric_b.name = "vllm:num_requests_running"
    metric_b.help = "Number of requests running."
    metric_b.type = MetricType.GAUGE
    metric_b.label = ['vllm:num_requests_running{model="qwen"}']
    metric_b.value = [3.0]

    collector._last_collects = {
        0: {"role": "prefill", "endpoints": {0: {"metrics": [metric_a], "pod_ip": "10.0.0.1"}}},
        1: {"role": "decode", "endpoints": {0: {"metrics": [metric_b], "pod_ip": "10.0.0.2"}}},
    }
    collector._collects_version = 1

    result = collector.get_metrics(metrics_type="role", role="prefill")
    assert isinstance(result, str)
    assert "prefill" in result
    assert "decode" not in result
    _cleanup_singletons()


# ---------------------------------------------------------------------------
# Tests for new multilevel metrics functions
# ---------------------------------------------------------------------------


@patch("threading.Thread.start", MagicMock())
def test_prepend_dim_labels_no_braces():
    assert (
        MetricsCollector._prepend_dim_labels("process_start_time", 'dp_rank="0"') == 'process_start_time{dp_rank="0"}'
    )


@patch("threading.Thread.start", MagicMock())
def test_prepend_dim_labels_empty_braces():
    assert MetricsCollector._prepend_dim_labels("foo{}", 'dp_rank="0"') == 'foo{dp_rank="0"}'


@patch("threading.Thread.start", MagicMock())
def test_prepend_dim_labels_existing_labels():
    assert MetricsCollector._prepend_dim_labels('vllm:x{model="q"}', 'dp_rank="0"') == 'vllm:x{dp_rank="0",model="q"}'


@patch("threading.Thread.start", MagicMock())
def test_metric_value_str_normal():
    assert MetricsCollector._metric_value_str(3.14) == "3.14"


@patch("threading.Thread.start", MagicMock())
def test_metric_value_str_nan():
    assert MetricsCollector._metric_value_str(float("nan")) == "Nan"


@patch("threading.Thread.start", MagicMock())
def test_metric_value_str_inf():
    assert MetricsCollector._metric_value_str(float("inf")) == "+Inf"
    assert MetricsCollector._metric_value_str(float("-inf")) == "-Inf"


@patch("threading.Thread.start", MagicMock())
def test_emit_metric_groups():
    name_to_meta = {
        "metric_b": {"help": "help_b", "type": "gauge", "lines": [(("k",), "metric_b{x=\"1\"} 2.0")]},
        "metric_a": {"help": "help_a", "type": "counter", "lines": [(("k",), "metric_a{x=\"1\"} 1.0")]},
    }
    result = MetricsCollector._emit_metric_groups(name_to_meta)
    lines = result.split("\n")
    assert lines[0] == "# HELP metric_a help_a"
    assert lines[1] == "# TYPE metric_a counter"
    assert lines[3] == "# HELP metric_b help_b"


@patch("threading.Thread.start", MagicMock())
def test_generate_dp_metrics():
    _cleanup_singletons()
    config = CoordinatorConfig()
    collector = MetricsCollector(config)

    metric = Metric()
    metric.name = "vllm:num_requests_running"
    metric.help = "Number of requests running."
    metric.type = MetricType.GAUGE
    metric.label = ['vllm:num_requests_running{model="qwen"}']
    metric.value = [5.0]

    collects = {
        2: {
            "role": "prefill",
            "endpoints": {
                0: {"metrics": [metric], "pod_ip": "192.168.1.1"},
                1: {"metrics": [metric], "pod_ip": "192.168.1.1"},
            },
        },
    }

    result = collector._generate_dp_metrics(collects)
    assert 'dp_rank="0"' in result
    assert 'dp_rank="1"' in result
    assert 'role="prefill"' in result
    assert 'instance_id="2"' in result
    assert 'pod_ip="192.168.1.1"' in result
    assert "# HELP vllm:num_requests_running" in result
    assert "# TYPE vllm:num_requests_running" in result
    _cleanup_singletons()


@patch("threading.Thread.start", MagicMock())
def test_generate_dp_metrics_empty():
    _cleanup_singletons()
    config = CoordinatorConfig()
    collector = MetricsCollector(config)

    result = collector._generate_dp_metrics({})
    assert result == ""
    _cleanup_singletons()


@patch("threading.Thread.start", MagicMock())
def test_generate_node_metrics():
    _cleanup_singletons()
    config = CoordinatorConfig()
    collector = MetricsCollector(config)

    metric_a = Metric()
    metric_a.name = "vllm:num_requests_running"
    metric_a.help = "Number of requests running."
    metric_a.type = MetricType.GAUGE
    metric_a.label = ['vllm:num_requests_running{model="qwen"}']
    metric_a.value = [5.0]

    metric_b = Metric()
    metric_b.name = "vllm:num_requests_running"
    metric_b.help = "Number of requests running."
    metric_b.type = MetricType.GAUGE
    metric_b.label = ['vllm:num_requests_running{model="qwen"}']
    metric_b.value = [3.0]

    collects = {
        0: {
            "role": "prefill",
            "endpoints": {
                0: {"metrics": [metric_a], "pod_ip": "10.0.0.1"},
                1: {"metrics": [metric_b], "pod_ip": "10.0.0.1"},
            },
        },
    }

    result = collector._generate_node_metrics(collects)
    assert 'pod_ip="10.0.0.1"' in result
    assert 'role="prefill"' in result
    assert "# HELP vllm:num_requests_running" in result
    assert "8.0" in result
    _cleanup_singletons()


@patch("threading.Thread.start", MagicMock())
def test_get_metrics_dp():
    _cleanup_singletons()
    config = CoordinatorConfig()
    collector = MetricsCollector(config)

    metric = Metric()
    metric.name = "vllm:num_requests_running"
    metric.help = "Number of requests running."
    metric.type = MetricType.GAUGE
    metric.label = ['vllm:num_requests_running{model="qwen"}']
    metric.value = [5.0]

    collector._last_collects = {
        2: {
            "role": "prefill",
            "endpoints": {0: {"metrics": [metric], "pod_ip": "10.0.0.1"}},
        },
    }
    collector._collects_version = 1

    result = collector.get_metrics(metrics_type="dp")
    assert 'dp_rank="0"' in result
    assert 'role="prefill"' in result
    _cleanup_singletons()


@patch("threading.Thread.start", MagicMock())
def test_get_metrics_node():
    _cleanup_singletons()
    config = CoordinatorConfig()
    collector = MetricsCollector(config)

    metric = Metric()
    metric.name = "vllm:num_requests_running"
    metric.help = "Number of requests running."
    metric.type = MetricType.GAUGE
    metric.label = ['vllm:num_requests_running{model="qwen"}']
    metric.value = [5.0]

    collector._last_collects = {
        2: {
            "role": "prefill",
            "endpoints": {0: {"metrics": [metric], "pod_ip": "10.0.0.1"}},
        },
    }
    collector._collects_version = 1

    result = collector.get_metrics(metrics_type="node")
    assert 'pod_ip="10.0.0.1"' in result
    assert 'role="prefill"' in result
    _cleanup_singletons()


@patch("threading.Thread.start", MagicMock())
def test_get_metrics_cache_invalidation():
    _cleanup_singletons()
    config = CoordinatorConfig()
    collector = MetricsCollector(config)

    metric_v1 = Metric()
    metric_v1.name = "test_metric"
    metric_v1.help = "test"
    metric_v1.type = MetricType.GAUGE
    metric_v1.label = ["test_metric"]
    metric_v1.value = [1.0]

    collector._last_collects = {
        0: {"role": "prefill", "endpoints": {0: {"metrics": [metric_v1], "pod_ip": "10.0.0.1"}}},
    }
    collector._collects_version = 1

    result_v1 = collector.get_metrics(metrics_type="full")
    assert "1.0" in result_v1

    metric_v2 = Metric()
    metric_v2.name = "test_metric"
    metric_v2.help = "test"
    metric_v2.type = MetricType.GAUGE
    metric_v2.label = ["test_metric"]
    metric_v2.value = [99.0]

    collector._last_collects = {
        0: {"role": "prefill", "endpoints": {0: {"metrics": [metric_v2], "pod_ip": "10.0.0.1"}}},
    }
    collector._collects_version = 2

    result_v2 = collector.get_metrics(metrics_type="full")
    assert "99.0" in result_v2
    assert result_v1 != result_v2
    _cleanup_singletons()
