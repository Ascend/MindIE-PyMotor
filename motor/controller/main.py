# coding=utf-8
# Copyright (c) 2025, HUAWEI CORPORATION.  All rights reserved.
import sys
import signal
import argparse
from typing import Any

from motor.utils.logger import get_logger
from motor.controller.api_server.controller_api import ControllerAPI
from motor.config.controller import ControllerConfig, set_config_path, find_config_file
from motor.controller.core.instance_assembler import InstanceAssembler
from motor.controller.core.instance_manager import InstanceManager
from motor.controller.core.event_pusher import EventPusher


logger = get_logger(__name__)


observers_list = {
    "InstanceAssembler",
    "EventPusher",
    "FaultManager",
}
modules: dict[str, Any] = {}


def stop_all_modules() -> None:
    for module in modules.values():
        if hasattr(module, 'stop'):
            module.stop()
    logger.info("All modules stopped.")


def signal_handler(sig, frame) -> None:
    logger.info("Receive signal %d, exit gracefully...", sig)
    stop_all_modules()
    sys.exit(0)


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Motor Controller')
    parser.add_argument('--config', '-c', 
                       type=str, 
                       default=None,
                       help='Path to configuration file (default: auto-detect)')
    return parser.parse_args()


def attach_observer() -> None:
    """Attach observers to instance manager"""
    instance_manager = modules.get("InstanceManager")
    if instance_manager is None:
        logger.error("InstanceManager not found in modules")
        return

    for module_name, module in modules.items():
        if module_name in observers_list:
            logger.info("Attaching %s to instance manager", module_name)
            instance_manager.attach(module)
    logger.info("All observers attached to instance manager")


def start_modules() -> None:
    """Start modules that need to be started"""
    for module_name, module in modules.items():
        if hasattr(module, 'start'):
            logger.info("Starting %s", module_name)
            module.start()
    logger.info("All modules started")


def main() -> None:
    args = parse_arguments()

    # Set configuration path if provided
    if args.config:
        set_config_path(args.config)
        logger.info("Using configuration file: %s", args.config)
    else:
        logger.info("Using auto-detected configuration file")

    # Create configuration instance
    config_path = args.config if args.config else None
    try:
        if config_path:
            config = ControllerConfig.from_json(config_path)
        else:
            # Find config file automatically
            config = ControllerConfig.from_json(find_config_file())
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}, using default configuration")
        config = ControllerConfig()

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    modules["InstanceManager"] = InstanceManager(config)
    modules["InstanceAssembler"] = InstanceAssembler(config)
    modules["EventPusher"] = EventPusher(config)
    modules["ControllerAPI"] = ControllerAPI(config)
    if config.enable_fault_tolerance:
        from motor.controller.ft.fault_manager import FaultManager
        modules["FaultManager"] = FaultManager(config)

    attach_observer()

    start_modules()

    logger.info("All modules started, monitoring...")
    
    logger.info("Press Ctrl+C or type 'stop' to exit.")
    try:
        while True:
            try:
                user_input = input().strip().lower()
                if user_input == 'stop':
                    break
                elif user_input:
                    logger.error("Unknown command: %s", user_input)
            except EOFError:
                # In non-interactive environment, keep program running
                import time
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    
if __name__ == '__main__':
    main()