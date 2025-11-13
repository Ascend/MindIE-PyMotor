#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import os
import json
from typing import Any, Optional

from motor.resources.http_msg_spec import Ranktable
from motor.resources.instance import ParallelConfig, PDRole
from motor.resources.endpoint import DeviceInfo
from motor.node_manager.core.daemon import Daemon
from motor.utils.singleton import ThreadSafeSingleton
from motor.utils.env import Env
from motor.utils.patch_check import safe_open
from motor.utils.logger import get_logger

PP = "pp"
TP = "tp"

logger = get_logger(__name__)


class NodeManagerConfig(ThreadSafeSingleton):
    """
    Global configuration singleton for node manager.
    Loads basic config and HCCL config file.
    """

    pod_ip: Optional[str] = None
    host_ip: Optional[str] = None
    parallel_config: Optional[ParallelConfig] = None
    endpoint_num: int = 0

    node_manager_port: int = 8080
    mgmt_ports: list[str] = []
    service_ports: list[str] = []

    job_name: str = Env.job_name
    role: Optional[PDRole] = None
    model_name: str = None
    daemon: Daemon = Daemon()
    device_info: list[DeviceInfo] = []
    heartbeat_interval_seconds: int = 1
    tls_config: {} = None

    controller_api_dns: Optional[str] = None
    controller_api_port: Optional[int] = None

    ranktable: Ranktable = None

    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return  # Ensure singleton only initializes once

        config_path = os.path.join(Env.config_path, "config", "node_manager_config.json")
        hccl_path = os.path.join(Env.home_hccl_path, "hccl.json")

        NodeManagerConfig.parse_config_json(config_path)
        NodeManagerConfig.parse_hccl_json(hccl_path)
        NodeManagerConfig.calculate_endpoint_num()

        self._initialized = True

    @staticmethod
    def _check_json(json_data: dict[str, Any]) -> bool:
        """Validate required config json fields"""
        required_fields = [
            "parallel_config",
            "role",
            "controller_api_dns",
            "controller_api_port",
            "node_manager_port",
            "model_name",
        ]

        # Ensure required top-level fields exist
        for field in required_fields:
            if field not in json_data:
                logger.error(f"Missing required config field: {field}")
                return False

        # Validate parallel config structure
        pc = json_data.get("parallel_config")
        if not isinstance(pc, dict):
            logger.error("parallel_config must be a dict")
            return False

        if TP not in pc or PP not in pc:
            logger.error("parallel_config must contain tp and pp fields")
            return False

        if not isinstance(pc[TP], int) or not isinstance(pc[PP], int):
            logger.error("tp and pp must be integers")
            return False

        if pc[TP] <= 0 or pc[PP] <= 0:
            logger.error("tp and pp must be > 0")
            return False

        return True

    @classmethod
    def parse_config_json(cls, file_path: str):
        """Load basic node config JSON and validate required settings."""
        with safe_open(file_path, "r") as f:
            cfg = json.load(f)

        if not cls._check_json(cfg):
            raise ValueError("Invalid config json")

        cls.model_name = str(cfg.get("model_name"))

        cls.parallel_config = ParallelConfig(**cfg["parallel_config"])

        try:
            cls.role = PDRole(cfg.get("role"))
        except ValueError as e:
            raise ValueError(f"Invalid role value") from e

        cls.controller_api_dns = cfg.get("controller_api_dns") or cfg.get("controller_api_dns")
        cls.controller_api_port = cfg.get("controller_api_port")

        cls.node_manager_port = cfg.get("node_manager_port", 8080)

        cls.heartbeat_interval_seconds = cfg.get("heartbeat_interval_seconds", 1)

        cls.tls_config = cfg.get("nodemanager_tls_config", {})

        logger.info(
            f"[NodeManagerConfig] Loaded: role={cls.role}, "
            f"controller={cls.controller_api_dns}:{cls.controller_api_port}, "
            f"NM_port={cls.node_manager_port}"
        )

    @classmethod
    def parse_hccl_json(cls, file_path: str):
        """Load HCCL topology info JSON. It includes devices & IP mapping."""
        with safe_open(file_path, "r") as f:
            data = json.load(f)

        try:
            cls.ranktable = Ranktable(**data)
        except ValueError as e:
            raise ValueError("Invalid HCCL json") from e

        server = (data.get("server_list") or [None])[0]

        cls.pod_ip = server.get("container_ip") if server else None
        cls.host_ip = server.get("host_ip") if server else None

        devices = server.get("device") if server else []
        for d in devices or []:
            dev_info = DeviceInfo(
                device_ip=d.get("device_ip"),
                device_id=d.get("device_id"),
                rank_id=d.get("rank_id"),
            )
            if d.get("super_device_id"):
                dev_info.super_device_id = d["super_device_id"]

            cls.device_info.append(dev_info)

    @classmethod
    def calculate_endpoint_num(cls):
        """
        Calculate endpoint number based on tensor parallel & pipeline parallel config.
        Example: tp=2, pp=4 => 8 devices per pod
        """
        tp, pp = cls.parallel_config.tp_size, cls.parallel_config.pp_size
        devices_per_pod = tp * pp

        if len(cls.device_info) % devices_per_pod != 0:
            raise ValueError(
                f"Device count ({len(cls.device_info)}) must be divisible "
                f"by devices per pod ({devices_per_pod})"
            )

        cls.endpoint_num = max(1, len(cls.device_info) // devices_per_pod)

        # Generate port mapping from daemon utility
        ports = Daemon().gen_engine_ports(cls.endpoint_num)
        cls.mgmt_ports = ports.get("mgmt_ports", [])
        cls.service_ports = ports.get("service_ports", [])

        logger.info(
            f"endpoint_num: {cls.endpoint_num}, mgmt_ports: {cls.mgmt_ports}, "
            f"service_ports: {cls.service_ports}"
        )

