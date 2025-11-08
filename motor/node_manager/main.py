#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import signal
import time

from motor.utils.logger import get_logger
from motor.node_manager.api_server.node_manager_api import NodeManagerAPI
from motor.config.node_manager import NodeManagerConfig
from motor.node_manager.core.daemon import Daemon
from motor.node_manager.core.engine_manager import EngineManager
from motor.node_manager.core.heartbeat_manager import HeartbeatManager

logger = get_logger(__name__)

modules = []
_should_exit = False


def stop_all_modules() -> None:
    while modules:
        module = modules.pop()
        if hasattr(module, 'stop'):
            module.stop()
    logger.info("All modules stoped.")


def signal_handler(sig, frame) -> None:
    global _should_exit
    if _should_exit:
        return
    _should_exit = True
    logger.info(f"\nRecive signal {sig}，exit gracefully...")
    stop_all_modules()


def main() -> None:
    global _should_exit
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # kill

    modules.append(NodeManagerConfig())
    modules.append(Daemon())
    modules.append(EngineManager())
    modules.append(HeartbeatManager())
    modules.append(NodeManagerAPI(
        host=NodeManagerConfig.pod_ip,
        port=NodeManagerConfig.node_manager_port,
    ))

    logger.info("All modules started, monitoring...")
    
    logger.info("Press Ctrl+C or type 'stop' to exit.")
    try:
        while not _should_exit:
            try:
                user_input = input().strip().lower()
                if user_input == 'stop':
                    _should_exit = True
                elif user_input:
                    logger.warning(f"Unknow command: {user_input}")
            except EOFError:
                if not _should_exit:
                    time.sleep(1)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, shutting down...")
        _should_exit = True
    finally:
        stop_all_modules()


if __name__ == '__main__':
    main()