# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

from motor.engine_server.config.base import IConfig
from motor.common.utils.http_client import AsyncSafeHTTPSClient
from motor.common.utils.logger import get_logger

logger = get_logger("engine_server")


class HealthCollector:
    def __init__(self, config: IConfig):
        server_config = config.get_server_config()
        self.host = server_config.server_host
        self.port = server_config.engine_port
        self.infer_tls_config = server_config.deploy_config.infer_tls_config
        self.timeout = server_config.deploy_config.health_check_config.health_collector_timeout
        self.address = f"{self.host}:{self.port}"
        self._has_connected = False

    async def is_healthy(self) -> bool:
        try:
            async with AsyncSafeHTTPSClient.create_client(
                address=self.address,
                tls_config=self.infer_tls_config,
                timeout=self.timeout,
            ) as client:
                response = await client.get("/health")
                response.raise_for_status()
                response_text = await response.aread()
                health_status = response_text.decode('utf-8').lower() == 'true'
                self._has_connected = True
                return health_status
        except Exception as e:
            logger.debug(f"Health check failed: {e}")
            if self._has_connected:
                return False
            else:
                raise e
