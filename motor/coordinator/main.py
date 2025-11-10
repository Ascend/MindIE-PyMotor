#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2012-2020. All rights reserved.

import asyncio
import sys
import os
import traceback
from typing import Any

# Add project root directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.append(project_root)

from motor.coordinator.api_server.coordinator_server import (
    CoordinatorServer
)
from motor.coordinator.core.instance_manager import InstanceManager
from motor.coordinator.core.request_manager import RequestManager
from motor.coordinator.core.instance_healthchecker import InstanceHealthChecker
from motor.config.coordinator import CoordinatorConfig
from motor.coordinator.metrics.metrics_collector import MetricsCollector
from motor.utils.logger import get_logger

logger = get_logger(__name__)

modules: dict[str, Any] = {}


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


def initialize_components():
    """Initialize all coordinator components"""
    logger.info("Initializing coordinator components...")
    
    logger.info("Initializing CoordinatorConfig...")
    try:
        modules["CoordinatorConfig"] = CoordinatorConfig()
    except Exception as e:
        logger.error(f"Failed to initialize CoordinatorConfig: {e}")
        raise RuntimeError("Failed to initialize CoordinatorConfig") from e
    
    logger.info("Initializing InstanceManager...")
    modules["InstanceManager"] = InstanceManager()
    
    logger.info("Initializing RequestManager...")
    modules["RequestManager"] = RequestManager()

    logger.info("Initializing MetricsListener...")
    modules["MetricsListener"] = MetricsCollector()
    
    logger.info("Initializing InstanceHealthChecker...")
    modules["InstanceHealthChecker"] = InstanceHealthChecker()
    
    logger.info("Creating server configurations...")
    coordinator_config = modules.get("CoordinatorConfig")
    
    logger.info("Initializing CoordinatorServer...")
    coordinator_server = CoordinatorServer(
        coordinator_config=coordinator_config
    )
    modules["CoordinatorServer"] = coordinator_server
    
    logger.info("All components initialized successfully")


async def main():
    try:
        logger.info("Starting Motor Coordinator HTTP server...")
        
        initialize_components()
        
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
        stop_all_modules()
        logger.info("Coordinator server shutdown complete")


if __name__ == "__main__":
    if sys.version_info < (3, 6):
        logger.error("Python 3.6 or higher is required")
        sys.exit(1)
    
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
