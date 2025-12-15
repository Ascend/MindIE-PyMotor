#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import time
from typing import Set

import psutil

from motor.common.utils.logger import get_logger

logger = get_logger("engine_server")


class ProcManager:
    def __init__(self, main_pid: int):
        """
        Initialize process manager
        :param main_pid: Main process ID to monitor
        """
        self.main_pid = main_pid
        self.child_pids: Set[int] = set()
        self._shutdown_triggered = False

        if not self._is_process_exist(main_pid):
            raise ValueError(f"process {main_pid} does not exist")

    @staticmethod
    def _kill_process(pid: int) -> None:
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except psutil.TimeoutExpired:
                proc.kill()
        except Exception as e:
            logger.warning(f"process {pid} exited with error: {e}")

    @staticmethod
    def _is_process_exist(pid: int) -> bool:
        try:
            proc = psutil.Process(pid)
            return proc.is_running() and not proc.status() == psutil.STATUS_ZOMBIE
        except Exception as e:
            logger.warning(f"check process {pid} with error: {e}")
            return False

    @staticmethod
    def _get_all_children_pids(pid: int) -> Set[int]:
        children_pids = set()
        try:
            parent_process = psutil.Process(pid)
            all_children = parent_process.children(recursive=True)
            # Extract PIDs from Process objects
            children_pids.update(child.pid for child in all_children)
        except Exception as e:
            logger.warning(f"get children pids error: {e}")
        return children_pids

    def shutdown(self) -> None:
        if self._shutdown_triggered:
            return
        logger.info(f"Shutting down process manager {self.main_pid}")
        self._shutdown_triggered = True

        for pid in self.child_pids:
            self._kill_process(pid)
        self._kill_process(self.main_pid)

    def join(self) -> None:
        if self._shutdown_triggered:
            return
        self._update_child_pids()
        try:
            while len(self.child_pids) > 0:
                dead_pids = []
                for pid in self.child_pids:
                    if not self._is_process_exist(pid):
                        logger.warning(f"process {pid} exited, prepare to shutdown")
                        dead_pids.append(pid)

                if len(dead_pids) > 0:
                    self.shutdown()
                    break

                time.sleep(5)
        except Exception as e:
            logger.error(f"exception occur while join: {e}")
            self.shutdown()

    def _update_child_pids(self) -> None:
        if self._shutdown_triggered:
            return
        self.child_pids.clear()
        if self._is_process_exist(self.main_pid):
            self.child_pids.update(self._get_all_children_pids(self.main_pid))