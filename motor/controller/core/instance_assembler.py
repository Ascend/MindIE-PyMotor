# coding=utf-8
# Copyright (c) 2025, HUAWEI CORPORATION.  All rights reserved.

import time
import threading
import hashlib
import json
from enum import Enum
from dataclasses import dataclass, asdict
from typing import Any

from motor.common.utils.logger import get_logger
from motor.common.utils.data_builder import build_ins_ranktable, build_endpoints
from motor.common.utils.singleton import ThreadSafeSingleton
from motor.common.resources import RegisterMsg, StartCmdMsg, ReregisterMsg, Instance, Endpoint
from motor.controller.api_client.node_manager_api_client import NodeManagerApiClient
from motor.controller.core import InstanceManager
from motor.config.controller import ControllerConfig
from motor.common.utils.etcd_client import EtcdClient

logger = get_logger(__name__)


class RegisterStatus(Enum):
    NOT_REGISTERED = 0
    ASSEMBLING = 1
    ASSEMBLED = 2


@dataclass
class AssembleInstanceMetadata:
    instance: Instance
    register_status: RegisterStatus = RegisterStatus.NOT_REGISTERED
    start_command_send_times: int = 0
    register_timestamp: float = 0.0
    is_reregister: bool = False
    lock: threading.Lock = None

    def __post_init__(self):
        if self.lock is None:
            self.lock = threading.Lock()


@dataclass
class PersistentAssembleInstanceMetadataState:
    """Enhanced persistent state for assemble instance metadata with version control and data integrity"""
    metadata_data: dict[str, Any]
    version: int
    timestamp: float
    checksum: str

    def calculate_checksum(self) -> str:
        """Calculate checksum for data integrity verification"""
        try:
            # Calculate checksum using raw data (not normalized) for performance
            # Type consistency is handled in is_valid() method
            sorted_data = str(sorted(self.metadata_data.items()))
            data_str = f"{sorted_data}{self.version}{self.timestamp}"
            return hashlib.sha256(data_str.encode()).hexdigest()
        except Exception as e:
            logger.error("Error calculating checksum: %s", e)
            return ""

    def is_valid(self) -> bool:
        """Validate data integrity using checksum"""
        # Try both original format checksum and JSON normalized checksum for compatibility
        current_checksum = self.calculate_checksum()
        if self.checksum == current_checksum:
            return True

        # Also try with JSON normalized data for cases where data was stored with different types
        try:
            normalized_data = json.loads(json.dumps(self.metadata_data, sort_keys=True))
            normalized_checksum = hashlib.sha256(
                f"{normalized_data}{self.version}{self.timestamp}".encode()
            ).hexdigest()
            return self.checksum == normalized_checksum
        except Exception:
            return False


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
            self.instance_assemble_timeout = config.instance_config.instance_assemble_timeout
            self.instance_assembler_check_internal = config.instance_config.instance_assembler_check_internal
            self.instance_assembler_cmd_send_internal = config.instance_config.instance_assembler_cmd_send_internal
            self.send_cmd_retry_times = config.instance_config.send_cmd_retry_times

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
            self.instance_assemble_timeout = config.instance_config.instance_assemble_timeout
            self.instance_assembler_check_internal = config.instance_config.instance_assembler_check_internal
            self.instance_assembler_cmd_send_internal = config.instance_config.instance_assembler_cmd_send_internal
            self.send_cmd_retry_times = config.instance_config.send_cmd_retry_times

            # Update ETCD client with new configuration
            self.etcd_client = EtcdClient(
                host=self.etcd_config.etcd_host,
                port=self.etcd_config.etcd_port,
                ca_cert=self.etcd_config.etcd_ca_cert,
                cert_key=self.etcd_config.etcd_cert_key,
                cert_cert=self.etcd_config.etcd_cert_cert,
                timeout=self.etcd_config.etcd_timeout
            )
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
        metadata.instance.add_node_mgr(msg.pod_ip, msg.host_ip, msg.nm_port)
        logger.info("Endpoints added for instance %s from pod %s.", msg.job_name, msg.host_ip)

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
        metadata.instance.add_node_mgr(msg.pod_ip, msg.host_ip, msg.nm_port)
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

                persistent_states = {}
                # Persist ins_id_cnt
                ins_id_cnt_data = {"ins_id_cnt": self.ins_id_cnt}
                ins_id_cnt_state = PersistentAssembleInstanceMetadataState(
                    metadata_data=ins_id_cnt_data,
                    version=next_version,
                    timestamp=current_time,
                    checksum=""  # Will be calculated
                )
                ins_id_cnt_state.checksum = ins_id_cnt_state.calculate_checksum()
                persistent_states["ins_id_cnt"] = ins_id_cnt_state

                # Persist instances metadata
                for job_name, metadata in self.instances.items():
                    # Create persistent state for metadata
                    role_value = (metadata.instance.role.value
                                  if hasattr(metadata.instance.role, 'value')
                                  else str(metadata.instance.role))
                    parallel_config_data = (metadata.instance.parallel_config.model_dump()
                                            if hasattr(metadata.instance.parallel_config, 'model_dump')
                                            else metadata.instance.parallel_config)
                    endpoints_data = {
                        pod_ip: {eid: endpoint.model_dump() for eid, endpoint in endpoints.items()}
                        for pod_ip, endpoints in metadata.instance.endpoints.items()
                    }

                    metadata_data = {
                        "job_name": metadata.instance.job_name,
                        "model_name": metadata.instance.model_name,
                        "instance_id": metadata.instance.id,
                        "role": role_value,
                        "parallel_config": parallel_config_data,
                        "endpoints": endpoints_data,
                        "node_managers": [
                            (nm.pod_ip, nm.host_ip, nm.port) for nm in metadata.instance.node_managers
                        ],
                        "register_status": metadata.register_status.value,
                        "start_command_send_times": metadata.start_command_send_times,
                        "register_timestamp": metadata.register_timestamp,
                        "is_reregister": metadata.is_reregister
                    }
                    persistent_state = PersistentAssembleInstanceMetadataState(
                        metadata_data=metadata_data,
                        version=next_version,
                        timestamp=current_time,
                        checksum=""  # Will be calculated
                    )
                    persistent_state.checksum = persistent_state.calculate_checksum()
                    persistent_states[job_name] = persistent_state

                # Convert dataclass objects to dict for etcd storage
                dict_states = {key: asdict(state) for key, state in persistent_states.items()}
                success = self.etcd_client.persist_data("/controller/instance_assembler", dict_states)
                if success:
                    logger.info("Successfully persisted %d instance assembler states with version %d",
                                len(persistent_states), next_version)
                return success

        except Exception as e:
            logger.error("Error persisting instance assembler data: %s", e)
            return False

    def restore_data(self) -> bool:
        """Restore instance assembler data from ETCD with version control and validation"""
        try:
            persistent_states = self.etcd_client.restore_data(
                "/controller/instance_assembler",
                PersistentAssembleInstanceMetadataState
            )
            if persistent_states is None:
                logger.info("No instance assembler data found in ETCD, starting with empty state")
                return True

            # Process enhanced persistent format
            with self.lock:
                self.instances.clear()
                current_time = time.time()
                valid_states = 0
                invalid_states = 0

                for key, persistent_state in persistent_states.items():
                    if isinstance(persistent_state, PersistentAssembleInstanceMetadataState):
                        # Validate data integrity
                        if not persistent_state.is_valid():
                            logger.warning("Data integrity check failed for instance assembler state %s, skipping",
                                           key)
                            invalid_states += 1
                            continue

                        # Reconstruct data from persistent state
                        try:
                            if key == "ins_id_cnt":
                                # Restore ins_id_cnt
                                self.ins_id_cnt = persistent_state.metadata_data.get("ins_id_cnt", 0)
                                logger.info("Restored ins_id_cnt: %d (v%d)",
                                            self.ins_id_cnt, persistent_state.version)
                            else:
                                # Restore instance metadata
                                metadata_data = persistent_state.metadata_data
                                instance = Instance(
                                    job_name=metadata_data["job_name"],
                                    model_name=metadata_data["model_name"],
                                    id=metadata_data["instance_id"],
                                    role=metadata_data["role"],
                                    parallel_config=metadata_data["parallel_config"]
                                )

                                # Restore endpoints
                                for pod_ip, endpoints_data in metadata_data["endpoints"].items():
                                    endpoints = {}
                                    for eid, endpoint_data in endpoints_data.items():
                                        # Reconstruct Endpoint from dict data
                                        endpoint = Endpoint(**endpoint_data)
                                        endpoints[int(eid)] = endpoint
                                    instance.endpoints[pod_ip] = endpoints

                                # Restore node managers
                                for nm_data in metadata_data["node_managers"]:
                                    pod_ip, host_ip, port = nm_data
                                    instance.add_node_mgr(pod_ip, host_ip, port)

                                # Create metadata
                                # Handle type conversion for data from ETCD (may be strings)
                                register_status_value = metadata_data["register_status"]
                                if isinstance(register_status_value, str):
                                    register_status_value = int(register_status_value)
                                start_command_send_times_value = metadata_data["start_command_send_times"]
                                if isinstance(start_command_send_times_value, str):
                                    start_command_send_times_value = int(start_command_send_times_value)
                                register_timestamp_value = metadata_data["register_timestamp"]
                                if isinstance(register_timestamp_value, str):
                                    register_timestamp_value = float(register_timestamp_value)
                                is_reregister_value = metadata_data["is_reregister"]
                                if isinstance(is_reregister_value, str):
                                    is_reregister_value = is_reregister_value.lower() == 'true'

                                metadata = AssembleInstanceMetadata(
                                    instance=instance,
                                    register_status=RegisterStatus(register_status_value),
                                    start_command_send_times=start_command_send_times_value,
                                    register_timestamp=register_timestamp_value,
                                    is_reregister=is_reregister_value
                                )

                                self.instances[key] = metadata
                                logger.info("Restored instance assembler state for %s (v%d)",
                                            key, persistent_state.version)

                            # Update data version
                            with self._version_lock:
                                self._data_version = max(self._data_version, persistent_state.version)

                            valid_states += 1

                        except Exception as e:
                            logger.error("Error reconstructing instance assembler state %s: %s", key, e)
                            invalid_states += 1
                            continue

                logger.info("Successfully restored %d valid instance assembler states, %d invalid states skipped",
                            valid_states, invalid_states)
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
                        continue
                    metadata = self.instances[job_name]
                    with metadata.lock:
                        if metadata.register_status == RegisterStatus.ASSEMBLED:
                            continue

                self._assemble_instance(metadata)

            with self.config_lock:
                check_interval = self.instance_assembler_check_internal
            time.sleep(check_interval)

    def _assemble_instance(self, metadata: AssembleInstanceMetadata) -> None:
        job_name = metadata.instance.job_name
        logger.debug("Assembling instance %s(id:%d)...", job_name, metadata.instance.id)
        need_persist = False

        if metadata.instance.is_endpoints_enough():
            # Assemble successfully
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

    def _get_next_version(self) -> int:
        """Get next data version for persistence"""
        with self._version_lock:
            self._data_version += 1
            return self._data_version
