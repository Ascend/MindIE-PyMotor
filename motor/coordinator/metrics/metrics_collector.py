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
from dataclasses import dataclass, field
from enum import Enum
from collections import Counter
from typing import Any
from collections.abc import Callable

from motor.common.resources.instance import Instance
from motor.common.resources import PDRole
from motor.common.logger import get_logger
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


@dataclass
class Metric:
    name: str = ""
    help: str = ""
    type: MetricType = MetricType.NONE
    label: list[str] = field(default_factory=list)
    value: list[float] = field(default_factory=list)

    def copy(self) -> "Metric":
        return Metric(
            name=self.name,
            help=self.help,
            type=self.type,
            label=list(self.label),
            value=list(self.value),
        )


class MetricsCollector(ThreadSafeSingleton):
    METRICS_KEY = "metrics"
    _ENGINE_LABEL_RE = re.compile(r'engine="\d+",')

    def __init__(self, config: CoordinatorConfig | None = None):
        if hasattr(self, "_initialized"):
            return

        self._config_lock = threading.RLock()
        if config is None:
            config = CoordinatorConfig()
        self._prometheus_metrics_config = config.prometheus_metrics_config
        self._deploy_config = config.deploy_config

        # Initial metrics state
        self._inactive_instance_metrics_aggregate: list[Metric] = []
        self._instance_metrics_cached: dict[int, dict[str, list[Metric]]] = {}
        self._last_metrics: str = ""
        self._last_instance_metrics: dict[int, list[Metric]] = {}
        self._last_instance_roles: dict[int, str] = {}

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._metrics_update_thread = None
        # Event loop for async get_all_instances (set from lifespan)
        self._loop = None
        # When set, use this to get scheduler (same view as scheduling); must be set in lifespan
        self._scheduler_provider: Callable[[], Any] | None = None

        self._initialized = True
        logger.info("MetricsCollector initialized.")

    def set_event_loop(self, loop):
        """Set the event loop for async calls from the metrics thread (call from lifespan)."""
        self._loop = loop

    def set_scheduler_provider(self, get_scheduler: Callable[[], Any]) -> None:
        """Use same instance view as scheduling: get_scheduler().get_all_instances() (call from lifespan)."""
        self._scheduler_provider = get_scheduler

    def start(self) -> None:
        """Start update metrics thread."""
        if self._stop_event.is_set():
            self._stop_event.clear()
        self._metrics_update_thread = threading.Thread(
            target=self._update_metrics_thread, daemon=True, name="MetricsUpdate"
        )
        self._metrics_update_thread.start()
        logger.info("MetricsCollector started.")

    def stop(self) -> None:
        """Stop update metrics thread."""
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

    def get_metrics(self, metrics_type: str = "full", role: str | None = None) -> str:
        """
        Unified metrics retrieval with type selection.  All types return Prometheus text.

        :param metrics_type: "full" (default), "instance", or "role"
        :param role: when metrics_type is "role", filter to a specific role (e.g. "prefill", "decode")
        :returns: Prometheus text
        """
        if metrics_type == "instance":
            return self._format_instance_metrics()
        if metrics_type == "role":
            role_metrics = self._build_role_metrics()
            if role:
                return role_metrics.get(role, "")
            return "\n".join(role_metrics.values())
        with self._lock:
            return self._last_metrics

    def _snapshot_instance_state(self) -> tuple[dict, dict]:
        with self._lock:
            return dict(self._last_instance_metrics), dict(self._last_instance_roles)

    @classmethod
    def _inject_labels(cls, metric: Metric, **labels: str) -> Metric:
        extra = ",".join(f'{k}="{v}"' for k, v in labels.items())
        result = metric.copy()
        result.label = [
            l.replace("{", "{" + extra + ",") if "{" in l else l + "{" + extra + "}"
            for l in metric.label
        ]
        return result

    def _format_instance_metrics(self) -> str:
        instance_metrics, instance_roles = self._snapshot_instance_state()
        if not instance_metrics:
            return ""

        all_metrics: list[Metric] = []
        for ins_id, metrics_list in instance_metrics.items():
            role = instance_roles.get(ins_id, "unknown")
            for m in metrics_list:
                all_metrics.append(self._inject_labels(m, instance_id=str(ins_id), role=role))
        return self._format_prometheus(all_metrics)

    def _build_role_metrics(self) -> dict[str, str]:
        instance_metrics, instance_roles = self._snapshot_instance_state()

        role_metrics: dict[str, list[list[Metric]]] = {}
        for ins_id, metrics_list in instance_metrics.items():
            role = instance_roles.get(ins_id, "unknown")
            if role not in role_metrics:
                role_metrics[role] = []
            role_metrics[role].append(metrics_list)

        result: dict[str, str] = {}
        for role, metrics_lists in role_metrics.items():
            aggregated = self._aggregate_metrics(metrics_lists)
            if aggregated:
                labeled = [self._inject_labels(m, role=role) for m in aggregated]
                result[role] = self._format_prometheus(labeled)

        return result

    def _update_metrics_thread(self) -> None:
        while not self._stop_event.is_set():
            metrics, instance_metrics, instance_roles = self._collect_and_aggregate()
            with self._lock:
                if metrics and instance_metrics:
                    self._last_metrics = metrics
                    self._last_instance_metrics = instance_metrics
                    self._last_instance_roles = instance_roles
            with self._config_lock:
                reuse_time = self._prometheus_metrics_config.reuse_time
            time.sleep(reuse_time)

    def _collect_and_aggregate(self) -> tuple[str, dict[str, list[Metric]], dict[int, str]]:
        """Get and Aggregate metrics."""

        available_instances, unavailable_instances = self._get_available_instances()
        self._clear_inactive_metrics(unavailable_instances)

        instance_roles: dict[int, str] = {}
        for ins_id, ins in available_instances.items():
            instance_roles[ins_id] = ins.role

        # Step 1: get instances/endpoints info and get all endpoints metrics text.
        collects = self._fetch_instance_metrics(available_instances)

        # Step 2: parse metrics text to format data for all instances/endpoints.
        if not self._parse_metrics(collects):
            logger.error("[Metrics] Parse vllm server metrics failed.")
            return "", {}, instance_roles

        # Step 3: for each instance, aggreagte metrics of all endpoints.
        self._aggregate_metrics_by_instance(collects)

        # Step 4: aggreagte metrics of all instances.
        aggregate = self._aggregate_metrics_all_instance(collects)

        # Step 5: add coordinator metrics
        self._add_coordinator_metrics(aggregate, available_instances)

        # Step 6: serialize and return to handler.
        return (
            self._format_prometheus(aggregate),
            self._collect_instance_metrics(collects),
            instance_roles,
        )

    def _get_available_instances(self) -> tuple[dict[int, Instance], dict[int, Instance]]:
        loop = self._loop
        if loop is None or self._scheduler_provider is None:
            return {}, {}
        try:
            future = asyncio.run_coroutine_threadsafe(self._scheduler_provider().get_all_instances(), loop)
            return future.result(timeout=10)
        except Exception as e:
            logger.warning("[Metrics] get_all_instances failed: %s", e)
            return {}, {}

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
                aggr_input_single.append(self._copy_metric_zero_gauge(metric))
            aggr_input.append(aggr_input_single)

        # 3. add history metric to input data
        aggr_input_single = []
        for metric in self._inactive_instance_metrics_aggregate:
            aggr_input_single.append(metric)
        aggr_input.append(aggr_input_single)

        # 4. excute aggregate and update history metric
        self._inactive_instance_metrics_aggregate = self._aggregate_metrics(aggr_input)

        # 5. remove ins_id from cache
        for ins_id in clear_ins_list:
            del self._instance_metrics_cached[ins_id]

    def _parse_metrics(self, collects: dict[int, dict[str, dict[int, dict[str, str]]]]) -> bool:
        if not isinstance(collects, dict):
            logger.error("[Metrics] Invalid collects type, expected dict.")
            return False
        if not collects:
            return True

        for instance_id, inst_data in collects.items():
            if not isinstance(inst_data, dict) or not inst_data:
                logger.error("[Metrics] Invalid instance entry for instance %s", instance_id)
                return False
            pods = inst_data.get("endpoints")
            if not pods:
                logger.error("[Metrics] Missing 'endpoints' in instance %s", instance_id)
                return False

            for pod_info in pods.values():
                metrics_str = pod_info.get("metrics_str")
                if not metrics_str:
                    logger.error("[Metrics] Missing 'metrics_str' for endpoint in instance %s", instance_id)
                    return False
                parsed_metric = self._parse_metric_text(metrics_str)
                if not parsed_metric:
                    logger.error("[Metrics] Parse metric text failed for instance %s", instance_id)
                    return False
                pod_info[self.METRICS_KEY] = parsed_metric
        return True

    def _parse_metric_text(self, metrics_str: str) -> list[Metric]:
        lines = [ln for ln in metrics_str.strip().split("\n") if ln]
        if not lines:
            return []

        metric_array: list[Metric] = []
        i, n = 0, len(lines)
        while i < n:
            metric = Metric()
            if not self._parse_metric_help(metric, lines[i]):
                return []
            i += 1
            if i >= n or not self._parse_metric_type(metric, lines[i]):
                return []
            i += 1
            while i < n and not lines[i].startswith("#"):
                if not self._parse_metric_body_block(metric, lines[i]):
                    return []
                i += 1
            metric_array.append(metric)
        return metric_array

    @staticmethod
    def _parse_metric_help(metric: Metric, line: str) -> bool:
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "#" and parts[1] == "HELP":
            metric.name = parts[2]
            metric.help = " ".join(parts[3:])
            return True
        logger.error("[Metrics] Parse metric help failed.")
        return False

    @staticmethod
    def _parse_metric_type(metric: Metric, line: str) -> bool:
        parts = line.split()
        if len(parts) == 4 and parts[0] == "#" and parts[1] == "TYPE":
            try:
                metric.type = MetricType.from_string(parts[3])
                return True
            except KeyError:
                logger.error("[Metrics] Illegal metric type: %s", parts[3])
                return False
        logger.error("[Metrics] Parse metric type failed.")
        return False

    @classmethod
    def _parse_metric_body_block(cls, metric: Metric, line: str) -> bool:
        parts = line.split()
        if len(parts) != 2:
            logger.error("[Metrics] Parse metric body failed.")
            return False

        label = cls._ENGINE_LABEL_RE.sub("", parts[0])
        metric.label.append(label)
        try:
            value = float(parts[1])
            if value < 0:
                logger.error("[Metrics] Illegal metric value: %s", parts[1])
                return False
            metric.value.append(value)
        except ValueError:
            logger.error("[Metrics] Illegal metric value: %s", parts[1])
            return False
        return True

    def _fetch_instance_metrics(self, available_instances: dict[int, Instance]) -> dict[int, dict[str, dict[int, str]]]:
        """Get instances/endpoints info and get all endpoints metrics text.

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
            collect = self._fetch_endpoint_metrics(ins_info)
            if collect:
                collects[ins_info.id] = collect

        return collects

    def _fetch_endpoint_metrics(self, ins_info: Instance) -> dict[str, dict[int, str]]:
        """Get all endpoints metrics text in single instance.

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
        collect = {"endpoints": {}}

        for ens_info in ins_info.endpoints.values():
            for en_info in ens_info.values():
                metrics_str = EngineServerApiClient.query_metrics(f"{en_info.ip}:{en_info.mgmt_port}")
                if not metrics_str:
                    return {}
                collect["endpoints"][en_info.id] = {"metrics_str": metrics_str}

        return collect

    def _aggregate_metrics_by_instance(
        self, collects: dict[int, dict[str, dict[int, dict[str, list[Metric]]]]]
    ) -> None:
        """For each instance, aggreagte metrics of all endpoints.

        :param collects:
            collects before call:
            {
                instance_id0: {
                    "endpoints": {
                        endpoint_id0: {
                            "metrics": metrics_value # the type of metrics_value: list[Metric]
                        },
                        endpoint_id1: ...
                    }
                },
                instance_id1: ...
            }
            collects after call:
            {
                instance_id0: {
                    "metrics": metrics_value # the type of metrics_value: list[Metric]
                },
                instance_id1: ...
            }
        """
        for instance_id in collects.keys():
            endpoints = collects[instance_id]["endpoints"]
            if not endpoints:
                continue

            aggr_input = []
            for pod in endpoints.values():
                aggr_input.append(pod[self.METRICS_KEY])
            collects[instance_id][self.METRICS_KEY] = self._aggregate_metrics(aggr_input)
            del collects[instance_id]["endpoints"]

            # update cache
            self._instance_metrics_cached[instance_id] = {self.METRICS_KEY: collects[instance_id][self.METRICS_KEY]}

    def _aggregate_metrics_all_instance(self, collects: dict[int, dict[str, list[Metric]]]) -> list[Metric]:
        """Aggreagte metrics of all instances."""

        if not self._instance_metrics_cached:
            return []

        aggr_input = []
        # 1. add cache data to input data
        for ins_id, ins_info in self._instance_metrics_cached.items():
            aggr_input_single = []
            for metric in ins_info[self.METRICS_KEY]:
                aggr_input_single.append(self._copy_metric_zero_gauge(metric) if ins_id not in collects else metric)
            aggr_input.append(aggr_input_single)

        # 2. add history metric to input data
        aggr_input_single = []
        for metric in self._inactive_instance_metrics_aggregate:
            aggr_input_single.append(metric)
        aggr_input.append(aggr_input_single)

        # 3. excute aggregate
        aggregate = self._aggregate_metrics(aggr_input)

        return aggregate

    @staticmethod
    def _copy_metric_zero_gauge(metric: Metric) -> Metric:
        """Copy metric; if gauge, zero out values (inactive instances contribute 0)."""
        if metric.type != MetricType.GAUGE:
            return metric
        copy = metric.copy()
        copy.value = [0.0] * len(copy.value)
        return copy

    def _aggregate_labels_by_sum(self, metric_list: list[Metric]) -> dict[str, float]:
        """Aggregate all labels by sum."""
        aggregate = {}
        for metric in metric_list:
            for i, label in enumerate(metric.label):
                if label not in aggregate:
                    aggregate[label] = 0.0
                aggregate[label] += metric.value[i]
        return aggregate

    def _aggregate_labels_by_mean(self, metric_list: list[Metric]) -> dict[str, float]:
        """Aggregate all labels by mean."""
        aggregate = self._aggregate_labels_by_sum(metric_list)
        for label in aggregate:
            aggregate[label] /= len(metric_list)
        return aggregate

    def _aggregate_metrics(self, metrics_list: list[list[Metric]]) -> list[Metric]:
        template = max(metrics_list, key=len)
        aggr_input: dict[str, list[Metric]] = {m.name: [] for m in template}
        for metrics in metrics_list:
            for metric in metrics:
                aggr_input[metric.name].append(metric)
        return [self._aggregate_single_metric(v) for v in aggr_input.values()]

    def _aggregate_single_metric(self, metric_list: list[Metric]) -> Metric:
        first = metric_list[0]
        agg_fn = (
            self._aggregate_labels_by_mean
            if first.name == "vllm:kv_cache_usage_perc"
            else self._aggregate_labels_by_sum
        )
        aggregate = agg_fn(metric_list)
        return Metric(
            name=first.name,
            help=first.help,
            type=first.type,
            label=list(aggregate.keys()),
            value=list(aggregate.values()),
        )

    def _add_coordinator_metrics(self, aggregate: list[Metric], available_instances: dict[int, Instance]) -> None:
        available_role_counts = Counter(instance.role for instance in available_instances.values())
        available_p = available_role_counts.get(PDRole.ROLE_P, 0)
        available_d = available_role_counts.get(PDRole.ROLE_D, 0)

        p_num = self._deploy_config.p_instances_num
        d_num = self._deploy_config.d_instances_num
        unavailable_p = p_num - available_p
        unavailable_d = d_num - available_d

        def _new_worker_count_metric(name: str, num: int) -> Metric:
            return Metric(name=name, help="Number of instances", type=MetricType.GAUGE, label=[name], value=[num])

        aggregate.append(_new_worker_count_metric("motor_active_prefill_workers", available_p))
        aggregate.append(_new_worker_count_metric("motor_active_decode_workers", available_d))
        aggregate.append(_new_worker_count_metric("motor_inactive_prefill_workers", unavailable_p))
        aggregate.append(_new_worker_count_metric("motor_inactive_decode_workers", unavailable_d))

    def _format_prometheus(self, aggregate: list[Metric]) -> str:
        lines = []
        for item in aggregate:
            lines.append("# HELP {} {}".format(item.name, item.help))
            lines.append("# TYPE {} {}".format(item.name, item.type))
            for i, label in enumerate(item.label):
                v = item.value[i]
                if v != v:  # NaN
                    vs = "Nan"
                elif v == float("inf"):
                    vs = "+Inf"
                elif v == float("-inf"):
                    vs = "-Inf"
                else:
                    vs = str(v)
                lines.append("{} {}".format(label, vs))
        return "\n".join(lines)

    def _collect_instance_metrics(self, collects: dict[int, dict[str, list[Metric]]]) -> dict[int, list[Metric]]:
        """Instance metrics serialize."""
        instance_metrics = {}
        for ins_id in collects.keys():
            instance_metrics[ins_id] = self._instance_metrics_cached[ins_id][self.METRICS_KEY]

        return instance_metrics
