import time
import hashlib
import pytest
from unittest.mock import MagicMock, patch, call

from motor.common.utils.data_builder import build_pod_ranktable, build_endpoints
from motor.common.resources.instance import Instance, ParallelConfig
from motor.common.resources.http_msg_spec import RegisterMsg, ReregisterMsg
from motor.controller.core.instance_assembler import (
    InstanceAssembler,
    AssembleInstanceMetadata,
    RegisterStatus,
    PersistentAssembleInstanceMetadataState
)
from motor.common.utils.singleton import ThreadSafeSingleton
from motor.controller.core import InstanceManager



@pytest.fixture
def test_config():
    """Test configuration fixture"""
    dp = 4
    tp = 2
    role = "prefill"
    pod_ip1 = "127.0.0.1"
    pod_ip2 = "127.0.0.2"
    parallel_config = ParallelConfig(dp=dp, tp=tp)
    return {
        'dp': dp,
        'tp': tp,
        'role': role,
        'pod_ip1': pod_ip1,
        'pod_ip2': pod_ip2,
        'parallel_config': parallel_config
    }


def _cleanup_singletons():
    """Clean up singleton instances to ensure test isolation"""
    singletons_to_cleanup = [InstanceAssembler, InstanceManager]

    for singleton_cls in singletons_to_cleanup:
        if singleton_cls in ThreadSafeSingleton._instances:
            instance = ThreadSafeSingleton._instances[singleton_cls]
            try:
                if hasattr(instance, 'stop'):
                    instance.stop()
            except Exception:
                pass  # Ignore errors during cleanup
            del ThreadSafeSingleton._instances[singleton_cls]


@pytest.fixture(autouse=True)
def cleanup_singletons():
    """Auto cleanup singletons before and after each test"""
    _cleanup_singletons()
    yield
    _cleanup_singletons()


@pytest.fixture
def mock_config():
    """Mock controller config"""
    from motor.config.controller import ControllerConfig
    config = ControllerConfig()
    # Disable ETCD persistence for most tests to avoid complexity
    config.etcd_config.enable_etcd_persistence = False
    config.instance_config.instance_assemble_timeout = 1.0  # Fast timeout for tests
    config.instance_assembler_check_internal = 0.1
    config.instance_assembler_cmd_send_internal = 0.1
    config.send_cmd_retry_times = 3
    return config


@pytest.fixture
def instance_assembler(mock_config):
    """Setup mock assembler with threading mocked to prevent actual thread starts"""
    with patch('threading.Thread') as mock_thread_class:
        mock_thread = MagicMock()
        mock_thread_class.return_value = mock_thread

        with patch('motor.controller.core.instance_assembler.EtcdClient') as mock_etcd_class:
            mock_etcd = MagicMock()
            mock_etcd_class.return_value = mock_etcd

            assembler = InstanceAssembler(mock_config)
            yield assembler


# Helper functions for test data creation
def create_register_msg(job_name: str, pod_ip: str, config: dict, **kwargs) -> RegisterMsg:
    """Create a RegisterMsg with common defaults"""
    defaults = {
        'model_name': "test_model",
        'role': config['role'],
        'host_ip': pod_ip,
        'business_port': ["8080", "8084"],
        'mgmt_port': ["9090", "9094"],
        'nm_port': "8088",
        'parallel_config': config['parallel_config'],
        'ranktable': build_pod_ranktable(pod_ip=pod_ip, pod_device_num=2*config['tp'])
    }
    defaults.update(kwargs)

    return RegisterMsg(
        job_name=job_name,
        pod_ip=pod_ip,
        **defaults
    )


def create_reregister_msg(job_name: str, pod_ip: str, instance_id: int, config: dict, endpoints: list) -> ReregisterMsg:
    """Create a ReregisterMsg with common defaults"""
    # Convert endpoints dict to list if needed
    if isinstance(endpoints, dict):
        endpoints_list = list(endpoints.values())
    else:
        endpoints_list = endpoints

    return ReregisterMsg(
        job_name=job_name,
        model_name="test_model",
        instance_id=instance_id,
        role=config['role'],
        pod_ip=pod_ip,
        host_ip=pod_ip,
        nm_port="8088",
        parallel_config=config['parallel_config'],
        endpoints=endpoints_list
    )


def register_instance_with_pods(assembler: InstanceAssembler, job_name: str, config: dict, pod_count: int = 2) -> bool:
    """Register pods for an instance and return whether assembly is complete"""
    pod_ips = [f"127.0.0.{i+1}" for i in range(pod_count)]

    for i, pod_ip in enumerate(pod_ips):
        rank_offset = i * 2 * config['tp']
        msg = create_register_msg(
            job_name, pod_ip, config,
            ranktable=build_pod_ranktable(
                pod_ip=pod_ip,
                pod_device_num=2 * config['tp'],
                rank_offset=rank_offset
            )
        )
        result = assembler.register(msg)
        assert result == 0

    # Try to assemble
    if job_name in assembler.instances:
        metadata = assembler.instances[job_name]
        assembler._assemble_instance(metadata)
        return metadata.register_status == RegisterStatus.ASSEMBLED

    return False


def create_assembled_instance(assembler: InstanceAssembler, job_name: str, config: dict) -> AssembleInstanceMetadata:
    """Create and assemble a complete instance"""
    success = register_instance_with_pods(assembler, job_name, config)
    assert success, f"Failed to assemble instance {job_name}"
    return assembler.instances[job_name]


# ===== Basic Functionality Tests =====

def test_initialization(mock_config):
    """Test InstanceAssembler initialization"""
    with patch('threading.Thread') as mock_thread_class:
        with patch('motor.controller.core.instance_assembler.EtcdClient') as mock_etcd_class:
            assembler = InstanceAssembler(mock_config)

            assert assembler.etcd_config is mock_config.etcd_config
            assert assembler.ins_id_cnt == 1
            assert len(assembler.instances) == 0
            assert not assembler.stop_event.is_set()
            assert assembler._data_version == 0


def test_singleton_behavior(mock_config):
    """Test singleton pattern prevents re-initialization"""
    with patch('threading.Thread'), patch('motor.controller.core.instance_assembler.EtcdClient'):
        assembler1 = InstanceAssembler(mock_config)
        original_timeout = assembler1.instance_assemble_timeout

        # Create a different config and try to create another instance
        from motor.config.controller import ControllerConfig
        different_config = ControllerConfig()
        different_config.instance_config.instance_assemble_timeout = 999
        assembler2 = InstanceAssembler(different_config)

        # Should return the same instance
        assert assembler1 is assembler2
        # Config should not be changed by second initialization
        assert assembler1.instance_assemble_timeout == original_timeout


def test_init_with_none_config():
    """Test initialization with None config uses default"""
    with patch('threading.Thread'), patch('motor.controller.core.instance_assembler.EtcdClient'):
        assembler = InstanceAssembler(config=None)
        assert assembler.instance_assemble_timeout is not None
        assert hasattr(assembler, 'instance_assemble_timeout')


def test_register_new_instance(instance_assembler, test_config):
    """Test registering a new instance"""
    job_name = "test_job"
    msg = create_register_msg(job_name, test_config['pod_ip1'], test_config)

    result = instance_assembler.register(msg)

    assert result == 0
    assert job_name in instance_assembler.instances
    metadata = instance_assembler.instances[job_name]
    assert metadata.register_status == RegisterStatus.NOT_REGISTERED  # Initial state
    assert metadata.instance.job_name == job_name
    assert metadata.instance.id == 1  # First instance
    assert instance_assembler.ins_id_cnt == 2
    # Verify endpoints and node managers were added
    assert len(metadata.instance.endpoints) == 1
    assert len(metadata.instance.node_managers) == 1


def test_register_existing_instance(instance_assembler, test_config):
    """Test registering additional pods to existing instance"""
    job_name = "test_job"

    # First registration
    msg1 = create_register_msg(job_name, test_config['pod_ip1'], test_config)
    result1 = instance_assembler.register(msg1)
    assert result1 == 0
    assert len(instance_assembler.instances) == 1

    # Second registration to same instance
    msg2 = create_register_msg(job_name, test_config['pod_ip2'], test_config,
                              ranktable=build_pod_ranktable(
                                  pod_ip=test_config['pod_ip2'],
                                  pod_device_num=2 * test_config['tp'],
                                  rank_offset=2 * test_config['tp']
                              ))
    result2 = instance_assembler.register(msg2)
    assert result2 == 0

    # Should still be only one instance entry
    assert len(instance_assembler.instances) == 1
    metadata = instance_assembler.instances[job_name]
    assert len(metadata.instance.endpoints) == 2  # Two pods registered


def test_register_already_assembled_instance(instance_assembler, test_config):
    """Test registering to an already assembled instance returns -1"""
    job_name = "test_job"

    # Create and assemble complete instance
    metadata = create_assembled_instance(instance_assembler, job_name, test_config)

    # For new registration, instance stays in assembler with ASSEMBLED status waiting for start command
    # Only when start command is sent successfully, it gets removed
    assert job_name in instance_assembler.instances
    assert metadata.register_status == RegisterStatus.ASSEMBLED

    # Mock successful start command to remove it from assembler
    def stop_sleep(*args, **kwargs):
        raise RuntimeError("Stop iteration")

    with patch('motor.controller.api_client.node_manager_api_client.NodeManagerApiClient.send_start_command', return_value=True):
        with patch('time.sleep', side_effect=stop_sleep):
            try:
                instance_assembler._start_commmand_sender()
            except RuntimeError as e:
                if "Stop iteration" not in str(e):
                    raise

    # Now instance should be removed from assembler
    assert job_name not in instance_assembler.instances

    # Now try to register again - should return -1 since instance is fully managed
    with patch.object(InstanceManager(), 'has_active_instance_by_job_name', return_value=True):
        msg = create_register_msg(job_name, "127.0.0.3", test_config)
        result = instance_assembler.register(msg)
        assert result == -1


def test_reregister_new_instance(instance_assembler, test_config):
    """Test reregistering a new instance"""
    job_name = "test_reregister"

    # Build endpoints for reregister
    reg_msg = create_register_msg(job_name, test_config['pod_ip1'], test_config)
    endpoints = build_endpoints(reg_msg)

    msg = create_reregister_msg(job_name, test_config['pod_ip1'], instance_id=5, config=test_config, endpoints=endpoints)
    result = instance_assembler.reregister(msg)

    assert result == 0
    assert job_name in instance_assembler.instances
    metadata = instance_assembler.instances[job_name]
    assert metadata.register_status == RegisterStatus.NOT_REGISTERED  # Initial state
    assert metadata.is_reregister == True
    assert metadata.instance.id == 5
    assert instance_assembler.ins_id_cnt == 6  # instance_id + 1


def test_reregister_already_assembled_instance(instance_assembler, test_config):
    """Test reregistering to an already assembled instance returns -1"""
    job_name = "test_reregister"

    # First reregister and assemble
    reg_msg = create_register_msg(job_name, test_config['pod_ip1'], test_config)
    endpoints = build_endpoints(reg_msg)
    msg = create_reregister_msg(job_name, test_config['pod_ip1'], instance_id=0, config=test_config, endpoints=endpoints)
    result = instance_assembler.reregister(msg)
    assert result == 0

    # Register second pod to complete assembly
    reg_msg2 = create_register_msg(job_name, test_config['pod_ip2'], test_config,
                                  ranktable=build_pod_ranktable(
                                      pod_ip=test_config['pod_ip2'],
                                      pod_device_num=2 * test_config['tp'],
                                      rank_offset=2 * test_config['tp']
                                  ))
    endpoints2 = build_endpoints(reg_msg2, id_offset=test_config['tp'])
    msg2 = create_reregister_msg(job_name, test_config['pod_ip2'], instance_id=0, config=test_config, endpoints=endpoints2)
    result2 = instance_assembler.reregister(msg2)
    assert result2 == 0

    # Assemble the instance
    metadata = instance_assembler.instances[job_name]
    instance_assembler._assemble_instance(metadata)

    # Verify instance is assembled and moved to InstanceManager
    assert job_name not in instance_assembler.instances

    # Try to reregister again
    with patch.object(InstanceManager(), 'has_active_instance_by_job_name', return_value=True):
        msg3 = create_reregister_msg(job_name, "127.0.0.3", instance_id=0, config=test_config, endpoints=endpoints)
        result3 = instance_assembler.reregister(msg3)
        assert result3 == -1


def test_eval_register_status(instance_assembler, test_config):
    """Test _eval_register_status for different scenarios"""
    job_name_new = "test_new"
    job_name_assembling = "test_assembling"
    job_name_assembled = "test_assembled"

    # Test NOT_REGISTERED
    status = instance_assembler._eval_register_status(job_name_new)
    assert status == RegisterStatus.NOT_REGISTERED

    # Test ASSEMBLING
    msg = create_register_msg(job_name_assembling, test_config['pod_ip1'], test_config)
    instance_assembler.register(msg)
    status = instance_assembler._eval_register_status(job_name_assembling)
    assert status == RegisterStatus.ASSEMBLING

    # Test ASSEMBLED (instance managed by InstanceManager)
    with patch.object(InstanceManager(), 'has_active_instance_by_job_name', return_value=True):
        status = instance_assembler._eval_register_status(job_name_assembled)
        assert status == RegisterStatus.ASSEMBLED


def test_invalid_register_message(instance_assembler):
    """Test exception handling for invalid register messages"""
    with pytest.raises(Exception, match="Invalid msg provided to register"):
        instance_assembler.register(None)

    with pytest.raises(Exception, match="Invalid msg provided to register"):
        instance_assembler.register({})


def test_invalid_reregister_message(instance_assembler):
    """Test exception handling for invalid reregister messages"""
    with pytest.raises(Exception, match="Invalid msg provided to reregister"):
        instance_assembler.reregister(None)

    with pytest.raises(Exception, match="Invalid msg provided to reregister"):
        instance_assembler.reregister({})


def test_assembly_incomplete_instance(instance_assembler, test_config):
    """Test assembly of incomplete instance (not enough endpoints)"""
    job_name = "test_incomplete"

    # Register only one pod
    msg = create_register_msg(job_name, test_config['pod_ip1'], test_config, business_port=["8080"])
    instance_assembler.register(msg)

    metadata = instance_assembler.instances[job_name]
    original_status = metadata.register_status

    # Try to assemble
    instance_assembler._assemble_instance(metadata)

    # Should remain in assembling state
    assert metadata.register_status == original_status
    assert job_name in instance_assembler.instances


def test_assembly_complete_instance_new_registration(instance_assembler, test_config):
    """Test assembly of complete instance (new registration)"""
    job_name = "test_complete_new"

    # Create assembled instance
    metadata = create_assembled_instance(instance_assembler, job_name, test_config)

    # Should be assembled but still in instances (waiting for start command)
    assert metadata.register_status == RegisterStatus.ASSEMBLED
    assert job_name in instance_assembler.instances

    # Verify instance was added to InstanceManager
    instance_manager = InstanceManager()
    assert instance_manager.has_instance_by_job_name(job_name)


def test_assembly_complete_instance_reregistration(instance_assembler, test_config):
    """Test assembly of complete instance (reregistration)"""
    job_name = "test_complete_reregister"

    # Build endpoints for reregistration
    reg_msg1 = create_register_msg(job_name, test_config['pod_ip1'], test_config)
    reg_msg2 = create_register_msg(job_name, test_config['pod_ip2'], test_config,
                                  ranktable=build_pod_ranktable(
                                      pod_ip=test_config['pod_ip2'],
                                      pod_device_num=2 * test_config['tp'],
                                      rank_offset=2 * test_config['tp']
                                  ))

    endpoints1 = build_endpoints(reg_msg1)
    endpoints2 = build_endpoints(reg_msg2, id_offset=test_config['tp'])

    # Reregister both pods
    msg1 = create_reregister_msg(job_name, test_config['pod_ip1'], 0, config=test_config, endpoints=endpoints1)
    msg2 = create_reregister_msg(job_name, test_config['pod_ip2'], 0, config=test_config, endpoints=endpoints2)

    instance_assembler.reregister(msg1)
    instance_assembler.reregister(msg2)

    metadata = instance_assembler.instances[job_name]
    assert metadata.is_reregister == True

    # Assemble
    instance_assembler._assemble_instance(metadata)

    # For reregistration, instance should be removed from assembler after assembly
    assert job_name not in instance_assembler.instances

    # Verify instance was added to InstanceManager
    instance_manager = InstanceManager()
    assert instance_manager.has_instance_by_job_name(job_name)


def test_assembly_timeout(instance_assembler, test_config):
    """Test instance assembly timeout"""
    job_name = "test_timeout"

    # Set short timeout
    instance_assembler.instance_assemble_timeout = 0.1

    # Register incomplete instance
    msg = create_register_msg(job_name, test_config['pod_ip1'], test_config, business_port=["8080"])
    instance_assembler.register(msg)

    # Wait for timeout
    import time
    time.sleep(0.15)

    # Try to assemble - should remove timed out instance
    metadata = instance_assembler.instances[job_name]
    instance_assembler._assemble_instance(metadata)

    # Instance should be removed due to timeout
    assert job_name not in instance_assembler.instances


def test_send_start_command_success(instance_assembler, test_config):
    """Test successful start command sending"""
    job_name = "test_start_success"

    # Create assembled instance
    metadata = create_assembled_instance(instance_assembler, job_name, test_config)

    # Mock successful API calls
    with patch('motor.controller.api_client.node_manager_api_client.NodeManagerApiClient.send_start_command') as mock_send:
        mock_send.return_value = True

        result = instance_assembler._send_start_command(metadata)

        assert result == True
        # Should be called for each node manager
        assert mock_send.call_count == len(metadata.instance.node_managers)


def test_send_start_command_partial_failure(instance_assembler, test_config):
    """Test start command with partial failure"""
    job_name = "test_start_partial_failure"

    # Create assembled instance
    metadata = create_assembled_instance(instance_assembler, job_name, test_config)

    # Mock partial failure
    call_count = 0
    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return call_count == 1  # First call succeeds, second fails

    with patch('motor.controller.api_client.node_manager_api_client.NodeManagerApiClient.send_start_command') as mock_send:
        mock_send.side_effect = side_effect

        result = instance_assembler._send_start_command(metadata)

        assert result == False  # Should return False if any fails
        assert mock_send.call_count == len(metadata.instance.node_managers)


def test_send_start_command_no_endpoints(instance_assembler, test_config):
    """Test start command when some node managers have no endpoints"""
    # Create instance with node managers but only one has endpoints
    instance = Instance(
        job_name="test_no_endpoints",
        model_name="test_model",
        id=1,
        role=test_config['role'],
        parallel_config=test_config['parallel_config']
    )

    # Add node managers
    instance.add_node_mgr("127.0.0.1", "127.0.0.1", "8088")
    instance.add_node_mgr("127.0.0.2", "127.0.0.2", "8089")

    # Only add endpoints for first node manager
    reg_msg = create_register_msg("test", "127.0.0.1", test_config)
    pod_endpoints = build_endpoints(reg_msg)
    instance.add_endpoints("127.0.0.1", pod_endpoints)

    metadata = AssembleInstanceMetadata(instance=instance)

    with patch('motor.controller.api_client.node_manager_api_client.NodeManagerApiClient.send_start_command') as mock_send:
        mock_send.return_value = True

        result = instance_assembler._send_start_command(metadata)

        assert result == True
        # Should only be called for node manager with endpoints
        assert mock_send.call_count == 1


def test_start_command_sender_success(instance_assembler, test_config):
    """Test _start_command_sender removes instance after successful start"""
    job_name = "test_sender_success"

    # Create assembled instance
    metadata = create_assembled_instance(instance_assembler, job_name, test_config)

    # Mock successful send
    def stop_sleep(*args, **kwargs):
        raise RuntimeError("Stop iteration")

    with patch('motor.controller.api_client.node_manager_api_client.NodeManagerApiClient.send_start_command') as mock_send:
        mock_send.return_value = True

        # Mock time.sleep to stop after one iteration
        with patch('time.sleep', side_effect=stop_sleep):
            try:
                instance_assembler._start_commmand_sender()
            except RuntimeError as e:
                if "Stop iteration" not in str(e):
                    raise

        # Instance should be removed after successful start command
        assert job_name not in instance_assembler.instances


def test_start_command_sender_retry(instance_assembler, test_config):
    """Test _start_command_sender retries on failure"""
    job_name = "test_sender_retry"

    # Create assembled instance
    metadata = create_assembled_instance(instance_assembler, job_name, test_config)

    # Mock failed send
    def stop_sleep(*args, **kwargs):
        raise RuntimeError("Stop iteration")

    with patch('motor.controller.api_client.node_manager_api_client.NodeManagerApiClient.send_start_command') as mock_send:
        mock_send.return_value = False

        # Mock time.sleep to stop after one iteration
        with patch('time.sleep', side_effect=stop_sleep):
            try:
                instance_assembler._start_commmand_sender()
            except RuntimeError as e:
                if "Stop iteration" not in str(e):
                    raise

        # Instance should still be there with incremented retry count
        assert job_name in instance_assembler.instances
        assert instance_assembler.instances[job_name].start_command_send_times == 1


def test_start_command_sender_max_retries(instance_assembler, test_config):
    """Test _start_command_sender removes instance after max retries"""
    job_name = "test_sender_max_retries"

    # Set max retries to 2 (so we can see the retry count increment)
    instance_assembler.send_cmd_retry_times = 2

    # Create assembled instance
    metadata = create_assembled_instance(instance_assembler, job_name, test_config)

    # Mock failed sends
    def stop_sleep(*args, **kwargs):
        raise RuntimeError("Stop iteration")

    with patch('motor.controller.api_client.node_manager_api_client.NodeManagerApiClient.send_start_command') as mock_send:
        mock_send.return_value = False

        # First attempt - should increment retry count
        with patch('time.sleep', side_effect=stop_sleep):
            try:
                instance_assembler._start_commmand_sender()
            except RuntimeError as e:
                if "Stop iteration" not in str(e):
                    raise

        # Should still be there after first failure, retry count incremented
        assert job_name in instance_assembler.instances
        assert instance_assembler.instances[job_name].start_command_send_times == 1

        # Second attempt - should remove instance since max retries (2) reached
        with patch('time.sleep', side_effect=stop_sleep):
            try:
                instance_assembler._start_commmand_sender()
            except RuntimeError as e:
                if "Stop iteration" not in str(e):
                    raise

        # Instance should be removed after max retries
        assert job_name not in instance_assembler.instances


def test_persist_data_disabled(mock_config):
    """Test persist_data when ETCD persistence is disabled"""
    # Create assembler with persistence disabled
    mock_config.etcd_config.enable_etcd_persistence = False

    with patch('threading.Thread'), patch('motor.controller.core.instance_assembler.EtcdClient') as mock_etcd_class:
        mock_etcd = MagicMock()
        mock_etcd.persist_data.return_value = True
        mock_etcd_class.return_value = mock_etcd

        assembler = InstanceAssembler(mock_config)

        result = assembler.persist_data()
        # persist_data always calls etcd_client.persist_data regardless of enable_etcd_persistence flag
        assert result == True


def test_persist_data_enabled(instance_assembler, test_config):
    """Test persist_data when ETCD persistence is enabled"""
    # etcd persistence is already enabled in the config used for initialization

    # Create some test data
    create_assembled_instance(instance_assembler, "test_job", test_config)

    # Reset mock to clear previous calls
    instance_assembler.etcd_client.persist_data.reset_mock()

    result = instance_assembler.persist_data()

    # Verify persist was called on etcd_client
    instance_assembler.etcd_client.persist_data.assert_called_once()
    args, kwargs = instance_assembler.etcd_client.persist_data.call_args
    assert "/controller/instance_assembler" in args[0]
    # Should contain data for ins_id_cnt and the instance
    assert len(args[1]) >= 2


def test_restore_data_disabled(instance_assembler, test_config):
    """Test restore_data when ETCD persistence is disabled"""
    result = instance_assembler.restore_data()
    assert result == True


def test_restore_data_enabled(instance_assembler, test_config):
    """Test restore_data when ETCD persistence is enabled"""
    # etcd persistence is already enabled in the config used for initialization

    # Mock ETCD returning some data
    mock_persistent_states = {
        "ins_id_cnt": PersistentAssembleInstanceMetadataState(
            metadata_data={"ins_id_cnt": 5},
            version=1,
            timestamp=time.time(),
            checksum="dummy_checksum"
        )
    }

    with patch.object(instance_assembler.etcd_client, 'restore_data', return_value=mock_persistent_states):
        with patch.object(mock_persistent_states["ins_id_cnt"], 'is_valid', return_value=True):
            result = instance_assembler.restore_data()

            assert result == True
            assert instance_assembler.ins_id_cnt == 5


def test_checksum_calculation(instance_assembler, test_config):
    """Test checksum calculation for data integrity"""
    # Create test metadata
    metadata = create_assembled_instance(instance_assembler, "test_checksum", test_config)

    # Create a persistent state to test checksum calculation
    metadata_data = {
        "job_name": metadata.instance.job_name,
        "model_name": metadata.instance.model_name,
        "instance_id": metadata.instance.id,
        "role": metadata.instance.role.value if hasattr(metadata.instance.role, 'value') else str(metadata.instance.role),
        "parallel_config": metadata.instance.parallel_config.model_dump() if hasattr(metadata.instance.parallel_config, 'model_dump') else metadata.instance.parallel_config,
        "endpoints": {
            pod_ip: {eid: endpoint.model_dump() for eid, endpoint in endpoints.items()}
            for pod_ip, endpoints in metadata.instance.endpoints.items()
        },
        "node_managers": [
            (nm.pod_ip, nm.host_ip, nm.port) for nm in metadata.instance.node_managers
        ],
        "register_status": metadata.register_status.value,
        "start_command_send_times": metadata.start_command_send_times,
        "register_timestamp": metadata.register_timestamp,
        "is_reregister": metadata.is_reregister
    }

    state = PersistentAssembleInstanceMetadataState(
        metadata_data=metadata_data,
        version=1,
        timestamp=time.time(),
        checksum=""
    )

    checksum = state.calculate_checksum()

    assert isinstance(checksum, str)
    assert len(checksum) > 0

    # Same data should produce same checksum
    checksum2 = state.calculate_checksum()
    assert checksum == checksum2


def test_ins_id_cnt_checksum(instance_assembler):
    """Test checksum calculation for ins_id_cnt"""
    instance_assembler.ins_id_cnt = 42

    # Create a persistent state for ins_id_cnt
    ins_id_cnt_data = {"ins_id_cnt": instance_assembler.ins_id_cnt}
    state = PersistentAssembleInstanceMetadataState(
        metadata_data=ins_id_cnt_data,
        version=1,
        timestamp=time.time(),
        checksum=""
    )

    checksum = state.calculate_checksum()

    assert isinstance(checksum, str)
    assert len(checksum) > 0

    # Same value should produce same checksum
    checksum2 = state.calculate_checksum()
    assert checksum == checksum2


def test_persist_data_exception_handling(instance_assembler, test_config):
    """Test persist_data exception handling"""
    # Create test data
    create_assembled_instance(instance_assembler, "test_persist_exception", test_config)

    # Mock etcd_client.persist_data to raise an exception
    with patch.object(instance_assembler.etcd_client, 'persist_data', side_effect=Exception("ETCD connection failed")):
        result = instance_assembler.persist_data()

        assert result == False


def test_restore_data_exception_handling(instance_assembler):
    """Test restore_data exception handling"""
    # Mock etcd_client.restore_data to raise an exception
    with patch.object(instance_assembler.etcd_client, 'restore_data', side_effect=Exception("ETCD connection failed")):
        result = instance_assembler.restore_data()

        assert result == False


def test_restore_data_invalid_checksum(instance_assembler):
    """Test restore_data with invalid checksum (corrupted data)"""
    # Create mock persistent state with invalid checksum
    mock_persistent_states = {
        "ins_id_cnt": PersistentAssembleInstanceMetadataState(
            metadata_data={"ins_id_cnt": 5},
            version=1,
            timestamp=time.time(),
            checksum="invalid_checksum"  # Wrong checksum
        )
    }

    with patch.object(instance_assembler.etcd_client, 'restore_data', return_value=mock_persistent_states):
        result = instance_assembler.restore_data()

        assert result == True  # Should succeed but skip invalid data
        assert instance_assembler.ins_id_cnt == 1  # Should not restore invalid data


def test_restore_data_reconstruction_exception(instance_assembler):
    """Test restore_data with reconstruction exception"""
    # Mock the Instance constructor to raise an exception
    with patch('motor.controller.core.instance_assembler.Instance') as mock_instance_class:
        mock_instance_class.side_effect = Exception("Instance creation failed")

        mock_persistent_states = {
            "test_instance": PersistentAssembleInstanceMetadataState(
                metadata_data={
                    "job_name": "test_instance",
                    "model_name": "test_model",
                    "instance_id": 0,
                    "role": "prefill",
                    "parallel_config": {
                        "dp_size": 1, "cp_size": 1, "tp_size": 1, "sp_size": 1,
                        "ep_size": 1, "pp_size": 1, "world_size": 1
                    },
                    "endpoints": {},
                    "node_managers": [],
                    "register_status": 0,
                    "start_command_send_times": 0,
                    "register_timestamp": time.time(),
                    "is_reregister": False
                },
                version=1,
                timestamp=time.time(),
                checksum="dummy_checksum"
            )
        }

        with patch.object(instance_assembler.etcd_client, 'restore_data', return_value=mock_persistent_states):
            with patch.object(mock_persistent_states["test_instance"], 'is_valid', return_value=True):
                result = instance_assembler.restore_data()

                assert result == True  # Should succeed but skip problematic instance
                assert len(instance_assembler.instances) == 0  # Should not restore invalid instance


def test_checksum_calculation_exception_handling(instance_assembler, test_config):
    """Test checksum calculation exception handling"""
    # Create test metadata
    metadata = create_assembled_instance(instance_assembler, "test_checksum_exception", test_config)

    # Create a persistent state to test checksum calculation
    metadata_data = {
        "job_name": metadata.instance.job_name,
        "model_name": metadata.instance.model_name,
        "instance_id": metadata.instance.id,
        "role": metadata.instance.role.value if hasattr(metadata.instance.role, 'value') else str(metadata.instance.role),
        "parallel_config": metadata.instance.parallel_config.model_dump() if hasattr(metadata.instance.parallel_config, 'model_dump') else metadata.instance.parallel_config,
        "endpoints": {
            pod_ip: {eid: endpoint.model_dump() for eid, endpoint in endpoints.items()}
            for pod_ip, endpoints in metadata.instance.endpoints.items()
        },
        "node_managers": [
            (nm.pod_ip, nm.host_ip, nm.port) for nm in metadata.instance.node_managers
        ],
        "register_status": metadata.register_status.value,
        "start_command_send_times": metadata.start_command_send_times,
        "register_timestamp": metadata.register_timestamp,
        "is_reregister": metadata.is_reregister
    }

    state = PersistentAssembleInstanceMetadataState(
        metadata_data=metadata_data,
        version=1,
        timestamp=time.time(),
        checksum=""
    )

    # Mock hashlib.sha256 to raise an exception
    with patch.object(hashlib, 'sha256', side_effect=Exception("Hash calculation failed")):
        checksum = state.calculate_checksum()

        assert checksum == ""  # Should return empty string on exception


def test_ins_id_cnt_checksum_exception_handling(instance_assembler):
    """Test ins_id_cnt checksum calculation exception handling"""
    instance_assembler.ins_id_cnt = 42

    # Create a persistent state for ins_id_cnt
    ins_id_cnt_data = {"ins_id_cnt": instance_assembler.ins_id_cnt}
    state = PersistentAssembleInstanceMetadataState(
        metadata_data=ins_id_cnt_data,
        version=1,
        timestamp=time.time(),
        checksum=""
    )

    # Mock hashlib.sha256 to raise an exception
    with patch.object(hashlib, 'sha256', side_effect=Exception("Hash calculation failed")):
        checksum = state.calculate_checksum()

        assert checksum == ""  # Should return empty string on exception


def test_persistent_state_is_valid_method():
    """Test PersistentAssembleInstanceMetadataState.is_valid method"""
    # Create a valid state
    valid_state = PersistentAssembleInstanceMetadataState(
        metadata_data={"test": "data"},
        version=1,
        timestamp=time.time(),
        checksum=""  # Will be calculated
    )

    # Manually set correct checksum
    valid_state.checksum = valid_state.calculate_checksum()
    assert valid_state.is_valid() == True

    # Create invalid state with wrong checksum
    invalid_state = PersistentAssembleInstanceMetadataState(
        metadata_data={"test": "data"},
        version=1,
        timestamp=time.time(),
        checksum="wrong_checksum"
    )
    assert invalid_state.is_valid() == False


def test_restore_data_with_type_conversion():
    """Test restoration with string-formatted data from ETCD (type conversion)"""
    # Simulate metadata data as it would come from ETCD - all values are strings
    etcd_string_metadata = {
        "job_name": "test_type_conversion",
        "model_name": "test_model",
        "instance_id": "208",  # int as string
        "role": "prefill",
        "parallel_config": None,
        "endpoints": {},  # dict
        "node_managers": [],  # list
        "register_status": "2",  # enum value as string from ETCD
        "start_command_send_times": "0",  # int as string
        "register_timestamp": str(time.time()),  # float as string
        "is_reregister": "False"  # bool as string
    }

    # Mock persistent state with string-formatted metadata
    mock_persistent_states = {
        "test_type_conversion": PersistentAssembleInstanceMetadataState(
            metadata_data=etcd_string_metadata,
            version=1,
            timestamp=time.time(),
            checksum=""  # Will be calculated
        )
    }

    # Calculate correct checksum for the string metadata
    mock_persistent_states["test_type_conversion"].checksum = mock_persistent_states["test_type_conversion"].calculate_checksum()

    with patch('motor.controller.core.instance_assembler.EtcdClient') as mock_etcd_class:
        mock_client = MagicMock()
        mock_client.restore_data.return_value = mock_persistent_states
        mock_etcd_class.return_value = mock_client

        # Create assembler with ETCD enabled
        from motor.config.controller import ControllerConfig
        config = ControllerConfig()
        config.etcd_config.enable_etcd_persistence = True

        with patch('threading.Thread'):
            assembler = InstanceAssembler(config)
            result = assembler.restore_data()

        # Should succeed - Pydantic should handle type conversion
        assert result == True
        assert "test_type_conversion" in assembler.instances

        metadata = assembler.instances["test_type_conversion"]
        assert metadata.instance.id == 208  # string "208" converted to int 208
        assert metadata.register_status == RegisterStatus.ASSEMBLED  # string "2" converted to enum
        assert metadata.start_command_send_times == 0  # string "0" converted to int 0
        assert metadata.is_reregister == False  # string "False" converted to bool False


def test_restore_data_with_invalid_enum_value():
    """Test restoration fails gracefully with invalid enum values in metadata"""
    # Simulate corrupted metadata with invalid enum value
    corrupted_metadata = {
        "job_name": "test_invalid_enum",
        "model_name": "test_model",
        "instance_id": "209",
        "role": "prefill",
        "parallel_config": None,
        "endpoints": {},
        "node_managers": [],
        "register_status": "999",  # Invalid enum value as string
        "start_command_send_times": "0",
        "register_timestamp": str(time.time()),
        "is_reregister": "False"
    }

    mock_persistent_states = {
        "test_invalid_enum": PersistentAssembleInstanceMetadataState(
            metadata_data=corrupted_metadata,
            version=1,
            timestamp=time.time(),
            checksum=""  # Will be calculated
        )
    }

    # Calculate checksum for corrupted metadata
    mock_persistent_states["test_invalid_enum"].checksum = mock_persistent_states["test_invalid_enum"].calculate_checksum()

    with patch('motor.controller.core.instance_assembler.EtcdClient') as mock_etcd_class:
        mock_client = MagicMock()
        mock_client.restore_data.return_value = mock_persistent_states
        mock_etcd_class.return_value = mock_client

        # Create assembler with ETCD enabled
        from motor.config.controller import ControllerConfig
        config = ControllerConfig()
        config.etcd_config.enable_etcd_persistence = True

        with patch('threading.Thread'):
            assembler = InstanceAssembler(config)

            # This should succeed (data restoration succeeds) but metadata reconstruction fails
            result = assembler.restore_data()
            assert result == True

            # Instance should not be restored due to validation error
            assert "test_invalid_enum" not in assembler.instances


def test_start_method(mock_config):
    """Test start method starts threads"""
    with patch('threading.Thread') as mock_thread_class:
        with patch('motor.controller.core.instance_assembler.EtcdClient'):
            assembler = InstanceAssembler(mock_config)

            assembler.start()

            # Verify two threads were created and started
            assert mock_thread_class.call_count == 2
            assert mock_thread_class.return_value.start.call_count == 2


def test_stop_method(mock_config):
    """Test stop method sets stop event and joins threads"""
    with patch('threading.Thread') as mock_thread_class:
        with patch('motor.controller.core.instance_assembler.EtcdClient'):
            mock_thread1 = MagicMock()
            mock_thread2 = MagicMock()
            mock_thread1.is_alive.return_value = True
            mock_thread2.is_alive.return_value = True
            mock_thread_class.side_effect = [mock_thread1, mock_thread2]

            assembler = InstanceAssembler(mock_config)
            assembler.start()  # Start to initialize threads

            assembler.stop()

            # Verify stop event is set
            assert assembler.stop_event.is_set()

            # Verify threads were joined
            mock_thread1.join.assert_called_once()
            mock_thread2.join.assert_called_once()


def test_instances_assembler_loop_stop_event(instance_assembler, test_config):
    """Test _instances_assembler_loop respects stop event"""
    # Set stop event
    instance_assembler.stop_event.set()

    # Mock sleep to raise RuntimeError when stop event is set
    def stop_sleep(*args, **kwargs):
        raise RuntimeError("Stop iteration")

    with patch('time.sleep', side_effect=stop_sleep):
        try:
            instance_assembler._instances_assembler_loop()
        except RuntimeError as e:
            if "Stop iteration" not in str(e):
                raise

    # Should exit without processing


def test_multiple_instances_registration(instance_assembler, test_config):
    """Test registering multiple instances"""
    num_instances = 5

    for i in range(num_instances):
        job_name = f"perf_test_{i}"
        success = register_instance_with_pods(instance_assembler, job_name, test_config)
        assert success

    assert len(instance_assembler.instances) == num_instances

    # Verify all instances have unique IDs
    ids = [metadata.instance.id for metadata in instance_assembler.instances.values()]
    assert len(set(ids)) == num_instances


def test_ins_id_cnt_increment(instance_assembler, test_config):
    """Test ins_id_cnt increments correctly"""
    initial_cnt = instance_assembler.ins_id_cnt

    # Register first instance
    register_instance_with_pods(instance_assembler, "job1", test_config)
    assert instance_assembler.ins_id_cnt == initial_cnt + 1

    # Register second instance
    register_instance_with_pods(instance_assembler, "job2", test_config)
    assert instance_assembler.ins_id_cnt == initial_cnt + 2


def test_update_config(instance_assembler):
    """Test update_config method updates configuration and recreates ETCD client"""
    from unittest.mock import patch

    # Store original etcd config
    original_etcd_config = instance_assembler.etcd_config

    # Create new config with different ETCD settings
    from motor.config.controller import ControllerConfig
    new_config = ControllerConfig()
    new_config.etcd_config.etcd_host = "new-etcd-host"
    new_config.etcd_config.etcd_port = 2380
    new_config.etcd_config.etcd_timeout = 30.0
    new_config.etcd_config.enable_etcd_persistence = True

    with patch('motor.controller.core.instance_assembler.EtcdClient') as mock_etcd_class:
        mock_client = MagicMock()
        mock_etcd_class.return_value = mock_client

        # Clear the mock call history to track new calls
        mock_etcd_class.reset_mock()

        # Update config
        instance_assembler.update_config(new_config)

        # Verify config was updated
        assert instance_assembler.etcd_config is new_config.etcd_config
        assert instance_assembler.etcd_config.etcd_host == "new-etcd-host"
        assert instance_assembler.etcd_config.etcd_port == 2380
        assert instance_assembler.etcd_config.etcd_timeout == 30.0

        # Verify ETCD client constructor was called with new config
        mock_etcd_class.assert_called_once_with(
            host="new-etcd-host",
            port=2380,
            ca_cert=new_config.etcd_config.etcd_ca_cert,
            cert_key=new_config.etcd_config.etcd_cert_key,
            cert_cert=new_config.etcd_config.etcd_cert_cert,
            timeout=30.0
        )


# ===== Persistence and Recovery Tests =====

def test_persist_and_restore_data_success(instance_assembler, test_config):
    """Test successful persist and restore of instance assembler data"""
    # Create test instance
    metadata = create_assembled_instance(instance_assembler, "test_persist", test_config)

    # Enable persistence in config
    instance_assembler.etcd_config.enable_etcd_persistence = True

    # Mock successful ETCD operations
    with patch.object(instance_assembler.etcd_client, 'persist_data', return_value=True) as mock_persist:
        with patch.object(instance_assembler.etcd_client, 'restore_data') as mock_restore:
            # Persist data
            persist_result = instance_assembler.persist_data()
            assert persist_result == True

            # Verify persist was called
            mock_persist.assert_called_once()
            args, kwargs = mock_persist.call_args
            assert "/controller/instance_assembler" in args[0]

            # Create mock persistent states for restore
            mock_persistent_states = {}

            # Add ins_id_cnt state
            ins_id_cnt_state = PersistentAssembleInstanceMetadataState(
                metadata_data={"ins_id_cnt": instance_assembler.ins_id_cnt},
                version=1,
                timestamp=time.time(),
                checksum=""
            )
            ins_id_cnt_state.checksum = ins_id_cnt_state.calculate_checksum()
            mock_persistent_states["ins_id_cnt"] = ins_id_cnt_state

            # Add instance state
            metadata_data = {
                "job_name": metadata.instance.job_name,
                "model_name": metadata.instance.model_name,
                "instance_id": metadata.instance.id,
                "role": metadata.instance.role.value if hasattr(metadata.instance.role, 'value') else str(metadata.instance.role),
                "parallel_config": metadata.instance.parallel_config.model_dump() if hasattr(metadata.instance.parallel_config, 'model_dump') else metadata.instance.parallel_config,
                "endpoints": {
                    pod_ip: {eid: endpoint.model_dump() for eid, endpoint in endpoints.items()}
                    for pod_ip, endpoints in metadata.instance.endpoints.items()
                },
                "node_managers": [
                    (nm.pod_ip, nm.host_ip, nm.port) for nm in metadata.instance.node_managers
                ],
                "register_status": metadata.register_status.value,
                "start_command_send_times": metadata.start_command_send_times,
                "register_timestamp": metadata.register_timestamp,
                "is_reregister": metadata.is_reregister
            }

            instance_state = PersistentAssembleInstanceMetadataState(
                metadata_data=metadata_data,
                version=1,
                timestamp=time.time(),
                checksum=""
            )
            instance_state.checksum = instance_state.calculate_checksum()
            mock_persistent_states["test_persist"] = instance_state

            mock_restore.return_value = mock_persistent_states

            # Create new assembler instance for restore test
            with patch('threading.Thread'), patch('motor.controller.core.instance_assembler.EtcdClient'):
                # Create a mock config similar to the original
                from motor.config.controller import ControllerConfig
                new_config = ControllerConfig()
                new_config.etcd_config.enable_etcd_persistence = True
                new_config.instance_config.instance_assemble_timeout = 1.0
                new_config.instance_config.instance_assembler_check_internal = 0.1
                new_config.instance_config.instance_assembler_cmd_send_internal = 0.1
                new_config.instance_config.send_cmd_retry_times = 3
                new_assembler = InstanceAssembler(new_config)

                # Restore data
                restore_result = new_assembler.restore_data()
                assert restore_result == True

                # Verify data was restored
                assert new_assembler.ins_id_cnt == instance_assembler.ins_id_cnt
                assert "test_persist" in new_assembler.instances
                restored_metadata = new_assembler.instances["test_persist"]
                assert restored_metadata.instance.job_name == metadata.instance.job_name
                assert restored_metadata.register_status == metadata.register_status


def test_persist_data_with_checksum_validation(instance_assembler, test_config):
    """Test that persisted data includes correct checksums"""
    # Create test instance
    metadata = create_assembled_instance(instance_assembler, "test_checksum", test_config)

    # Enable persistence
    instance_assembler.etcd_config.enable_etcd_persistence = True

    with patch.object(instance_assembler.etcd_client, 'persist_data', return_value=True) as mock_persist:
        # Persist data
        result = instance_assembler.persist_data()
        assert result == True

        # Verify the data passed to persist_data
        args, kwargs = mock_persist.call_args
        persisted_data = args[1]

        # Should contain ins_id_cnt and the instance
        assert "ins_id_cnt" in persisted_data
        assert "test_checksum" in persisted_data

        # Verify checksums are present and non-empty
        for key, data_dict in persisted_data.items():
            assert "checksum" in data_dict
            assert len(data_dict["checksum"]) > 0

            # Verify checksum is valid by reconstructing the state
            state = PersistentAssembleInstanceMetadataState(**data_dict)
            assert state.is_valid()


def test_restore_data_with_invalid_checksum(instance_assembler, test_config):
    """Test restore skips data with invalid checksums"""
    # Create mock persistent states with invalid checksum
    mock_persistent_states = {
        "ins_id_cnt": PersistentAssembleInstanceMetadataState(
            metadata_data={"ins_id_cnt": 5},
            version=1,
            timestamp=time.time(),
            checksum="invalid_checksum"  # Wrong checksum
        )
    }

    with patch.object(instance_assembler.etcd_client, 'restore_data', return_value=mock_persistent_states):
        result = instance_assembler.restore_data()

        # Should succeed but skip invalid data
        assert result == True
        assert instance_assembler.ins_id_cnt == 1  # Should not restore invalid data


def test_persistence_disabled_in_config(instance_assembler, test_config):
    """Test that persistence is properly disabled when config flag is False"""
    # Ensure persistence is disabled
    instance_assembler.etcd_config.enable_etcd_persistence = False

    # Create test instance
    create_assembled_instance(instance_assembler, "test_disabled", test_config)

    # Register should not call persist (only called when enable_persistence is True)
    with patch.object(instance_assembler.etcd_client, 'persist_data', return_value=True) as mock_persist:
        # Try to persist manually - should still work but not be called from register
        result = instance_assembler.persist_data()
        assert result == True  # persist_data always calls etcd_client.persist_data regardless of flag

        # But register should not call persist when disabled
        msg = create_register_msg("test_register_disabled", test_config['pod_ip1'], test_config)
        instance_assembler.register(msg)

        # persist_data should not have been called again (only once from manual call above)
        assert mock_persist.call_count == 1


def test_persist_empty_state(instance_assembler):
    """Test persisting when no instances exist"""
    # Enable persistence
    instance_assembler.etcd_config.enable_etcd_persistence = True

    with patch.object(instance_assembler.etcd_client, 'persist_data', return_value=True) as mock_persist:
        result = instance_assembler.persist_data()
        assert result == True

        # Verify data was persisted
        args, kwargs = mock_persist.call_args
        persisted_data = args[1]

        # Should only contain ins_id_cnt
        assert len(persisted_data) == 1
        assert "ins_id_cnt" in persisted_data

        # Verify ins_id_cnt data
        ins_id_cnt_data = persisted_data["ins_id_cnt"]
        assert ins_id_cnt_data["metadata_data"]["ins_id_cnt"] == instance_assembler.ins_id_cnt
        assert ins_id_cnt_data["version"] >= 1
        assert ins_id_cnt_data["timestamp"] > 0
        assert len(ins_id_cnt_data["checksum"]) > 0


def test_restore_no_data_available(instance_assembler):
    """Test restore when no data is available in ETCD"""
    with patch.object(instance_assembler.etcd_client, 'restore_data', return_value=None):
        result = instance_assembler.restore_data()

        # Should succeed with empty state
        assert result == True
        assert len(instance_assembler.instances) == 0
        assert instance_assembler.ins_id_cnt == 1  # Default value
