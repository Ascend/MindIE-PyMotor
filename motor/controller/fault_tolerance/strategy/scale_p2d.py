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
import time

from motor.common.utils.logger import get_logger
from motor.controller.fault_tolerance.strategy import StrategyBase

logger = get_logger(__name__)


class ScaleP2DStrategy(StrategyBase):
    def __init__(self) -> None:
        super().__init__()
    
    def execute(self, instance_id: int) -> None:
        while not self.event.is_set():
            time.sleep(5)
    
    def stop(self) -> None:
        self.event.set()
        with self._lock:
            self._is_finished = True
        logger.info("Stop Scale P2D strategy.")

    def scale_p2d(self, instance_id: int) -> None:
        logger.info("Scale P2D strategy timeout, scale p2d.")