# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import os
from abc import ABC, abstractmethod

from motor.engine_server.config.base import IConfig
from motor.engine_server.core.endpoint import Endpoint
from motor.engine_server.core.http_server import HttpServer
from motor.engine_server.utils.proc import ProcManager
from motor.common.utils.logger import get_logger

logger = get_logger("engine_server")


class IServerCore(ABC):
    @abstractmethod
    def __init__(self, config: IConfig):
        pass

    @abstractmethod
    def initialize(self) -> None:
        pass

    @abstractmethod
    def run(self) -> None:
        pass

    @abstractmethod
    def join(self) -> None:
        pass

    @abstractmethod
    def shutdown(self) -> None:
        pass

    @abstractmethod
    def status(self) -> str:
        pass


class BaseServerCore(IServerCore):
    def __init__(self, config: IConfig):
        super().__init__(config)
        self.config = config
        self.http_server: HttpServer | None = None
        self.endpoint = Endpoint(self.config)
        self.proc_manager = ProcManager(os.getpid())
        self._http_server_settings = None

    @property
    def http_server_settings(self) -> dict | None:
        return self._http_server_settings

    @http_server_settings.setter
    def http_server_settings(self, value: dict | None) -> None:
        if self._http_server_settings is None and value is not None:
            if self.http_server is None:
                self.http_server = HttpServer(
                    config=self.config,
                    init_params=value,
                )
                if hasattr(self, '_is_running') and self._is_running:
                    self.http_server.run()
                    logger.info("HttpServer started successfully after http_server_settings was set")
            else:
                logger.warning("HttpServer already initialized, skipping creation")
        self._http_server_settings = value

    def initialize(self) -> None:
        pass

    def run(self) -> None:
        self._is_running = True
        self.endpoint.run()

    def join(self) -> None:
        self.proc_manager.join()

    def shutdown(self) -> None:
        self._is_running = False

        if self.http_server is not None:
            self.http_server.shutdown()
            self.http_server = None
            logger.info("HttpServer shutdown completed")

        self.endpoint.shutdown()
        self.proc_manager.shutdown()

    def status(self) -> str:
        pass
