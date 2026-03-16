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
from typing import Any, Callable
from functools import wraps

import uvicorn
from fastapi import FastAPI, Request, HTTPException

from motor.common.resources import RegisterMsg, ReregisterMsg, HeartbeatMsg, TerminateInstanceMsg
from motor.common.standby.standby_manager import StandbyManager, StandbyRole
from motor.common.utils.cert_util import CertUtil
from motor.common.utils.logger import get_logger, ApiAccessFilter
from motor.common.utils.http_response import format_success_response, raise_internal_error
from motor.common.alarm.record import Record
from motor.config.controller import ControllerConfig
from motor.controller.api_client import NodeManagerApiClient
from motor.controller.observability.observability import Observability
from motor.controller.core.instance_assembler import InstanceAssembler
from motor.controller.core.instance_manager import InstanceManager
from motor.controller.observability.inventory.inventory_collector import InventoryCollector

logger = get_logger(__name__)


def observability_enabled_required(func: Callable):
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        if not self.enable_observability_api:
            return raise_internal_error(
                message="Observability is not enabled."
            )
        return await func(self, *args, **kwargs)
    return wrapper


class ControllerAPI:
    def __init__(self, config: ControllerConfig | None = None, modules: dict[str, Any] | None = None,
                 host: str = None, port: int = None):
        if config is None:
            config = ControllerConfig()

        self.observability = Observability()
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

        # Observability API configuration
        self.enable_observability_api = config.observability_config.observability_enable
        self.observability_api_host = host if host is not None else config.api_config.controller_api_host
        self.observability_api_port = config.api_config.observability_api_port
        self.observability_tls_config = config.observability_tls_config
        self.observability_server = None
        self.observability_loop = None
        self.observability_app = self._create_observability_app()
        self.observability_api_server_thread = None

        logger.info("ControllerAPI initialized.")

    def start(self) -> None:
        # Create API server thread
        self.api_server_thread = threading.Thread(
            target=self._run_api_server,
            daemon=True,
            name="APIServer"
        )
        self.api_server_thread.start()

        # Observability API startup logic merging
        self.observability_api_server_thread = threading.Thread(
            target=self._run_observability_api_server,
            daemon=True,
            name="ObservabilityAPIServer"
        )
        self.observability_api_server_thread.start()
        logger.info("Observability API started successfully. Host: %s, Port: %d", 
                self.observability_api_host, self.observability_api_port)

        logger.info("ControllerAPI started.")

    def is_alive(self) -> bool:
        """Check if the API server thread is alive"""
        # Controller API server status
        controller_alive = self.api_server_thread is not None and self.api_server_thread.is_alive()
        
        # check observability API server status
        observability_alive = (self.observability_api_server_thread is not None 
                            and self.observability_api_server_thread.is_alive())        
        return controller_alive and observability_alive

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

        # stop observability API server
        if self.observability_server and self.observability_loop:
            try:
                future = asyncio.run_coroutine_threadsafe(self.observability_server.shutdown(), self.observability_loop)
                future.result(timeout=3)
                logger.info("Observability API server stopped gracefully")
            except Exception as e:
                logger.error("Error stopping Observability API server: %s", e)
                if self.observability_loop and not self.observability_loop.is_closed():
                    self.observability_loop.call_soon_threadsafe(self.observability_loop.stop)

    def update_config(self, config: ControllerConfig) -> None:
        """Update configuration for the controller API"""
        # Note: API server configuration cannot be updated while running
        # Only update the extracted config fields for future use
        with self.config_lock:
            self.enable_master_standby = config.standby_config.enable_master_standby
            self.mgmt_tls_config = config.mgmt_tls_config

            # Observability API configuration update
            self.enable_observability_api = config.observability_config.observability_enable
            self.observability_tls_config = config.observability_tls_config

            logger.info("ControllerAPI configuration updated (runtime changes may require restart)")

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI):
        logger.info("API server startup started")
        yield
        logger.info("API server shutdown completed")

    # Observability API lifecycle management
    @asynccontextmanager
    async def _observability_api_lifespan(self, app: FastAPI):
        logger.info("Observability API server startup started")
        yield
        logger.info("Observability API server shutdown completed")

    @observability_enabled_required
    async def _get_inventory(self) -> dict[str, Any]:
        try:
            return format_success_response(InventoryCollector().collect_inventory())

        except Exception as e:
            logger.error("Failed to get inventory: %s", e)
            raise_internal_error(f"Internal server error: {str(e)}")

    @observability_enabled_required
    async def _get_metrics(self) -> dict[str, Any]:
        try:
            metrics_data = self.observability.get_metrics()	 
            return format_success_response(metrics_data)

        except Exception as e:
            logger.error("Failed to get metrics: %s", e)
            raise_internal_error(f"Internal server error: {str(e)}")

    @observability_enabled_required
    async def _get_alarms(self, request: Request) -> dict[str, Any]:
        try:
            source_id = request.query_params.get("source_id", None)	 
            alarms = self.observability.get_alarms(source_id=source_id)	 

            return format_success_response({ 
                "total": len(alarms), 
                "alarms": alarms, 
            })
        except Exception as e:
            logger.error("Failed to get alarms: %s", e)
            raise_internal_error(f"Internal server error: {str(e)}")

    def _create_app(self) -> FastAPI:
        app = FastAPI(lifespan=self._lifespan)

        # Apply filter to suppress access logs for specified APIs unless level >= configured level
        api_filters = {
            "/controller/heartbeat": logging.ERROR,
            "/controller/register": logging.INFO,
            "/controller/reregister": logging.INFO,
            "/controller/terminate_instance": logging.INFO,
            "/observability/add_alarm": logging.INFO,
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

        app.add_api_route("/observability/add_alarm", self._add_alarm, methods=post_methods)

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

    async def _add_alarm(self, request: Request) -> dict:	 
        body = await request.json()	 
        try:	 
            if not self.enable_observability_api: 
                return format_success_response( 
                    message="OM is not enabled." 
                ) 
            record = Record(**body) 
            self.observability.add_alarm(record)
            return format_success_response()
        except Exception as e:
            logger.error("Failed to add alarms: %s", e)
            raise_internal_error(f"Internal server error: {str(e)}")

    def _create_observability_app(self) -> FastAPI:
        app = FastAPI(lifespan=self._observability_api_lifespan)

        # Apply filter to suppress access logs for specified APIs unless level >= configured level
        api_filters = {
            "/observability/inventory": logging.ERROR,
            "/observability/metrics": logging.ERROR,
            "/observability/alarms": logging.ERROR,
        }
        logging.getLogger("uvicorn.access").addFilter(ApiAccessFilter(api_filters))
        
        # Register middleware check role 
        app.middleware("http")(self._master_standby_middleware)

        # Register observability routes
        get_methods = ["GET"]
        app.add_api_route("/observability/inventory", self._get_inventory, methods=get_methods)
        app.add_api_route("/observability/metrics", self._get_metrics, methods=get_methods)
        app.add_api_route("/observability/alarms", self._get_alarms, methods=get_methods)

        return app

    async def _master_standby_middleware(self, request: Request, call_next):	 
        # if enable master/standby and is standby role then raise exception 
        if self.enable_master_standby and not StandbyManager().is_master(): 
            # raise exception at the middleware layer is an incorrect way. It is better to construct the response. 
            raise_internal_error("This controller is not master") 
        # master continue 
        response: Response = await call_next(request) 
        return response

    def _run_observability_api_server(self) -> None:
        try:
            server_config = uvicorn.Config(
                self.observability_app, 
                host=self.observability_api_host, 
                port=self.observability_api_port, 
                log_level="info"
            )
            if self.observability_tls_config.enable_tls:
                server_config.load()
                context = CertUtil.create_ssl_context(self.observability_tls_config)
                if not context:
                    raise RuntimeError("Failed to create SSL context")

                server_config.ssl = context
                logger.info(f"Starting observability API server on https://"
                            f"{self.observability_api_host}:{self.observability_api_port}")
            else:
                logger.info(f"Starting observability API server on http://"
                            f"{self.observability_api_host}:{self.observability_api_port}")
  
            self.observability_server = uvicorn.Server(server_config)
            self.observability_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.observability_loop)
            self.observability_loop.run_until_complete(self.observability_server.serve())

        except Exception as e:
            logger.error("Observability API server error: %s", e)
        finally:
            if self.observability_loop and not self.observability_loop.is_closed():
                self.observability_loop.close()