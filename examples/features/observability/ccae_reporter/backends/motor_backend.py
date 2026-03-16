# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import os
import base64

from ccae_reporter.common.logging import Log
from motor.common.utils.http_client import SafeHTTPSClient
from .base_backend import BaseBackend


class MotorBackend(BaseBackend):
    def __init__(self, identity: str):
        super().__init__(identity)
        self.logger = Log(__name__).getlog()
        pod_ip = os.getenv("POD_IP")
        self.om_client = SafeHTTPSClient(address=f"{pod_ip}:1027")
        self.probe_client = SafeHTTPSClient(address=f"{pod_ip}:1026")

    def fetch_alarm_info(self) -> list:
        if not self.is_alive():
            self.logger.warning(f"CCAE is not alive, skip fetching alarms info")
            return []
        url = "/observability/alarms"
        try:
            response = self.om_client.do_get(f"{url}?source_id={os.getenv('NORTH_PLATFORM', 'ccae_reporter')}")
            if response.status_code != 200:
                self.logger.error(f"Failed to fetch alarms info from {url}")
                return []
            alarm_info = response.json()
            data = alarm_info.get("data")
            return data.get("alarms", [])
        except Exception as e:
            self.logger.error(f"Failed to fetch alarms info from {url}: {e}")
            return []

    def fetch_inventory_info(self, model_id: str) -> dict:
        inventory_url = f"/observability/inventory"
        try:
            response = self.om_client.do_get(inventory_url)
            if response.status_code != 200:
                self.logger.error(f"Failed to fetch inventory info from {inventory_url}")
                return {}
            inventory_info = response.json()
            data = inventory_info.get("data")
            if data:
                metric_info = self._fetch_metrics_info()
                if metric_info:
                    data["metrics"] = {"metric": base64.b64encode(metric_info.encode()).decode(), 
                    "metricPeriod": 1}
                else:
                    data["metrics"] = {"metric": "", "metricPeriod": 1}
                data["modelID"] = model_id
            return {
                "componentType": 0 if self.identity == "Controller" else 1,
                "modelServiceInfo": [
                    data
                ]
            }
        except Exception as e:
            self.logger.error(f"Failed to fetch inventory info from {inventory_url}: {e}")
            return {}

    def is_alive(self) -> bool:
        url = "/readiness"
        try:
            response = self.probe_client.do_get(url)
            if response.status_code != 200:
                return False
            return True
        except Exception as e:
            self.logger.error(f"Failed to check liveness from {url}: {e}")
            return False
        
    def _fetch_metrics_info(self) -> str:
        metrics_url = f"/observability/metrics"
        try:
            response = self.om_client.do_get(metrics_url)
            if response.status_code != 200:
                self.logger.error(f"Failed to fetch metrics info from {metrics_url}")
                return ""
            return response.json().get("data", "")
        except Exception as e:
            self.logger.error(f"Failed to fetch metrics info from {metrics_url}: {e}")
            return ""
