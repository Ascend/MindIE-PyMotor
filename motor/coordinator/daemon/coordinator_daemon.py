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
CoordinatorDaemon: unified process management for Mgmt, Scheduler, Infer.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time

from motor.config.coordinator import CoordinatorConfig
from motor.coordinator.daemon.subprocess_supervisor import SubprocessSupervisor
from motor.coordinator.process.base import BaseProcessManager
from motor.coordinator.process.constants import (
    PROCESS_KEY_INFERENCE,
    PROCESS_KEY_MGMT,
    PROCESS_KEY_SCHEDULER,
    STOP_ORDER,
)
from motor.coordinator.process.inference_manager import (
    InferenceProcessManager,
    create_shared_socket,
)
from motor.coordinator.process.mgmt_manager import MgmtProcessManager
from motor.coordinator.process.scheduler_manager import SchedulerProcessManager
from motor.common.standby.daemon_heartbeat_writer import DaemonHeartbeatWriter
from motor.common.standby.standby_manager import StandbyManager
from motor.common.utils.logger import get_logger

logger = get_logger(__name__)


class CoordinatorDaemon:
    """Coordinator daemon: starts and monitors Mgmt / Scheduler / Infer processes.

    With master/standby enabled: Scheduler and Mgmt run on both nodes; only Inference
    is started on master and stopped on standby (on_become_master / on_become_standby).
    """

    def __init__(self, config: CoordinatorConfig):
        self.config = config
        self._process_managers: dict[str, BaseProcessManager] = {}
        self._supervisor: SubprocessSupervisor | None = None
        self._standby_manager: StandbyManager | None = None
        self._heartbeat_writer: DaemonHeartbeatWriter | None = None
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        """Daemon main loop."""
        self._initialize_process_managers()

        # So Mgmt can detect Daemon crash: if getppid() != MOTOR_DAEMON_PID then we're orphaned.
        # Required because in containers Daemon is often PID 1, so getppid()==1 cannot mean "orphaned".
        daemon_pid = os.getpid()
        os.environ["MOTOR_DAEMON_PID"] = str(daemon_pid)
        logger.debug(
            "[Standby] MOTOR_DAEMON_PID set for child processes: daemon_pid=%s",
            daemon_pid,
        )

        # Scheduler always first (both master and standby); Mgmt can then connect to it.
        self._start_processes([PROCESS_KEY_SCHEDULER])

        if self.config.standby_config.enable_master_standby:
            self._standby_manager = StandbyManager(self.config)
            self._standby_manager.start(
                on_become_master=self._start_inference_only,
                on_become_standby=self._stop_inference_only,
            )
            is_master_provider = self._is_master_via_standby
        else:
            self._start_processes([PROCESS_KEY_INFERENCE])
            is_master_provider = None
            # Non-standby: still write heartbeat if configured, so Mgmt can detect Daemon liveness
            sc = self.config.standby_config
            if float(getattr(sc, "role_heartbeat_interval_sec", 0) or 0) > 0:
                self._heartbeat_writer = DaemonHeartbeatWriter(sc)
                self._heartbeat_writer.start()

        # Mgmt always runs last (readiness/metrics; after Scheduler so connect() succeeds).
        self._start_processes([PROCESS_KEY_MGMT])

        self._supervisor = SubprocessSupervisor(
            self._process_managers,
            is_master_provider=is_master_provider,
        )

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(
                    sig,
                    self._on_stop_signal,
                )
            except (ValueError, OSError):
                logger.warning("Cannot add signal handler for %s", sig)

        try:
            await self._supervisor.run(self._stop_event)
        finally:
            self._stop_all_processes()
            if self._standby_manager is not None:
                self._standby_manager.stop()
                logger.info("Standby manager stopped")
            if self._heartbeat_writer is not None:
                self._heartbeat_writer.stop()
                self._heartbeat_writer = None

    def _start_inference_only(self) -> None:
        """Start only Inference (used when becoming master; Scheduler and Mgmt already running)."""
        self._start_processes([PROCESS_KEY_INFERENCE])

    def _stop_inference_only(self) -> None:
        """Stop only Inference (used when becoming standby); keep Mgmt and Scheduler."""
        self._stop_all_processes(
            exclude_processes={PROCESS_KEY_MGMT, PROCESS_KEY_SCHEDULER}
        )

    def _initialize_process_managers(self) -> None:
        """Initialize Mgmt / Scheduler / Infer process managers."""
        self._process_managers[PROCESS_KEY_SCHEDULER] = SchedulerProcessManager(
            self.config
        )

        self._process_managers[PROCESS_KEY_MGMT] = MgmtProcessManager(self.config)

        host = self.config.http_config.coordinator_api_host
        port = self.config.http_config.coordinator_api_infer_port
        sock = create_shared_socket(host, port)
        if sock is not None:
            num_workers = self.config.inference_workers_config.num_workers
            self._process_managers[PROCESS_KEY_INFERENCE] = InferenceProcessManager(
                self.config, (host, port), sock, num_workers
            )
        else:
            logger.warning("Shared socket not available, inference workers disabled")

    def _start_processes(self, names: list[str]) -> None:
        """Start given process managers in order; sleep(2) after Scheduler."""
        for name in names:
            mgr = self._process_managers.get(name)
            if mgr is None:
                continue
            logger.info("Starting %s...", name)
            try:
                started = mgr.start()
                if not started:
                    logger.error("Failed to start %s", name)
                    continue
                logger.info("%s started successfully", name)
            except Exception as e:
                logger.error("Error starting %s: %s", name, e, exc_info=True)
                continue
            if name == PROCESS_KEY_SCHEDULER:
                time.sleep(2)

    def _stop_all_processes(
        self, exclude_processes: set[str] | None = None
    ) -> None:
        """Stop in order: Infer -> Mgmt -> Scheduler. Skip specified processes when exclude is set."""
        exclude = exclude_processes or set()
        for name in STOP_ORDER:
            if name in exclude:
                logger.info("Skipping %s (excluded)", name)
                continue
            mgr = self._process_managers.get(name)
            if mgr is not None and hasattr(mgr, "stop"):
                logger.info("Stopping %s...", name)
                try:
                    mgr.stop()
                except Exception as e:
                    logger.error("Error stopping %s: %s", name, e)
        logger.info("All processes stopped")

    def _on_stop_signal(self) -> None:
        """Handle SIGTERM/SIGINT."""
        logger.info("Received stop signal")
        self._stop_event.set()

    def _is_master_via_standby(self) -> bool:
        """Return whether this node is master (standby mode)."""
        return self._standby_manager.is_master()
