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

"""
Controller OM metrics collector: client to coordinator for full (aggregated) metrics.
If the interval between two collections is less than 15 seconds, returns the last
collected metrics; otherwise fetches from coordinator. Only full metrics are supported,
not per-instance metrics.
"""

import threading
import time
from typing import Optional

from motor.common.utils.logger import get_logger
from motor.controller.api_client.coordinator_api_client import CoordinatorApiClient
from motor.config.controller import ControllerConfig

logger = get_logger(__name__)


class MetricsCollector:
    """
    Client-side metrics collector: fetches full metrics from coordinator with 15s cache.
    """

    def __init__(self, config: ControllerConfig | None = None) -> None:
        if config is None:
            config = ControllerConfig()
        self.config = config

        self._lock = threading.RLock()
        self._last_metrics: Optional[str] = None
        self._last_fetch_time: float = 0.0
        self._cache_ttl_sec = self.config.observability_config.metrics_ttl

    def get_full_metrics(self) -> str:
        """
        Get full aggregated metrics. If last fetch was within 15 seconds, returns
        cached value; otherwise fetches from coordinator and updates cache.
        Returns empty string on error (and keeps previous cache for next call).
        """
        now = time.monotonic()
        with self._lock:
            if (self._last_metrics is not None and
                    (now - self._last_fetch_time) < self._cache_ttl_sec):
                return self._last_metrics

        metrics = CoordinatorApiClient.get_full_metrics()
        with self._lock:
            if metrics is not None:
                self._last_metrics = metrics
                self._last_fetch_time = time.monotonic()
                return self._last_metrics
            # On failure, return last cached if any
            if self._last_metrics is not None:
                return self._last_metrics
            return ""
