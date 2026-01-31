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
from typing import Any

from motor.common.resources import NodeManagerInfo, StartCmdMsg
from motor.common.utils.http_client import SafeHTTPSClient
from motor.common.utils.logger import get_logger
from motor.config.controller import ControllerConfig

logger = get_logger(__name__)


class NodeManagerApiClient:
    tls_config = ControllerConfig.from_json().mgmt_tls_config

    @staticmethod
    def send_start_command(node_mgr: NodeManagerInfo, start_cmd_msg: StartCmdMsg) -> bool:
        is_succeed = True
        try:
            # For `superpod_id` we need to use `exclude_none` to avoid error,
            # when we use atlas A2 server which doesn't have superpod_id.
            client_args = NodeManagerApiClient._generate_client_args(node_mgr)
            client = SafeHTTPSClient(**client_args)
            response = client.post(
                "/node-manager/start",
                data=start_cmd_msg.model_dump(exclude_none=True),
            )
            logger.info("Start command sent to node manager %s for instance %s successfully.",
                        client_args.get('address', 'unknown'), start_cmd_msg.job_name)
        except Exception as e:
            is_succeed = False
            logger.error("Error sending start command to node manager %s for instance %s: %s",
                         client_args.get('address', 'unknown'), start_cmd_msg.job_name, e)
        finally:
            client.close()

        return is_succeed

    @staticmethod
    def stop(node_mgr: NodeManagerInfo) -> bool:
        is_succeed = True
        try:
            client_args = NodeManagerApiClient._generate_client_args(node_mgr)
            client = SafeHTTPSClient(**client_args)
            response = client.post("/node-manager/stop", data={})
            logger.info(f"Stop command sent to node manager {node_mgr.pod_ip}:{node_mgr.port}")
        except Exception as e:
            is_succeed = False
            logger.error(f"Error sending stop command to node manager {node_mgr.pod_ip}:{node_mgr.port}, \
                        details: {e}")
        finally:
            client.close()

        return is_succeed

    @classmethod
    def query_status(cls, node_mgr: NodeManagerInfo) -> dict[str, Any]:
        client_args = NodeManagerApiClient._generate_client_args(node_mgr)
        client = SafeHTTPSClient(**client_args)
        response = client.get("/node-manager/status")
        return response

    @classmethod
    def _generate_client_args(cls, node_mgr: NodeManagerInfo) -> dict[str, str]:
        client_ars = {
            "address": f"{node_mgr.pod_ip}:{node_mgr.port}",
            "tls_config": cls.tls_config,
        }
        return client_ars
