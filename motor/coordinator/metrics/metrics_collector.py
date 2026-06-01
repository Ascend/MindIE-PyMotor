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
import math
import re
import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from collections import Counter
from collections.abc import Callable
from typing import Any

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
        self._last_collects: dict[int, dict[str, Any]] = {}

        self._collects_version: int = 0
        self._caches: dict[str, Any] = {}
        self._cache_version: int = -1
        self._serialize_lock = threading.Lock()

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

    def get_metrics(
        self,
        metrics_type: str = "full",
        role: str | None = None,
    ) -> str:
        """
        Unified metrics retrieval with type selection.  All types return Prometheus text.

        :param metrics_type: "full" (default), "instance", "role", "dp", or "node"
        :param role: when metrics_type is "role", filter to a specific role (e.g. "prefill", "decode")
        :returns: Prometheus text
        """
        with self._lock:
            version = self._collects_version
            collects = self._last_collects
        with self._serialize_lock:
            if self._cache_version != version:
                self._caches = {}
                self._cache_version = version
            if metrics_type == "instance":
                if "instance" not in self._caches:
                    self._caches["instance"] = self._generate_instance_metrics(collects)
                return self._caches["instance"]
            if metrics_type == "role":
                if "role" not in self._caches:
                    self._caches["role"] = self._generate_role_metrics(collects)
                if role:
                    return self._caches["role"].get(role, "")
                return "\n".join(self._caches["role"].values())
            if metrics_type == "dp":
                if "dp" not in self._caches:
                    self._caches["dp"] = self._generate_dp_metrics(collects)
                return self._caches["dp"]
            if metrics_type == "node":
                if "node" not in self._caches:
                    self._caches["node"] = self._generate_node_metrics(collects)
                return self._caches["node"]
            if "full" not in self._caches:
                self._caches["full"] = self._generate_full_metrics(collects)
            return self._caches["full"]

    @classmethod
    def _inject_labels(
        cls,
        metric: Metric,
        **labels: str,
    ) -> Metric:
        extra = ",".join(f'{k}="{v}"' for k, v in labels.items())
        result = metric.copy()
        result.label = [
            lbl.replace("{", "{" + extra + ",") if "{" in lbl else lbl + "{" + extra + "}" for lbl in metric.label
        ]
        return result

    def _generate_instance_metrics(
        self,
        collects: dict[int, dict[str, Any]],
    ) -> str:
        instance_metrics = self._aggregate_collects_by_instance(collects)
        if not instance_metrics:
            return ""
        all_metrics: list[Metric] = []
        for ins_id, metrics_list in instance_metrics.items():
            role = collects.get(ins_id, {}).get("role", "unknown")
            for m in metrics_list:
                all_metrics.append(self._inject_labels(m, instance_id=str(ins_id), role=role))
        return self._format_prometheus(all_metrics)

    def _generate_role_metrics(
        self,
        collects: dict[int, dict[str, Any]],
    ) -> dict[str, str]:
        instance_metrics = self._aggregate_collects_by_instance(collects)
        role_groups: dict[str, list[list[Metric]]] = {}
        for ins_id, metrics_list in instance_metrics.items():
            role = collects.get(ins_id, {}).get("role", "unknown")
            role_groups.setdefault(role, []).append(metrics_list)

        result: dict[str, str] = {}
        for role, metrics_lists in role_groups.items():
            aggregated = self._aggregate_metrics(metrics_lists)
            if aggregated:
                labeled = [self._inject_labels(m, role=role) for m in aggregated]
                result[role] = self._format_prometheus(labeled)
        return result

    def _update_metrics_thread(self) -> None:
        while not self._stop_event.is_set():
            collects = self._collect_metrics()
            if collects is not None:
                with self._lock:
                    self._last_collects = collects
                    self._collects_version += 1
            with self._config_lock:
                reuse_time = self._prometheus_metrics_config.reuse_time
            time.sleep(reuse_time)

    def _collect_metrics(self) -> dict[int, dict[str, Any]] | None:
        available_instances, unavailable_instances = self._get_available_instances()
        self._clear_inactive_metrics(unavailable_instances)

        # Step 1: get instances/endpoints info and get all endpoints metrics text.
        collects = self._fetch_instance_metrics(available_instances)

        # Step 2: parse metrics text to format data for all instances/endpoints.
        if not self._parse_metrics(collects):
            logger.error("[Metrics] Parse vllm server metrics failed.")
            return None

        return collects

    def _aggregate_collects_by_instance(
        self,
        collects: dict[int, dict[str, Any]],
    ) -> dict[int, list[Metric]]:
        """Non-destructively aggregate endpoints per instance from raw collects."""
        instance_metrics: dict[int, list[Metric]] = {}
        for ins_id, ins_data in collects.items():
            if not isinstance(ins_data, dict) or "endpoints" not in ins_data:
                continue
            aggr_input = []
            for pod_info in ins_data["endpoints"].values():
                if self.METRICS_KEY in pod_info:
                    aggr_input.append(pod_info[self.METRICS_KEY])
            if aggr_input:
                instance_metrics[ins_id] = self._aggregate_metrics(aggr_input)
        return instance_metrics

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
    def _parse_metric_help(
        metric: Metric,
        line: str,
    ) -> bool:
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "#" and parts[1] == "HELP":
            metric.name = parts[2]
            metric.help = " ".join(parts[3:])
            return True
        logger.error("[Metrics] Parse metric help failed.")
        return False

    @staticmethod
    def _parse_metric_type(
        metric: Metric,
        line: str,
    ) -> bool:
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
    def _parse_metric_body_block(
        cls,
        metric: Metric,
        line: str,
    ) -> bool:
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

    def _fetch_instance_metrics(
        self,
        available_instances: dict[int, Instance],
    ) -> dict[int, dict[str, dict[int, str]]]:
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
                collect["role"] = ins_info.role
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
                collect["endpoints"][en_info.id] = {
                    "metrics_str": metrics_str,
                    "pod_ip": en_info.ip,
                }

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
        zeroed = metric.copy()
        zeroed.value = [0.0] * len(zeroed.value)
        return zeroed

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

    def _append_coordinator_metrics(
        self,
        aggregate: list[Metric],
        collects: dict[int, dict[str, Any]],
    ) -> None:
        role_counts = Counter(ins_data.get("role", "") for ins_data in collects.values())
        available_p = role_counts.get(PDRole.ROLE_P, 0)
        available_d = role_counts.get(PDRole.ROLE_D, 0)
        with self._config_lock:
            p_num = self._deploy_config.p_instances_num
            d_num = self._deploy_config.d_instances_num

        def _new(name: str, num: int) -> Metric:
            return Metric(
                name=name,
                help="Number of instances",
                type=MetricType.GAUGE,
                label=[name],
                value=[num],
            )

        aggregate.append(_new("motor_active_prefill_workers", available_p))
        aggregate.append(_new("motor_active_decode_workers", available_d))
        aggregate.append(_new("motor_inactive_prefill_workers", p_num - available_p))
        aggregate.append(_new("motor_inactive_decode_workers", d_num - available_d))

    def _generate_full_metrics(
        self,
        collects: dict[int, dict[str, Any]],
    ) -> str:
        instance_metrics = self._aggregate_collects_by_instance(collects)
        if not instance_metrics:
            return ""
        aggregate = self._aggregate_metrics(list(instance_metrics.values()))
        self._append_coordinator_metrics(aggregate, collects)
        return self._format_prometheus(aggregate)

    def _format_prometheus(self, aggregate: list[Metric]) -> str:
        lines = []
        for item in aggregate:
            lines.append("# HELP {} {}".format(item.name, item.help))
            lines.append("# TYPE {} {}".format(item.name, item.type))
            for i, label in enumerate(item.label):
                v = item.value[i]
                if math.isnan(v):
                    vs = "Nan"
                elif v == float("inf"):
                    vs = "+Inf"
                elif v == float("-inf"):
                    vs = "-Inf"
                else:
                    vs = str(v)
                lines.append("{} {}".format(label, vs))
        return "\n".join(lines)

    @staticmethod
    def _prepend_dim_labels(
        label_str: str,
        dim_labels: str,
    ) -> str:
        if "{" not in label_str:
            return f"{label_str}{{{dim_labels}}}"
        name_part, rest = label_str.split("{", 1)
        if rest == "}":
            return f"{name_part}{{{dim_labels}}}"
        return f"{name_part}{{{dim_labels},{rest}"

    @staticmethod
    def _metric_value_str(value: float) -> str:
        if math.isnan(value):
            return "Nan"
        if value == float("inf"):
            return "+Inf"
        if value == float("-inf"):
            return "-Inf"
        return str(value)

    @staticmethod
    def _emit_metric_groups(name_to_meta: dict[str, dict[str, Any]]) -> str:
        out_lines: list[str] = []
        for name in sorted(name_to_meta.keys()):
            meta = name_to_meta[name]
            out_lines.append(f"# HELP {name} {meta['help']}")
            out_lines.append(f"# TYPE {name} {meta['type']}")
            meta["lines"].sort(key=lambda kv: (kv[0], kv[1]))
            out_lines.extend(line for _, line in meta["lines"])
        return "\n".join(out_lines)

    def _generate_dp_metrics(self, collects: dict[int, dict[str, Any]]) -> str:
        name_to_meta: dict[str, dict[str, Any]] = {}
        for instance_id, ins_collect in collects.items():
            if not isinstance(ins_collect, dict) or "endpoints" not in ins_collect:
                continue
            role = ins_collect.get("role", "")
            for ep_id, pod_info in ins_collect["endpoints"].items():
                if self.METRICS_KEY not in pod_info:
                    continue
                pod_ip = pod_info.get("pod_ip", "")
                dim_labels = f'dp_rank="{ep_id}",role="{role}",instance_id="{instance_id}",pod_ip="{pod_ip}"'
                for metric in pod_info[self.METRICS_KEY]:
                    meta = name_to_meta.setdefault(
                        metric.name,
                        {"help": metric.help, "type": metric.type, "lines": []},
                    )
                    for i, label_str in enumerate(metric.label):
                        new_label = self._prepend_dim_labels(label_str, dim_labels)
                        meta["lines"].append(
                            ((instance_id, ep_id), f"{new_label} {self._metric_value_str(metric.value[i])}")
                        )
        return self._emit_metric_groups(name_to_meta)

    def _generate_node_metrics(self, collects: dict[int, dict[str, Any]]) -> str:
        key_to_lists: dict[tuple[str, str], list[list[Metric]]] = {}
        for ins_collect in collects.values():
            if not isinstance(ins_collect, dict) or "endpoints" not in ins_collect:
                continue
            role = ins_collect.get("role", "")
            for pod_info in ins_collect["endpoints"].values():
                if self.METRICS_KEY not in pod_info:
                    continue
                pod_ip = pod_info.get("pod_ip", "")
                if not pod_ip:
                    continue
                key_to_lists.setdefault((pod_ip, role), []).append(pod_info[self.METRICS_KEY])
        pod_aggregates = {
            key: self._aggregate_metrics(metrics_lists) for key, metrics_lists in key_to_lists.items() if metrics_lists
        }
        name_to_meta: dict[str, dict[str, Any]] = {}
        for (pod_ip, role), aggregate in pod_aggregates.items():
            dim_labels = f'pod_ip="{pod_ip}",role="{role}"'
            for metric in aggregate:
                meta = name_to_meta.setdefault(
                    metric.name,
                    {"help": metric.help, "type": metric.type, "lines": []},
                )
                for i, label_str in enumerate(metric.label):
                    new_label = self._prepend_dim_labels(label_str, dim_labels)
                    meta["lines"].append(((pod_ip, role), f"{new_label} {self._metric_value_str(metric.value[i])}"))
        return self._emit_metric_groups(name_to_meta)
