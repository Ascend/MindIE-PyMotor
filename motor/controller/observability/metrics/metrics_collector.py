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
Controller Observability metrics collector: thin proxy that routes metrics
requests to the Coordinator. All data processing (aggregation, label injection,
Prometheus serialization) is handled by the Coordinator.
"""

from motor.controller.api_client.coordinator_api_client import CoordinatorApiClient


class MetricsCollector:
    """
    Client-side metrics collector: routes requests to Coordinator.
    """

    def __init__(self) -> None:
        pass

    def get_full_metrics(self) -> str:
        result = CoordinatorApiClient.get_metrics(metrics_type="full")
        return result if result is not None else ""

    def get_instance_metrics_prometheus(self) -> str:
        result = CoordinatorApiClient.get_metrics(metrics_type="instance")
        return result if result is not None else ""

    def get_role_metrics(self, role: str | None = None) -> str:
        result = CoordinatorApiClient.get_metrics(metrics_type="role", role=role)
        if result is None:
            return ""
        return result
