#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# See docs/design/role-shm-daemon-owned-refactor.md.

"""
Role shm holder: creates and owns role shared memory and heartbeat thread in Coordinator Daemon.
Daemon writes byte0 in on_role_changed callback (StandbyManager is shm-agnostic); heartbeat (bytes 1-8) is written here.
"""

import struct
import threading
import time
from multiprocessing import shared_memory
from typing import Any

from motor.config.coordinator import ROLE_SHM_MASTER, ROLE_SHM_SIZE
from motor.common.utils.logger import get_logger

logger = get_logger(__name__)


class RoleShmHolder:
    """
    Creates and owns role shm (ROLE_SHM_SIZE bytes). Runs a heartbeat thread that writes bytes 1-8 only.
    Daemon unlinks on stop().
    - When enable_master_standby is False: writes byte0=master (1) once in _write_initial().
    - When enable_master_standby is True: does not write byte0; Daemon writes it in on_role_changed callback.
    """

    def __init__(
        self,
        shm_name: str,
        heartbeat_interval_sec: float,
        *,
        stale_sec: float | None = None,
        initial_role_master: bool = True,
    ) -> None:
        self._shm_name = shm_name
        self._heartbeat_interval_sec = max(0.0, float(heartbeat_interval_sec or 0))
        self._stale_sec = float(stale_sec) if stale_sec is not None else None
        self._initial_role_master = initial_role_master
        self._shm: Any = None
        self._stop_event = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    def start(self) -> bool:
        """
        Create or attach to role shm, write initial role+heartbeat, start heartbeat thread if interval > 0.
        Returns True if shm is available (for passing to StandbyManager).
        """
        if self._shm is not None:
            return True
        try:
            self._shm = shared_memory.SharedMemory(
                name=self._shm_name,
                create=True,
                size=ROLE_SHM_SIZE,
            )
            self._write_initial()
            logger.info(
                "[Standby] Daemon role shm created: name=%s (Mgmt can detect Daemon liveness)",
                self._shm_name,
            )
        except FileExistsError:
            try:
                existing = shared_memory.SharedMemory(name=self._shm_name, create=False)
                size = len(existing.buf)
                if size >= ROLE_SHM_SIZE:
                    self._shm = existing
                    self._write_initial()
                    logger.info(
                        "[Standby] Daemon role shm attached to existing: name=%s size=%s (take over after restart)",
                        self._shm_name,
                        size,
                    )
                else:
                    existing.close()
                    existing.unlink()
                    logger.debug(
                        "[Standby] Removed stale role shm name=%s size=%s, will create new",
                        self._shm_name,
                        size,
                    )
                    self._shm = shared_memory.SharedMemory(
                        name=self._shm_name, create=True, size=ROLE_SHM_SIZE
                    )
                    self._write_initial()
                    logger.info(
                        "[Standby] Daemon role shm created: name=%s",
                        self._shm_name,
                    )
            except Exception as e2:
                logger.warning(
                    "Failed to attach to or recreate role shm %s: %s",
                    self._shm_name,
                    e2,
                )
                self._shm = None
                return False
        except Exception as e:
            logger.warning("Failed to create role shm %s: %s", self._shm_name, e)
            self._shm = None
            return False

        if self._heartbeat_interval_sec <= 0:
            return True
        interval = self._heartbeat_interval_sec
        if self._stale_sec is not None and self._stale_sec > 0 and self._stale_sec < 2 * interval:
            logger.warning(
                "[Standby] role_heartbeat_stale_sec=%.1f < 2*role_heartbeat_interval_sec=%.1f, may cause false 503",
                self._stale_sec,
                interval,
            )
        self._stop_event.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="DaemonRoleHeartbeat",
            daemon=False,
        )
        self._heartbeat_thread.start()
        logger.debug(
            "[Standby] Daemon role shm heartbeat thread started, interval=%.1fs",
            self._heartbeat_interval_sec,
        )
        return True

    def stop(self) -> None:
        """Stop heartbeat thread and close/unlink shm."""
        self._stop_event.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=5.0)
            self._heartbeat_thread = None
        if self._shm is not None:
            try:
                self._shm.close()
                self._shm.unlink()
                logger.debug("[Standby] Daemon role shm unlinked: name=%s", self._shm_name)
            except Exception as e:
                logger.debug("Unlink role shm %s: %s", self._shm_name, e)
            self._shm = None

    def get_shm(self) -> Any:
        """Return the SharedMemory instance for Daemon to write role byte in on_role_changed (read-only reference)."""
        return self._shm

    def _write_initial(self) -> None:
        if self._shm is None:
            return
        try:
            if self._initial_role_master:
                self._shm.buf[0] = ROLE_SHM_MASTER
            # else: byte0 is written only by StandbyManager (sole writer in master/standby mode)
            ns = time.monotonic_ns()
            self._shm.buf[1:ROLE_SHM_SIZE] = struct.pack("<Q", ns)
        except Exception as e:
            logger.warning("Failed to write initial role shm: %s", e)

    def _heartbeat_loop(self) -> None:
        if self._heartbeat_interval_sec <= 0:
            return
        while not self._stop_event.is_set():
            if self._stop_event.wait(timeout=self._heartbeat_interval_sec):
                break
            if self._shm is not None:
                try:
                    ns = time.monotonic_ns()
                    self._shm.buf[1:ROLE_SHM_SIZE] = struct.pack("<Q", ns)
                except Exception as e:
                    logger.warning("Failed to write heartbeat: %s", e)
