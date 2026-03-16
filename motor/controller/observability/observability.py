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

from motor.common.alarm.record import Record
from motor.common.utils.logger import get_logger
from motor.common.utils.singleton import ThreadSafeSingleton
from motor.config.controller import ControllerConfig
from motor.controller.observability.alarm.alarm_store import AlarmStore
from motor.controller.observability.metrics.metrics_collector import MetricsCollector


logger = get_logger(__name__)


class Observability(ThreadSafeSingleton):
    """
    Observability
    unified management and coordination of all operation and maintenance modules

    """
    def __init__(self, config: ControllerConfig | None = None) -> None:
        super().__init__()
        
        # If already initialized, return 
        if hasattr(self, '_initialized'):
            return
        self._initialized = True

        if config is None:
            config = ControllerConfig()
        self.config = config

        self.alarm_store = AlarmStore()
        self.metrics_collector = MetricsCollector(self.config)
    
    def start(self) -> None:
        """start observability"""
        logger.info("Starting observability...")

    def stop(self) -> None:
        """stop observability"""
        logger.info("Stopping observability...")
    
    def add_alarm(self, record: Record) -> bool:
        try:
            from motor.common.alarm.instance_exception_alarm import InstanceExceptionAlarm
            
            is_succeed = self.alarm_store.add_alarm(record)
            if is_succeed:
                logger.debug("Alarm added successfully via observability, %s", str(record))
            else:
                logger.warning("Alarm added failed via observability, %s", str(record))
            return is_succeed
        except Exception as e:
            logger.error("Failed to add alarm via observability: %s", e)
            return False

    def get_alarms(self, source_id: str = None) -> list[list[dict]]:
        try:
            return self.alarm_store.get_alarms(source_id)
        except Exception as e:
            logger.error("Failed to get alarms via observability: %s", e)
            return []

    def get_metrics(self) -> str:
        try:
            return self.metrics_collector.get_full_metrics()
        except Exception as e:
            logger.error(f"Failed to get metrics via observability: {e}")
            return ""