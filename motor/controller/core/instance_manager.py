# coding=utf-8
# Copyright (c) 2025, HUAWEI CORPORATION.  All rights reserved.

import time
import threading
from collections.abc import Callable
from fastapi import HTTPException

from motor.resources.http_msg_spec import HeartbeatMsg
from motor.utils.logger import get_logger
from motor.resources.instance import Instance, InsStatus, InsConditionEvent, ReadOnlyInstance
from motor.controller.core.observer import Observer, ObserverEvent
from motor.utils.singleton import ThreadSafeSingleton
from motor.config.controller import ControllerConfig

logger = get_logger(__name__)

# Heartbeat handle result code
HEARTBEAT_HANDLER_SUCCESS = 200
HEARTBEAT_HANDLER_ERROR = 500
HEARTBEAT_HANDLER_RE_REGISTER = 503


class InstanceManager(ThreadSafeSingleton):
    """
    Instance Manager
    Manages all instances, their states, and heartbeats.
    """

    def __init__(self, config: ControllerConfig | None = None) -> None:
        super().__init__()
        # If the instance manager is already initialized, return.
        if hasattr(self, '_initialized'):
            return

        if config is None:
            config = ControllerConfig()
        self.config = config

        self.instances: dict[int, Instance] = {}
        self.observers: list[Observer] = []

        self.stop_event = threading.Event()
        self.ins_lock = threading.Lock()

        """
        self.states: dict[InsStatus, Callable]: State handle function mapping
        """
        self.states: dict[InsStatus, Callable] = {
            InsStatus.INITIAL: self.handle_initial,
            InsStatus.ACTIVE: self.handle_active,
            InsStatus.INACTIVE: self.handle_inactive,
            InsStatus.DELTETED: self.handle_deleted
        }

        """
        self.transitions: dict[Tuple[InsStatus, InsConditionEvent], InsStatus]: State transition rules
        curStatus + event -> newStatus
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
            (InsStatus.INACTIVE, InsConditionEvent.INSTANCE_HEARTBEAT_TIMEOUT): InsStatus.DELTETED
        }

        # Create instance heartbeat timeout management thread
        self.heartbeat_timeout_manager_thread = threading.Thread(
            target=self._heartbeat_timeout_manager,
            daemon=True,
            name="InstanceHeartbeatManager"
        )

        self._initialized = True
        logger.info("InstanceManager initialized.")

    """
    State transition callback function
    """

    def handle_initial(self, from_state: InsStatus, condition_event: InsConditionEvent, instance: Instance) -> None:
        if from_state == InsStatus.INITIAL:
            return
        return

    def handle_active(self, from_state: InsStatus, condition_event: InsConditionEvent, instance: Instance) -> None:
        if from_state == InsStatus.ACTIVE:
            return
        if condition_event == InsConditionEvent.INSTANCE_NORMAL:
            instance.update_instance_status(InsStatus.ACTIVE)
            self.notify(instance, ObserverEvent.INSTANCE_ADDED)
        return

    def handle_inactive(self, from_state: InsStatus, condition_event: InsConditionEvent,
                        instance: Instance) -> None:
        if from_state == InsStatus.INACTIVE:
            return
        if condition_event == InsConditionEvent.INSTANCE_ABNORMAL or \
                condition_event == InsConditionEvent.INSTANCE_HEARTBEAT_TIMEOUT:
            instance.update_instance_status(InsStatus.INACTIVE)
            self.notify(instance, ObserverEvent.INSTANCE_SEPERATED)
        return

    def handle_deleted(self, from_state: InsStatus, condition_event: InsConditionEvent, instance: Instance) -> None:
        if from_state == InsStatus.DELTETED:
            return
        if condition_event == InsConditionEvent.INSTANCE_HEARTBEAT_TIMEOUT or \
                condition_event == InsConditionEvent.INSTANCE_ABNORMAL:
            instance.update_instance_status(InsStatus.DELTETED)
            self.notify(instance, ObserverEvent.INSTANCE_REMOVED)
            self.del_instance(instance.id)
        return

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
            logger.info("Instance %s(id:%d) added.", ins.job_name, ins.id)

    def del_instance(self, ins_id: int):
        with self.ins_lock:
            if ins_id in self.instances:
                job_name = self.instances[ins_id].job_name
                self.instances.pop(ins_id)
                logger.info("Instance %s(id:%d) removed.", job_name, ins_id)
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

        if self.handle_state_transition(instance):
            return True, HEARTBEAT_HANDLER_SUCCESS
        else:
            logger.error("Failed to handle state transition for instance %d.", ins_id)
            raise HTTPException(HEARTBEAT_HANDLER_ERROR)

    def handle_state_transition(self, instance: Instance) -> bool:
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
            logger.info("detected instance %s (id:%d) at least have one endpoint abnormal.",
                        instance.job_name, instance.id)
            event = InsConditionEvent.INSTANCE_ABNORMAL
            to_state = self.transitions.get((from_state, event), None)
        else:
            event = InsConditionEvent.INSTANCE_INIT
            to_state = self.transitions.get((from_state, event), None)

        if to_state is None:
            logger.error("No valid state transition for instance %d from %s on event %s.",
                         instance.id, from_state, event)
            return False

        state_handler = self.states.get(to_state, None)
        if state_handler:
            state_handler(from_state, event, instance)
        return True

    def get_instance_num(self) -> int:
        with self.ins_lock:
            return len(self.instances)

    def start(self) -> None:
        """Start the instance heartbeat timeout management thread"""
        self.heartbeat_timeout_manager_thread.start()
        logger.info("InstanceManager started.")

    def stop(self) -> None:
        self.stop_event.set()
        # Only join thread that have been started
        if self.heartbeat_timeout_manager_thread.is_alive():
            self.heartbeat_timeout_manager_thread.join()
        logger.info("InstanceManager stopped.")

    def attach(self, observer: Observer) -> None:
        # For observer pattern
        if observer not in self.observers:
            self.observers.append(observer)

    # notify all observers with read-only instance
    def notify(self, instance: Instance, event: ObserverEvent) -> None:
        readonly_instance = ReadOnlyInstance(instance)
        for observer in self.observers:
            observer.update(readonly_instance, event)

    def _heartbeat_timeout_manager(self) -> None:
        """Instance heartbeat timeout management"""
        while not self.stop_event.is_set():
            with self.ins_lock:
                cur_instances = self.instances.values()

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

            time.sleep(self.config.instance_manager_check_internal)
