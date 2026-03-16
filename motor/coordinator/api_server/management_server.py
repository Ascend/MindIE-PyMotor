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
Management plane: runs in the dedicated Mgmt process only (spawned by CoordinatorDaemon via MgmtProcessManager).
Provides readiness, liveness, metrics, instances/refresh.
Does not create or start inference Workers; those are started by CoordinatorDaemon via InferenceProcessManager.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import PlainTextResponse

from motor.common.resources.http_msg_spec import InsEventMsg
from motor.common.utils.cert_util import CertUtil
from motor.common.utils.logger import get_logger
from motor.common.utils.security_utils import sanitize_error_message, log_audit_event
from motor.config.coordinator import CoordinatorConfig, DeployMode
from motor.coordinator.metrics.metrics_collector import MetricsCollector
from motor.coordinator.models.response import RequestResponse
from motor.coordinator.api_server.base_server import BaseCoordinatorServer
from motor.coordinator.scheduler.runtime import SchedulerConnectionManager
from motor.coordinator.api_server.app_builder import AppBuilder
from motor.coordinator.domain import InstanceReadiness
from motor.coordinator.domain.instance_manager import InstanceManager, TYPE_MGMT
from motor.coordinator.domain.probe import (
    DaemonLivenessProvider,
    LivenessProbe,
    LivenessResult,
    ReadinessProbe,
    ReadinessResult,
    RoleShmDaemonLivenessProvider,
)
logger = get_logger(__name__)

# Readiness 503: result -> HTTP detail.
_READINESS_503: dict[ReadinessResult, str] = {
    ReadinessResult.DAEMON_EXITED: "Coordinator daemon has exited; not ready",
    ReadinessResult.HEARTBEAT_STALE: "Coordinator daemon heartbeat stale; not ready",
    ReadinessResult.NOT_MASTER: "Coordinator is not master",
}

# Request body limits for /instances/refresh
_MAX_REQUEST_BODY_SIZE = 10 * 1024 * 1024  # 10MB
_REQUEST_BODY_PREVIEW_LENGTH = 200


def _build_ok_response(message: str) -> dict[str, str]:
    return {"status": "ok", "message": message}


def _build_readiness_response(message: str, ready: bool) -> dict[str, Any]:
    return {"status": "ok", "message": message, "ready": ready}

INSTANCE_REFRESH = "instance_refresh"
INSTANCE_REFRESH_URL = "/instances/refresh"


class ManagementServer(BaseCoordinatorServer):
    """
    Management plane: runs in the Mgmt process only (spawned by MgmtProcessManager); does not start inference Workers.
    """

    def __init__(
        self,
        config: CoordinatorConfig | None = None,
        instance_manager: InstanceManager | None = None,
        daemon_pid: int | None = None,
        daemon_liveness: DaemonLivenessProvider | None = None,
    ):
        super().__init__(config)
        self._mgmt_ssl_config = self.coordinator_config.mgmt_tls_config
        self._daemon_liveness = daemon_liveness or RoleShmDaemonLivenessProvider(
            daemon_pid=daemon_pid,
        )
        self._liveness_probe = LivenessProbe(self._daemon_liveness)
        # Create dependencies before app so lifespan and routes see them (lifespan runs on uvicorn start)
        self._scheduler_connection = SchedulerConnectionManager.from_config(self.coordinator_config)
        self._instance_manager = (
            instance_manager if instance_manager is not None
            else InstanceManager(self.coordinator_config, TYPE_MGMT)
        )
        deploy_mode = (
            self.coordinator_config.scheduler_config.deploy_mode
            if (self.coordinator_config and self.coordinator_config.scheduler_config)
            else DeployMode.PD_SEPARATE
        )
        self._readiness_probe = ReadinessProbe(
            self._daemon_liveness,
            self._instance_manager,
            deploy_mode=deploy_mode,
            enable_master_standby=self.coordinator_config.standby_config.enable_master_standby,
        )
        self._app_builder = AppBuilder(self.coordinator_config)
        self.management_app = self._app_builder.create_management_app(lifespan=self._lifespan)
        self._register_routes()

    @property
    def instance_manager(self) -> InstanceManager:
        """Public accessor for Mgmt process InstanceManager (G.CLS.11: avoid protected access)."""
        return self._instance_manager

    @instance_manager.setter
    def instance_manager(self, value: InstanceManager) -> None:
        """Allow tests to inject a custom instance manager."""
        self._instance_manager = value
        self._readiness_probe.instance_manager = value

    @property
    def lifespan(self):
        """Public accessor for lifespan context manager (G.CLS.11: avoid protected access)."""
        return self._lifespan

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI):
        logger.info("Management server is starting...")
        await self._scheduler_connection.connect()
        try:
            MetricsCollector().set_event_loop(asyncio.get_running_loop())
            MetricsCollector().set_scheduler_provider(lambda: self._instance_manager)
            MetricsCollector().start()
        except Exception as e:
            logger.warning("Ignored error setting metrics collector: %s", e)
        try:
            yield
        except asyncio.CancelledError:
            logger.info("Management server startup was cancelled")
        except Exception as e:
            logger.error("Management server startup failed: %s", e)
            raise
        finally:
            logger.info("Management server is shutting down...")
            try:
                MetricsCollector().stop()
            except Exception as e:
                logger.warning("Ignored error stopping metrics collector: %s", e)
            await self._scheduler_connection.disconnect()

    async def run(self) -> None:
        """Run uvicorn on management port only; does not create or start inference Workers."""
        mgmt_config_kwargs = self.create_base_uvicorn_config(
            self.management_app,
            self.coordinator_config.http_config.coordinator_api_host,
            self.coordinator_config.http_config.coordinator_api_mgmt_port,
        )
        self.apply_timeout_to_config(mgmt_config_kwargs)
        mgmt_config = uvicorn.Config(**mgmt_config_kwargs)
        mgmt_config.load()
        if self._mgmt_ssl_config and self._mgmt_ssl_config.enable_tls:
            mgmt_ssl_context = CertUtil.create_ssl_context(tls_config=self._mgmt_ssl_config)
            if mgmt_ssl_context:
                mgmt_config.ssl = mgmt_ssl_context
        mgmt_server = uvicorn.Server(mgmt_config)
        await mgmt_server.serve()

    def _apply_config_changes(self, new_config: CoordinatorConfig) -> None:
        """Apply Mgmt-specific config changes."""
        self._mgmt_ssl_config = new_config.mgmt_tls_config

    def _log_configuration(self) -> None:
        super()._log_configuration()
        logger.info(
            "Mgmt SSL configuration: enable_tls=%s",
            self.coordinator_config.mgmt_tls_config.enable_tls,
        )
        if self.coordinator_config.mgmt_tls_config.enable_tls:
            logger.info(
                "Mgmt SSL: cert_file=%s, key_file=%s, ca_file=%s",
                self.coordinator_config.mgmt_tls_config.cert_file,
                self.coordinator_config.mgmt_tls_config.key_file,
                self.coordinator_config.mgmt_tls_config.ca_file,
            )

    def _register_routes(self) -> None:
        @self.management_app.get("/startup")
        async def startup_probe():
            logger.debug("Received startup probe request")
            return _build_ok_response("Coordinator is starting up")

        @self.management_app.get("/liveness")
        async def liveness_check():
            result = self._liveness_probe.check()
            if result == LivenessResult.OK:
                logger.debug("Received liveness check request, Coordinator is alive")
                return _build_ok_response("Coordinator is alive")
            if result == LivenessResult.DAEMON_EXITED:
                logger.warning(
                    "[Liveness] Daemon has exited (Mgmt orphaned), failing liveness for pod restart",
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Coordinator daemon has exited; liveness failed for pod restart",
                )
            logger.warning(
                "[Liveness] Daemon heartbeat stale, failing liveness for pod restart",
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Coordinator daemon heartbeat stale; liveness failed for pod restart",
            )

        @self.management_app.get("/readiness")
        async def readiness_check(request: Request):
            # Note: If this returns ready=False (e.g. no required instances), K8s removes the pod from
            # the Service. Then the controller's POST /instances/refresh cannot reach this pod (deadlock).
            logger.info("[Readiness] Probe received")
            try:
                out = await self._readiness_probe.check()
            except HTTPException:
                raise
            except Exception as e:
                logger.exception("[Readiness] Probe failed: %s", e)
                raise e from e

            logger.debug(
                "[Standby] Readiness: result=%s is_ready=%s instances_status=%s",
                out.result.value,
                out.is_ready,
                out.instance_readiness.value if out.instance_readiness else None,
            )
            if out.result in _READINESS_503:
                logger.warning("[Readiness] result=%s, returning 503", out.result.value)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=_READINESS_503[out.result],
                )
            msg = (
                "Coordinator is master"
                if out.result == ReadinessResult.OK_MASTER
                else "Coordinator is ok"
            )
            logger.info(
                "[Readiness] is_ready=%s instances_status=%s -> 200",
                out.is_ready,
                out.instance_readiness.value if out.instance_readiness else None,
            )
            return _build_readiness_response(msg, out.is_ready)

        @self.management_app.get("/metrics")
        async def get_metrics():
            metrics = MetricsCollector().prometheus_metrics_handler()
            return PlainTextResponse(content=metrics)

        @self.management_app.get("/instance/metrics")
        async def get_instance_metrics():
            return MetricsCollector().prometheus_instance_metrics_handler()

        @self.management_app.post("/instances/refresh", response_model=RequestResponse)
        @self.timeout_handler()
        async def refresh_instances(request: Request) -> RequestResponse:
            try:
                result = await self._handle_refresh_instances(request)
                log_audit_event(
                    request=request,
                    event_type=INSTANCE_REFRESH,
                    resource_name=INSTANCE_REFRESH_URL,
                    event_result="success",
                )
                return result
            except Exception as e:
                log_audit_event(
                    request=request,
                    event_type=INSTANCE_REFRESH,
                    resource_name=INSTANCE_REFRESH_URL,
                    event_result=f"failed: {sanitize_error_message(str(e))[:100]}",
                )
                raise

        @self.management_app.get("/")
        async def root():
            return {
                "service": "Motor Coordinator Management Server",
                "version": "1.0.0",
                "description": "Management plane: liveness, startup, readiness, metrics, instance refresh",
                "endpoints": {
                    "GET /liveness": "liveness check",
                    "GET /startup": "startup probe",
                    "GET /readiness": "readiness check",
                    "GET /metrics": "get metrics",
                    "GET /instance/metrics": "get instance metrics",
                    "POST /instances/refresh": "refresh instances",
                },
            }

    async def _handle_refresh_instances(self, request: Request) -> RequestResponse:
        try:
            raw_body = await request.body()
            if not raw_body:
                logger.error("Request body is empty")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Request body cannot be empty",
                )
            if len(raw_body) > _MAX_REQUEST_BODY_SIZE:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "Request body size exceeds maximum allowed size of "
                        f"{_MAX_REQUEST_BODY_SIZE // (1024 * 1024)}MB"
                    ),
                )
            body = json.loads(raw_body.decode("utf-8"))
            if not body:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Request body cannot be empty",
                )
        except HTTPException:
            raise
        except json.JSONDecodeError as e:
            logger.error("Failed to parse request body as JSON: %s", e)
            preview = (
                raw_body.decode("utf-8", errors="ignore")[:_REQUEST_BODY_PREVIEW_LENGTH]
                if raw_body else "empty"
            )
            logger.error("Request body (first %s chars): %s", _REQUEST_BODY_PREVIEW_LENGTH, preview)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSON format: {str(e)}",
            ) from e
        except Exception as e:
            logger.error("Failed to parse request body: %s, type: %s", e, type(e))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to parse request body: {str(e)}",
            ) from e

        try:
            event_msg = InsEventMsg(**body)
        except Exception as e:
            body_keys = list(body.keys()) if isinstance(body, dict) else "not a dict"
            logger.error("Failed to parse InsEventMsg: %s, body keys: %s", e, body_keys)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid request format: {str(e)}",
            ) from e

        await self._scheduler_connection.ensure_connected()
        client = self._scheduler_connection.get_client()
        if client is not None:
            await client.refresh_instances(event_msg.event, event_msg.instances)
        await self._instance_manager.refresh_instances(event_msg.event, event_msg.instances)

        return RequestResponse(
            request_id="refresh_request",
            status="success",
            message="Instance refresh completed",
            data={
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event_type": event_msg.event.value,
                "instance_count": len(event_msg.instances),
            },
        )
