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

from motor.common.resources import RegisterMsg, ReregisterMsg, HeartbeatMsg
from motor.common.utils.http_client import SafeHTTPSClient
from motor.common.utils.logger import get_logger
from motor.config.controller import ControllerConfig
from motor.config.node_manager import NodeManagerConfig

logger = get_logger(__name__)


class ControllerApiClient:
    controller_config = ControllerConfig.from_json()
    nodemanager_config = NodeManagerConfig.from_json()

    @staticmethod
    def register(register_msg: RegisterMsg):
        # Read config values under lock protection
        client_args = {}
        try:
            client_args = ControllerApiClient._generate_client_args()
            with SafeHTTPSClient(timeout=1, **client_args) as client:
                response = client.post("/controller/register", register_msg.model_dump())
                logger.info("Register success!")
                return True
        except Exception as e:
            logger.error(
                "Exception occurred while register to controller at %s: %s",
                client_args.get('address', 'unknown'), e
            )
            return False

    @staticmethod
    def re_register(re_register_msg: ReregisterMsg):
        client_args = {}
        try:
            client_args = ControllerApiClient._generate_client_args()
            with SafeHTTPSClient(timeout=1, **client_args) as client:
                response = client.post("/controller/reregister", re_register_msg.model_dump())
                logger.info("Register success!")
                return True
        except Exception as e:
            logger.error(
                "Exception occurred while reregister to controller at %s: %s",
                client_args.get('address', 'unknown'), e
            )
            return False

    @staticmethod
    def report_heartbeat(heartbeat_msg: HeartbeatMsg):
        client_args = ControllerApiClient._generate_client_args()
        with SafeHTTPSClient(timeout=5, **client_args) as client:
            response = client.post("/controller/heartbeat", heartbeat_msg.model_dump())
            logger.debug(f"Heartbeat success, response: {response}, "
                         f"message body: {heartbeat_msg.model_dump()}, "
                         f"address: {client_args['address']}")

    @classmethod
    def _generate_client_args(cls) -> dict[str, str]:
        api_config = cls.controller_config.api_config
        tls_config = cls.nodemanager_config.mgmt_tls_config
        address = f"{api_config.controller_api_dns}:{api_config.controller_api_port}"
        client_ars = {
            "address": f"{address}",
            "tls_config": tls_config
        }
        return client_ars
