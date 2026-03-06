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

import threading
import time
from typing import Dict, Any

from motor.engine_server.config.base import IConfig
from motor.common.utils.logger import get_logger
from motor.engine_server.factory.collector_factory import CollectorFactory
from motor.engine_server.utils.reader_writer_lock import ReadPriorityRWLock
from motor.engine_server.constants import constants

logger = get_logger("engine_server")


class DataController:
    def __init__(self, config: IConfig):
        self.collect_interval = 3
        collector_factory = CollectorFactory()
        self.vllm_collector = collector_factory.create_collector(config)
        self._data_map: Dict[str, Dict[str, Any]] = {
            "metrics": {},
            "health": {}
        }
        self._data_map_lock = ReadPriorityRWLock()
        self._server_core = None
        self._core_status = constants.INIT_STATUS
        self._stop_event = threading.Event()
        self._collect_thread = threading.Thread(
            target=self._collect_loop,
            name="data_controller_collect_thread",
            daemon=True
        )

    def run(self):
        if not self._collect_thread or not self._collect_thread.is_alive():
            self._collect_thread.start()
            logger.info(f"DataController started, collect interval: {self.collect_interval}s")

    def shutdown(self):
        self._stop_event.set()
        if self._collect_thread and self._collect_thread.is_alive():
            self._collect_thread.join()
        logger.info("DataController stopped")

    def set_server_core(self, server_core):
        self._server_core = server_core

    def get_metrics_data(self) -> Dict[str, Any]:
        with self._data_map_lock.gen_rlock():
            return {
                "latest_metrics": self._data_map["metrics"].copy(),
                "collector_name": self.vllm_collector.name
            }

    def get_health_data(self) -> Dict[str, Any]:
        with self._data_map_lock.gen_rlock():
            return {
                "latest_health": self._data_map["health"].copy(),
                "collector_name": self.vllm_collector.name
            }

    def _collect_loop(self):
        while not self._stop_event.is_set() and self._core_status == constants.INIT_STATUS:
            try:
                self._core_status = self._server_core.status() if self._server_core else constants.INIT_STATUS
            except Exception as e:
                logger.error(f"Failed to get core status: {str(e)}", exc_info=True)
            time.sleep(1)

        while not self._stop_event.is_set():
            self._do_collect()
            time.sleep(self.collect_interval)

    def _do_collect(self):
        try:
            latest_collect_result = self.vllm_collector.collect()
            raw_latest_metrics = latest_collect_result.get("metrics", {})
            raw_latest_health = latest_collect_result.get("health", {})

            with self._data_map_lock.gen_wlock():
                self._data_map["metrics"] = self._modify_data(raw_latest_metrics)
                self._data_map["health"] = self._modify_data(raw_latest_health)

        except Exception as e:
            logger.error(f"DataController collect failed: {str(e)}", exc_info=True)

    def _modify_data(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        updated_data = raw_data.copy()
        updated_data["core_status"] = self._core_status
        return updated_data
