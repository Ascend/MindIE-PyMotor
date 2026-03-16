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
import sys
import threading

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from ccae_reporter.common.logging import Log
from ccae_reporter.config import ConfigUtil
from ccae_reporter.reporters import select_reporter


logger = Log(__name__).getlog()


def _has_north_config():
    ConfigUtil.config = None
    return (ConfigUtil.get_config("north_config") and
            ConfigUtil.get_config("motor_deploy_config.tls_config.north_tls_config") is not None)


class UserConfigWatcher(FileSystemEventHandler):
    def __init__(self, config_file_path: str, ready_event: threading.Event):
        self.config_file_path = os.path.realpath(config_file_path)
        self.ready_event = ready_event

    def on_modified(self, event):
        if event.is_directory:
            return
        if os.path.realpath(event.src_path) == self.config_file_path:
            logger.debug("Config file changed, re-checking north_config")
            self._check_and_notify()

    def on_created(self, event):
        if event.is_directory:
            return
        if os.path.realpath(event.src_path) == self.config_file_path:
            logger.debug("Config file created, re-checking north_config")
            self._check_and_notify()

    def _check_and_notify(self):
        try:
            if _has_north_config():
                self.ready_event.set()
        except Exception as e:
            logger.debug("Check north config failed: %s", e)


class Runner:
    def __init__(self, identity: str):
        self.reporter = select_reporter("ccae_reporter")("motor", identity)
        logger.info("CCAE reporter initialized successfully, identity: %s", identity)

    def run(self):
        self.reporter.run()


def _wait_for_north_config():
    config_dir = os.getenv("CONFIG_PATH")
    if not config_dir:
        raise RuntimeError("Environment variable $CONFIG_PATH is not set.")
    config_dir = os.path.realpath(config_dir)
    config_file_path = os.path.join(config_dir, "user_config.json")

    ready_event = threading.Event()
    try:
        if _has_north_config():
            logger.info("North config detected, initial config check successful")
            return
    except Exception as e:
        logger.error("North config missing, initial config check failed: %s", e)

    watcher = UserConfigWatcher(config_file_path, ready_event)
    observer = Observer()
    observer.schedule(watcher, config_dir, recursive=False)
    observer.start()
    logger.info("Watching config file until north_config is present")
    try:
        ready_event.wait()
    finally:
        observer.stop()
        observer.join(timeout=5)


def main():
    if len(sys.argv) <= 1 or (sys.argv[1] != "Controller" and sys.argv[1] != "Coordinator"):
        raise RuntimeError("Need to identify the monitor, available choices are: `Controller` and `Coordinator`.")
    _wait_for_north_config()
    runner = Runner(sys.argv[1])
    runner.run()


if __name__ == '__main__':
    main()
