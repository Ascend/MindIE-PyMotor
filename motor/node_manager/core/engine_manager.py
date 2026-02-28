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

import json
import os
import signal
import threading
import time
from typing import Optional

from motor.common.resources.endpoint import Endpoint
from motor.common.resources.http_msg_spec import Ranktable, RegisterMsg, StartCmdMsg, ReregisterMsg
from motor.common.utils.env import Env
from motor.common.utils.logger import get_logger
from motor.common.utils.singleton import ThreadSafeSingleton
from motor.config.node_manager import NodeManagerConfig
from motor.node_manager.api_client.controller_api_client import ControllerApiClient

logger = get_logger(__name__)


class EngineManager(ThreadSafeSingleton):
    def __init__(self, config: NodeManagerConfig | None = None) -> None:
        if hasattr(self, "_initialized"):
            return

        self.endpoints: list[Endpoint] = []
        if config is None:
            config = NodeManagerConfig.from_json()
        self._config = config
        self.config_lock = threading.RLock()
        self.ranktable: Ranktable = None
        self.instance_ranktable: Ranktable = None
        self.instance_id: int = 0
        self.is_working = False

        self._register_thread = threading.Thread(
            target=self._register,
            daemon=True,
            name="engine_register"
        )
        self._register_thread.start()

        self._initialized = True
        logger.info("Engine Manager module start.")

    def update_config(self, config: NodeManagerConfig) -> None:
        """Update configuration for the engine manager"""
        pass

    def post_register_msg(self) -> Optional[bool]:
        register_msg = self._gen_register_msg()
        if register_msg is None:
            return False
        logger.debug("register_msg is %s", register_msg)

        return ControllerApiClient.register(register_msg)

    def post_reregister_msg(self) -> Optional[bool]:
        reregister_msg = self._gen_reregister_msg()
        if reregister_msg is None:
            return False
        logger.debug("reregister_msg is %s", reregister_msg)

        return ControllerApiClient.re_register(reregister_msg)

    def parse_start_cmd(self, start_cmd: StartCmdMsg):
        if not self._check_cmd_para(start_cmd):
            return False
        logger.info("start_cmd is %s", start_cmd)
        self.instance_id = start_cmd.instance_id
        self.endpoints = start_cmd.endpoints
        self.instance_ranktable = start_cmd.ranktable
        self._write_ranktable_to_file()
        return True

    def stop(self) -> None:
        try:
            if hasattr(self, "_register_thread") and self._register_thread.is_alive():
                self._register_thread.join(timeout=2.0)
        except Exception as e:
            logger.error("Failed to stop engine manager: %s", e)

    def _write_ranktable_to_file(self):
        """
        Write the instance's ranktable to a local JSON file.
        """

        # Determine output directory and filename
        output_path = os.path.join(Env.ranktable_path)

        try:
            # If ranktable is Ranktable type, use model_dump; otherwise, use as list[DeviceInfo]
            if isinstance(self.instance_ranktable, Ranktable):
                rk_dump = self.instance_ranktable.model_dump(exclude_none=True)
            else:
                rk_dump = self.instance_ranktable

            with open(output_path, "w") as f:
                json.dump(rk_dump, f, ensure_ascii=False, indent=2)

            logger.info("Ranktable written to %s", output_path)
        except Exception as e:
            logger.error("Failed to write ranktable to file: %s", e)

    def _check_cmd_para(self, start_cmd: StartCmdMsg) -> bool:
        # Read config values under lock protection
        with self.config_lock:
            job_name = self._config.basic_config.job_name
            endpoint_num = self._config.endpoint_config.endpoint_num
            pod_ip = self._config.api_config.pod_ip

        if (
            start_cmd.job_name != job_name
            or len(start_cmd.endpoints) != endpoint_num
        ):
            logger.error(
                "check job_name:%s, endpoint_num:%d error",
                job_name, endpoint_num
            )
            return False
        if (
            not isinstance(start_cmd.instance_id, int)
            or not isinstance(start_cmd.ranktable, Ranktable)
        ):
            logger.error("check start_cmd ranktable error")
            return False
        for endpoint in start_cmd.endpoints:
            if endpoint.ip != pod_ip:
                logger.error("check pod_ip %s error", pod_ip)
                return False
        return True

    def _register(self) -> None:
        # Wait for NodeManagerAPI to be ready before registering
        # Import here to avoid circular import
        from motor.node_manager.api_server.node_manager_api import NodeManagerAPI
        
        logger.info("Waiting for NodeManagerAPI to be ready before registering...")
        if not NodeManagerAPI.wait_until_ready(timeout=30.0):
            logger.error("NodeManagerAPI did not become ready within timeout, registration may fail")
        else:
            logger.info("NodeManagerAPI is ready, proceeding with registration")

        max_retries = 5
        retry_interval = 2
        retries = 0

        while retries < max_retries:
            logger.info(
                "Attempting registration (Attempt %d of %d)...",
                retries + 1, max_retries
            )
            success = self.post_register_msg()

            if success:
                return
            else:
                retries += 1
                if retries < max_retries:
                    logger.warning(
                        "Registration attempt %d failed. Retrying in %d seconds...",
                        retries, retry_interval
                    )
                    time.sleep(retry_interval)
                    retry_interval = retry_interval * 2
                else:
                    logger.error("Registration failed after maximum retries.")

        logger.error("Failed to register after 5 attempts.")
        try:
            # triggering the signal handler in main using a process signal
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception as e:
            logger.error("failed to send SIGTERM after registration failure: %s", e)

    def _check_config_paras(self) -> bool:
        # Read config values under lock protection
        with self.config_lock:
            job_name = self._config.basic_config.job_name

        if job_name is None:
            logger.error("job name is None, please check")
            return False
        return True

    def _get_ranktable(self) -> Ranktable | None:
        """Get ranktable from HCCL file"""
        # Read config values under lock protection
        with self.config_lock:
            hccl_path = self._config.hccl_path

        try:
            with open(hccl_path, 'r') as f:
                data = json.load(f)
            if self._config.single_container_config.single_container_flag:
                device_offset = self._config.single_container_config.device_offset
                device_num = self._config.single_container_config.device_num
                server_list_key = 'server_list'
                device_key = 'device'
                if server_list_key in data and len(data[server_list_key]) > 0 and \
                        device_key in data[server_list_key][0]:
                    data[server_list_key][0][device_key] = \
                            data[server_list_key][0][device_key][device_offset: device_offset + device_num]
            return Ranktable(**data)
        except Exception as e:
            logger.error("Failed to load ranktable from %s: %s", hccl_path, e)
            return None

    def _gen_register_msg(self) -> RegisterMsg | None:
        if not self._check_config_paras():
            return None

        # Get ranktable from HCCL file
        self.ranktable = self._get_ranktable()
        if self.ranktable is None:
            logger.error("Failed to get ranktable")
            return None

        # Read config values under lock protection
        with self.config_lock:
            job_name = self._config.basic_config.job_name
            model_name = self._config.basic_config.model_name
            role = self._config.basic_config.role
            pod_ip = self._config.api_config.pod_ip
            business_port = self._config.endpoint_config.service_ports
            mgmt_port = self._config.endpoint_config.mgmt_ports
            node_manager_port = self._config.api_config.node_manager_port
            parallel_config = self._config.basic_config.parallel_config

        register_msg = RegisterMsg(
            job_name=job_name,
            model_name=model_name,
            role=role,
            pod_ip=pod_ip,
            business_port=business_port,
            mgmt_port=mgmt_port,
            nm_port=str(node_manager_port),
            parallel_config=parallel_config,
            ranktable=self.ranktable,
        )
        return register_msg

    def _gen_reregister_msg(self) -> ReregisterMsg | None:
        if not self._check_config_paras():
            return None
        if len(self.endpoints) == 0 or self.instance_id <= 0:
            logger.error(
                "para check fail for reregister, please check"
                "len[endpoints]:%d, instance_id:%s",
                len(self.endpoints), type(self.instance_id)
            )
            return None

        # Read config values under lock protection
        with self.config_lock:
            job_name = self._config.basic_config.job_name
            model_name = self._config.basic_config.model_name
            role = self._config.basic_config.role
            pod_ip = self._config.api_config.pod_ip
            node_manager_port = self._config.api_config.node_manager_port
            parallel_config = self._config.basic_config.parallel_config

        reregister_msg = ReregisterMsg(
            job_name=job_name,
            model_name=model_name,
            role=role,
            pod_ip=pod_ip,
            nm_port=str(node_manager_port),
            parallel_config=parallel_config,
            instance_id=self.instance_id,
            endpoints=self.endpoints,
        )
        return reregister_msg
