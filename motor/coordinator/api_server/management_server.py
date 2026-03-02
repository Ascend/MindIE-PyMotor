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
import struct
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import PlainTextResponse

from motor.common.resources.http_msg_spec import InsEventMsg
from motor.common.standby.standby_manager import StandbyManager, StandbyRole
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
from motor.coordinator.domain.instance_manager import InstanceManager

logger = get_logger(__name__)


class DaemonExitedError(Exception):
    """Raised when Mgmt detects it is orphaned (parent PID is no longer the Daemon)."""


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
    ):
        super().__init__(config)
        self._mgmt_ssl_config = self.coordinator_config.mgmt_tls_config
        # Create dependencies before app so lifespan and routes see them (lifespan runs on uvicorn start)
        self._scheduler_connection = SchedulerConnectionManager.from_config(self.coordinator_config)
        self._main_instance_manager = (
            instance_manager if instance_manager is not None
            else InstanceManager(self.coordinator_config)
        )
        self._app_builder = AppBuilder(self.coordinator_config)
        self.management_app = self._app_builder.create_management_app(lifespan=self._lifespan)
        # When standby is enabled, ensure role_shm_name is set so Mgmt reads from shm (not StandbyManager fallback).
        # Config may arrive with empty role_shm_name (e.g. pickle/reload); match CoordinatorConfig.__post_init__.
        if self.coordinator_config.standby_config.enable_master_standby:
            sc = self.coordinator_config.standby_config
            if not (sc.role_shm_name or "").strip():
                sc.role_shm_name = "coordinator_standby_role"
                logger.info(
                    "[Standby] Mgmt: role_shm_name was empty, set default=%s for readiness",
                    sc.role_shm_name,
                )
            StandbyManager(self.coordinator_config)
        self._register_routes()

    @property
    def instance_manager(self) -> InstanceManager:
        """Public accessor for Mgmt process InstanceManager (G.CLS.11: avoid protected access)."""
        return self._main_instance_manager

    @instance_manager.setter
    def instance_manager(self, value: InstanceManager) -> None:
        """Allow tests to inject a custom instance manager."""
        self._main_instance_manager = value

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
            MetricsCollector().set_scheduler_provider(lambda: self._main_instance_manager)
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

    def _is_daemon_our_parent(self) -> bool:
        """Return False if we were spawned by Daemon but it has exited (we're orphaned). Used for liveness."""
        if os.name == "nt":
            return True
        try:
            daemon_pid = int(os.environ.get("MOTOR_DAEMON_PID", "0") or "0")
        except (ValueError, TypeError):
            return True
        if not daemon_pid:
            return True
        ppid = os.getppid()
        if ppid != daemon_pid:
            return False
        # When Daemon is PID 1 (container entrypoint), after it exits we're reparented to init (also PID 1).
        # So getppid() stays 1 and we cannot tell Daemon from init. Only when daemon_pid != 1 can we rely on ppid.
        if daemon_pid == 1:
            return True
        # Confirm the process at daemon_pid still exists (handles edge cases).
        try:
            os.kill(daemon_pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True  # Process exists but we can't signal (e.g. PermissionError); assume alive
        return True

    def _read_role_and_heartbeat_from_ipc(self) -> tuple[bool, bool]:
        """
        Read role shm and optional heartbeat. Returns (is_master, heartbeat_stale).
        heartbeat_stale is True only when role_heartbeat_stale_sec > 0 and heartbeat not updated in time.
        Strategy 1: heartbeat_ns==0 (old Daemon or not enabled) -> heartbeat_stale=False.
        """
        from multiprocessing import shared_memory as shm_mod

        sc = self.coordinator_config.standby_config
        role_shm = (sc.role_shm_name or "").strip()
        # Use default name only when we need to read shm (standby or heartbeat check).
        # Empty = disable shm (StandbyManager fallback).
        if not role_shm and (
            sc.enable_master_standby
            or float(getattr(sc, "role_heartbeat_stale_sec", 0.0) or 0.0) > 0
        ):
            role_shm = "coordinator_standby_role"
            logger.debug("[Standby] role_shm_name empty at read time, using default=%s", role_shm)
        if role_shm:
            if os.name != "nt":
                try:
                    daemon_pid = int(os.environ.get("MOTOR_DAEMON_PID", "0") or "0")
                except (ValueError, TypeError):
                    daemon_pid = 0
                ppid = os.getppid()
                if daemon_pid and ppid != daemon_pid:
                    logger.warning(
                        "[Standby] Mgmt orphaned (parent not Daemon): ppid=%s daemon_pid=%s",
                        ppid,
                        daemon_pid,
                    )
                    raise DaemonExitedError(
                        "Coordinator daemon has exited; Mgmt process is orphaned"
                    )
                logger.debug(
                    "[Standby] Mgmt parent check: ppid=%s daemon_pid=%s role_shm=%s",
                    ppid,
                    daemon_pid,
                    role_shm,
                )
            try:
                shm = shm_mod.SharedMemory(name=role_shm, create=False)
                try:
                    val = shm.buf[0]
                    is_master = val == 1
                    heartbeat_stale = False
                    stale_sec = float(getattr(sc, "role_heartbeat_stale_sec", 0.0) or 0.0)
                    shm_size = len(shm.buf)
                    if stale_sec > 0 and shm_size >= 9:
                        try:
                            heartbeat_ns = struct.unpack("<Q", bytes(shm.buf[1:9]))[0]
                            if heartbeat_ns > 0:
                                now_ns = time.monotonic_ns()
                                age_ns = now_ns - heartbeat_ns
                                stale_threshold_ns = int(stale_sec * 1e9)
                                # Treat negative age (clock skew) as not stale
                                if age_ns >= 0 and age_ns > stale_threshold_ns:
                                    heartbeat_stale = True
                                    logger.warning(
                                        "[Standby] Daemon heartbeat stale (last_ns=%s age_sec=%.1f "
                                        "stale_threshold_sec=%.1f), failing liveness/readiness",
                                        heartbeat_ns,
                                        age_ns / 1e9,
                                        stale_sec,
                                    )
                                else:
                                    logger.debug(
                                        "[Standby] Heartbeat OK: age_sec=%.1f stale_sec=%.1f",
                                        age_ns / 1e9,
                                        stale_sec,
                                    )
                            # heartbeat_ns==0: strategy 1, do not treat as stale
                        except (struct.error, IndexError, ValueError) as e:
                            logger.debug("[Standby] Heartbeat parse error: %s, not treating as stale", e)
                    elif stale_sec > 0 and shm_size < 9:
                        logger.warning(
                            "[Standby] Role shm size=%s, need 9 for heartbeat; set "
                            "role_heartbeat_interval_sec>0 on Daemon and restart to enable heartbeat detection",
                            shm_size,
                        )
                    logger.debug(
                        "[Standby] Role shm read: name=%s byte=%s is_master=%s heartbeat_stale=%s",
                        role_shm,
                        val,
                        is_master,
                        heartbeat_stale,
                    )
                    return (is_master, heartbeat_stale)
                finally:
                    shm.close()
            except FileNotFoundError:
                logger.debug(
                    "[Standby] Role shm not found: name=%s (Daemon may not have created it yet) -> standby",
                    role_shm,
                )
                return (False, False)
            except Exception as e:
                logger.debug("Read role shm %s failed: %s, treat as standby", role_shm, e)
                return (False, False)
        logger.debug("[Standby] No role_shm_name, using StandbyManager().current_role")
        try:
            is_master = StandbyManager().current_role == StandbyRole.MASTER
            logger.debug("[Standby] StandbyManager current_role -> is_master=%s", is_master)
            return (is_master, False)
        except ValueError:
            logger.debug("[Standby] StandbyManager not initialized -> standby")
            return (False, False)

    def _read_master_standby_role_from_ipc(self) -> bool:
        """Return True if this node is master; delegates to _read_role_and_heartbeat_from_ipc."""
        is_master, _ = self._read_role_and_heartbeat_from_ipc()
        return is_master

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
            # If we were spawned by Daemon and it has exited (orphaned), fail liveness so K8s restarts the pod.
            if not self._is_daemon_our_parent():
                logger.warning(
                    "[Liveness] Daemon has exited (Mgmt orphaned, ppid=%s), failing liveness for pod restart",
                    os.getppid(),
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Coordinator daemon has exited; liveness failed for pod restart",
                )
            # If heartbeat is enabled and Daemon has not refreshed it, treat as dead (e.g. Daemon as PID 1).
            # Applies to both master/standby and non-standby (when Daemon writes heartbeat via DaemonHeartbeatWriter).
            sc = self.coordinator_config.standby_config
            stale_sec = float(getattr(sc, "role_heartbeat_stale_sec", 0.0) or 0.0)
            if stale_sec > 0:
                logger.info(
                    "[Liveness] Checking Daemon heartbeat (role_heartbeat_stale_sec=%.1f)",
                    stale_sec,
                )
                try:
                    _, heartbeat_stale = self._read_role_and_heartbeat_from_ipc()
                except DaemonExitedError as e:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Coordinator daemon has exited; liveness failed for pod restart",
                    ) from e
                if heartbeat_stale:
                    logger.warning(
                        "[Liveness] Daemon heartbeat stale, failing liveness for pod restart",
                    )
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Coordinator daemon heartbeat stale; liveness failed for pod restart",
                    )
            logger.debug("Received liveness check request, Coordinator is alive")
            return _build_ok_response("Coordinator is alive")

        @self.management_app.get("/readiness")
        async def readiness_check(request: Request):
            # Note: If this returns ready=False (e.g. no required instances), K8s removes the pod from
            # the Service. Then the controller's POST /instances/refresh cannot reach this pod (deadlock).
            # Prefer not depending on has_required_instances for management-plane readiness, or use a
            # separate Service for management so instance refresh is always reachable.
            logger.info("[Readiness] Probe received")
            try:
                deploy_mode = (
                    self.coordinator_config.scheduler_config.deploy_mode
                    if self.coordinator_config and self.coordinator_config.scheduler_config
                    else DeployMode.PD_SEPARATE
                )
                readiness = await asyncio.to_thread(
                    self._main_instance_manager.get_required_instances_status, deploy_mode
                )
                # PD mode: ready if has P+D or only P; not ready if only D, none, or unknown
                is_ready = readiness.is_ready() or readiness == InstanceReadiness.ONLY_PREFILL
                if not is_ready:
                    logger.debug(
                        "Received readiness check request, Coordinator is not ready (required instances status: %s)",
                        readiness.value,
                    )
                sc = self.coordinator_config.standby_config
                stale_sec = float(getattr(sc, "role_heartbeat_stale_sec", 0.0) or 0.0)
                need_role_heartbeat_read = sc.enable_master_standby or stale_sec > 0
                if need_role_heartbeat_read:
                    is_master, heartbeat_stale = self._read_role_and_heartbeat_from_ipc()
                    logger.debug(
                        "[Standby] Readiness: is_master=%s heartbeat_stale=%s is_ready=%s instances_status=%s",
                        is_master,
                        heartbeat_stale,
                        is_ready,
                        readiness.value,
                    )
                    if heartbeat_stale:
                        logger.warning(
                            "[Readiness] Daemon heartbeat stale, returning 503 (Daemon unreachable)",
                        )
                        raise HTTPException(
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Coordinator daemon heartbeat stale; not ready",
                        )
                    if sc.enable_master_standby:
                        if is_master:
                            logger.info(
                                "[Readiness] is_master=true is_ready=%s instances_status=%s -> 200",
                                is_ready,
                                readiness.value,
                            )
                            return _build_readiness_response("Coordinator is master", is_ready)
                        logger.info(
                            "[Readiness] is_master=false is_ready=%s instances_status=%s -> 503",
                            is_ready,
                            readiness.value,
                        )
                        raise HTTPException(
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Coordinator is not master",
                        )
                logger.info(
                    "[Readiness] is_ready=%s instances_status=%s -> 200",
                    is_ready,
                    readiness.value,
                )
                return _build_readiness_response("Coordinator is ok", is_ready)
            except DaemonExitedError as e:
                logger.warning("[Readiness] Mgmt orphaned (daemon exited), returning 503")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Coordinator daemon has exited; not ready",
                ) from e
            except HTTPException:
                raise
            except Exception as e:
                logger.exception("[Readiness] Probe failed: %s", e)
                raise e from e

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
        await self._main_instance_manager.refresh_instances(event_msg.event, event_msg.instances)

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
