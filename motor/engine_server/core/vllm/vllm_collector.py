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

import time
from typing import Dict, Any, Optional

import requests

from motor.common.utils.http_client import SafeHTTPSClient
from motor.engine_server.config.base import IConfig
from motor.engine_server.core.base_collector import BaseCollector
from motor.common.utils.logger import get_logger

logger = get_logger("engine_server")


class VLLMCollector(BaseCollector):
    def __init__(self, config: IConfig):
        super().__init__(config)
        self.host = config.get_args().host
        self.port = config.get_args().port
        self.infer_tls_config = config.get_server_config().deploy_config.infer_tls_config
        self.collect_interval = 3
        self.timeout = 2
        logger.info(
            f"VLLMCollector initialized: collect_interval={self.collect_interval}s"
        )

    @staticmethod
    def _build_error_result(error_msg: str, url: str, http_status_code: Optional[int]) -> Dict[str, Any]:
        return {
            "api_url": url,
            "status": "failed",
            "error": error_msg,
            "data": None,
            "collect_time": int(time.time() * 1000),
            "http_status_code": http_status_code
        }

    def _collect(self) -> Dict[str, Any]:
        metrics_data = self._do_collect_metrics()
        health_data = self._do_collect_health()

        return {
            "timestamp": int(time.time() * 1000),
            "collector_name": self.name,
            "metrics": metrics_data,
            "health": health_data,
        }

    def _do_collect_metrics(self) -> Dict[str, Any]:
        if self.infer_tls_config and self.infer_tls_config.enable_tls:
            metrics_url = f"https://{self.host}:{self.port}/metrics"
        else:
            metrics_url = f"http://{self.host}:{self.port}/metrics"
        logger.debug(f"Start collecting vLLM metrics from {metrics_url}")
        address = f"{self.host}:{self.port}"
        try:
            with SafeHTTPSClient(timeout=self.timeout, address=address, tls_config=self.infer_tls_config) as client:
                response = client.do_get("/metrics")
                logger.debug(f"Successfully collected vLLM metrics")
                return {
                    "api_url": metrics_url,
                    "status": "success",
                    "data": response.text,
                    "collect_time": int(time.time() * 1000),
                    "http_status_code": 200
                }
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            error_msg = f"Connect failed: {str(e)}"
            logger.error(f"Metrics collect failed: {error_msg}")
            return self._build_error_result(error_msg, metrics_url, None)

        except requests.exceptions.HTTPError as e:
            http_status_code = e.response.status_code if e.response else None
            error_msg = f"HTTP request failed: {str(e)} (status code: {http_status_code})"
            logger.error(f"Metrics collect failed: {error_msg}")
            return self._build_error_result(error_msg, metrics_url, http_status_code)

        except requests.exceptions.RequestException as e:
            error_msg = f"Request failed: {str(e)}"
            logger.error(f"Metrics collect failed: {error_msg}")
            return self._build_error_result(error_msg, metrics_url, None)

    def _do_collect_health(self) -> Dict[str, Any]:
        if self.infer_tls_config and self.infer_tls_config.enable_tls:
            health_url = f"https://{self.host}:{self.port}/health"
        else:
            health_url = f"http://{self.host}:{self.port}/health"
        logger.debug(f"Start collecting vLLM health from {health_url}")
        try:
            address = f"{self.host}:{self.port}"
            with SafeHTTPSClient(timeout=self.timeout, address=address, tls_config=self.infer_tls_config) as client:
                response = client.do_get("/health")
                if response.status_code == 200:
                    return {
                        "api_url": health_url,
                        "status": "success",
                        "data": None,
                        "collect_time": int(time.time() * 1000),
                        "http_status_code": 200
                    }
                else:
                    error_msg = f"Health check failed with HTTP status code: {response.status_code}"
                    logger.error(f"Health collect failed: {error_msg}")
                    return self._build_error_result(error_msg, health_url, response.status_code)

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            error_msg = f"Connect failed: {str(e)}"
            logger.error(f"Health collect failed: {error_msg}")
            return self._build_error_result(error_msg, health_url, None)

        except requests.exceptions.RequestException as e:
            error_msg = f"Request failed: {str(e)}"
            logger.error(f"Health collect failed: {error_msg}")
            return self._build_error_result(error_msg, health_url, None)
