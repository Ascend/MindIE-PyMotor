#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import threading
import time

from motor.node_manager.core.engine_manager import EngineManager
from motor.common.utils.logger import get_logger
from motor.common.utils.singleton import ThreadSafeSingleton
from motor.common.resources.endpoint import Endpoint, EndpointStatus
from motor.common.resources.http_msg_spec import StartCmdMsg, HeartbeatMsg
from motor.common.utils.http_client import SafeHTTPSClient
from motor.config.node_manager import NodeManagerConfig

logger = get_logger(__name__)


class HeartbeatManager(ThreadSafeSingleton):
    def __init__(self, config: NodeManagerConfig | None = None) -> None:
        if hasattr(self, "_initialized"):
            return

        self._endpoint_lock = threading.Lock()
        self.stop_event = threading.Event()

        if config is None:
            config = NodeManagerConfig.from_json()
        self.heartbeat_interval_seconds = config.basic_config.heartbeat_interval_seconds
        self.controller_api_dns = config.api_config.controller_api_dns
        self.controller_api_port = config.api_config.controller_api_port
        self.pod_ip = config.api_config.pod_ip
        self.controller_api_url = (
            f"http://{self.controller_api_dns}:{self.controller_api_port}"
        )

        self._job_name = ""
        self._role = "prefill"
        self._instance_id = -1
        self._endpoints: list[Endpoint] = []
        self._heartbeat_report_thread = threading.Thread(
            target=self._report_heartbeat_loop,
            daemon=True,
            name="heartbeat_report",
        )
        self._engine_server_status_thread = threading.Thread(
            target=self._refresh_endpoints_status_loop,
            daemon=True,
            name="endpoint_status_fetch",
        )
        self._thread_started = False
        self._engine_status_thread_start_time = None
        self._is_within_grace_period = True
        self._initialized = True
        logger.info("HeartBeatManager module start.")

    def start(self):
        if self._thread_started is False:
            self._heartbeat_report_thread.start()
            self._engine_server_status_thread.start()
            self._engine_status_thread_start_time = time.time()
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
        if self._heartbeat_report_thread.is_alive():
            self._heartbeat_report_thread.join(timeout=2.0)
        if self._engine_server_status_thread.is_alive():
            self._engine_server_status_thread.join(timeout=2.0)
        logger.info("HeartBeatManager stopped.")

    def _refresh_endpoints_status_loop(self) -> None:
        while not self.stop_event.is_set():
            self._get_engine_server_status()
            time.sleep(1)

    def _get_engine_server_status(self) -> None:
        with self._endpoint_lock:
            endpoints_snapshot = list(self._endpoints)

        # Check if within one minute after startup
        if (
            self._is_within_grace_period
            and self._engine_status_thread_start_time is not None
        ):
            elapsed_time = time.time() - self._engine_status_thread_start_time
            self._is_within_grace_period = elapsed_time < 60

        updated_endpoints = []
        client = None
        for item in endpoints_snapshot:
            engine_server_base_url = f"http://{item.ip}:{item.mgmt_port}"
            original_status = item.status
            client = None
            detected_status = None

            try:
                client = SafeHTTPSClient(base_url=engine_server_base_url, timeout=2)
                response = client.get("/status")
                if isinstance(response, dict) and "status" in response:
                    status_value = response.get("status")
                    try:
                        detected_status = EndpointStatus(status_value)
                        if detected_status != original_status:
                            logger.info(
                                f"Engine Server rank {item.id}, status change to "
                                f"{detected_status} from {original_status}"
                            )
                    except ValueError:
                        logger.error(
                            f"Invalid status value '{status_value}' from Engine Server "
                            f"{item.id}: {engine_server_base_url}"
                        )
                        detected_status = EndpointStatus.ABNORMAL
                else:
                    logger.error(
                        f"Invalid response format from Engine Server{item.id}: {engine_server_base_url}: {response}"
                    )
                    detected_status = EndpointStatus.ABNORMAL
            except Exception as e:
                if not self._is_within_grace_period:
                    logger.error(
                        f"Failed to get engine server status from {engine_server_base_url}: {e}"
                    )
                detected_status = EndpointStatus.ABNORMAL
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception as e:
                        logger.error(f"Failed to close client: {e}")

            # If within grace period and abnormal status detected, do not update status
            if (
                self._is_within_grace_period
                and detected_status == EndpointStatus.ABNORMAL
            ):
                logger.debug(
                    f"Engine server {engine_server_base_url} status is abnormal "
                    f"within grace period, keeping original status: {original_status}"
                )
                item.status = original_status
            else:
                item.status = detected_status

            updated_endpoints.append(item)

        with self._endpoint_lock:
            self._endpoints = updated_endpoints

    def _report_heartbeat_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                with self._endpoint_lock:
                    # If within grace period, abnormal status is not reported, report INITIAL instead
                    if self._is_within_grace_period:
                        endpoint_status_list = {
                            item.id: (
                                EndpointStatus.INITIAL
                                if item.status == EndpointStatus.ABNORMAL
                                else item.status
                            )
                            for item in self._endpoints
                        }
                    else:
                        endpoint_status_list = {
                            item.id: item.status
                            for item in self._endpoints
                        }

                heartbeat_msg = HeartbeatMsg(
                    job_name=self._job_name,
                    ins_id=self._instance_id,
                    ip=self.pod_ip,
                    status=endpoint_status_list,
                )
                with SafeHTTPSClient(
                    base_url=f"http://{self.controller_api_dns}:{self.controller_api_port}",
                    timeout=0.5,
                ) as client:
                    _ = client.post("/controller/heartbeat", heartbeat_msg.model_dump())
            except Exception as e:
                if "503" in str(e):
                    logger.warning("Received 503, maybe controller has been restarted, reregistering...")
                    self._reregister()
                else:
                    logger.error(
                        "Exception occurred while reporting endpoint status to controller at %s ",
                        self.controller_api_url,
                    )

            time.sleep(self.heartbeat_interval_seconds)

    def _reregister(self) -> None:
        ret = EngineManager().post_reregister_msg()
        if ret is False:
            logger.error("reregister failed")
        else:
            logger.info("reregister success")
