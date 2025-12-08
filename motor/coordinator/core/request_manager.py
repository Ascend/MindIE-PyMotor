#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import time
import uuid
import threading
from motor.common.utils.singleton import ThreadSafeSingleton
from motor.coordinator.models.request import RequestInfo
from motor.common.utils.logger import get_logger

logger = get_logger(__name__)


class RequestManager(ThreadSafeSingleton):

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
            
        # Thread-safe counter state
        self._counter = 0
        self._last_timestamp = 0
        self._lock = threading.Lock()  # Protects counter state
        
        self._req_info_dict: dict[str, RequestInfo] = {}
        
        self._initialized = True
        logger.info("RequestManager initialized")

    def generate_request_id(self) -> str:
        """
        Generate globally unique request ID
        
        Returns:
            Pure ID string in format: timestamp(16 digits) + counter(4 digits) + random(8 chars)
        """
        try:
            # Microsecond precision timestamp
            current_timestamp = int(time.time() * 1000000)
            random_suffix = uuid.uuid4().hex[:8]
            
            with self._lock:
                if current_timestamp == self._last_timestamp:
                    self._counter += 1
                else:
                    self._counter = 0
                    self._last_timestamp = current_timestamp
                
                # Format: timestamp + counter(4 digits) + random(8 chars)
                counter_part = f"{self._counter:04d}"
                request_id = f"{current_timestamp}{counter_part}{random_suffix}"
            
            logger.info(f"Generated request ID: {request_id}")
            return request_id
            
        except Exception as e:
            logger.error(f"Failed to generate request ID: {e}")
            # Emergency fallback
            return uuid.uuid4().hex

    def get_req_info(self, req_id: str) -> RequestInfo:
        return self._req_info_dict.get(req_id)

    def add_req_info(self, req_info: RequestInfo) -> bool:
        try:
            with self._lock:
                if req_info.req_id in self._req_info_dict:
                    logger.warning(f"Request ID {req_info.req_id} already exists")
                    return False

                self._req_info_dict[req_info.req_id] = req_info
            logger.debug("Added request info for ID: %s", {req_info.req_id})
            return True
        except Exception as e:
            logger.error(f"Failed to add request info for ID {req_info.req_id}: {e}")
            return False

    def del_req_info(self, req_id: str) -> bool:
        try:
            with self._lock:
                if req_id not in self._req_info_dict:
                    logger.warning(f"Request ID {req_id} not found for deletion")
                    return False
                
                del self._req_info_dict[req_id]
                logger.debug("Deleted request info for ID: %s", {req_id})
                return True
        except Exception as e:
            logger.error(f"Failed to delete request info for ID {req_id}: {e}")
            return False
