#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import threading
import time

from motor.config.node_manager import NodeManagerConfig
from motor.node_manager.core.engine_manager import EngineManager
from motor.utils.logger import get_logger
from motor.utils.singleton import ThreadSafeSingleton
from motor.resources.endpoint import Endpoint, EndpointStatus
from motor.resources.http_msg_spec import StartCmdMsg, HeartbeatMsg
from motor.utils.http_client import SafeHTTPSClient

logger = get_logger(__name__)


class HeartbeatManager(ThreadSafeSingleton):
    def __init__(self) -> None:
        if hasattr(self, '_initialized'):
            return
            
        self._endpoint_lock = threading.Lock()
        self._reregister_lock = threading.Lock()  # Lock for _reregistering flag
        self.stop_event = threading.Event()
        self.config = NodeManagerConfig()
        self._job_name = ""
        self._role = "prefill"
        self._instance_id = -1
        self._endpoints: list[Endpoint] = []
        self._heartbeat_report_thread = threading.Thread(target=self._report_heartbeat_loop,
                                                         daemon=True,
                                                         name="heartbeat_report")
        self._engine_server_status_thread = threading.Thread(target=self._refresh_endpoints_status_loop,
                                               daemon=True,
                                               name="endpoint_status_fetch")
        self._thread_started = False
        self._reregistering = False
        self._initialized = True
        logger.info("HeartBeatManager module start.")

    def start(self):
        if self._thread_started is False:
            self._heartbeat_report_thread.start()
            self._engine_server_status_thread.start()
            self._thread_started = True
        else:
            logger.info("Heartbeat thread has been started...")

    def update_endpoint(self, node_manager_info: StartCmdMsg) -> None:
        with self._endpoint_lock:
            self._job_name = node_manager_info.job_name
            self._role = node_manager_info.role
            self._instance_id = node_manager_info.instance_id
            self._endpoints.clear()
            for item in node_manager_info.endpoints:
                self._endpoints.append(item)

    def stop(self) -> None:
        self.stop_event.set()
        self._heartbeat_report_thread.join()
        logger.info("HeartBeatManager stopped.")
    
    def _refresh_endpoints_status_loop(self) -> None:
        while not self.stop_event.is_set():
            self._get_engine_server_status()
            time.sleep(1)

    def _get_engine_server_status(self) -> None:
        with self._endpoint_lock:
            endpoints_snapshot = list(self._endpoints)

        updated_endpoints = []

        for item in endpoints_snapshot:
            engine_server_base_url = f"http://{item.ip}:{item.port}"
            try:
                client = SafeHTTPSClient(
                    base_url=engine_server_base_url,
                    timeout=2
                )
                response = client.get("/v1/status")
                item.status = EndpointStatus(response.get("status"))
            except Exception:
                item.status = EndpointStatus("abnormal")
            finally:
                client.close()
            
            updated_endpoints.append(item)

        with self._endpoint_lock:
            self._endpoints = updated_endpoints

    def _report_heartbeat_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                with self._endpoint_lock:
                    endpoint_status_list = {item.id: item.status for item in self._endpoints}
                
                client = SafeHTTPSClient(
                    base_url=f"http://{self.config.controller_api_dns}:{self.config.controller_api_port}",
                    timeout=0.5
                )

                heartbeat_msg = HeartbeatMsg(
                    job_name=self._job_name,
                    ins_id=self._instance_id,
                    ip=self.config.pod_ip,
                    status=endpoint_status_list
                )
                _ = client.post("/controller/heartbeat", heartbeat_msg.model_dump())
                logger.info("report endpoint status to controller status successfully.")
            except Exception as e:
                if "503" in str(e):
                    with self._reregister_lock:
                        if self._reregistering is False:
                            self._reregistering = True
                            self._reregister_thread = threading.Thread(
                                target=self._reregister,
                                daemon=True,
                                name="reregister"
                            )
                            self._reregister_thread.start()
                        else:
                            logger.info("already in reregistering, skip reregister")
                logger.error(f"Exception occurred while reporting endpoint status to controller at "
                             f"{self.config.controller_api_dns}:{self.config.controller_api_port}: {e}")
            finally:
                client.close()

            time.sleep(1)

    def _reregister(self) -> None:
        ret = EngineManager().post_reregister_msg()
        if ret is False:
            logger.error("reregister failed")
            return
        with self._reregister_lock:
            self._reregistering = False
        logger.info("reregister success")

