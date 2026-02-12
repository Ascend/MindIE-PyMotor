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

import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, HTTPException

from motor.common.resources import RegisterMsg, ReregisterMsg, HeartbeatMsg, TerminateInstanceMsg
from motor.common.standby.standby_manager import StandbyManager, StandbyRole
from motor.common.utils.cert_util import CertUtil
from motor.common.utils.logger import get_logger, ApiAccessFilter
from motor.config.controller import ControllerConfig
from motor.controller.api_client import NodeManagerApiClient
from motor.controller.api_server import om_api
from motor.controller.core.instance_assembler import InstanceAssembler
from motor.controller.core.instance_manager import InstanceManager

logger = get_logger(__name__)


def validate_cert_and_key(cert_path: str, key_path: str):
    for path, desc in [(cert_path, 'cert file'), (key_path, 'key file')]:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"{desc} file is not exist: {path}")
        with open(path, 'r') as f:
            first_line = f.readline().strip()
            if not first_line.startswith('-----BEGIN'):
                raise ValueError(f"{desc} file format is nor correct: {path}")


class ControllerAPI:
    def __init__(self, config: ControllerConfig | None = None, modules: dict[str, Any] | None = None,
                 host: str = None, port: int = None):
        if config is None:
            config = ControllerConfig()

        # Extract required config fields for TLS and standby mode
        self.enable_master_standby = config.standby_config.enable_master_standby
        self.mgmt_tls_config = config.mgmt_tls_config

        self.modules = modules
        self.config_lock = threading.RLock()
        self.host = host if host is not None else config.api_config.controller_api_host
        self.port = port if port is not None else config.api_config.controller_api_port
        self.server = None
        self.loop = None
        self.app = self._create_app()
        self.api_server_thread = None
        logger.info("ControllerAPI initialized.")

    def start(self) -> None:
        # Create API server thread
        self.api_server_thread = threading.Thread(
            target=self._run_api_server,
            daemon=True,
            name="APIServer"
        )
        self.api_server_thread.start()
        logger.info("ControllerAPI started.")

    def is_alive(self) -> bool:
        """Check if the API server thread is alive"""
        return self.api_server_thread is not None and self.api_server_thread.is_alive()

    def stop(self) -> None:
        if self.server and self.loop:
            try:
                future = asyncio.run_coroutine_threadsafe(self.server.shutdown(), self.loop)
                future.result(timeout=3)
                logger.info("API server stopped gracefully")
            except Exception as e:
                logger.error("Error stopping server: %s", e)
                if self.loop and not self.loop.is_closed():
                    self.loop.call_soon_threadsafe(self.loop.stop)

    def update_config(self, config: ControllerConfig) -> None:
        """Update configuration for the controller API"""
        # Note: API server configuration cannot be updated while running
        # Only update the extracted config fields for future use
        with self.config_lock:
            self.enable_master_standby = config.standby_config.enable_master_standby
            self.mgmt_tls_config = config.mgmt_tls_config
            logger.info("ControllerAPI configuration updated (runtime changes may require restart)")

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI):
        logger.info("API server startup started")
        yield
        logger.info("API server shutdown completed")

    def _create_app(self) -> FastAPI:
        app = FastAPI(lifespan=self._lifespan)

        # Apply filter to suppress access logs for specified APIs unless level >= configured level
        api_filters = {
            "/controller/heartbeat": logging.ERROR,
            "/controller/register": logging.INFO,
            "/controller/reregister": logging.INFO,
            "/controller/terminate_instance": logging.INFO,
            "/v1/alarm/coordinator": logging.ERROR,
            "/startup": logging.ERROR,
            "/readiness": logging.ERROR,
            "/liveness": logging.ERROR
        }
        logging.getLogger("uvicorn.access").addFilter(ApiAccessFilter(api_filters))

        # Register routes
        post_methods = ["POST"]
        get_methods = ["GET"]
        app.add_api_route("/controller/heartbeat", self._heartbeat, methods=post_methods)
        app.add_api_route("/controller/register", self._register, methods=post_methods)
        app.add_api_route("/controller/reregister", self._reregister, methods=post_methods)
        app.add_api_route("/controller/terminate_instance", self._terminate_instance, methods=post_methods)

        app.add_api_route("/startup", self._startup, methods=get_methods)
        app.add_api_route("/readiness", self._readiness, methods=get_methods)
        app.add_api_route("/liveness", self._liveness, methods=get_methods)
        app.include_router(om_api.router)

        return app

    async def _heartbeat(self, request: Request):
        body = await request.json()
        try:
            hb_msg = HeartbeatMsg(**body)
        except Exception as e:
            logger.error("Failed to parse HeartbeatMsg: %s, body: %s", e, body)
            return {"error": "Invalid HeartbeatMsg format"}
        ret = InstanceManager().handle_heartbeat(hb_msg)
        return {"result": ret}

    async def _register(self, request: Request) -> dict:
        body = await request.json()
        try:
            register_msg = RegisterMsg(**body)
        except Exception as e:
            logger.error("Failed to parse RegisterMsg: %s, body: %s", e, body)
            return {"error": "Invalid RegisterMsg format"}
        ret = InstanceAssembler().register(register_msg)
        if ret == -1:
            return {"error": "Instance already registered"}
        else:
            return {"result": ret}

    async def _reregister(self, request: Request) -> dict:
        body = await request.json()
        try:
            reregister_msg = ReregisterMsg(**body)
        except Exception as e:
            logger.error("Failed to parse ReregisterMsg: %s, body: %s", e, body)
            return {"error": "Invalid ReregisterMsg format"}
        ret = InstanceAssembler().reregister(reregister_msg)
        if ret == -1:
            return {"error": "Instance already registered"}
        else:
            return {"result": ret}

    def _run_api_server(self) -> None:
        try:
            server_config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="info")
            if self.mgmt_tls_config.enable_tls:
                server_config.load()
                context = CertUtil.create_ssl_context(self.mgmt_tls_config)
                if not context:
                    raise RuntimeError("Failed to create SSL context")

                server_config.ssl = context
                logger.info(f"Starting Controller API server on https://{self.host}:{self.port}")
            else:
                logger.info(f"Starting Controller API server on http://{self.host}:{self.port}")

            self.server = uvicorn.Server(server_config)
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.server.serve())
        except Exception as e:
            logger.error("API server error: %s", e)
        finally:
            if self.loop and not self.loop.is_closed():
                self.loop.close()

    async def _terminate_instance(self, request: Request) -> dict:
        body = await request.json()
        try:
            terminate_instance_msg = TerminateInstanceMsg(**body)
        except Exception as e:
            logger.error("Failed to parse TerminateInstanceMsg: %s, body: %s", e, body)
            return {"error": "Invalid TerminateInstanceMsg format"}
        logger.warning("Terminate instance, reason: %s", terminate_instance_msg.reason)
        instance = InstanceManager().get_instance(terminate_instance_msg.instance_id)

        if instance is None:
            logger.error("Instance %d not found.", terminate_instance_msg.instance_id)
            return {"error": "Instance not found"}

        for node_mgr in instance.get_node_managers():
            NodeManagerApiClient.stop(node_mgr)
        return {"result": "Terminate instance succeed!"}

    async def _readiness(self) -> dict:
        """
        Readiness probe - returns result base on deploy mode and role:

        STANDALONE: returns 200 if overall healthy.
                    Otherwise, returns 503.

        MASTER_STANDBY: returns 200 only when role is master and overall healthy.
                        Otherwise, returns 503.

        """
        status = self._get_controller_status()
        msg = "message"
        reason = "reason"
        if status.get("overall_healthy") is False:
            raise HTTPException(
                status_code=503,
                detail={
                    msg: "Controller is not ready",
                    reason: "Overall not healthy"
                }
            )

        if status.get("deploy_mode") == "master_standby":
            if status.get("role") != StandbyRole.MASTER.value:
                raise HTTPException(
                    status_code=503,
                    detail={
                        msg: "Controller is not ready",
                        reason: "Not master"
                    }
                )
        return {msg: "Controller is ready"}

    def _get_controller_status(self) -> dict:
        """
        Get controller status including:
        - deploy mode: "master_standby" or "standalone"
        - role(Optional): "master" or "standby"
        - overall health of all modules
        """
        status = {}

        # Set deploy mode and role
        with self.config_lock:
            enable_master_standby = self.enable_master_standby
        if enable_master_standby:
            status["deploy_mode"] = "master_standby"
            # Get singleton instance (assumes it has been initialized)
            status["role"] = "master" if StandbyManager().is_master() else "standby"
        else:
            status["deploy_mode"] = "standalone"

        # Check module health
        # In master_standby mode, standby node doesn't run modules, so don't check health
        if enable_master_standby and not StandbyManager().is_master():
            # Standby node: modules are not running, but this is expected
            status["overall_healthy"] = True
        else:
            unhealthy_modules = []
            for name, module in self.modules.items():
                if not hasattr(module, 'is_alive'):
                    continue
                alive = module.is_alive()
                if not alive:
                    unhealthy_modules.append(name)

            if unhealthy_modules:
                status["overall_healthy"] = False
                logger.error("Unhealthy modules: %s", unhealthy_modules)
            else:
                status["overall_healthy"] = True

        return status

    async def _startup(self) -> dict:
        return {"message": "Controller startup"}

    async def _liveness(self) -> dict:
        """Liveness probe - returns 200 as long as the process is running"""
        status = self._get_controller_status()

        # For liveness, we just check if the process is responsive
        # Even standby controllers should be considered alive
        if status.get("overall_healthy") is False:
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "Controller is not alive",
                    "reason": "Overall not healthy"
                }
            )
        else:
            return {"message": "Controller is alive"}
