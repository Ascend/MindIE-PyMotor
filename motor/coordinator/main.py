#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2012-2020. All rights reserved.

import asyncio
import sys
import os
import traceback
from typing import Any

from motor.coordinator.api_server.coordinator_server import CoordinatorServer
from motor.coordinator.core.instance_manager import InstanceManager
from motor.coordinator.core.request_manager import RequestManager
from motor.config.coordinator import CoordinatorConfig
from motor.coordinator.metrics.metrics_collector import MetricsCollector
from motor.common.standby.standby_manager import StandbyManager
from motor.common.utils.config_watcher import ConfigWatcher
from motor.common.utils.logger import get_logger


logger = get_logger(__name__)

modules: dict[str, Any] = {}

# Global configuration
config: CoordinatorConfig | None = None

# Global config watcher
config_watcher: ConfigWatcher | None = None

# Global standby manager
standby_manager: StandbyManager | None = None


def log_config_summary(message_prefix: str | None = None) -> None:
    """Log configuration summary with optional message prefix"""
    if config:
        if message_prefix:
            logger.info(message_prefix)
        for line in config.get_config_summary().splitlines():
            if line.strip():  # Skip empty lines
                logger.info(line)


def on_config_updated() -> None:
    """Callback function called when configuration is updated"""
    global config, modules

    if config is None:
        logger.error("Configuration is None in config update callback")
        return

    # Update configuration for all modules
    logger.info("Updating configuration for all modules...")
    for module_name, module in modules.items():
        if hasattr(module, 'update_config'):
            try:
                module.update_config(config)
                logger.info(f"Updated configuration for {module_name}")
            except Exception as e:
                logger.error(f"Failed to update configuration for {module_name}: {e}")

    # Log configuration summary after reload
    log_config_summary("Configuration reloaded, printing updated summary:")


def start_all_modules(exclude_modules: set[str] | None = None) -> None:
    """Start all modules gracefully, optionally excluding some modules"""
    logger.info("Starting all modules...")
    if exclude_modules is None:
        exclude_modules = set()

    for module_name, module in modules.items():
        if module_name in exclude_modules:
            continue
        if hasattr(module, 'start'):
            logger.info(f"Starting {module_name}...")
            try:
                module.start()
            except Exception as e:
                logger.error(f"Error starting {module_name}: {e}")
    logger.info("All modules started.")


def stop_all_modules() -> None:
    """Stop all modules gracefully"""
    for module_name, module in modules.items():
        if hasattr(module, 'stop'):
            logger.info(f"Stopping {module_name}...")
            try:
                module.stop()
            except Exception as e:
                logger.error(f"Error stopping {module_name}: {e}")
    logger.info("All modules stopped.")


def on_become_master() -> None:
    """Callback when becoming master - start all modules"""
    global config
    logger.info("Becoming master, starting all modules...")
    # Start all modules
    if not modules:
        if config is None:
            logger.error("Configuration is None in on_become_master")
            return
        initialize_components()
    start_all_modules()


def on_become_standby() -> None:
    """Callback when becoming standby - stop all modules"""
    logger.info("Becoming standby, stopping all modules...")
    # Stop all modules
    stop_all_modules()


def initialize_components() -> None:
    """Initialize all coordinator components"""
    logger.info("Initializing coordinator components...")

    logger.info("Initializing InstanceManager...")
    modules["InstanceManager"] = InstanceManager(config)

    logger.info("Initializing RequestManager...")
    modules["RequestManager"] = RequestManager(config)

    logger.info("Initializing MetricsListener...")
    modules["MetricsListener"] = MetricsCollector(config)

    logger.info("Initializing CoordinatorServer...")
    modules["CoordinatorServer"] = CoordinatorServer(config)

    logger.info("All components initialized successfully")


async def main():
    global config, config_watcher, standby_manager

    try:
        logger.info("Starting Motor Coordinator HTTP server...")

        # Load configuration from file
        config = CoordinatorConfig.from_json()
        if config.config_path:
            logger.info(f"Loaded configuration from: {config.config_path}")
        else:
            logger.info("Using default configuration (no config file specified)")

        initialize_components()

        # Log configuration summary
        log_config_summary()

        # Start configuration file watcher
        if config.config_path and os.path.exists(config.config_path):
            config_watcher = ConfigWatcher(
                config_path=config.config_path,
                reload_callback=config.reload,
                config_update_callback=on_config_updated
            )
            config_watcher.start()
            logger.info("Configuration file watcher started")

        if config.standby_config.enable_master_standby:
            logger.info("Master/standby feature is enabled, running in master-standby mode")
            standby_manager = StandbyManager(config)
            standby_manager.start(
                on_become_master=on_become_master,
                on_become_standby=on_become_standby
            )
        else:
            logger.info("Master/standby feature is disabled, running in standalone mode")
            start_all_modules()

        coordinator_server = modules.get("CoordinatorServer")

        if not coordinator_server:
            raise RuntimeError("Failed to initialize server")

        await coordinator_server.run()

    except KeyboardInterrupt:
        logger.info("Received stop signal")
    except asyncio.CancelledError:
        logger.info("Server task cancelled")
    except Exception as e:
        logger.error(f"Server startup failed: {e}")
        raise
    finally:
        # Stop config watcher
        if config_watcher:
            config_watcher.stop()
            logger.info("Configuration file watcher stopped")

        # Stop standby manager
        if standby_manager:
            standby_manager.stop()
            logger.info("Standby manager stopped")

        stop_all_modules()
        logger.info("Coordinator server shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, server stopped")
    except asyncio.CancelledError:
        logger.info("Server task cancelled")
    except Exception as e:
        logger.error(f"Startup failed: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        sys.exit(1)
