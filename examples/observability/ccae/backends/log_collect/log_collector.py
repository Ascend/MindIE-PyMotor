# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import os
import re

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from ccae.common.logging import Log
from ccae.common.util import PathCheck
from ccae.backends.log_collect.log_processor import LogDataProcessor
from ccae.backends.log_collect.data_class import LogFile


DEFAULT_COLLECT_PATH = os.getenv("MOTOR_LOG_PATH")

MONITOR_INTERVAL_MILLION_SECONDS = 3
# {pod_name}_{pid}.log
LOG_PATTERNS = "^.*-(controller|coordinator)-[a-zA-Z0-9]+-[a-zA-Z0-9]+_[0-9]+\\.log$"


class CollectHandler(FileSystemEventHandler):
    def __init__(self, identity=None):
        self.logger = Log(__name__).getlog()
        self.identity = identity
        self.log_processor = LogDataProcessor()

    def on_created(self, event):
        if self._check_valid_file(event):
            self.logger.info(f"[CCAE] File %s is created" % event.src_path)
            self.log_processor.watch_files[event.src_path] = LogFile(file_path=event.src_path)
            self.log_processor.modified_log_files.add(event.src_path)

    def on_modified(self, event):
        if self._check_valid_file(event):
            self.logger.debug(f"[CCAE] File %s is modified", event.src_path)  # 文件内容更新频率高
            if event.src_path not in self.log_processor.watch_files:
                self.log_processor.watch_files[event.src_path] = LogFile(file_path=event.src_path)
            self.log_processor.modified_log_files.add(event.src_path)

    def on_deleted(self, event):
        if self._check_valid_file(event):
            self.logger.info("[CCAE] File %s is deleted" % event.src_path)
            self.log_processor.watch_files.pop(event.src_path)
            self.log_processor.modified_log_files.pop(event.src_path)

    def on_moved(self, event):
        if self._check_valid_file(event):
            self.logger.info(f"[CCAE] File %s is changed to %s" % (event.src_path, event.dest_path))
            src_log_file = self.log_processor.watch_files.pop(event.src_path, LogFile(file_path=event.dest_path))
            src_log_file.file_path = event.dest_path
            src_log_file.last_read_position = 0  # 文件轮转后，更新读取位置
            self.log_processor.watch_files[event.dest_path] = src_log_file

    def _check_valid_file(self, event):
        if event.is_directory:
            return False
        
        filename = os.path.basename(event.src_path)
        
        pattern = LOG_PATTERNS
        if self.identity:
            identity_escaped = re.escape(self.identity.lower())
            pattern = f"^.*-({identity_escaped})-[a-zA-Z0-9]+-[a-zA-Z0-9]+_[0-9]+\\.log$"
            
        if not bool(re.match(pattern, filename, re.IGNORECASE)):
            return False
            
        return PathCheck.check_path_full(event.src_path)


class Collector:
    def __init__(self, collect_path=DEFAULT_COLLECT_PATH, identity=None):
        self.logger = Log(__name__).getlog()

        self.log_path = os.path.join(collect_path, identity.lower() if identity else "")
        if not self.log_path:
            err_msg = f"[CCAE] Init log monitor failed, the log_path is empty"
            self.logger.error(err_msg)
            raise Exception(err_msg)
        self.logger.info(f"[CCAE] Log monitor path is %s" % self.log_path)

        self.collect_handler = CollectHandler(identity)
        self.collect_observer = Observer()
        self.collect_observer.schedule(self.collect_handler, self.log_path, recursive=True)
        try:
            self.collect_observer.start()
        except Exception as e:
            self.logger.error(f"[CCAE] Failed to start collect_observer: {e}")
            self.running = False
            raise RuntimeError(f"Observer startup failed: {e}") from e

    def stop(self):
        self.collect_observer.stop()
        self.collect_observer.join()
