# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
import threading
import time
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, model_validator

from motor.common.resources import RegisterMsg, StartCmdMsg, ReregisterMsg, Instance
from motor.common.utils.data_builder import build_ins_ranktable, build_endpoints
from motor.common.etcd.etcd_client import EtcdClient
from motor.common.etcd.persistent_state import PersistentState
from motor.common.utils.singleton import ThreadSafeSingleton
from motor.config.controller import ControllerConfig
from motor.controller.api_client.node_manager_api_client import NodeManagerApiClient
from motor.controller.core import InstanceManager
from motor.common.utils.logger import get_logger

logger = get_logger(__name__)


class RegisterStatus(str, Enum):
    NOT_REGISTERED = "NOT_REGISTERED"
    ASSEMBLING = "ASSEMBLING"
    ASSEMBLED = "ASSEMBLED"


class AssembleInstanceMetadata(BaseModel):
    """
    Metadata for instance assembly process.
    """
    instance: Instance = Field(..., description="Instance object")
    register_status: RegisterStatus = Field(default=RegisterStatus.NOT_REGISTERED, description="Registration status")
    start_command_send_times: int = Field(default=0, description="Number of times start command was sent")
    register_timestamp: float = Field(default=0.0, description="Registration timestamp")
    is_reregister: bool = Field(default=False, description="Whether this is a re-registration")
    
    # Non-serializable field (excluded from serialization)
    lock: Any = Field(default=None, exclude=True)
    
    @model_validator(mode='after')
    def init_lock(self):
        """Initialize lock if not provided"""
        if self.lock is None:
            self.lock = threading.Lock()
        return self


class InstanceAssembler(ThreadSafeSingleton):
    def __init__(self, config: ControllerConfig | None = None) -> None:
        super().__init__()
        # If the instance assembler is already initialized, return.
        if hasattr(self, '_initialized'):
            return

        # Use default config if not provided (for backward compatibility)
        if config is None:
            config = ControllerConfig()

        self.ins_id_cnt = 1
        self.instances: dict[str, AssembleInstanceMetadata] = {}

        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.config_lock = threading.RLock()

        # Extract required config fields
        with self.config_lock:
            self.etcd_config = config.etcd_config
            self.etcd_tls_config = config.etcd_tls_config
            self.instance_assemble_timeout = config.instance_config.instance_assemble_timeout
            self.instance_assembler_check_interval = config.instance_config.instance_assembler_check_interval
            self.instance_assembler_cmd_send_internal = config.instance_config.instance_assembler_cmd_send_interval
            self.send_cmd_retry_times = config.instance_config.send_cmd_retry_times

        # Version control for data persistence
        self._data_version = 0
        self._version_lock = threading.Lock()

        with self.config_lock:
            self.etcd_client = EtcdClient(etcd_config=self.etcd_config, tls_config=self.etcd_tls_config)

        self.assemble_instance_thread = None
        self.start_command_thread = None

        self._initialized = True
        logger.info("InstanceAssembler initialized.")

    def start(self) -> None:
        """Start the instance assembler threads"""
        # Reset stop_event if it was previously set (for singleton reuse)
        if self.stop_event.is_set():
            self.stop_event.clear()

        # Try to restore data from ETCD, if failed,
        # it will start with empty state.
        with self.config_lock:
            enable_persistence = self.etcd_config.enable_etcd_persistence
        if enable_persistence and not self.restore_data():
            logger.warning("Failed to restore instance assembler data from ETCD, starting with empty state")

        # Create instance assembler threads
        self.assemble_instance_thread = threading.Thread(
            target=self._instances_assembler_loop,
            daemon=True,
            name="InstanceAssemblerLoop"
        )
        self.start_command_thread = threading.Thread(
            target=self._start_commmand_sender,
            daemon=True,
            name="StartCommandSender"
        )

        self.assemble_instance_thread.start()
        self.start_command_thread.start()
        logger.info("InstanceAssembler started.")

    def stop(self) -> None:
        self.stop_event.set()
        # Only join threads that have been started
        if (
            hasattr(self, 'assemble_instance_thread')
            and self.assemble_instance_thread is not None
            and self.assemble_instance_thread.is_alive()
        ):
            self.assemble_instance_thread.join()
        if (
            hasattr(self, 'start_command_thread')
            and self.start_command_thread is not None
            and self.start_command_thread.is_alive()
        ):
            self.start_command_thread.join()

        logger.info("InstanceAssembler stopped.")

    def is_alive(self) -> bool:
        """Check if the instance_assembler threads are alive"""
        return (
            (self.assemble_instance_thread is not None and self.assemble_instance_thread.is_alive())
            and (self.start_command_thread is not None and self.start_command_thread.is_alive())
        )

    def update_config(self, config: ControllerConfig) -> None:
        """Update configuration for the instance assembler"""
        with self.config_lock:
            # Update config fields
            self.etcd_config = config.etcd_config
            self.etcd_tls_config = config.etcd_tls_config
            self.instance_assemble_timeout = config.instance_config.instance_assemble_timeout
            self.instance_assembler_check_interval = config.instance_config.instance_assembler_check_interval
            self.instance_assembler_cmd_send_internal = config.instance_config.instance_assembler_cmd_send_interval
            self.send_cmd_retry_times = config.instance_config.send_cmd_retry_times

            # Update ETCD client with new configuration
            self.etcd_client = EtcdClient(etcd_config=self.etcd_config, tls_config=self.etcd_tls_config)
            logger.info("InstanceAssembler configuration updated")

    def register(self, msg: RegisterMsg) -> int:
        """
        Each node manager(nm) will register to instance assembler when it starts,
        and instance assembler will create or update the instance, then check
        wether the instance is ready to be start. If ready, notify the relative
        node manager to start inference engine and handle this instance to the
        instance manager to manager instance's status.

        Args:
            msg (RegisterMsg):
        """
        if msg is None or not isinstance(msg, RegisterMsg):
            raise Exception(f"Invalid msg provided to register. "
                            f"expect RegisterMsg, got {type(msg)}")

        with self.lock:
            status = self._eval_register_status(msg.job_name)
            if status == RegisterStatus.ASSEMBLED:
                logger.info("Instance %s already registered, no need to register again.",
                            msg.job_name)
                return -1
            elif status == RegisterStatus.NOT_REGISTERED:
                instance = Instance(
                    job_name=msg.job_name,
                    model_name=msg.model_name,
                    id=self.ins_id_cnt,
                    role=msg.role,
                    parallel_config=msg.parallel_config
                )
                metadata = AssembleInstanceMetadata(
                    instance=instance,
                    register_timestamp=time.time()
                )
                self.instances[msg.job_name] = metadata
                self.ins_id_cnt += 1
                logger.info("New instance %s(id:%d) created and added.", msg.job_name, instance.id)
            elif status == RegisterStatus.ASSEMBLING:
                metadata = self.instances[msg.job_name]
                with metadata.lock:
                    metadata.register_timestamp = time.time()

        pod_endpoints = build_endpoints(msg, metadata.instance.get_endpoints_num())
        metadata.instance.add_endpoints(msg.pod_ip, pod_endpoints)
        metadata.instance.add_node_mgr(msg.pod_ip, msg.nm_port)
        logger.info("Endpoints added for instance %s from pod %s.", msg.job_name, msg.pod_ip)

        # Persist data on state change
        with self.config_lock:
            enable_persistence = self.etcd_config.enable_etcd_persistence
        if enable_persistence and not self.persist_data():
            logger.warning("Failed to persist instance assembler data to ETCD")

        return 0

    def reregister(self, msg: ReregisterMsg) -> int:
        """
        When controller restarts, all node manager will re-register to controller,
        instance assembler will recover its instance info and max instance's id and
        max device's cluster id according to the reregister msg.
        """
        if msg is None or not isinstance(msg, ReregisterMsg):
            raise Exception(f"Invalid msg provided to reregister. "
                            f"expect ReregisterMsg, got {type(msg)}")

        with self.lock:
            status = self._eval_register_status(msg.job_name)
            if status == RegisterStatus.ASSEMBLED:
                logger.info("Instance %s already registered, no need to reregister again.",
                            msg.job_name)
                return -1
            elif status == RegisterStatus.NOT_REGISTERED:
                instance = Instance(
                    job_name=msg.job_name,
                    model_name=msg.model_name,
                    id=msg.instance_id,
                    role=msg.role,
                    parallel_config=msg.parallel_config
                )
                metadata = AssembleInstanceMetadata(
                    instance=instance,
                    register_timestamp=time.time(),
                    is_reregister=True
                )
                self.instances[msg.job_name] = metadata
                logger.info("New instance %s(id:%d) created and added by re-registration.",
                            msg.job_name, instance.id)
            elif status == RegisterStatus.ASSEMBLING:
                metadata = self.instances[msg.job_name]
                with metadata.lock:
                    metadata.register_timestamp = time.time()
                    metadata.is_reregister = True

            # recover ins_id_cnt
            self.ins_id_cnt = max(self.ins_id_cnt, msg.instance_id + 1)

        metadata.instance.add_endpoints(msg.pod_ip, {endpoint.id: endpoint for endpoint in msg.endpoints})
        metadata.instance.add_node_mgr(msg.pod_ip, msg.nm_port)
        logger.info("Recovery instance assembler's info, current ins_id_idx is %d.", self.ins_id_cnt)

        # Persist data on state change
        with self.config_lock:
            enable_persistence = self.etcd_config.enable_etcd_persistence
        if enable_persistence and not self.persist_data():
            logger.warning("Failed to persist instance assembler data to ETCD after reregistration")

        return 0

    def persist_data(self) -> bool:
        """Persist instance assembler data to ETCD with version control and checksum"""
        try:
            with self.lock:
                current_time = time.time()
                next_version = self._get_next_version()

                # Prepare instance assembler data - all data in one dict
                assembler_data = {
                    "ins_id_cnt": self.ins_id_cnt,
                    "instances": {}
                }
                for job_name, metadata in self.instances.items():
                    assembler_data["instances"][job_name] = metadata.model_dump(mode='json')
                logger.debug("Persisting instance assembler data - full data: %s", assembler_data)

                # Create persistent state with version control and checksum
                persistent_state = PersistentState(
                    data=assembler_data,
                    version=next_version,
                    timestamp=current_time,
                    checksum=""  # Will be calculated
                )
                persistent_state.checksum = persistent_state.calculate_checksum()
                logger.debug("Persisting instance assembler data - calculated checksum: %s, version: %s, timestamp: %s",
                             persistent_state.checksum, next_version, current_time)

                # Convert PersistentState to dict for etcd storage
                dict_data = {"state": persistent_state.model_dump()}
                success = self.etcd_client.persist_data("/controller/instance_assembler", dict_data)
                if success:
                    logger.info("Successfully persisted instance assembler data with version %d", next_version)
                return success

        except Exception as e:
            logger.error("Error persisting instance assembler data: %s", e)
            return False

    def restore_data(self) -> bool:
        """Restore instance assembler data from ETCD with version control and validation"""
        try:
            persistent_states = self.etcd_client.restore_data(
                "/controller/instance_assembler",
                PersistentState
            )
            if persistent_states is None:
                logger.info("No instance assembler data found in ETCD, starting with empty state")
                return True

            logger.info("Restoring instance assembler data from ETCD")
            
            persistent_state = persistent_states.get("state")
            if persistent_state is None:
                logger.warning("Expected 'state' key not found in persistent states, found keys: %s",
                             list(persistent_states.keys()))
                return False
            if not isinstance(persistent_state, PersistentState):
                logger.error("Invalid persistent state format, expected PersistentState instance")
                return False

            # Validate data integrity
            if not persistent_state.is_valid():
                logger.error("Data integrity check failed for instance_assembler, cannot restore")
                return False

            # Update data version
            with self._version_lock:
                self._data_version = max(self._data_version, persistent_state.version)

            with self.lock:
                self.instances.clear()
                
                # Restore ins_id_cnt
                self.ins_id_cnt = persistent_state.data.get("ins_id_cnt", 0)
                logger.info("Restored ins_id_cnt: %d (v%d)", self.ins_id_cnt, persistent_state.version)
                
                # Restore instances metadata
                instances_data = persistent_state.data.get("instances", {})
                valid_instances, invalid_instances = 0, 0
                
                for job_name, metadata_data in instances_data.items():
                    try:
                        metadata = AssembleInstanceMetadata.model_validate(metadata_data)
                        self.instances[job_name] = metadata
                        logger.info("Restored instance assembler state for %s (v%d)",
                                    job_name, persistent_state.version)
                        valid_instances += 1
                    except Exception as e:
                        logger.error("Error reconstructing instance assembler state %s: %s", job_name, e)
                        invalid_instances += 1
                        continue
                
                logger.info("Successfully restored instance assembler data: %d valid instances,"
                            " %d invalid instances skipped", valid_instances, invalid_instances)
                return True
        except Exception as e:
            logger.error("Error restoring instance assembler data: %s", e)
            return False

    def _eval_register_status(self, job_name: str) -> RegisterStatus:
        # First check if instance is already managed by InstanceManager (fully registered)
        if InstanceManager().has_active_instance_by_job_name(job_name):
            return RegisterStatus.ASSEMBLED

        # Then check if instance is still being assembled locally
        if job_name in self.instances.keys():
            return RegisterStatus.ASSEMBLING

        # Instance not found anywhere
        return RegisterStatus.NOT_REGISTERED

    def _start_commmand_sender(self) -> None:
        while not self.stop_event.is_set():
            with self.lock:
                job_names = list(self.instances.keys())

            with self.config_lock:
                max_retry_times = self.send_cmd_retry_times

            state_changed = False
            for job_name in job_names:
                with self.lock:
                    if job_name not in self.instances:
                        continue
                    metadata = self.instances[job_name]
                    with metadata.lock:
                        if metadata.register_status != RegisterStatus.ASSEMBLED:
                            continue

                if self._send_start_command(metadata):
                    logger.info("Start command sent for instance %s successfully.", job_name)
                    with self.lock:
                        self.instances.pop(job_name, None)
                    # Persist data on state change (instance removed after successful start command)
                    state_changed = True
                else:
                    retry_times = metadata.start_command_send_times + 1
                    if retry_times < max_retry_times:
                        logger.warning("Failed to send start command to instance %s with (%d/%d) times.",
                                       job_name, retry_times, max_retry_times)
                        metadata.start_command_send_times = retry_times
                        # Persist data on state change (retry count updated)
                        state_changed = True
                    else:
                        logger.error("Failed to send start command to instance %s with (%d/%d) times, "
                                     "abort it.", job_name, retry_times, max_retry_times)
                        with self.lock:
                            self.instances.pop(job_name, None)
                        # Persist data on state change (instance removed after max retries)
                        state_changed = True

            with self.config_lock:
                enable_persistence = self.etcd_config.enable_etcd_persistence
                sleep_interval = self.instance_assembler_cmd_send_internal

            # Persist data if any state changes occurred and persistence is enabled
            if state_changed and enable_persistence and not self.persist_data():
                logger.warning("Failed to persist instance assembler data to ETCD after sending start command")

            time.sleep(sleep_interval)

    def _send_start_command(self, metadata: AssembleInstanceMetadata) -> bool:
        ins_ranktable = build_ins_ranktable(metadata.instance)

        is_succeed = True
        for node_mgr in metadata.instance.get_node_managers():
            endpoints = metadata.instance.get_endpoints(node_mgr.pod_ip)
            if not endpoints:
                continue

            start_cmd_msg = StartCmdMsg(
                job_name=metadata.instance.job_name,
                role=metadata.instance.role,
                instance_id=metadata.instance.id,
                endpoints=[endpoint for endpoint in endpoints.values()],
                ranktable=ins_ranktable
            )

            is_succeed = NodeManagerApiClient.send_start_command(node_mgr, start_cmd_msg) and is_succeed
        return is_succeed

    def _instances_assembler_loop(self) -> None:
        # Check all instances in assembling, if one instance is ready,
        # notify relative node manager to start inference engine and 
        # handle this instance to instance manager.
        while not self.stop_event.is_set():
            with self.lock:
                keys = list(self.instances.keys())

            logger.debug("Assembling instance... remain %d instances.", len(keys))
            for job_name in keys:
                with self.lock:
                    if job_name not in self.instances:
                        logger.warning("Instance %s is not exist!", job_name)
                        continue
                    metadata = self.instances[job_name]
                    with metadata.lock:
                        if metadata.register_status == RegisterStatus.ASSEMBLED:
                            logger.info("Instance %s is already assembled!", job_name)
                            continue

                self._assemble_instance(metadata)

            with self.config_lock:
                check_interval = self.instance_assembler_check_interval
            time.sleep(check_interval)

    def _assemble_instance(self, metadata: AssembleInstanceMetadata) -> None:
        job_name = metadata.instance.job_name
        logger.debug("Assembling instance %s(id:%d)...", job_name, metadata.instance.id)
        need_persist = False

        # Filter abnormal endpoints before assembling
        self._filter_abnormal_endpoints(metadata.instance)
        if metadata.instance.is_endpoints_enough():
            # All endpoints are healthy, assemble successfully
            with metadata.lock:
                metadata.register_status = RegisterStatus.ASSEMBLED
                if metadata.is_reregister:
                    # Reregister instance, just handle it to instance manager.
                    InstanceManager().add_instance(metadata.instance)
                    with self.lock:
                        self.instances.pop(job_name, None)
                    need_persist = True
                else:
                    # Only new registered instance need to send start command
                    # Keep it in instances with ASSEMBLED status for _start_commmand_sender to handle
                    InstanceManager().add_instance(metadata.instance)
                    # No need to persist for new registration until start command is sent
        else:
            # Assembling... check if this instance registration is timeout
            with self.config_lock:
                assemble_timeout = self.instance_assemble_timeout
            with metadata.lock:
                if time.time() - metadata.register_timestamp > assemble_timeout:
                    with self.lock:
                        self.instances.pop(job_name, None)
                    need_persist = True
                    logger.warning("Instance %s registration timed out and removed.", job_name)

        # Persist data on state change
        with self.config_lock:
            enable_persistence = self.etcd_config.enable_etcd_persistence
        if need_persist and enable_persistence and not self.persist_data():
            logger.warning("Failed to persist instance assembler data to ETCD")

    def _filter_abnormal_endpoints(self, instance: Instance) -> None:
        """
        Filter abnormal endpoints by checking node managers status.
        Remove any abnormal endpoints found during the check.
        """
        node_managers = instance.get_node_managers()
        if not node_managers:
            logger.warning("No node managers found for instance %s(id:%d), cannot filter endpoints",
                           instance.job_name, instance.id)
            return

        for node_mgr in node_managers:
            if not self._is_node_manager_alive(node_mgr, instance):
                instance.del_endpoints(node_mgr.pod_ip)
                instance.del_node_mgr(node_mgr.pod_ip, node_mgr.port)

        logger.info("Endpoint filtering completed for instance %s(id:%d)",
                    instance.job_name, instance.id)

    def _is_node_manager_alive(self, node_mgr, instance: Instance) -> bool:
        """ Check if a node manager is alive for instance"""
        try:
            _ = NodeManagerApiClient.query_status(node_mgr)
            # Only check if node manager is reachable and responsive, not endpoint status
            logger.debug("Node manager %s:%s is reachable for instance %s(id:%d)",
                         node_mgr.pod_ip, node_mgr.port, instance.job_name, instance.id)
            return True
        except Exception as e:
            logger.warning("Node manager %s:%s is not alive for instance %s(id:%d): %s",
                           node_mgr.pod_ip, node_mgr.port, instance.job_name, instance.id, e)
            return False

    def _get_next_version(self) -> int:
        """Get next data version for persistence"""
        with self._version_lock:
            self._data_version += 1
            return self._data_version
