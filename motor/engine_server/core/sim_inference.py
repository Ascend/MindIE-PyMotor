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
import threading
import time
import uuid
from typing import Optional
import httpx
from motor.common.utils.http_client import AsyncSafeHTTPSClient
from motor.common.utils.logger import get_logger
from motor.engine_server.utils.aicore import get_aicore_usage
from motor.engine_server.constants import constants
logger = get_logger("engine_server")


class SimInference:
    """Virtual inference utility class for sending virtual health check requests"""
    
    def __init__(self, args, infer_tls_config, health_check_config=None):
        """Initialize virtual inference utility
        
        Args:
            args: Command line arguments
            infer_tls_config: TLS configuration for inference service
            health_check_config: Health check configuration, including npu_usage_threshold and other parameters
        """
        self.args = args
        self.infer_tls_config = infer_tls_config
        self._status = constants.INIT_STATUS
        self._health_check_task: Optional[asyncio.Task] = None
        self._abnormal_status_lock = threading.Lock()
        self._is_abnormal = False
        
        self.health_check_config = health_check_config or None
        # Get npu_usage_threshold with default value
        if self.health_check_config:
            self.npu_usage_threshold = getattr(self.health_check_config, 'npu_usage_threshold', 10)
            self.enable_virtual_inference = getattr(self.health_check_config, 'enable_virtual_inference', True)
        else:
            self.npu_usage_threshold = 10
            self.enable_virtual_inference = True
        
        self._shared_data_lock = threading.Lock()
        self._max_aicore_usage = 0
        self._check_count = 0
        self._max_check_count = 4
        
        # Condition variable to control aicore usage check execution
        self._aicore_check_condition = threading.Condition()
        self._aicore_check_active = False
        self._aicore_thread = None
        
        # 初始化HTTP客户端
        self._client = None
        self._client_address = f"{self.args.host}:{self.args.port}"
    

    @staticmethod
    def generate_request_id() -> str:
        """
        Generate globally unique request ID (async, does not block event loop).
        Returns: Pure ID string in format: timestamp(16 digits) + counter(4 digits) + random(8 chars)
        """
        current_timestamp = int(time.time() * 1000000)
        request_id = f"{current_timestamp}_virtual"
        logger.debug("Generated virtual request ID: %s", request_id)
        return request_id

    
    def set_status(self, status):
        self._status = status

    def start_health_check(self):
        # only start virtual inference when enable_virtual_inference is True and npu_usage_threshold is above 0
        if not self.enable_virtual_inference:
            logger.info("Health check is disabled")
            return
            
        if self.npu_usage_threshold <= 0 or self.npu_usage_threshold > 100:
            logger.info(f"Health check is disabled because npu_usage_threshold {self.npu_usage_threshold} is abnormal")
            return
            
        if not self._health_check_task or self._health_check_task.done():
            
            def _run_in_thread():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                try:
                    # start and run health check task
                    task = loop.create_task(self.health_check_loop())
                    self._health_check_task = task
                    loop.run_until_complete(task)
                except asyncio.CancelledError:
                    logger.info("Health check task cancelled")
                except Exception as e:
                    logger.error(f"Health check task error: {e}")
                finally:
                    if not loop.is_closed():
                        loop.close()
            
            thread = threading.Thread(target=_run_in_thread, daemon=True)
            thread.start()
            logger.info(f"Health check task started, will send virtual requests every 10 seconds \
                        with npu_usage_threshold={self.npu_usage_threshold}%")
        
        # Start aicore usage check thread if not already running
        if not self._aicore_thread or not self._aicore_thread.is_alive():
            self._aicore_thread = threading.Thread(target=self.check_aicore_usage_worker, daemon=True)
            self._aicore_thread.start()
            logger.info("AICore usage check thread started")

    def check_aicore_usage_worker(self):
        while True:
            # Wait for signal to start checking
            with self._aicore_check_condition:
                while not self._aicore_check_active:
                    self._aicore_check_condition.wait()
                
                # NPU aicore check
                max_usage = 0
                end_time = time.time() + 3
                self._check_count = 0
                
                while time.time() < end_time or self._check_count <= self._max_check_count:
                    self._check_count += 1
                    try:
                        usage = get_aicore_usage()
                    except Exception as e:
                        logger.error(f"Error checking AICore usage: {e}")
                        time.sleep(0.5)
                        continue
                    max_usage = max(max_usage, usage)
                    logger.debug(f"Aicore usage check: {usage}%, current max: {max_usage}%")
                    time.sleep(0.5)
                with self._shared_data_lock:
                    self._max_aicore_usage = max_usage
                    
                logger.debug(f"Max Aicore usage in 3 seconds: {max_usage}%")
                
                # Reset active flag after checking
                self._aicore_check_active = False
                self._aicore_check_condition.notify_all()


    
    async def init_client(self, timeout):
        """初始化HTTP客户端（异步）"""
        if self._client is None or self._client.is_closed:
            logger.debug(f"Initializing HTTP client for address: {self._client_address}")
            self._client = AsyncSafeHTTPSClient.create_client(
                address=self._client_address,
                tls_config=self.infer_tls_config,
                timeout=timeout
            )
    
    async def send_virtual_request_async(self, timeout):
        # construct virtual request
        virtual_request = {
            "model": self.args.served_model_name[0],
            "prompt": "1",
            "max_tokens": 1
        }
        
        logger.debug(f"Sending virtual health check request {virtual_request} to {self._client_address}/v1/completions")
        try:
            await self.init_client(timeout)
            
            req_id = self.generate_request_id()
            response = await self._client.post(f"/v1/completions",
                                               json=virtual_request,
                                               headers={'Content-Type': 'application/json', 'X-Request-Id': req_id},
                                               timeout=timeout)
            response.raise_for_status()
            
            response_data = response.json()
            logger.debug(f"Received health check response: {response_data}")
            logger.debug("Health check request successful")
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error in virtual request: {e}")
            raise
        except httpx.RequestError as e:
            logger.error(f"Request error in virtual request: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in virtual request: {e}")
            raise
    
    async def health_check_loop(self):
        """Regular virtual inference loop, sends virtual requests every 10 seconds"""
        while self._status == constants.NORMAL_STATUS:
            try:
                with self._shared_data_lock:
                    self._max_aicore_usage = 0
                    self._check_count = 0
                
                timeout = httpx.Timeout(5.0)
                sim_inference_success = True
                try:
                    await self.send_virtual_request_async(timeout)
                except Exception as e:
                    logger.error(f"Virtual request failed: {e}")
                    sim_inference_success = False
                
                logger.debug(f"Virtual request {'successful' if sim_inference_success else 'failed'}")

                # Signal aicore check thread to start checking at line 152
                with self._aicore_check_condition:
                    self._aicore_check_active = True
                    self._aicore_check_condition.notify_all()
                
                # Wait for aicore check to complete
                with self._aicore_check_condition:
                    if self._aicore_check_active:
                        self._aicore_check_condition.wait(timeout=3)
                    if self._aicore_check_active:
                        logger.warning("AICore usage check thread timeout")
                
                with self._shared_data_lock:
                    max_usage = self._max_aicore_usage
                
                logger.info(
                    f"Aicore usage rate: {max_usage}%, "
                    f"virtual request: {'successful' if sim_inference_success else 'failed'}"
                )

                # Set abnormal status when AICore Usage below threshold and virtual inference failed
                if max_usage < self.npu_usage_threshold and not sim_inference_success:
                    logger.warning(
                        f"AICore usage ({max_usage}%) < threshold ({self.npu_usage_threshold}%) "
                        f"and virtual request failed, setting abnormal status"
                    )
                    self.set_abnormal_status()
                else:
                    if self.is_abnormal():
                        self.reset_abnormal_status()
            except Exception as e:
                logger.error(f"Error in health check loop: {e}")
                self.set_abnormal_status()
                logger.warning(f"Status changed to ABNORMAL_STATUS due to health check failure")
            
            await asyncio.sleep(5)
    
    
    def set_abnormal_status(self):
        """Set abnormal status (thread-safe)"""
        with self._abnormal_status_lock:
            self._is_abnormal = True
        logger.warning("Abnormal status flag set to True")
    
    def is_abnormal(self) -> bool:
        """Check if in abnormal status (thread-safe)"""
        with self._abnormal_status_lock:
            return self._is_abnormal
    
    def reset_abnormal_status(self):
        """Reset abnormal status (thread-safe)"""
        with self._abnormal_status_lock:
            self._is_abnormal = False
        logger.info("Abnormal status flag set to False")
    
    def stop_health_check(self):
        """Stop health check task"""
        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()
            logger.info("Health check task stopped")
        self.reset_abnormal_status()