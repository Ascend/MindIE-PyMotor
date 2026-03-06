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

import os
from abc import ABC, abstractmethod

from motor.engine_server.config.base import IConfig
from motor.engine_server.core.data_controller import DataController
from motor.engine_server.core.endpoint import Endpoint
from motor.engine_server.core.service import MetricsService, HealthService
from motor.engine_server.constants.constants import METRICS_SERVICE, HEALTH_SERVICE
from motor.engine_server.utils.proc import ProcManager


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
        self.data_controller = DataController(self.config)
        self.services = {
            METRICS_SERVICE: MetricsService(self.data_controller),
            HEALTH_SERVICE: HealthService(self.data_controller),
        }
        self.endpoint = Endpoint(self.config.get_server_config(), self.services)
        self.proc_manager = ProcManager(os.getpid())

    def initialize(self) -> None:
        pass

    def run(self) -> None:
        self.data_controller.run()
        self.endpoint.run()

    def join(self) -> None:
        self.proc_manager.join()

    def shutdown(self) -> None:
        self.endpoint.shutdown()
        self.data_controller.shutdown()
        self.proc_manager.shutdown()

    def is_shutting_down(self) -> bool:
        return self.proc_manager.is_shutting_down()

    def status(self) -> str:
        pass
