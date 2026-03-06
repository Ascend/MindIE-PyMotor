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

from typing import Dict, Any, List, Optional
import threading

from fastapi import FastAPI, Response
import uvicorn

from motor.common.utils.cert_util import CertUtil
from motor.engine_server.config.base import ServerConfig
from motor.engine_server.constants.constants import (
    STATUS_INTERFACE,
    METRICS_INTERFACE,
    INIT_STATUS,
    FAILED_STATUS,
    ABNORMAL_STATUS,
    NORMAL_STATUS,
    SUCCESS_STATUS,
    STATUS_KEY,
    LATEST_HEALTH,
    LATEST_METRICS,
    CORE_STATUS,
    TEXT_PLAIN,
    DATA_KEY,
    METRICS_SERVICE,
    HEALTH_SERVICE
)
from motor.engine_server.core.service import Service
from motor.common.utils.logger import get_logger
from motor.engine_server.core.sim_inference import SimInference

logger = get_logger("engine_server")


class Endpoint:
    def __init__(self, server_config: ServerConfig, services: dict[str, Service],
                 sim_inference: SimInference | None = None):
        self.host = server_config.server_host
        self.port = server_config.server_port
        self.mgmt_tls_config = server_config.deploy_config.mgmt_tls_config
        for service_key in [METRICS_SERVICE, HEALTH_SERVICE]:
            if service_key not in services:
                raise ValueError(f"services must contain key: {service_key}")
        self.metrics_service = services[METRICS_SERVICE]
        self.health_service = services[HEALTH_SERVICE]
        self.sim_inference = sim_inference
        self.app = FastAPI(title="EngineServer Endpoint")
        self._stop_event = threading.Event()
        self._server: Optional[uvicorn.Server] = None
        self._server_thread = threading.Thread(
            target=self._run_server,
            name="endpoint_server_thread",
            daemon=True
        )

        self._register_routes()

    def run(self):
        if not self._server_thread or not self._server_thread.is_alive():
            self._server_thread.start()

    def shutdown(self):
        if self._server:
            self._server.should_exit = True
            logger.info("Endpoint: Uvicorn server exit triggered")
        self._stop_event.set()
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=5)
            log_msg = "exited" if not self._server_thread.is_alive() else "timeout"
            logger.info(f"Endpoint server thread {log_msg}")
        logger.info("Endpoint server stopped completely")

    def _register_routes(self):
        @self.app.get(STATUS_INTERFACE)
        def get_status(response: Response) -> Dict[str, Any]:
            response.status_code = 200
            server_core_status = INIT_STATUS
            collect_status = INIT_STATUS
            if self.health_service:
                health_data = self.health_service.get_data()
                server_core_status = health_data.get(LATEST_HEALTH, {}).get(CORE_STATUS, INIT_STATUS)
                collect_status = health_data.get(LATEST_HEALTH, {}).get(STATUS_KEY, INIT_STATUS)
            
            if server_core_status == INIT_STATUS:
                logger.debug("Server core is initializing.")
                return {
                    STATUS_KEY: INIT_STATUS
                }
            if collect_status == FAILED_STATUS or server_core_status == ABNORMAL_STATUS:
                return {
                    STATUS_KEY: ABNORMAL_STATUS
                }
            # check sim_inference status
            if self.sim_inference and self.sim_inference.is_abnormal():
                logger.warning("SimInference is in abnormal status, returning ABNORMAL_STATUS")
                return {
                    STATUS_KEY: ABNORMAL_STATUS
                }
            
            return {
                STATUS_KEY: NORMAL_STATUS
                }

        @self.app.get(METRICS_INTERFACE)
        def get_metrics(response: Response):
            metrics_data = {}
            server_core_status = INIT_STATUS
            collect_status = INIT_STATUS
            if self.metrics_service:
                metrics_data = self.metrics_service.get_data()
                server_core_status = metrics_data.get(LATEST_METRICS, {}).get(CORE_STATUS, INIT_STATUS)
                collect_status = metrics_data.get(LATEST_METRICS, {}).get(STATUS_KEY, INIT_STATUS)

            if server_core_status == INIT_STATUS:
                logger.debug("Server core is initializing.")
                return Response(
                    content="",
                    media_type=TEXT_PLAIN,
                    status_code=200
                )
            if collect_status == SUCCESS_STATUS:
                response_data = metrics_data.get(LATEST_METRICS, {}).get(DATA_KEY)
                return Response(
                    content=response_data,
                    media_type=TEXT_PLAIN,
                    status_code=200
                )
            return Response(
                content="",
                media_type=TEXT_PLAIN,
                status_code=200
            )

    def _run_server(self):
        config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
            workers=1
        )

        config.load()
        if self.mgmt_tls_config and self.mgmt_tls_config.enable_tls:
            ssl_context = CertUtil.create_ssl_context(self.mgmt_tls_config)
            if ssl_context:
                config.ssl = ssl_context
            else:
                raise RuntimeError("Failed to create ssl context")
            logger.info(f"Endpoint server started: https://{self.host}:{self.port}")
        else:
            logger.info(f"Endpoint server started: http://{self.host}:{self.port}")

        self._server = uvicorn.Server(config)
        if not self._stop_event.is_set():
            self._server.run()
