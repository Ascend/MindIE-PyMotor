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

import asyncio
import re
import time
import threading
from enum import Enum
from collections import Counter
from typing import Any, Callable

from motor.common.resources.instance import Instance
from motor.common.resources import PDRole
from motor.common.utils.logger import get_logger
from motor.common.utils.singleton import ThreadSafeSingleton
from motor.config.coordinator import CoordinatorConfig
from motor.coordinator.api_client.engine_server_api_client import EngineServerApiClient

logger = get_logger(__name__)


class MetricType(Enum):
    GAUGE = "gauge"
    COUNTER = "counter"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"
    NONE = ""

    def __str__(self):
        return self.value

    @classmethod
    def from_string(cls, type_string):
        return cls[type_string.upper()]


class SingleMetric():
    def __init__(self, single_metric=None):
        if single_metric:
            self.name: str = single_metric.name
            self.help: str = single_metric.help
            self.type: MetricType = single_metric.type
            self.label: list[str] = single_metric.label
            self.value: list[float] = [0.0] * len(single_metric.label)
        else:
            self.name: str = ""
            self.help: str = ""
            self.type: MetricType = MetricType.NONE
            self.label: list[str] = []
            self.value: list[float] = []


class MetricsCollector(ThreadSafeSingleton):
    METRICS_KEY = "metrics"

    def __init__(self, config: CoordinatorConfig | None = None):
        if hasattr(self, '_initialized'):
            return

        self._config_lock = threading.RLock()
        if config is None:
            config = CoordinatorConfig()
        self._prometheus_metrics_config = config.prometheus_metrics_config
        self._deploy_config = config.deploy_config

        # Initial metrics state
        self._inactive_instance_metrics_aggregate: list[SingleMetric] = []
        self._instance_metrics_cached: dict[int, dict[str, list[SingleMetric]]] = {}
        self._last_metrics: str = ""
        self._last_instance_metrics: dict[int, list[SingleMetric]] = {}

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._metrics_update_thread = None
        # Event loop for async get_all_instances (set from lifespan)
        self._loop = None
        # When set, use this to get scheduler (same view as scheduling); must be set in lifespan
        self._scheduler_provider: Callable[[], Any] | None = None

        self._initialized = True
        logger.info("MetricsCollector initialized.")

    @staticmethod
    def _get_value_str(value: float) -> str:
        """ Transform float to str.  """

        if value == float("nan"):
            return "Nan"
        elif value == float("inf"):
            return "+Inf"
        elif value == float("-inf"):
            return "-Inf"
        return str(value)

    def set_event_loop(self, loop):
        """Set the event loop for async calls from the metrics thread (call from lifespan)."""
        self._loop = loop

    def set_scheduler_provider(self, get_scheduler: Callable[[], Any]) -> None:
        """Use same instance view as scheduling: get_scheduler().get_all_instances() (call from lifespan)."""
        self._scheduler_provider = get_scheduler

    def start(self) -> None:
        """
        Start update metrics thread.

        :returns:
        """
        if self._stop_event.is_set():
            self._stop_event.clear()
        self._metrics_update_thread = threading.Thread(
            target=self._update_metrics_thread,
            daemon=True,
            name="MetricsUpdate"
        )
        self._metrics_update_thread.start()
        logger.info("MetricsCollector started.")

    def stop(self) -> None:
        """
        Stop update metrics thread.

        :returns:
        """

        self._stop_event.set()
        if self._metrics_update_thread and self._metrics_update_thread.is_alive():
            self._metrics_update_thread.join()
        logger.info("MetricsCollector stopped.")

    def update_config(self, config: CoordinatorConfig) -> None:
        """Update configuration for the metrics collector"""
        with self._config_lock:
            self._prometheus_metrics_config = config.prometheus_metrics_config
            self._deploy_config = config.deploy_config
        logger.info("MetricsCollector configuration updated")

    def prometheus_instance_metrics_handler(self):
        """
        Callback of http /metrics.

        :returns:
        """

        with self._lock:
            instance_metrics = self._last_instance_metrics
        return instance_metrics

    def prometheus_metrics_handler(self):
        """
        Callback of http /instance/metrics.

        :returns:
        """

        with self._lock:
            metrics = self._last_metrics
        return metrics

    def _update_metrics_thread(self) -> None:
        """
        Start update metrics thread.

        :returns:
        """

        while not self._stop_event.is_set():
            metrics, instance_metrics = self._get_and_aggregate_metrics()
            with self._lock:
                if metrics and instance_metrics:
                    self._last_metrics = metrics
                    self._last_instance_metrics = instance_metrics
            with self._config_lock:
                reuse_time = self._prometheus_metrics_config.reuse_time
            time.sleep(reuse_time)

    def _get_server_metrics_single(self, ip: str, port: str) -> str:
        """
        Get metrics of single engine server.

        :param ip: engine server ip
        :param port: engine server port
        :returns: engine server metrics. If request failed, return "".
        """
        return EngineServerApiClient.query_metrics(f"{ip}:{port}")

    def _get_server_metrics_endpoints(self, ins_info: Instance) -> dict[str, dict[int, str]]:
        """
        Get all endpoints metrics text in single instance.

        :param ins_info:
        :returns: if any failed, return {}
            for example:
            {
                "endpoints": {
                    endpoint_id0: {
                        "metrics_str": "xxx"
                    },
                    endpoint_id1: ...
                }
            }
        """

        collect = {
            "endpoints": {}
        }

        for ens_info in ins_info.endpoints.values():
            for en_info in ens_info.values():
                metrics_str = self._get_server_metrics_single(en_info.ip, en_info.mgmt_port)
                if not metrics_str:
                    return {}
                collect["endpoints"][en_info.id] = {
                    "metrics_str": metrics_str
                }

        return collect

    def _get_server_metrics(
        self,
        available_instances: dict[int, Instance]
    ) -> dict[int, dict[str, dict[int, str]]]:
        """
        Get instances/endpoints info and get all endpoints metrics text.

        :param available_instances: alive instances
        :returns:
            for example:
            {
                instance_id0: {
                    "endpoints": {
                        endpoint_id0: {
                            "metrics_str": "xxx"
                        },
                        endpoint_id1: ...
                    }
                },
                instance_id1: ...
            }
        """

        collects = {}
        for ins_info in available_instances.values():
            collect = self._get_server_metrics_endpoints(ins_info)
            if collect:
                collects[ins_info.id] = collect

        return collects

    def _parse_metric_help(self, single_metric: SingleMetric, line: str) -> bool:
        """
        Parse help line.

        :param single_metric:
        :param line: format: "# HELP <name> <help description>"
        :returns: if success, update single_metric and return True, else return False
        """

        parts = line.split()
        sharp_index = 0
        help_mark_index = 1
        name_index = 2
        help_desc_index = 3
        if len(parts) >= help_desc_index + 1 and parts[sharp_index] == "#" and parts[help_mark_index] == "HELP":
            single_metric.name = parts[name_index]
            single_metric.help = " ".join(parts[help_desc_index:])
            return True
        else:
            logger.error("[Metrics] Parse metric help failed.")
            return False

    def _parse_metric_type(self, single_metric: SingleMetric, line: str) -> bool:
        """
        Parse type line.

        :param single_metric:
        :param line: format: "# TYPE <name> <gauge|counter|histogram|summary>"
        :returns: if success, update single_metric and return True, else return False
        """

        parts = line.split()
        sharp_index = 0
        type_mark_index = 1
        type_index = 3
        if len(parts) == type_index + 1 and parts[sharp_index] == "#" and parts[type_mark_index] == "TYPE":
            try:
                single_metric.type = MetricType.from_string(parts[3])
            except KeyError:
                logger.error("[Metrics] Illegal metric type: %s", parts[3])
                return False
            return True
        else:
            logger.error("[Metrics] Parse metric type failed.")
            return False

    def _parse_metric_body_block(self, single_metric: SingleMetric, line: str) -> bool:
        """
        Parse label and value line.

        :param single_metric:
        :param line: format: "<label> <value>"
        :returns: if success, update single_metric and return True, else return False
        """

        parts = line.split()
        label_index = 0
        value_index = 1

        if len(parts) != value_index + 1:
            logger.error("[Metrics] Parse metric body failed.")
            return False

        # Remove sub label engine if exist
        label = re.sub(r'engine="\d+",', '', parts[label_index])
        single_metric.label.append(label)
        try:
            value = float(parts[value_index])
            if value < 0:
                logger.error("[Metrics] Illegal metric value: %s", parts[value_index])
                return False
            single_metric.value.append(value)
        except ValueError:
            logger.error("[Metrics] Illegal metric value: %s", parts[value_index])
            return False
        return True

    def _parse_metric_text(self, metrics_str) -> list[SingleMetric]:
        """
        Parse metrics from text to format data.

        :param metrics_str:
            metrics_str format:
                # HELP <name> <help description>
                # TYPE <name> <gauge|counter|histogram|summary>
                <label0> <value0>
                <label1> <value1>
                ...
                <labeln> <valuen>
                # HELP ...
                # TYPE ...
                ...
        :returns: If success, return a list of SingleMetric, else return []
            SingleMetric format:
                name = <name>
                help = <help description>
                type = <gauge|counter|histogram|summary>
                label = [label0, label1, ..., labeln]
                value = [value0, value1, ..., valuen]
        """

        metric_array = []
        lines = metrics_str.strip().split("\n")
        i = 0
        while i < len(lines):
            single_metric = SingleMetric()
            if i < len(lines) and not self._parse_metric_help(single_metric, lines[i]):
                return []
            i += 1
            if i < len(lines) and not self._parse_metric_type(single_metric, lines[i]):
                return []
            i += 1
            while i < len(lines) and lines[i][0] != "#":
                if not self._parse_metric_body_block(single_metric, lines[i]):
                    return []
                i += 1
            metric_array.append(single_metric)
        return metric_array

    def _parse_metrics(
        self,
        collects: dict[int, dict[str, dict[int, dict[str, str]]]]
    ) -> bool:
        """
        Parse metrics text to format data for all instances/endpoints.

        :param collects:
            collects before call:
            {
                instance_id0: {
                    "endpoints": {
                        endpoint_id0: {
                            "metrics_str": "xxx"
                        },
                        endpoint_id1: ...
                    }
                },
                instance_id1: ...
            }
            collects after call:
            {
                instance_id0: {
                    "endpoints": {
                        endpoint_id0: {
                            "metrics": metrics_value # the type of metrics_value: list[SingleMetric]
                        },
                        endpoint_id1: ...
                    }
                },
                instance_id1: ...
            }
        :returns: if success, replace "metrics_str" by "metrics" and return True, else return False
        """

        if not isinstance(collects, dict):
            logger.error("[Metrics] Invalid pods metric JSON file.")
            return False

        if not collects:
            return True

        for instance_id in collects.keys():
            if (
                not isinstance(collects[instance_id], dict)
                or not collects[instance_id]
                or "endpoints" not in collects[instance_id]
            ):
                logger.error("[Metrics] Invalid pods metric JSON file.")
                return False

            pods = collects[instance_id]["endpoints"]
            for pod_info in pods.values():
                if "metrics_str" not in pod_info:
                    logger.error("[Metrics] Invalid 'metrics_str' in pod metrics JSON file.")
                    return False
                parsed_metric = self._parse_metric_text(pod_info["metrics_str"])
                if not parsed_metric:
                    logger.error("[Metrics] Parse metric text failed.")
                    return False
                pod_info[self.METRICS_KEY] = parsed_metric
        return True

    def _aggregate_labels_by_sum(self, metric_list: list[SingleMetric]) -> dict[str, float]:
        """
        Aggregate all labels by sum.

        :param metric_list:
        :returns:
        """
        aggregate = {}
        for metric in metric_list:
            for i, label in enumerate(metric.label):
                if label not in aggregate:
                    aggregate[label] = 0.0
                aggregate[label] += metric.value[i]
        return aggregate

    def _aggregate_labels_by_mean(self, metric_list: list[SingleMetric]) -> dict[str, float]:
        """
        Aggregate all labels by mean.

        :param metric_list:
        :returns:
        """
        aggregate = self._aggregate_labels_by_sum(metric_list)
        for label in aggregate:
            aggregate[label] /= len(metric_list)
        return aggregate

    def _aggregate_metric_common(self, metric_list: list[SingleMetric]) -> SingleMetric:
        """
        Aggregate single metric using different rule according metric name.

        :param metric_list: the same metric from different pods or instances
        :returns:
        """

        # vllm:kv_cache_usage_perc use mean rule, other use sum rule
        if metric_list[0].name == "vllm:kv_cache_usage_perc":
            aggregate = self._aggregate_labels_by_mean(metric_list)
        else:
            aggregate = self._aggregate_labels_by_sum(metric_list)

        metric_aggregate = SingleMetric()
        metric_aggregate.name = metric_list[0].name
        metric_aggregate.help = metric_list[0].help
        metric_aggregate.type = metric_list[0].type
        metric_aggregate.label = []
        metric_aggregate.value = []
        for label, value in aggregate.items():
            metric_aggregate.label.append(label)
            metric_aggregate.value.append(value)
        return metric_aggregate

    def _aggregate_metrics_common(self, metrics_list: list[list[SingleMetric]]) -> list[SingleMetric]:
        """
        Aggregate metrics using different rule according metric name.

        :param metrics_list:
        :returns:
        """

        # 1. find longest metrics as aggregate format
        max_index = 0
        max_length = len(metrics_list[max_index])
        for i, metrics in enumerate(metrics_list):
            if len(metrics) > max_length:
                max_index = i
                max_length = len(metrics)

        # 2. insert metric.name sequence by sequence
        aggr_input = {}
        for metric in metrics_list[max_index]:
            aggr_input[metric.name] = []

        # 3. prepare input metric data to be aggregated
        for metrics in metrics_list:
            for metric in metrics:
                aggr_input[metric.name].append(metric)

        # 4. aggregate all metrics
        metrics_aggregate = []
        for value in aggr_input.values():
            metrics_aggregate.append(self._aggregate_metric_common(value))

        return metrics_aggregate

    def _aggregate_metrics_by_instance(
        self,
        collects: dict[int, dict[str, dict[int, dict[str, list[SingleMetric]]]]]
    ) -> None:
        """
        For each instance, aggreagte metrics of all endpoints.

        :param collects:
            collects before call:
            {
                instance_id0: {
                    "endpoints": {
                        endpoint_id0: {
                            "metrics": metrics_value # the type of metrics_value: list[SingleMetric]
                        },
                        endpoint_id1: ...
                    }
                },
                instance_id1: ...
            }
            collects after call:
            {
                instance_id0: {
                    "metrics": metrics_value # the type of metrics_value: list[SingleMetric]
                },
                instance_id1: ...
            }
        :returns:
        """

        for instance_id in collects.keys():
            endpoints = collects[instance_id]["endpoints"]
            if not endpoints:
                continue

            aggr_input = []
            for pod in endpoints.values():
                aggr_input.append(pod[self.METRICS_KEY])
            collects[instance_id][self.METRICS_KEY] = self._aggregate_metrics_common(aggr_input)
            del collects[instance_id]["endpoints"]

            # update cache
            self._instance_metrics_cached[instance_id] = {
                self.METRICS_KEY: collects[instance_id][self.METRICS_KEY]
            }

    def _aggregate_metrics_all_instance(
        self, collects: dict[int, dict[str, list[SingleMetric]]]
    ) -> list[SingleMetric]:
        """ Aggreagte metrics of all instances.  """

        if not self._instance_metrics_cached:
            return []

        aggr_input = []
        # 1. add cache data to input data
        for ins_id, ins_info in self._instance_metrics_cached.items():
            aggr_input_single = []
            for metric in ins_info[self.METRICS_KEY]:
                # gauge type only aggregate active instances
                if metric.type == MetricType.GAUGE and ins_id not in collects:
                    new_metric = SingleMetric(metric)
                    aggr_input_single.append(new_metric)
                else:
                    aggr_input_single.append(metric)
            aggr_input.append(aggr_input_single)

        # 2. add history metric to input data
        aggr_input_single = []
        for metric in self._inactive_instance_metrics_aggregate:
            aggr_input_single.append(metric)
        aggr_input.append(aggr_input_single)

        # 3. excute aggregate
        aggregate = self._aggregate_metrics_common(aggr_input)

        return aggregate

    def _get_serialize_metrics(self, aggregate: list[SingleMetric]) -> str:
        """ Metrics serialize.  """

        lines = []
        for item in aggregate:
            lines.append("# HELP {} {}".format(item.name, item.help))
            lines.append("# TYPE {} {}".format(item.name, item.type))
            for i, lable_name in enumerate(item.label):
                lines.append("{} {}".format(lable_name, self._get_value_str(item.value[i])))
        return "\n".join(lines)

    def _get_serialize_instance_metrics(
        self,
        collects: dict[int, dict[str, list[SingleMetric]]]
    ) -> dict[int, list[SingleMetric]]:
        """ Instance metrics serialize.  """

        instance_metrics = {}
        for ins_id in collects.keys():
            instance_metrics[ins_id] = self._instance_metrics_cached[ins_id][self.METRICS_KEY]

        return instance_metrics

    def _clear_inactive_metrics(self, unavailable_pool: dict[int, Instance]) -> None:
        # 1. get instance list to clear
        clear_ins_list = []
        for ins_id in unavailable_pool.keys():
            if ins_id in self._instance_metrics_cached:
                clear_ins_list.append(ins_id)

        # 2. add clear cache data to input data
        aggr_input = []
        for ins_id in clear_ins_list:
            metrics = self._instance_metrics_cached[ins_id][self.METRICS_KEY]
            aggr_input_single = []
            for metric in metrics:
                # gauge type only aggregate active instances
                if metric.type == MetricType.GAUGE:
                    new_metric = SingleMetric(metric)
                    aggr_input_single.append(new_metric)
                else:
                    aggr_input_single.append(metric)
            aggr_input.append(aggr_input_single)

        # 3. add history metric to input data
        aggr_input_single = []
        for metric in self._inactive_instance_metrics_aggregate:
            aggr_input_single.append(metric)
        aggr_input.append(aggr_input_single)

        # 4. excute aggregate and update history metric
        self._inactive_instance_metrics_aggregate = self._aggregate_metrics_common(aggr_input)

        # 5. remove ins_id from cache
        for ins_id in clear_ins_list:
            del self._instance_metrics_cached[ins_id]

    def _get_instances_metrics(self, 
                               name: str,
                               num: int
    ) -> SingleMetric:
        single_metric = SingleMetric()
        single_metric.name = name
        single_metric.help = "Number of instances"
        single_metric.type = MetricType.GAUGE
        single_metric.label = [name]
        single_metric.value = [num]
        return single_metric

    def _add_coordinator_metrics(self, 
                                 aggregate: list[SingleMetric],
                                 available_instances: dict[int, Instance]
    ) -> None:
        available_role_counts = Counter(instance.role for instance in available_instances.values())
        available_p = available_role_counts.get(PDRole.ROLE_P, 0)
        available_d = available_role_counts.get(PDRole.ROLE_D, 0)

        p_num = self._deploy_config.p_instances_num
        d_num = self._deploy_config.d_instances_num
        unavailable_p = p_num - available_p
        unavailable_d = d_num - available_d

        aggregate.append(self._get_instances_metrics("motor_active_prefill_workers", available_p))
        aggregate.append(self._get_instances_metrics("motor_active_decode_workers", available_d))
        aggregate.append(self._get_instances_metrics("motor_inactive_prefill_workers", unavailable_p))
        aggregate.append(self._get_instances_metrics("motor_inactive_decode_workers", unavailable_d))
        return

    def _get_and_aggregate_metrics(self) -> tuple[str, dict[str, list[SingleMetric]]]:
        """ Get and Aggregate metrics.  """

        # Step 0: get instances (same view as scheduling when _scheduler_provider is set)
        loop = getattr(self, '_loop', None)
        if loop is None:
            available_instances, unavailable_instances = {}, {}
        else:
            try:
                get_scheduler = getattr(self, '_scheduler_provider', None)
                if get_scheduler is None:
                    logger.warning("[Metrics] scheduler_provider not set, skipping instance metrics")
                    available_instances, unavailable_instances = {}, {}
                else:
                    scheduler = get_scheduler()
                    future = asyncio.run_coroutine_threadsafe(
                        scheduler.get_all_instances(), loop
                    )
                    available_instances, unavailable_instances = future.result(timeout=10)
            except Exception as e:
                logger.warning("[Metrics] get_all_instances failed: %s", e)
                available_instances, unavailable_instances = {}, {}
        self._clear_inactive_metrics(unavailable_instances)

        # Step 1: get instances/endpoints info and get all endpoints metrics text.
        collects = self._get_server_metrics(available_instances)

        # Step 2: parse metrics text to format data for all instances/endpoints.
        if not self._parse_metrics(collects):
            logger.error("[Metrics] Parse vllm server metrics failed.")
            return "", {}

        # Step 3: for each instance, aggreagte metrics of all endpoints.
        self._aggregate_metrics_by_instance(collects)

        # Step 4: aggreagte metrics of all instances.
        aggregate = self._aggregate_metrics_all_instance(collects)

        # Step 5: add coordinator metrics
        self._add_coordinator_metrics(aggregate, available_instances)

        # Step 6: serialize and return to handler.
        return self._get_serialize_metrics(aggregate), self._get_serialize_instance_metrics(collects)
