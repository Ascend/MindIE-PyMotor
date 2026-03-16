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

        if not ProcManager.is_process_exist(main_pid):
            raise ValueError(f"process {main_pid} does not exist")

    @staticmethod
    def kill_process(pid: int) -> None:
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
    def is_process_exist(pid: int) -> bool:
        try:
            proc = psutil.Process(pid)
            return proc.is_running() and not proc.status() == psutil.STATUS_ZOMBIE
        except Exception as e:
            logger.warning(f"check process {pid} with error: {e}")
            return False

    @staticmethod
    def get_children_pids_by_depth(pid: int, depth: int) -> Set[int]:
        if depth <= 0:
            logger.warning("Recursive depth must be greater than 0, current input: %d, return empty set", depth)
            return set()

        logger.info("parent pid is: %d", pid)
        try:
            current_level_processes = [psutil.Process(pid)]
        except psutil.NoSuchProcess:
            logger.warning("Parent process %d does not exist", pid)
            return set()

        def _log_process_info(current_depth: int, children, parent):
            for child in children:
                cmd_line = child.cmdline()
                cmd = "".join(cmd_line) if cmd_line else "unknown"
                logger.info("depth: %d, pid: %d, cmd: %s", current_depth, parent.pid, cmd)

        children_pids = set()
        for depth in range(1, depth + 1):
            next_level_processes = []
            for process in current_level_processes:
                try:
                    direct_children = process.children(recursive=False)
                    child_pids = set()
                    for child in direct_children:
                        try:
                            # 获取进程命令行并检查是否包含npu-smi
                            cmd_line = ' '.join(child.cmdline())
                            if 'npu-smi' not in cmd_line:
                                child_pids.add(child.pid)
                        except psutil.NoSuchProcess:
                            logger.warning(f"Process {child.pid} no longer exists, skipping")
                    children_pids.update(child_pids)

                    _log_process_info(depth, direct_children, process)

                    next_level_processes.extend(direct_children)
                except psutil.NoSuchProcess:
                    logger.warning("Process %d has exited, skip getting its child processes", process.pid)
                except Exception as e:
                    logger.warning("Failed to get level %d child processes of process %d: %s", depth, process.pid, e)
            current_level_processes = next_level_processes
            if not current_level_processes:
                logger.info("No more child processes at level %d, terminate traversal early", depth)
                break

        return children_pids

    def shutdown(self) -> None:
        if self._shutdown_triggered:
            return
        logger.info(f"Shutting down process manager {self.main_pid}")
        self._shutdown_triggered = True

        for pid in self.child_pids:
            ProcManager.kill_process(pid)
        ProcManager.kill_process(self.main_pid)

    def join(self) -> None:
        if self._shutdown_triggered:
            return
        self._update_child_pids()
        try:
            while len(self.child_pids) > 0:
                dead_pids = []
                for pid in self.child_pids:
                    if not ProcManager.is_process_exist(pid):
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
        if ProcManager.is_process_exist(self.main_pid):
            # get children and grandchildren pids
            self.child_pids.update(ProcManager.get_children_pids_by_depth(self.main_pid, 2))
