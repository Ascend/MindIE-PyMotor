# coding=utf-8
# Copyright (c) 2025, HUAWEI CORPORATION.  All rights reserved.

import time
import threading
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, asdict
from typing import Any
from fastapi import HTTPException

from motor.config.controller import ControllerConfig
from motor.common.utils.logger import get_logger
from motor.controller.core import Observer, ObserverEvent
from motor.common.utils.singleton import ThreadSafeSingleton
from motor.common.utils.etcd_client import EtcdClient
from motor.common.resources import (HeartbeatMsg, Instance, InsStatus,
                                    InsConditionEvent, ReadOnlyInstance, EndpointStatus)


logger = get_logger(__name__)

# Heartbeat handle result code
HEARTBEAT_HANDLER_SUCCESS = 200
HEARTBEAT_HANDLER_ERROR = 500
HEARTBEAT_HANDLER_RE_REGISTER = 503


@dataclass
class PersistentInstanceState:
    """
    Enhanced persistent instance state with version control and data integrity

    Note: all fix methods are used to fix the data types mismatch between the
    original data and the stored data in ETCD.
    For example, the endpoints field is a dict with string keys, but the stored 
    data in ETCD is a dict with int keys.  So we need to fix the data types 
    mismatch by converting the string keys to int keys.
    The fix methods are:
    - _fix_data_types: fix the data types mismatch between the original data and the stored data in ETCD.
    - _fix_endpoints: fix the endpoints field by converting the string keys to int keys.
    - _fix_endpoint_dict: fix a single endpoint dictionary by converting the string keys to int keys.
    - _fix_list: fix a list by recursively processing any dict elements.
    """
    instance_data: dict[str, Any]
    version: int
    timestamp: float
    checksum: str

    @staticmethod
    def _fix_data_types(data: dict) -> dict:
        """Fix data types that are lost during JSON serialization/deserialization"""
        if not isinstance(data, dict):
            return data

        fixed_data = {}
        for key, value in data.items():
            fixed_data[key] = PersistentInstanceState._fix_value(key, value)

        return fixed_data

    @staticmethod
    def _fix_value(key: str, value) -> any:
        """Fix a single value based on its key and type"""
        # Special handling for endpoints field
        if key == "endpoints":
            return PersistentInstanceState._fix_endpoints(value)

        # Handle different value types
        if isinstance(value, dict):
            return PersistentInstanceState._fix_data_types(value)
        elif isinstance(value, list):
            return PersistentInstanceState._fix_list(value)
        else:
            return value

    @staticmethod
    def _fix_list(data_list: list) -> list:
        """Fix a list by recursively processing any dict elements"""
        return [
            PersistentInstanceState._fix_data_types(item)
            if isinstance(item, dict) else item for item in data_list
        ]

    @staticmethod
    def _fix_endpoints(endpoints: dict) -> dict:
        """Fix endpoints data structure, converting inner dict keys from str to int"""
        if not isinstance(endpoints, dict):
            return endpoints

        fixed_endpoints = {}
        for pod_ip, endpoint_dict in endpoints.items():
            fixed_endpoints[pod_ip] = PersistentInstanceState._fix_endpoint_dict(endpoint_dict)

        return fixed_endpoints

    @staticmethod
    def _fix_endpoint_dict(endpoint_dict) -> dict:
        """Fix a single endpoint dictionary, converting string keys to int where possible"""
        if not isinstance(endpoint_dict, dict):
            return endpoint_dict

        fixed_dict = {}
        for endpoint_key, endpoint_value in endpoint_dict.items():
            # Convert string key to int if it's a digit string, otherwise return as-is
            try:
                if isinstance(endpoint_key, str) and endpoint_key.isdigit():
                    fixed_key = int(endpoint_key)
                else:
                    fixed_key = endpoint_key
            except (ValueError, TypeError):
                fixed_key = endpoint_key
            fixed_dict[fixed_key] = endpoint_value

        return fixed_dict

    def calculate_checksum(self) -> str:
        """Calculate checksum for data integrity verification"""
        try:
            # Fix data types first to ensure consistency
            fixed_data = PersistentInstanceState._fix_data_types(self.instance_data)
            # Calculate checksum using type-fixed data for consistency
            sorted_data = str(sorted(fixed_data.items()))
            data_str = f"{sorted_data}{self.version}{self.timestamp}"
            return hashlib.sha256(data_str.encode()).hexdigest()
        except Exception as e:
            logger.error("Error calculating checksum: %s", e)
            return ""

    def is_valid(self) -> bool:
        """Validate data integrity using checksum"""
        # Try both original format checksum and JSON normalized checksum for compatibility
        current_checksum = self.calculate_checksum()
        logger.debug("Validating data integrity - stored checksum: %s, calculated checksum: %s",
                     self.checksum, current_checksum)
        logger.debug("Validation data - version: %s, timestamp: %s", self.version, self.timestamp)

        if self.checksum == current_checksum:
            return True

        logger.warning("Original format checksum mismatch, attempting JSON normalized checksum")

        # Also try with JSON normalized data for cases where data was stored with different types
        try:
            # First normalize the data through JSON serialization/deserialization
            normalized_data = json.loads(json.dumps(self.instance_data, sort_keys=True))

            # Fix data types that are lost during JSON serialization (especially dict keys)
            fixed_data = PersistentInstanceState._fix_data_types(normalized_data)

            normalized_checksum = hashlib.sha256(
                f"{fixed_data}{self.version}{self.timestamp}".encode()
            ).hexdigest()

            logger.debug("JSON normalized checksum: %s", normalized_checksum)
            logger.debug("Original data type information: %s",
                         {k: type(v).__name__ for k, v in self.instance_data.items()})

            if self.checksum == normalized_checksum:
                logger.debug("Type-fixed checksum matched successfully")
                return True
            else:
                logger.error("Validation failed - stored checksum: %s, normalized checksum: %s",
                             self.checksum, normalized_checksum)
                return False
        except Exception as e:
            logger.error("Exception occurred during JSON normalization: %s", e)
            logger.error("Original data content: %s", self.instance_data)
            return False


class InstanceManager(ThreadSafeSingleton):
    """ Instance Manager
    Manages all instances including states and heartbeats.
    It is a singleton class and can be accessed by InstanceManager().
    It is responsible for:
    - Adding and removing instances
    - Managing instance states
    - Managing instance heartbeats
    - Managing instance separation and recovery
    - Managing instance state transitions
    - Managing instance notifications
    """

    def __init__(self, config: ControllerConfig | None = None) -> None:
        super().__init__()
        # If the instance manager is already initialized, return.
        if hasattr(self, '_initialized'):
            return

        if config is None:
            config = ControllerConfig()

        self.instances: dict[int, Instance] = {}
        self.observers: list[Observer] = []

        # Track instances that are forcibly separated and should not
        # be reactivated by heartbeats
        self.forced_separated_instances: set[int] = set()

        self.stop_event = threading.Event()
        self.ins_lock = threading.Lock()
        self.config_lock = threading.RLock()

        # Extract required config fields
        with self.config_lock:
            self.etcd_config = config.etcd_config
            self.instance_manager_check_internal = config.instance_config.instance_manager_check_internal

        # Version control for data persistence
        self._data_version = 0
        self._version_lock = threading.Lock()

        with self.config_lock:
            self.etcd_client = EtcdClient(
                host=self.etcd_config.etcd_host,
                port=self.etcd_config.etcd_port,
                ca_cert=self.etcd_config.etcd_ca_cert,
                cert_key=self.etcd_config.etcd_cert_key,
                cert_cert=self.etcd_config.etcd_cert_cert,
                timeout=self.etcd_config.etcd_timeout
            )

        """
        self.states: dict[InsStatus, Callable]: State handle function mapping
        """
        self.states: dict[InsStatus, Callable] = {
            InsStatus.INITIAL: self._handle_initial,
            InsStatus.ACTIVE: self._handle_active,
            InsStatus.INACTIVE: self._handle_inactive,
            InsStatus.DELTETED: self._handle_deleted
        }

        """
        self.transitions: dict[tuple[InsStatus, InsConditionEvent], InsStatus]
        State transition rules: curStatus + event -> newStatus
        """
        self.transitions: dict[tuple[InsStatus, InsConditionEvent], InsStatus] = {
            (InsStatus.INITIAL, InsConditionEvent.INSTANCE_INIT): InsStatus.INITIAL,
            (InsStatus.INITIAL, InsConditionEvent.INSTANCE_NORMAL): InsStatus.ACTIVE,
            (InsStatus.INITIAL, InsConditionEvent.INSTANCE_ABNORMAL): InsStatus.DELTETED,
            (InsStatus.INITIAL, InsConditionEvent.INSTANCE_HEARTBEAT_TIMEOUT): InsStatus.DELTETED,
            (InsStatus.ACTIVE, InsConditionEvent.INSTANCE_NORMAL): InsStatus.ACTIVE,
            (InsStatus.ACTIVE, InsConditionEvent.INSTANCE_HEARTBEAT_TIMEOUT): InsStatus.INACTIVE,
            (InsStatus.ACTIVE, InsConditionEvent.INSTANCE_ABNORMAL): InsStatus.INACTIVE,
            (InsStatus.INACTIVE, InsConditionEvent.INSTANCE_ABNORMAL): InsStatus.INACTIVE,
            (InsStatus.INACTIVE, InsConditionEvent.INSTANCE_NORMAL): InsStatus.ACTIVE,
            (InsStatus.INACTIVE, InsConditionEvent.INSTANCE_INIT): InsStatus.INITIAL,
            (InsStatus.INACTIVE, InsConditionEvent.INSTANCE_HEARTBEAT_TIMEOUT): InsStatus.DELTETED
        }

        self.instances_management_thread = None

        self._initialized = True
        logger.info("InstanceManager initialized.")

    def start(self) -> None:
        """Start the instance heartbeat timeout management thread"""
        # Reset stop_event if it was previously set (for singleton reuse)
        if self.stop_event.is_set():
            self.stop_event.clear()

        # Try to restore data from ETCD, if failed,
        # it will start with empty state.
        with self.config_lock:
            enable_persistence = self.etcd_config.enable_etcd_persistence
        if enable_persistence and not self.restore_data():
            logger.warning("Failed to restore instance manager data from ETCD, starting with empty state")
        elif enable_persistence is False:
            # when presistence is off, we should also refresh heartbeat timestamp
            current_time = time.time()
            with self.ins_lock:
                for instance in self.instances.values():
                    self._maybe_refresh_heartbeat(
                        instance,
                        current_time,
                        self._data_version,
                        should_notify=False
                    )

        # Create instance heartbeat timeout management thread
        self.instances_management_thread = threading.Thread(
            target=self._instances_management_loop,
            daemon=True,
            name="InstancesManagementLoop"
        )
        self.instances_management_thread.start()

        logger.info("InstanceManager started.")

    def stop(self) -> None:
        self.stop_event.set()
        # Only join thread that have been started
        if self.instances_management_thread and self.instances_management_thread.is_alive():
            self.instances_management_thread.join()
        logger.info("InstanceManager stopped.")

    def is_alive(self) -> bool:
        """Check if the instances_management threads are alive"""
        return self.instances_management_thread is not None and self.instances_management_thread.is_alive()

    def update_config(self, config: ControllerConfig) -> None:
        """Update configuration for the instance manager"""
        with self.config_lock:
            # Update config fields
            self.etcd_config = config.etcd_config
            self.instance_manager_check_internal = config.instance_config.instance_manager_check_internal

            # Update ETCD client with new configuration
            self.etcd_client = EtcdClient(
                host=self.etcd_config.etcd_host,
                port=self.etcd_config.etcd_port,
                ca_cert=self.etcd_config.etcd_ca_cert,
                cert_key=self.etcd_config.etcd_cert_key,
                cert_cert=self.etcd_config.etcd_cert_cert,
                timeout=self.etcd_config.etcd_timeout
            )
            logger.info("InstanceManager configuration updated")

    def attach(self, observer: Observer) -> None:
        # For observer pattern
        if observer not in self.observers:
            self.observers.append(observer)

    # notify all observers with read-only instance
    def notify(self, instance: Instance, event: ObserverEvent) -> None:
        readonly_instance = ReadOnlyInstance(instance)
        for observer in self.observers:
            observer.update(readonly_instance, event)

    def persist_data(self) -> bool:
        """Persist instance manager data to ETCD with version control and checksum"""
        try:
            with self.ins_lock:
                current_time = time.time()
                next_version = self._get_next_version()

                persistent_states = {}
                for ins_id, instance in self.instances.items():
                    # Create persistent state with version control and checksum
                    instance_data = instance.model_dump()
                    logger.debug("Persisting instance %s - full data: %s", ins_id, instance_data)

                    persistent_state = PersistentInstanceState(
                        instance_data=instance_data,
                        version=next_version,
                        timestamp=current_time,
                        checksum=""  # Will be calculated
                    )
                    persistent_state.checksum = persistent_state.calculate_checksum()
                    logger.debug("Persisting instance %s - calculated checksum: %s, version: %s, timestamp: %s",
                                 ins_id, persistent_state.checksum, next_version, current_time)

                    persistent_states[str(ins_id)] = persistent_state

                # Convert dataclass objects to dict for etcd storage
                dict_states = {key: asdict(state) for key, state in persistent_states.items()}
                logger.debug("Persistence data being saved to ETCD: %s", dict_states)

                success = self.etcd_client.persist_data("/controller/instances", dict_states)
                if success:
                    logger.info("Successfully persisted %d instances with version %d",
                                len(persistent_states), next_version)
                return success

        except Exception as e:
            logger.error("Error persisting instance manager data: %s", e)
            return False

    def restore_data(self) -> bool:
        """Restore instance manager data from ETCD with version control and validation"""
        try:
            persistent_states = self.etcd_client.restore_data("/controller/instances", PersistentInstanceState)
            if persistent_states is None:
                logger.info("No instance data found in ETCD, starting with empty state")
                return True

            logger.info("Restoring %d instances from ETCD", len(persistent_states))

            # Process enhanced persistent format
            with self.ins_lock:
                self.instances.clear()
                current_time = time.time()
                valid_instances = 0
                invalid_instances = 0

                for ins_id_str, persistent_state in persistent_states.items():
                    if isinstance(persistent_state, PersistentInstanceState):
                        # Validate data integrity
                        if not persistent_state.is_valid():
                            logger.warning("Data integrity check failed for instance %s, skipping", ins_id_str)
                            invalid_instances += 1
                            continue

                        # Reconstruct instance from persistent state
                        try:
                            instance = Instance(**persistent_state.instance_data)

                            # Update data version
                            with self._version_lock:
                                self._data_version = max(self._data_version, persistent_state.version)

                            # Maybe refresh heartbeat for restored instance
                            self._maybe_refresh_heartbeat(instance, current_time, persistent_state.version)

                            self.instances[instance.id] = instance
                            valid_instances += 1

                        except Exception as e:
                            logger.error("Error reconstructing instance %s: %s", ins_id_str, e)
                            invalid_instances += 1
                            continue

                logger.info("Successfully restored %d valid instances, %d invalid instances skipped",
                            valid_instances, invalid_instances)
                return True

        except Exception as e:
            logger.error("Error restoring instance manager data: %s", e)
            return False

    def get_instance_num(self) -> int:
        with self.ins_lock:
            return len(self.instances)

    # Get all instances with the status of 'ACTION'
    def get_active_instances(self) -> list[Instance]:
        active_instances = []
        with self.ins_lock:
            for instance in self.instances.values():
                if instance.status == InsStatus.ACTIVE:
                    active_instances.append(instance)
        return active_instances

    # Get all instances with the status of 'INITIAL'
    def get_initial_instances(self) -> list[Instance]:
        initial_instances = []
        with self.ins_lock:
            for instance in self.instances.values():
                if instance.status == InsStatus.INITIAL:
                    initial_instances.append(instance)
        return initial_instances

    # Get all instances with the status of 'INACTIVE'
    def get_inactive_instances(self) -> list[Instance]:
        inactive_instances = []
        with self.ins_lock:
            for instance in self.instances.values():
                if instance.status == InsStatus.INACTIVE:
                    inactive_instances.append(instance)
        return inactive_instances

    def add_instance(self, ins: Instance):
        if ins is None or not isinstance(ins, Instance):
            logger.error("Invalid instance provided to add_instance.")
            return

        with self.ins_lock:
            if ins.id in self.instances:
                logger.error("Instance %s(id:%d) already exists.", ins.job_name, ins.id)
                return
            self.instances[ins.id] = ins

            # Refresh heartbeat for all endpoints with initial status
            timestamp = time.time()
            for pod_ip in ins.endpoints.keys():
                # Create initial status dict for all endpoints in this pod
                initial_status = {
                    endpoint.id: EndpointStatus.INITIAL 
                    for endpoint in ins.endpoints[pod_ip].values()
                }
                if ins.update_heartbeat(pod_ip, timestamp, initial_status):
                    logger.debug("Refreshed heartbeat for pod_ip %s in instance %s(id:%d) with initial status",
                                 pod_ip, ins.job_name, ins.id)
                else:
                    logger.warning("Failed to refresh heartbeat for pod_ip %s in instance %s(id:%d)",
                                   pod_ip, ins.job_name, ins.id)

            logger.info("Instance %s(id:%d) role:%s added.", ins.job_name, ins.id, ins.role)

    def del_instance(self, ins_id: int):
        with self.ins_lock:
            if ins_id in self.instances:
                job_name = self.instances[ins_id].job_name
                role = self.instances[ins_id].role
                self.instances.pop(ins_id)
                # Also remove from forced separated set if present
                self.forced_separated_instances.discard(ins_id)
                logger.info("Instance %s(id:%d) role:%s removed.", job_name, ins_id, role)
            else:
                logger.error("Instance %d not found.", ins_id)

    def get_instance(self, ins_id: int) -> Instance | None:
        with self.ins_lock:
            if ins_id in self.instances:
                return self.instances[ins_id]
            else:
                logger.error("Instance %d not found.", ins_id)
                return None

    def get_instance_by_podip(self, pod_ip: str) -> Instance | None:
        with self.ins_lock:
            for _, instance in self.instances.items():
                if instance.is_ip_in_endpoints(pod_ip):
                    logger.info("found instance contain %s.", pod_ip)
                    return instance

            logger.error("Instance %s not found.", pod_ip)
            return None

    def separate_instance(self, instance_id: int) -> None:
        """
        Separate a specific instance by its ID, marking it as INACTIVE and notifying observers.
        Only notifies when instance status actually changes from non-INACTIVE to INACTIVE.

        Args:
            instance_id: The instance ID to separate
        """
        try:
            instance = self.get_instance(instance_id)
            if instance is not None:
                # Check if instance is already in INACTIVE state to avoid duplicate notifications
                was_already_inactive = instance.status == InsStatus.INACTIVE

                # Set instance status to INACTIVE and mark as forcibly separated
                instance.update_instance_status(InsStatus.INACTIVE)
                with self.ins_lock:
                    self.forced_separated_instances.add(instance.id)

                # Only notify if this is the first time the instance becomes INACTIVE
                if not was_already_inactive:
                    self.notify(instance, ObserverEvent.INSTANCE_SEPERATED)

                logger.info("Successfully separated instance %s (id:%d)",
                            instance.job_name, instance.id)
            else:
                logger.warning("No instance found for instance ID %d", instance_id)
        except Exception as e:
            logger.error("Error separating instance %d: %s", instance_id, e)

    def recover_instance(self, instance_id: int) -> None:
        """
        Recover a specific instance by its ID, removing it from forced separation list.
        Instance will naturally transition back to ACTIVE state via heartbeat if healthy.

        Args:
            instance_id: The instance ID to recover
        """
        try:
            instance = self.get_instance(instance_id)
            if instance is not None and instance.id in self.forced_separated_instances:
                # Remove from forced separated set to allow natural heartbeat recovery
                with self.ins_lock:
                    self.forced_separated_instances.discard(instance.id)
                logger.info("Successfully recovered instance %s (id:%d)",
                            instance.job_name, instance.id)
            elif instance is not None:
                logger.warning("Instance %s (id:%d) is not in forced separated list, no need to recover",
                               instance.job_name, instance.id)
            else:
                logger.warning("No instance found for instance ID %d", instance_id)
        except Exception as e:
            logger.error("Error recovering instance %d: %s", instance_id, e)

    def has_instance_by_job_name(self, job_name: str) -> bool:
        """Check if instance exists by job name"""
        with self.ins_lock:
            for _, instance in self.instances.items():
                if instance.job_name == job_name:
                    return True
            return False

    def has_active_instance_by_job_name(self, job_name: str) -> bool:
        """Check if active instance exists by job name"""
        with self.ins_lock:
            for _, instance in self.instances.items():
                if instance.job_name == job_name and instance.status == InsStatus.ACTIVE:
                    return True
            return False

    def handle_heartbeat(self, heartbeat_msg: HeartbeatMsg) -> tuple[bool, str]:
        """
        Handle heartbeat and update instance status
        Returns:
            tuple[bool, str]: Whether handle is successful and the handle result code
        """
        if heartbeat_msg is None or not isinstance(heartbeat_msg, HeartbeatMsg):
            logger.error("Invalid heartbeat message.")
            return False, HEARTBEAT_HANDLER_ERROR

        ins_id = heartbeat_msg.ins_id
        pod_ip = heartbeat_msg.ip
        timestamp = time.time()

        # Retrieve instances from instances and update their status and heartbeat
        with self.ins_lock:
            instance = self.instances.get(ins_id, None)

        if instance is None:
            logger.error("Instance %d not exists, need to re-register.", ins_id)
            raise HTTPException(HEARTBEAT_HANDLER_RE_REGISTER)

        if instance.update_heartbeat(pod_ip, timestamp, heartbeat_msg.status):
            logger.debug("Heartbeat received successfully  for instance %d from IP %s.", ins_id, pod_ip)
        else:
            logger.error("Failed to update heartbeat for instance %d.", ins_id)
            raise HTTPException(HEARTBEAT_HANDLER_ERROR)

        if self._handle_state_transition(instance):
            return True, HEARTBEAT_HANDLER_SUCCESS
        else:
            logger.error("Failed to handle state transition for instance %d.", ins_id)
            raise HTTPException(HEARTBEAT_HANDLER_ERROR)

    """
    State transition callback function
    """
    def _instances_management_loop(self) -> None:
        """Instance management loop"""
        while not self.stop_event.is_set():
            with self.ins_lock:
                # use copy to avoid modifying the dictionary while iterating
                cur_instances = list(self.instances.values())

            for instance in cur_instances:
                if instance.status == InsStatus.DELTETED:
                    continue
                if instance.is_all_endpoints_alive():
                    continue
                logger.info("detected Instance %s (id:%d) heartbeat timeout on some endpoints.",
                            instance.job_name, instance.id)
                # Instance heartbeat timeout, handle state transition
                from_state = instance.status
                event = InsConditionEvent.INSTANCE_HEARTBEAT_TIMEOUT
                to_state = self.transitions.get((from_state, event), None)
                if to_state is None:
                    logger.error("No valid state transition for instance %d from %s on event %s.",
                                 instance.id, from_state, event)
                    continue

                state_handler = self.states.get(to_state, None)
                if state_handler:
                    state_handler(from_state, event, instance)

            with self.config_lock:
                check_interval = self.instance_manager_check_internal
            time.sleep(check_interval)

    def _handle_initial(
        self,
        from_state: InsStatus,
        condition_event: InsConditionEvent,
        instance: Instance
    ) -> None:
        if from_state == InsStatus.INITIAL:
            return
        # When transitioning from INACTIVE to INITIAL (re-initialization),
        # remove from forced separated instances set to allow re-activation
        if from_state == InsStatus.INACTIVE and condition_event == InsConditionEvent.INSTANCE_INIT:
            with self.ins_lock:
                self.forced_separated_instances.discard(instance.id)
            logger.info("Instance %d (%s) re-initializing, removed from forced separated set",
                        instance.id, instance.job_name)
        return

    def _handle_active(
        self,
        from_state: InsStatus,
        condition_event: InsConditionEvent,
        instance: Instance
    ) -> None:
        if from_state == InsStatus.ACTIVE:
            return
        if condition_event == InsConditionEvent.INSTANCE_NORMAL:
            instance.update_instance_status(InsStatus.ACTIVE)
            self.notify(instance, ObserverEvent.INSTANCE_ADDED)
        return

    def _handle_inactive(
        self,
        from_state: InsStatus,
        condition_event: InsConditionEvent,
        instance: Instance
    ) -> None:
        if from_state == InsStatus.INACTIVE:
            return
        if condition_event == InsConditionEvent.INSTANCE_ABNORMAL or \
                condition_event == InsConditionEvent.INSTANCE_HEARTBEAT_TIMEOUT:
            instance.update_instance_status(InsStatus.INACTIVE)
            self.notify(instance, ObserverEvent.INSTANCE_SEPERATED)
        return

    def _handle_deleted(
        self,
        from_state: InsStatus,
        condition_event: InsConditionEvent,
        instance: Instance
    ) -> None:
        if from_state == InsStatus.DELTETED:
            return
        if condition_event == InsConditionEvent.INSTANCE_HEARTBEAT_TIMEOUT or \
                condition_event == InsConditionEvent.INSTANCE_ABNORMAL:
            instance.update_instance_status(InsStatus.DELTETED)
            self.notify(instance, ObserverEvent.INSTANCE_REMOVED)
            self.del_instance(instance.id)
        return

    def _handle_state_transition(self, instance: Instance) -> bool:
        """
        Handle state transition based on current state and condition event
        Returns:
            bool: Whether handle state transition is successful
        """
        from_state = instance.status
        if instance.is_all_endpoints_ready():
            event = InsConditionEvent.INSTANCE_NORMAL
            to_state = self.transitions.get((from_state, event), None)
        elif instance.is_have_one_endpoint_abnormal():
            event = InsConditionEvent.INSTANCE_ABNORMAL
            to_state = self.transitions.get((from_state, event), None)
        else:
            event = InsConditionEvent.INSTANCE_INIT
            to_state = self.transitions.get((from_state, event), None)

        if to_state is None:
            logger.error("No valid state transition for instance %d from %s on event %s.",
                         instance.id, from_state, event)
            return False

        # Check if this instance is forcibly separated and prevent reactivation to ACTIVE
        if instance.id in self.forced_separated_instances and to_state == InsStatus.ACTIVE:
            logger.info("Instance %d (%s) is forcibly separated, preventing reactivation to ACTIVE state",
                        instance.id, instance.job_name)
            return True  # Return success but skip state transition

        state_handler = self.states.get(to_state, None)
        if state_handler:
            state_handler(from_state, event, instance)

            # Active persistence on state change
            with self.config_lock:
                enable_persistence = self.etcd_config.enable_etcd_persistence
            if from_state != to_state and enable_persistence:
                logger.info("Instance %d state changed from %s to %s, triggering persistence",
                            instance.id, from_state, to_state)
                if not self.persist_data():
                    logger.error("Failed to persist instance %d state change from %s to %s",
                                 instance.id, from_state, to_state)

            # Remove from forced separated set if transitioning to DELETED
            if to_state == InsStatus.DELTETED and instance.id in self.forced_separated_instances:
                with self.ins_lock:
                    self.forced_separated_instances.discard(instance.id)
                logger.info("Instance %d (%s) transitioned to DELETED, removing from forced separated set",
                            instance.id, instance.job_name)

        return True

    def _maybe_refresh_heartbeat(
        self,
        instance: Instance,
        current_time: float,
        version: int,
        should_notify: bool = True
    ) -> None:
        """Handle logic for restored instance, including heartbeat refresh and notification"""
        # Refresh heartbeat timestamp for ACTIVE instances to avoid immediate timeout
        if instance.status == InsStatus.ACTIVE:
            # Update heartbeat timestamp for all endpoints
            try:
                for endpoints in instance.endpoints.values():
                    for endpoint in endpoints.values():
                        endpoint.hb_timestamp = current_time
                logger.debug("Refreshed heartbeat timestamp for instance %d (%s) to %f",
                             instance.id, instance.job_name, current_time)
            except Exception as e:
                logger.error("Error refreshing heartbeat for instance %d: %s", instance.id, e)

            if should_notify:
                self.notify(instance, ObserverEvent.INSTANCE_ADDED)
            logger.info("Restored ACTIVE instance %d (%s) with refreshed heartbeat (v%d)",
                        instance.id, instance.job_name, version)
        else:
            logger.info("Restored instance %d (%s) with status %s (v%d)",
                        instance.id,
                        instance.job_name,
                        instance.status.value,
                        version)

    def _get_next_version(self) -> int:
        """Get next data version for persistence"""
        with self._version_lock:
            self._data_version += 1
            return self._data_version
