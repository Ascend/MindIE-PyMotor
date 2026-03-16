# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

"""
WorkloadSharedMemoryReader: Worker-side reader for workload shared memory.
"""

import time
from multiprocessing import shared_memory
from typing import Any

from motor.common.resources.instance import PDRole
from motor.common.resources.endpoint import Workload
from motor.common.utils.logger import get_logger
from motor.coordinator.scheduler.runtime.workload_shm.layout import (
    MAGIC,
    HEADER_SIZE,
    ENTRY_SIZE,
    HEARTBEAT_STALE_SEC,
    unpack_header,
    unpack_entry,
    ROLE_PREFILL,
    ROLE_DECODE,
    ROLE_HYBRID,
)

logger = get_logger(__name__)


def _shm_role_to_pdrole(role: int) -> PDRole:
    """Map workload_shm layout role byte to PDRole."""
    if role == ROLE_PREFILL:
        return PDRole.ROLE_P
    if role == ROLE_DECODE:
        return PDRole.ROLE_D
    return PDRole.ROLE_U


class WorkloadSharedMemoryReader:
    """
    Reads workload data from shared memory. Used by Worker process.
    """

    def __init__(self, shm_name: str):
        self._shm_name = shm_name
        self._shm: shared_memory.SharedMemory | None = None
        self._buf: memoryview | None = None
        self._last_heartbeat_value: int = 0
        self._last_heartbeat_time: float = 0.0

    def attach(self) -> None:
        """Attach to existing shared memory."""
        self._shm = shared_memory.SharedMemory(name=self._shm_name, create=False)
        self._buf = memoryview(self._shm.buf)

    def detach(self) -> None:
        """Detach from shared memory. Release buffer before closing shm to avoid BufferError (exported pointers)."""
        if self._shm:
            # Release memoryview first so mmap has no exported pointers when we close.
            self._buf = None
            try:
                self._shm.close()
            except Exception as e:
                logger.warning("WorkloadSharedMemoryReader detach error: %s", e)
            self._shm = None

    def read_and_patch_cache(self, cache: Any) -> tuple[int | None, bool]:
        """
        Read shared memory and patch cache workload.
        Returns (instance_version, heartbeat_stale).
        When heartbeat_stale is True, Scheduler likely restarted; caller should get_available_instances.
        """
        if not self._buf:
            return (None, False)
        try:
            header = unpack_header(self._buf)
            if header.magic != MAGIC:
                return (None, False)
            if header.entry_count < 0 or header.entry_count > header.max_entries:
                logger.debug(
                    "WorkloadSharedMemoryReader invalid entry_count=%s max_entries=%s",
                    header.entry_count, header.max_entries,
                )
                return (None, False)
            required_size = HEADER_SIZE + header.entry_count * ENTRY_SIZE
            if required_size > len(self._buf):
                logger.debug(
                    "WorkloadSharedMemoryReader buf too small need=%s len=%s",
                    required_size, len(self._buf),
                )
                return (None, False)

            now = time.time()
            if header.heartbeat_sequence != self._last_heartbeat_value:
                self._last_heartbeat_value = header.heartbeat_sequence
                self._last_heartbeat_time = now
            # Only treat as stale after we have seen at least one heartbeat update.
            heartbeat_stale = (
                self._last_heartbeat_time > 0
                and (now - self._last_heartbeat_time) > HEARTBEAT_STALE_SEC
            )

            for slot in range(header.entry_count):
                entry = unpack_entry(self._buf, slot)
                pdrole = _shm_role_to_pdrole(entry.role)
                cache.patch_workload_from_shm(
                    entry.instance_id,
                    entry.endpoint_id,
                    pdrole,
                    entry.active_tokens,
                    entry.active_kv_cache,
                )
            return (header.instance_version, heartbeat_stale)
        except Exception as e:
            logger.debug("WorkloadSharedMemoryReader read error: %s", e)
            return (None, False)
