# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import asyncio
import threading
import time
import uuid
from motor.config.coordinator import CoordinatorConfig
from motor.coordinator.models.request import RequestInfo
from motor.common.resources.endpoint import Workload
from motor.common.resources.instance import PDRole
from motor.common.utils.logger import get_logger

logger = get_logger(__name__)


class RequestManager:
    """
    Request/workload state. Hot-path methods use asyncio.Lock to avoid blocking the event loop.
    """

    def __init__(self, config: CoordinatorConfig | None = None):
        if config is None:
            config = CoordinatorConfig()
        self._rate_limit_config = config.rate_limit_config
        self._config_lock = threading.RLock()

        # Counter and req/workload dicts: asyncio.Lock so hot path does not block event loop
        self._counter = 0
        self._last_timestamp = 0
        self._lock = asyncio.Lock()

        self._req_info_dict: dict[str, RequestInfo] = {}

        # Request workload dictionary: {(req_id, role) -> Workload}
        # Used to track workload allocation for each request and role
        self._req_workload_dict: dict[tuple[str, PDRole], Workload] = {}
        logger.info("RequestManager initialized")

    async def generate_request_id(self) -> str:
        """
        Generate globally unique request ID (async, does not block event loop).
        Returns: Pure ID string in format: timestamp(16 digits) + counter(4 digits) + random(8 chars)
        """
        try:
            async with self._lock:
                current_timestamp = int(time.time() * 1000000)
                if current_timestamp == self._last_timestamp:
                    self._counter += 1
                else:
                    self._counter = 0
                    self._last_timestamp = current_timestamp
                counter_part = f"{self._counter:04d}"
            random_suffix = uuid.uuid4().hex[:8]
            request_id = f"{current_timestamp}{counter_part}{random_suffix}"
            logger.info("Generated request ID: %s", request_id)
            return request_id
        except Exception as e:
            logger.error("Failed to generate request ID: %s", e, exc_info=True)
            return uuid.uuid4().hex

    async def get_req_info(self, req_id: str) -> RequestInfo | None:
        """Get request info by req_id (async, does not block event loop)."""
        async with self._lock:
            return self._req_info_dict.get(req_id)

    async def add_req_info(self, req_info: RequestInfo) -> bool:
        try:
            async with self._lock:
                if req_info.req_id in self._req_info_dict:
                    logger.debug("Request ID %s already exists", req_info.req_id)
                    return False
                self._req_info_dict[req_info.req_id] = req_info
            logger.debug("Added request info for ID: %s", req_info.req_id)
            return True
        except Exception as e:
            logger.error("Failed to add request info for ID %s: %s", req_info.req_id, e)
            return False

    async def del_req_info(self, req_id: str) -> bool:
        try:
            async with self._lock:
                if req_id not in self._req_info_dict:
                    logger.debug("Request ID %s not found for deletion", req_id)
                    return False
                del self._req_info_dict[req_id]
                keys_to_delete = [k for k in self._req_workload_dict if k[0] == req_id]
                for k in keys_to_delete:
                    del self._req_workload_dict[k]
            logger.debug("Deleted request info and workloads for ID: %s", req_id)
            return True
        except Exception as e:
            logger.error("Failed to delete request info for ID %s: %s", req_id, e)
            return False

    # ==================== Workload Management (async, hot path) ====================

    async def add_req_workload(self, req_id: str, role: PDRole, workload: Workload) -> bool:
        """Add workload record for a request and role (async, does not block event loop)."""
        try:
            async with self._lock:
                key = (req_id, role)
                if key in self._req_workload_dict:
                    logger.debug("Workload for request %s, role %s already exists", req_id, role)
                    return False
                self._req_workload_dict[key] = workload
            logger.debug("Added workload for request %s, role %s", req_id, role)
            return True
        except Exception as e:
            logger.error("Failed to add workload for request %s, role %s: %s", req_id, role, e)
            return False

    async def get_req_workload(self, req_id: str, role: PDRole) -> Workload | None:
        async with self._lock:
            return self._req_workload_dict.get((req_id, role))

    async def update_req_workload(self, req_id: str, role: PDRole, workload: Workload) -> bool:
        try:
            async with self._lock:
                key = (req_id, role)
                if key not in self._req_workload_dict:
                    logger.debug("Workload for request %s, role %s not found", req_id, role)
                    return False
                self._req_workload_dict[key] = workload
            logger.debug("Updated workload for request %s, role %s", req_id, role)
            return True
        except Exception as e:
            logger.error("Failed to update workload for request %s, role %s: %s", req_id, role, e)
            return False

    async def del_req_workload(self, req_id: str, role: PDRole) -> bool:
        try:
            async with self._lock:
                key = (req_id, role)
                if key not in self._req_workload_dict:
                    logger.debug("Workload for request %s, role %s not found", req_id, role)
                    return False
                del self._req_workload_dict[key]
            logger.debug("Deleted workload for request %s, role %s", req_id, role)
            return True
        except Exception as e:
            logger.error("Failed to delete workload for request %s, role %s: %s", req_id, role, e)
            return False

    def update_config(self, config: CoordinatorConfig) -> None:
        """Update configuration for the request manager"""
        with self._config_lock:
            self._rate_limit_config = config.rate_limit_config
        logger.info("RequestManager configuration updated")
