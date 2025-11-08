#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

from typing import Optional
import time
import threading
import os
import json
import signal

from motor.resources.http_msg_spec import Ranktable, RegisterMsg, StartCmdMsg, ReregisterMsg
from motor.utils.http_client import SafeHTTPSClient
from motor.resources.endpoint import Endpoint, DeviceInfo
from motor.utils.singleton import ThreadSafeSingleton
from motor.config.node_manager import NodeManagerConfig
from motor.utils.logger import get_logger


logger = get_logger(__name__)


class EngineManager(ThreadSafeSingleton):
    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return

        self.endpoints: list[Endpoint] = []
        self.config = NodeManagerConfig()
        self.instance_ranktable: list[DeviceInfo] = []
        self.instance_id: int = 0
        self.is_working = False
        self._register_thread = threading.Thread(
            target=self._register, daemon=True, name="engine_register"
        )
        self._register_thread.start()

        self._initialized = True
        logger.info("Engine Manager module start.")

    def post_register_msg(self) -> Optional[bool]:
        register_msg = self._gen_register_msg()
        if register_msg is None:
            return False
        logger.debug(f"register_msg is {register_msg}")
        try:
            with SafeHTTPSClient(
                base_url=f"http://{self.config.controller_api_dns}:{self.config.controller_api_port}",
                timeout=1,
            ) as client:
                response = client.post("/controller/register", register_msg.model_dump())
                logger.info(f"Register success!")
                return True
        except Exception as e:
            logger.error(
                "Exception occurred while register to controller at "
                f"{self.config.controller_api_dns}:{self.config.controller_api_port}: {e}"
            )
            return False

    def post_reregister_msg(self) -> Optional[bool]:
        reregister_msg = self._gen_reregister_msg()
        if reregister_msg is None:
            return False
        logger.debug(f"reregister_msg is {reregister_msg}")
        try:
            with SafeHTTPSClient(
                base_url=f"http://{self.config.controller_api_dns}:{self.config.controller_api_port}",
                timeout=1,
            ) as client:
                response = client.post("/controller/reregister", reregister_msg.model_dump())
                logger.info(f"Register success!")
                return True
        except Exception as e:
            logger.error(
                "Exception occurred while reregister to controller at "
                f"{self.config.controller_api_dns}:{self.config.controller_api_port}: {e}"
            )
            return False

    def parse_start_cmd(self, start_cmd: StartCmdMsg):
        if not self._check_cmd_para(start_cmd):
            return False
        logger.debug(f"start_cmd is {start_cmd}")
        self.instance_id = start_cmd.instance_id
        self.endpoints = start_cmd.endpoints
        self.instance_ranktable = start_cmd.ranktable
        self._write_ranktable_to_file()
        return True

    def stop(self) -> None:
        try:
            if hasattr(self, "_register_thread") and self._register_thread.is_alive():
                self._register_thread.join(timeout=0.1)
        except Exception as e:
            logger.error(f"Failed to stop engine manager: {e}")

    def _write_ranktable_to_file(self):
        """
        Write the instance's ranktable to a local JSON file.
        """

        # Determine output directory and filename
        output_dir = os.path.join(os.getcwd(), "ranktables")
        os.makedirs(output_dir, exist_ok=True)
        if hasattr(self, "instance_id"):
            fname = f"ranktable_{self.instance_id}.json"
        else:
            fname = "ranktable_unknown.json"
        output_path = os.path.join(output_dir, fname)

        try:
            # If ranktable has model_dump (pydantic), use it; otherwise, use as dict
            if hasattr(self.instance_ranktable, "model_dump"):
                rk_dump = self.instance_ranktable.model_dump(exclude_none=True)
            else:
                rk_dump = self.instance_ranktable

            with open(output_path, "w") as f:
                json.dump(rk_dump, f, ensure_ascii=False, indent=2)

            logger.info(f"Ranktable written to {output_path}")
        except Exception as e:
            logger.error(f"Failed to write ranktable to file: {e}")

    def _check_cmd_para(self, start_cmd: StartCmdMsg) -> bool:
        if (
            start_cmd.job_name != self.config.job_name
            or len(start_cmd.endpoints) != self.config.endpoint_num
        ):
            return False
        if not isinstance(start_cmd.instance_id, int) or not isinstance(
            start_cmd.ranktable, Ranktable
        ):
            return False
        for endpoint in start_cmd.endpoints:
            if endpoint.ip != self.config.pod_ip:
                return False
        return True

    def _register(self) -> None:
        max_retries = 5
        retry_interval = 2
        retries = 0

        while retries < max_retries:
            logger.info(
                f"Attempting registration (Attempt {retries + 1} of {max_retries})..."
            )
            success = self.post_register_msg()

            if success:
                return
            else:
                retries += 1
                if retries < max_retries:
                    logger.warning(
                        f"Registration attempt {retries} failed. Retrying in {retry_interval} seconds..."
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
            logger.error(f"failed to send SIGTERM after registration failure: {e}")

    def _check_config_paras(self) -> bool:
        if self.config.job_name is None:
            logger.error("job name is None, please check")
            return False
        return True

    def _gen_register_msg(self) -> RegisterMsg | None:
        if not self._check_config_paras():
            return None

        register_msg = RegisterMsg(
            job_name=self.config.job_name,
            model_name=self.config.model_name,
            role=self.config.role,
            pod_ip=self.config.pod_ip,
            host_ip=self.config.host_ip,
            bussiness_port=self.config.service_ports,
            mgmt_port=str(self.config.node_manager_port),
            parallel_config=self.config.parallel_config,
            ranktable=self.config.ranktable,
        )
        return register_msg

    def _gen_reregister_msg(self) -> ReregisterMsg | None:
        if not self._check_config_paras():
            return None
        if len(self.endpoints) == 0 or self.instance_id is None:
            logger.error(
                f"para check fail for reregister, please check"
                f"len[endpoints]:{len(self.endpoints)}, instance_id:{type(self.instance_id)}"
            )
            return None
        reregister_msg = ReregisterMsg(
            job_name=self.config.job_name,
            model_name="",
            role=self.config.role,
            pod_ip=self.config.pod_ip,
            host_ip=self.config.host_ip,
            mgmt_port=str(self.config.node_manager_port),
            parallel_config=self.config.parallel_config,
            instance_id=self.instance_id,
            endpoints=self.endpoints,
        )
        return reregister_msg