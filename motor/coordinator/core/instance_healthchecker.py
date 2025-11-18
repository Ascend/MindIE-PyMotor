#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import time
from typing import Any, Optional
import threading
import requests
from motor.common.utils.logger import get_logger
from motor.common.resources.instance import Instance
from motor.common.resources.endpoint import Endpoint
from motor.coordinator.core.instance_manager import InstanceManager, UpdateInstanceMode
from motor.common.utils.singleton import ThreadSafeSingleton
from motor.config.coordinator import CoordinatorConfig
from motor.common.utils.dummy_request import DummyRequestUtil

logger = get_logger(__name__)


class InstanceHealthChecker(ThreadSafeSingleton):
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return

        self.config = CoordinatorConfig().health_check_config
        
        # Monitoring state - record instance IDs to be probed, corresponding endpoints and failure counts
        self._monitored_instances: dict[int, dict[str, Any]] = {}  # instance_id -> monitoring info
        self._consecutive_failures: dict[int, int] = {}  # instance_id -> consecutive failure count
        
        # Use threading.Event for thread-safe shutdown control
        self._shutdown_event = threading.Event()
        self._monitoring_thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()  # Reentrant lock for thread safety
        
        self._dummy_request_util = DummyRequestUtil()
        
        # Start monitoring thread automatically
        self._start_monitoring_thread()
        
        self._initialized = True
        logger.info("InstanceHealthChecker initialized and started")
    
    def stop(self):
        """Stop the health checker"""
        if self._shutdown_event.is_set():
            logger.warning("InstanceHealthChecker is already stopped")
            return
        
        # Set shutdown event to signal thread to exit
        self._shutdown_event.set()
        
        # Wait for monitoring thread to finish
        if self._monitoring_thread and self._monitoring_thread.is_alive():
            self._monitoring_thread.join(timeout=self.config.thread_join_timeout)
            if self._monitoring_thread.is_alive():
                logger.warning("InstanceHealthChecker monitoring thread did not terminate gracefully")
        
        self._dummy_request_util.close()
        
        logger.info("InstanceHealthChecker stopped")
    
    def push_exception_instance(self, instance: Instance, endpoint: Endpoint):
        """
        Receive abnormal instance information pushed by router module
        
        Args:
            instance: Abnormal instance
            endpoint: Abnormal endpoint
        """
        instance_id = instance.id
        
        with self._lock:
            if instance_id in self._monitored_instances:
                logger.debug(f"Instance {instance_id} is already being monitored")
                return
            
            # Record monitoring information, including the endpoint to probe
            self._monitored_instances[instance_id] = {
                "instance": instance,
                "endpoint": endpoint,
                "start_time": time.time(),
                "last_check_time": time.time()
            }
            self._consecutive_failures[instance_id] = 0
            
            logger.info(f"Started monitoring instance {instance_id} with endpoint {endpoint.id}")
        
        # Notify instanceManager to isolate the instance
        try:
            InstanceManager().update_instance_state(instance_id, UpdateInstanceMode.UNAVAILABLE)
        except Exception as e:
            logger.error(f"Failed to mark instance {instance_id} as unavailable: {e}")
    
    def check_state_alarm(self) -> bool:
        """
        Handle instance availability check request from HTTP server module
        
        Returns:
            bool: True if there are available instances, False otherwise
        """
        try:
            # Call InstanceManager to check instance availability
            is_available = InstanceManager().is_available()
            
            if not is_available:
                # No available instances, report alarm
                self._report_availability_alarm()
                logger.warning("No available instances detected, alarm reported to controller")
            else:
                logger.debug("Instance availability check passed")
            
            return is_available
            
        except Exception as e:
            logger.error(f"Failed to check instance state: {e}")
            # In case of exception, assume no available instances
            return False

    def _start_monitoring_thread(self):
        """Start the health checker monitoring thread"""
        self._shutdown_event.clear()
        self._monitoring_thread = threading.Thread(
            target=self._monitoring_loop,
            name="InstanceHealthChecker",
            daemon=True
        )
        self._monitoring_thread.start()
        logger.info("InstanceHealthChecker monitoring thread started")
            
    def _monitoring_loop(self):
        """Monitoring loop, periodically sends inference requests to probe instance status"""
        logger.info("InstanceHealthChecker monitoring loop started")
        
        while not self._shutdown_event.is_set():
            try:
                self._check_monitored_instances()
                
                # Wait for the configured interval or until shutdown is requested
                self._shutdown_event.wait(self.config.dummy_request_interval)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                # Wait a short time on error, but check shutdown event
                self._shutdown_event.wait(self.config.error_retry_interval)
        
        logger.info("InstanceHealthChecker monitoring loop stopped")
    
    def _check_monitored_instances(self):
        """Check all monitored instances"""
        if not self._monitored_instances:
            return
        
        with self._lock:
            instance_ids = list(self._monitored_instances.keys())
        
        # Check all instances sequentially (avoid concurrency complexity)
        for instance_id in instance_ids:
            # Check if shutdown was requested
            if self._shutdown_event.is_set():
                break
            self._check_single_instance(instance_id)
    
    def _check_single_instance(self, instance_id: int):
        """Check single instance status using inference request"""
        try:
            with self._lock:
                if instance_id not in self._monitored_instances:
                    return
                
                monitoring_info = self._monitored_instances[instance_id]
                endpoint = monitoring_info["endpoint"]
                self._monitored_instances[instance_id]["last_check_time"] = time.time()
            
            is_healthy = self._dummy_request_util.send_dummy_request(endpoint)
            
            with self._lock:
                if instance_id not in self._monitored_instances:
                    return
                
                if is_healthy:
                    # Virtual request successful, reset failure count and remove from monitoring
                    if instance_id in self._consecutive_failures:
                        del self._consecutive_failures[instance_id]
                    
                    # Notify instanceManager to recover instance
                    self._recover_instance(instance_id)
                    
                else:
                    # Virtual request failed
                    self._consecutive_failures[instance_id] = self._consecutive_failures.get(instance_id, 0) + 1
                    failure_count = self._consecutive_failures[instance_id]
                    
                    logger.warning(
                        f"Instance {instance_id} dummy request failed "
                        f"({failure_count}/{self.config.max_consecutive_failures})"
                    )
                    
                    if failure_count >= self.config.max_consecutive_failures:
                        # Consecutive failures reached threshold, terminate instance
                        self._terminate_instance(instance_id)
                        
        except Exception as e:
            logger.error(f"Error checking instance {instance_id}: {e}")
    
    def _recover_instance(self, instance_id: int):
        """Recover instance"""
        try:
            # Notify instanceManager to recover instance status
            InstanceManager().update_instance_state(
                instance_id,
                UpdateInstanceMode.AVAILABLE
            )
            
            # Remove from monitoring collection and failure counts
            with self._lock:
                if instance_id in self._monitored_instances:
                    del self._monitored_instances[instance_id]
                if instance_id in self._consecutive_failures:
                    del self._consecutive_failures[instance_id]
            
            logger.info(f"Instance {instance_id} recovered and removed from monitoring")
            
        except Exception as e:
            logger.error(f"Failed to recover instance {instance_id}: {e}")
    
    def _terminate_instance(self, instance_id: int):
        """Terminate abnormal instance with improved error handling."""
        try:
            instance_info = self._get_instance_info(instance_id)
            if not instance_info:
                logger.warning(f"Instance {instance_id} not found in monitored instances")
                return
            
            if not self._terminate_via_controller(instance_id, instance_info):
                return
            
            self._cleanup_instance_state(instance_id)
            
            logger.info(f"Instance {instance_id} terminated and cleaned up successfully")
            
        except Exception as e:
            logger.error(f"Failed to terminate instance {instance_id}: {e}")

    def _get_instance_info(self, instance_id: int) -> Optional[dict[str, Any]]:
        """Get instance information with proper error handling."""
        try:
            with self._lock:
                if instance_id not in self._monitored_instances:
                    return None
                
                instance_data = self._monitored_instances[instance_id]
                instance_role = instance_data["instance"].role
                return {
                    "role": instance_role,
                    "data": instance_data
                }
        except Exception as e:
            logger.error(f"Failed to get instance info for {instance_id}: {e}")
            raise

    def _terminate_via_controller(self, instance_id: int, instance_info: dict[str, Any]) -> bool:
        """Terminate instance via controller with proper error handling."""
        try:
            reason = (
                f"Coordinator: detect {instance_info['role']} instance {instance_id} "
                f"is abnormal after {self.config.max_consecutive_failures} consecutive failures"
            )
            
            terminate_success = self._call_controller_terminate(instance_id, reason)
            if not terminate_success:
                logger.error(f"Failed to terminate instance {instance_id} via controller")
                return False
            
            try:
                InstanceManager().delete_unavailable_instance(instance_id)
            except Exception as e:
                logger.error(f"Failed to delete instance {instance_id} from InstanceManager: {e}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error during controller termination for instance {instance_id}: {e}")
            return False

    def _cleanup_instance_state(self, instance_id: int) -> None:
        """Clean up local instance state with proper error handling."""
        try:
            with self._lock:
                if instance_id in self._monitored_instances:
                    del self._monitored_instances[instance_id]
                
                if instance_id in self._consecutive_failures:
                    del self._consecutive_failures[instance_id]
                    
        except Exception as e:
            logger.error(f"Failed to cleanup instance state for {instance_id}: {e}")
        
    def _report_availability_alarm(self):
        """Report availability alarm to controller"""
        try:
            # Call controller's alarm interface
            self._call_controller_alarm(
                alarm_type="no_available_instances",
                message="No available P and D instances found",
                severity="critical"
            )
            logger.info("Availability alarm reported to controller")
        except Exception as e:
            logger.error(f"Failed to report availability alarm: {e}")

    def _call_controller_alarm(self, alarm_type: str, message: str, severity: str) -> bool:
        """Call controller report alarm interface"""
        try:
            url = f"{self.config.controller_api_dns}:{self.config.controller_api_port}{self.config.alarm_endpoint}"
            response = requests.post(
                url,
                json={
                    "alarm_type": alarm_type,
                    "message": message,
                    "severity": severity
                },
                timeout=self.config.alarm_timeout
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Failed to call controller alarm interface: {e}")
            return False

    def _call_controller_terminate(self, instance_id: int, reason: str) -> bool:
        """Call controller terminate instance interface"""
        try:
            url = f"{self.config.controller_api_dns}:{self.config.controller_api_port}\
                {self.config.terminate_instance_endpoint}"
            response = requests.post(
                url,
                json={
                    "instance_id": instance_id,
                    "reason": reason
                },
                timeout=5.0
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Failed to call controller terminate interface: {e}")
            return False