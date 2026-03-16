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
""" Test cases are organized according to the following 7 logical blocks:
1. Initialization
2. Persistence and Recovery
3. Start and Update Methods
4. Dynamic Configuration Update
5. Resource Monitoring and Update
6. Instance and node status Updating
7. Strategy Center Processing
"""
import pytest
from unittest.mock import Mock, patch, MagicMock

from motor.common.resources.instance import Instance, NodeManagerInfo
from motor.config.controller import ControllerConfig
from motor.controller.core import ObserverEvent
from motor.controller.fault_tolerance.fault_manager import (
    FaultManager, NodeMetadata, InstanceMetadata
)
from motor.controller.fault_tolerance.k8s.cluster_fault_codes import (
    NodeStatus, FaultLevel, FaultType, FaultInfo, SpecialFaultCode,
)

# Test constants
TEST_IPS = ["192.168.1.1", "192.168.1.2", "192.168.1.99"]
TEST_PORT = "8080"
TEST_FAULT_CODES = [0x1234, 0x2000, 0x3000, 0x3001, 0x4000, 0x00f1fef5]


def FI(*, fault_type, npu_name, fault_code, fault_level):
    """Short constructor for reusable FaultInfo constants in tests."""
    return FaultInfo(
        fault_type=fault_type,
        npu_name=npu_name,
        fault_code=fault_code,
        fault_level=fault_level,
    )


FAULT_DEVICE_L1_0x1000 = FI(
    fault_type=FaultType.CARD_UNHEALTHY, npu_name="npu0", fault_code=0x1000, fault_level=FaultLevel.L1
)
FAULT_DEVICE_L2_0x1000 = FI(
    fault_type=FaultType.CARD_UNHEALTHY, npu_name="npu0", fault_code=0x1000, fault_level=FaultLevel.L2
)
FAULT_DEVICE_L2 = FI(
    fault_type=FaultType.CARD_UNHEALTHY, npu_name="npu0", fault_code=0x2000, fault_level=FaultLevel.L2
)
FAULT_DEVICE_L3 = FI(
    fault_type=FaultType.CARD_UNHEALTHY, npu_name="npu0", fault_code=0x2000, fault_level=FaultLevel.L3
)
FAULT_SWITCH_L2 = FI(
    fault_type=FaultType.CARD_NETWORK_UNHEALTHY, npu_name="switch0", fault_code=0x2000, fault_level=FaultLevel.L2
)
FAULT_NODE_L3 = FI(
    fault_type=FaultType.NODE_UNHEALTHY, npu_name="", fault_code=0x3000, fault_level=FaultLevel.L3
)
FAULT_CM_DEVICE_L3_0x1234 = FI(
    fault_type=FaultType.CARD_UNHEALTHY, npu_name="npu0", fault_code=0x1234, fault_level=FaultLevel.L3
)
FAULT_CM_SWITCH_L2_0x5678 = FI(
    fault_type=FaultType.CARD_NETWORK_UNHEALTHY, npu_name="switch0", fault_code=0x5678, fault_level=FaultLevel.L2
)


def _assert_instance_fault(instance, *, fault_level, fault_code):
    assert instance.fault_level == fault_level
    assert instance.fault_code == fault_code


def _assert_fault_info(fault, *, fault_level, fault_code, fault_type):
    assert fault is not None
    assert fault.fault_level == fault_level
    assert fault.fault_code == fault_code
    assert fault.fault_type == fault_type


def _etcd_node_entry(*, pod_ip, node_name, instance_id, node_status, fault_infos):
    return {
        "pod_ip": pod_ip,
        "node_name": node_name,
        "instance_id": instance_id,
        "node_status": node_status.value,
        "fault_infos": fault_infos,
    }


def _etcd_instance_entry(*, instance_id, fault_level, fault_code):
    return {"instance_id": instance_id, "fault_level": fault_level.value, "fault_code": fault_code}


@pytest.fixture(autouse=True)
def mock_etcd_client():
    """Mock EtcdClient to avoid real ETCD operations in tests"""
    with patch('motor.controller.fault_tolerance.fault_manager.EtcdClient') as mock_etcd_class:
        mock_client = MagicMock()
        mock_client.persist_data.return_value = True
        mock_client.restore_data.return_value = None
        mock_etcd_class.return_value = mock_client
        yield mock_client


@pytest.fixture(autouse=True)
def setup_test_environment():
    """Setup and teardown for each test"""
    from motor.common.utils.singleton import ThreadSafeSingleton
    # Clear singleton instances before each test
    if FaultManager in ThreadSafeSingleton._instances:
        fault_manager = ThreadSafeSingleton._instances[FaultManager]
        fault_manager.stop()
        del ThreadSafeSingleton._instances[FaultManager]


@pytest.fixture
def fault_manager():
    """Create a basic FaultManager instance for testing"""
    config = ControllerConfig()
    return FaultManager(config)


@pytest.fixture
def fault_manager_with_instances():
    """Create a FaultManager instance with pre-configured instances and nodes"""
    with patch('motor.controller.fault_tolerance.fault_manager.EtcdClient') as mock_etcd_class:
        mock_client = MagicMock()
        mock_client.persist_data.return_value = True
        mock_client.restore_data.return_value = None
        mock_etcd_class.return_value = mock_client

        config = ControllerConfig()
        manager = FaultManager(config)

        ins_metadata1 = InstanceMetadata(instance_id=1)
        manager.instances[1] = ins_metadata1
        manager.nodes["node_0"] = NodeMetadata(pod_ip="192.168.1.1", node_name="node_0", instance_id=1)
        manager.nodes["node_1"] = NodeMetadata(pod_ip="192.168.1.2", node_name="node_1", instance_id=1)

        ins_metadata2 = InstanceMetadata(instance_id=2)
        manager.instances[2] = ins_metadata2
        manager.nodes["node_2"] = NodeMetadata(pod_ip="192.168.1.3", node_name="node_2", instance_id=2)

        yield manager


@pytest.fixture
def mock_instance():
    """Create a mock instance for testing"""
    instance = Mock(spec=Instance)
    instance.id = 1
    instance.job_name = "test_job"
    instance.get_node_managers.return_value = [NodeManagerInfo(pod_ip="192.168.1.1", port="8080")]
    return instance


@pytest.fixture
def mock_instance_manager(mock_instance):
    """Create mock instance manager"""
    with patch('motor.controller.fault_tolerance.fault_manager.InstanceManager') as mock_cls:
        instance_manager = Mock()
        mock_cls.return_value = instance_manager
        instance_manager.get_instance_by_podip = Mock(return_value=mock_instance)
        instance_manager.get_instance = Mock(return_value=mock_instance)
        instance_manager.notify = Mock()
        instance_manager.separate_instance = Mock()
        instance_manager.recover_instance = Mock()
        yield instance_manager


@pytest.fixture
def mock_instance_manager(mock_instance):
    """Create mock instance manager"""
    with patch('motor.controller.fault_tolerance.fault_manager.InstanceManager') as mock_cls:
        instance_manager = Mock()
        mock_cls.return_value = instance_manager
        instance_manager.get_instance_by_podip = Mock(return_value=mock_instance)
        instance_manager.get_instance = Mock(return_value=mock_instance)
        instance_manager.notify = Mock()
        instance_manager.separate_instance = Mock()
        instance_manager.recover_instance = Mock()
        yield instance_manager


# =============================================================================
# 1. Initialization
# =============================================================================

def test_fault_manager_initialization(fault_manager):
    """Test FaultManager initialization with default config"""
    assert fault_manager.config is not None
    assert len(fault_manager.nodes) == 0
    assert len(fault_manager.instances) == 0
    assert fault_manager.etcd_client is not None


def test_fault_manager_initialization_with_custom_config():
    """Test FaultManager initialization with custom configuration"""
    config = ControllerConfig()
    config.etcd_config.etcd_host = "custom-etcd-host"
    config.etcd_config.etcd_port = 1234

    with patch('motor.controller.fault_tolerance.fault_manager.EtcdClient') as mock_etcd_class:
        mock_client = MagicMock()
        mock_etcd_class.return_value = mock_client

        manager = FaultManager(config)

        # Verify EtcdClient was called with custom config
        mock_etcd_class.assert_called_once_with(etcd_config=config.etcd_config, tls_config=config.etcd_tls_config)
        assert manager.config is config


def test_fault_manager_singleton_behavior():
    """Test that FaultManager behaves as a singleton"""
    config1 = ControllerConfig()
    config2 = ControllerConfig()

    with patch('motor.controller.fault_tolerance.fault_manager.EtcdClient'):
        manager1 = FaultManager(config1)
        manager2 = FaultManager(config2)

        # They should be the same instance (singleton behavior)
        assert manager1 is manager2


# =============================================================================
# 2. Persistence and Recovery
# =============================================================================

def test_persist_data_success(fault_manager_with_instances):
    """Test successful data persistence to ETCD"""
    manager = fault_manager_with_instances

    with patch.object(manager.etcd_client, 'persist_data', return_value=True) as mock_persist:
        # Call persist_data
        result = manager.persist_data()

        assert result is True
        assert mock_persist.call_count == 1
        call = mock_persist.call_args
        assert call[0][0] == "/controller/fault_manager"

        stored_data = call[0][1]
        assert 'state' in stored_data
        persistent_state_data = stored_data['state']

        assert 'data' in persistent_state_data
        assert 'version' in persistent_state_data
        assert 'timestamp' in persistent_state_data
        assert 'checksum' in persistent_state_data

        fault_data = persistent_state_data['data']
        assert 'nodes' in fault_data
        assert 'instances' in fault_data

        nodes_data = fault_data['nodes']
        assert isinstance(nodes_data, dict)
        assert len(nodes_data) == 3  # Three nodes in test setup (instance 1: 2 nodes, instance 2: 1 node)

        node_data = nodes_data["node_0"]  # Use node_name as key
        assert node_data['pod_ip'] == TEST_IPS[0]
        assert node_data['node_name'] == "node_0"
        assert node_data['node_status'] == NodeStatus.READY.value
        assert 'fault_infos' in node_data

        instances_data = fault_data['instances']
        assert isinstance(instances_data, dict)
        assert len(instances_data) == 2  # Two instances in test setup

        instance_data = instances_data["1"]  # instance_id 1 (using str key)
        assert instance_data['instance_id'] == 1
        assert 'fault_level' in instance_data
        assert 'fault_code' in instance_data


def test_persist_data_etcd_failure(fault_manager_with_instances):
    """Test data persistence when ETCD operations fail"""
    manager = fault_manager_with_instances

    with patch.object(manager.etcd_client, 'persist_data', return_value=False) as mock_persist:
        # Call persist_data
        result = manager.persist_data()

        # Verify failure
        assert result is False


def test_persist_data_exception_handling(fault_manager_with_instances):
    """Test data persistence exception handling"""
    manager = fault_manager_with_instances

    with patch.object(manager.etcd_client, 'persist_data',
                      side_effect=Exception("ETCD connection error")) as mock_persist:
        result = manager.persist_data()

        assert result is False # Verify persist_data failure


def test_persist_data_empty_data(fault_manager):
    """Test data persistence with empty data"""
    manager = fault_manager

    manager.nodes.clear()
    manager.instances.clear()

    with patch.object(manager.etcd_client, 'persist_data', return_value=True) as mock_persist:
        result = manager.persist_data()
        assert result is True # Verify persist_data success

        call = mock_persist.call_args
        stored_data = call[0][1]

        assert 'state' in stored_data # Verify fault_manager field
        persistent_state_data = stored_data['state']
        assert 'data' in persistent_state_data

        fault_data = persistent_state_data['data']
        assert 'nodes' in fault_data
        assert 'instances' in fault_data

        nodes_data = fault_data['nodes']
        instances_data = fault_data['instances']
        assert nodes_data == {}
        assert instances_data == {}


def test_restore_data_success(fault_manager):
    """Test successful data restoration from ETCD"""
    from motor.common.etcd.persistent_state import PersistentState
    manager = fault_manager
    fault_data = {
        'nodes': {
            "node_0": _etcd_node_entry(
                pod_ip=TEST_IPS[0],
                node_name="node_0",
                instance_id=1,
                node_status=NodeStatus.READY,
                fault_infos={
                    TEST_FAULT_CODES[0]: {
                        "fault_type": FaultType.CARD_UNHEALTHY.value,
                        "npu_name": "npu0",
                        "fault_code": TEST_FAULT_CODES[0],
                        "fault_level": FaultLevel.L3.value,
                    }
                },
            )
        },
        'instances': {"1": _etcd_instance_entry(instance_id=1, fault_level=FaultLevel.HEALTHY, fault_code=0x0)}
    }

    persistent_state = PersistentState(
        data=fault_data,
        version=1,
        timestamp=1234567890.0,
        checksum=""
    )
    persistent_state.checksum = persistent_state.calculate_checksum()

    with patch.object(manager.etcd_client, 'restore_data', return_value={"state": persistent_state}) as mock_restore:
        result = manager.restore_data()

        assert result is True # Verify restore_data success

        assert len(manager.nodes) == 1 # Verify nodes restored
        assert "node_0" in manager.nodes

        node = manager.nodes["node_0"]
        assert node.pod_ip == TEST_IPS[0]
        assert node.node_name == "node_0"
        assert node.node_status == NodeStatus.READY
        assert len(node.fault_infos) == 1
        fault_info = next(iter(node.fault_infos.values()))
        assert fault_info.fault_level == FaultLevel.L3
        assert fault_info.fault_code == TEST_FAULT_CODES[0]
        assert len(manager.instances) == 1
        assert 1 in manager.instances

    instance = manager.instances[1]
    assert instance.instance_id == 1
    _assert_instance_fault(instance, fault_level=FaultLevel.HEALTHY, fault_code=0x0)


def test_restore_data_none_data(fault_manager):
    """Test data restoration when ETCD returns None (no data)"""
    manager = fault_manager

    with patch.object(manager.etcd_client, 'restore_data', return_value=None) as mock_restore:
        result = manager.restore_data()
        assert result is True # Verify restore_data success

        assert len(manager.nodes) == 0
        assert len(manager.instances) == 0


def test_restore_data_etcd_failure(fault_manager):
    """Test data restoration when ETCD operations fail"""
    manager = fault_manager

    with patch.object(manager.etcd_client, 'restore_data',
                      side_effect=Exception("ETCD connection error")) as mock_restore:
        result = manager.restore_data()

        assert result is False # Verify restore_data failure


def test_restore_data_corrupted_data(fault_manager):
    """Test data restoration with corrupted PersistentState data"""
    from motor.common.etcd.persistent_state import PersistentState
    manager = fault_manager
    # Create corrupted PersistentState with invalid checksum
    corrupted_fault_data = {
        'nodes': {
            TEST_IPS[0]: _etcd_node_entry(
                pod_ip=TEST_IPS[0],
                node_name="node_0",
                instance_id=1,
                node_status=NodeStatus.READY,
                fault_infos={},
            )
        },
        'instances': {"1": _etcd_instance_entry(instance_id=1, fault_level=FaultLevel.HEALTHY, fault_code=0x0)}
    }
    corrupted_state = PersistentState(
        data=corrupted_fault_data,
        version=1,
        timestamp=1234567890.0,
        checksum="invalid_checksum"  # Invalid checksum
    )
    with patch.object(manager.etcd_client, 'restore_data', return_value={"state": corrupted_state}) as mock_restore:
        result = manager.restore_data()
        assert result is False # Verify restore_data failure


# =============================================================================
# 3. Start and Update Methods
# =============================================================================

def test_fault_manager_start_with_persistence_enabled(fault_manager):
    """Test starting FaultManager with persistence enabled"""
    fault_manager.etcd_config.enable_etcd_persistence = True

    with patch.object(fault_manager, 'restore_data', return_value=True) as mock_restore:
        with patch('threading.Thread') as mock_thread:
            fault_manager.start()

            mock_thread.assert_called_once_with(
                target=fault_manager._ft_strategy_center,
                daemon=True,
                name="FaultToleranceStrategyCenter"
            )
            mock_restore.assert_called_once() # Verify restore_data was called
            mock_thread.return_value.start.assert_called_once()


def test_fault_manager_start_with_persistence_disabled(fault_manager):
    """Test starting FaultManager with persistence disabled"""
    fault_manager.etcd_config.enable_etcd_persistence = False

    with patch.object(fault_manager, 'restore_data') as mock_restore:
        with patch('threading.Thread') as mock_thread:
            fault_manager.start()

            mock_thread.assert_called_once_with(
                target=fault_manager._ft_strategy_center,
                daemon=True,
                name="FaultToleranceStrategyCenter"
            )
            mock_restore.assert_not_called()
            mock_thread.return_value.start.assert_called_once()


def test_fault_manager_start_restore_data_failed(fault_manager):
    """Test starting FaultManager when restore_data fails"""
    fault_manager.etcd_config.enable_etcd_persistence = True

    with patch.object(fault_manager, 'restore_data', return_value=False) as mock_restore:
        with patch('threading.Thread') as mock_thread:
            with patch('motor.controller.fault_tolerance.fault_manager.logger') as mock_logger:
                fault_manager.start()

                mock_thread.assert_called_once_with(
                    target=fault_manager._ft_strategy_center,
                    daemon=True,
                    name="FaultToleranceStrategyCenter"
                )
                mock_restore.assert_called_once()
                mock_logger.warning.assert_called_once_with(
                    "Failed to restore fault manager's data from ETCD, start with empty state"
                )
                mock_thread.return_value.start.assert_called_once()


def test_fault_manager_start_with_stop_event_reset(fault_manager):
    """Test starting FaultManager when stop_event was previously set"""
    fault_manager.stop_event.set()

    with patch.object(fault_manager, 'restore_data', return_value=True):
        with patch('threading.Thread'):
            fault_manager.start()
            assert not fault_manager.stop_event.is_set()


def test_fault_manager_start_creates_resource_monitors(fault_manager_with_instances):
    """Test that starting FaultManager creates ResourceMonitors for all nodes"""
    manager = fault_manager_with_instances

    with patch.object(manager, 'restore_data', return_value=True):
        with patch('threading.Thread'):
            with patch.object(manager, '_create_resource_monitor_for_node') as mock_create_monitor:
                manager.start()

                # Verify ResourceMonitors were created for all nodes (3 nodes in test setup)
                assert mock_create_monitor.call_count == 3
                mock_create_monitor.assert_any_call("node_0")
                mock_create_monitor.assert_any_call("node_1")
                mock_create_monitor.assert_any_call("node_2")


def test_update_instance_initial(fault_manager, mock_instance):
    """Test update method with INSTANCE_INITIAL event"""
    mock_instance.get_node_managers.return_value = [
        NodeManagerInfo(pod_ip="192.168.1.1", port="80880"),
    ]
    
    with patch.object(fault_manager, "k8s_client") as mock_k8s_client:
        mock_k8s_client.get_node_hostname_by_pod_ip.return_value = "node_0"
        with patch.object(fault_manager, '_create_resource_monitor_for_node'):
            fault_manager.update(mock_instance, ObserverEvent.INSTANCE_INITIAL)
    
    assert mock_instance.id in fault_manager.instances
    assert len(fault_manager.nodes) > 0


def test_update_instance_removed(fault_manager, mock_instance):
    """Test update method with INSTANCE_REMOVED event"""
    mock_instance.id = 1
    fault_manager.instances[1] = InstanceMetadata(instance_id=1)
    fault_manager.nodes["node_0"] = NodeMetadata(
        pod_ip="192.168.1.1", node_name="node_0", instance_id=1
    )
    
    with patch.object(fault_manager, '_stop_resource_monitor_for_node'):
        fault_manager.update(mock_instance, ObserverEvent.INSTANCE_REMOVED)
    
    assert 1 not in fault_manager.instances
    assert "node_0" not in fault_manager.nodes


def test_handle_instance_initial_new_instance(fault_manager, mock_instance):
    """Test _handle_instance_initial with a new instance"""
    mock_instance.get_node_managers.return_value = [
        NodeManagerInfo(pod_ip="192.168.1.1", port="8080"),
        NodeManagerInfo(pod_ip="192.168.1.2", port="8080"),
    ]
    mock_instance.id = 1

    # Map pod_ip to node_name for this test
    pod_to_node = {
        "192.168.1.1": "node_0",
        "192.168.1.2": "node_1",
    }
    with patch.object(fault_manager, "k8s_client") as mock_k8s_client, \
         patch.object(fault_manager, '_create_resource_monitor_for_node') as mock_create_monitor:
        mock_k8s_client.get_node_hostname_by_pod_ip.side_effect = lambda ip: pod_to_node.get(ip)
        fault_manager.update(mock_instance, ObserverEvent.INSTANCE_INITIAL)

        assert set(fault_manager.instances.keys()) == {1}
        assert isinstance(fault_manager.instances[1], InstanceMetadata)
        assert set(fault_manager.nodes.keys()) == {"node_0", "node_1"}
        for node_name, pod_ip in [("node_0", "192.168.1.1"), ("node_1", "192.168.1.2")]:
            node = fault_manager.nodes[node_name]
            assert (node.pod_ip, node.node_name, node.instance_id) == (pod_ip, node_name, 1)

        # Check that ConfigMap monitors were created for both hosts
        assert mock_create_monitor.call_count == 2
        mock_create_monitor.assert_any_call("node_0")
        mock_create_monitor.assert_any_call("node_1")


def test_handle_instance_initial_existing_instance(fault_manager, mock_instance):
    """Test _handle_instance_initial when instance already exists"""
    mock_instance.id = 1
    fault_manager.instances[1] = InstanceMetadata(instance_id=1)

    with patch('motor.controller.fault_tolerance.fault_manager.logger') as mock_logger:
        with patch.object(fault_manager, '_create_resource_monitor_for_node') as mock_create_monitor:
            fault_manager.update(mock_instance, ObserverEvent.INSTANCE_INITIAL)

            mock_logger.debug.assert_called_once_with(
                "Instance %d already exists in fault manager, skipping add operation.", 1
            )
            mock_create_monitor.assert_not_called()


def test_handle_instance_initial_preserves_fault_info(fault_manager):
    """Test _handle_instance_initial preserves existing node fault information"""
    instance = Mock()
    instance.id = 1
    instance.job_name = "test_job"
    node_mgr1 = Mock()
    node_mgr1.node_name = "node_0"
    node_mgr1.pod_ip = "192.168.1.1"

    instance.get_node_managers.return_value = [node_mgr1]

    existing_node = NodeMetadata(
        pod_ip="192.168.1.100",  # Different pod_ip to test update
        node_name="node_0",
        instance_id=999,  # Different instance_id to test update
        node_status=NodeStatus.READY,
        fault_infos={FAULT_DEVICE_L2_0x1000.fault_code: FAULT_DEVICE_L2_0x1000}  # This should be preserved
    )

    with fault_manager.lock:
        fault_manager.nodes["node_0"] = existing_node

    # Mock k8s_client to resolve pod_ip to the expected node_name
    with patch.object(fault_manager, "k8s_client") as mock_k8s_client:
        mock_k8s_client.get_node_hostname_by_pod_ip.return_value = "node_0"
        fault_manager.update(instance, ObserverEvent.INSTANCE_INITIAL)

    assert "node_0" in fault_manager.nodes
    updated_node = fault_manager.nodes["node_0"]

    # Verify pod_ip and instance_id were updated
    assert updated_node.pod_ip == "192.168.1.1"
    assert updated_node.instance_id == 1

    # Verify fault info was preserved
    assert len(updated_node.fault_infos) == 1
    fault_info = next(iter(updated_node.fault_infos.values()))
    assert fault_info.fault_type == FaultType.CARD_UNHEALTHY
    assert fault_info.npu_name == "npu0"
    assert fault_info.fault_code == 0x1000
    assert fault_info.fault_level == FaultLevel.L2

    # Verify instance was created
    assert 1 in fault_manager.instances


def test_handle_instance_removed_existing_instance(fault_manager_with_instances):
    """Test _handle_instance_removed with existing instance"""
    manager = fault_manager_with_instances
    instance = Mock()
    instance.id = 1

    with patch.object(manager, '_stop_resource_monitor_for_node') as mock_stop_monitor:
        manager.update(instance, ObserverEvent.INSTANCE_REMOVED)
        assert mock_stop_monitor.call_count == 2  # Two nodes in instance 1
        mock_stop_monitor.assert_any_call("node_0")
        mock_stop_monitor.assert_any_call("node_1")

        # Should remove all nodes belonging to the instance
        assert "node_0" not in manager.nodes
        assert "node_1" not in manager.nodes

        assert 1 not in manager.instances


def test_handle_instance_removed_nonexistent_instance(fault_manager):
    """Test _handle_instance_removed with non-existent instance"""
    instance = Mock()
    instance.id = 999

    with patch.object(fault_manager, '_stop_resource_monitor_for_node') as mock_stop_monitor:
        fault_manager.update(instance, ObserverEvent.INSTANCE_REMOVED)
        mock_stop_monitor.assert_not_called()


# =============================================================================
# 4. Dynamic Configuration Update
# =============================================================================

def test_update_config():
    """Test update_config method updates configuration and recreates ETCD client"""
    # Create FaultManager with mocked dependencies
    with patch('motor.controller.fault_tolerance.fault_manager.EtcdClient') as mock_etcd_class:
        mock_client = MagicMock()
        mock_client.persist_data.return_value = True
        mock_client.restore_data.return_value = None
        mock_etcd_class.return_value = mock_client

        # Create FaultManager instance
        config = ControllerConfig()
        manager = FaultManager(config)

        # Create new config with different ETCD settings
        new_config = ControllerConfig()
        new_config.etcd_config.etcd_host = "new-etcd-host"
        new_config.etcd_config.etcd_port = 2380
        new_config.etcd_config.etcd_timeout = 30.0
        new_config.etcd_config.enable_etcd_persistence = True

        mock_etcd_class.reset_mock()
        manager.update_config(new_config)

        assert manager.config is new_config
        assert manager.config.etcd_config.etcd_host == "new-etcd-host"
        assert manager.config.etcd_config.etcd_port == 2380
        assert manager.config.etcd_config.etcd_timeout == 30.0
        mock_etcd_class.assert_called_once_with(etcd_config=new_config.etcd_config,
                                                tls_config=config.etcd_tls_config)


def test_update_config_with_configmap_changes():
    """Test update_config method when ConfigMap prefix/namespace changes"""
    manager = FaultManager(ControllerConfig())
    ins_metadata = InstanceMetadata(instance_id=1)
    manager.instances[1] = ins_metadata
    manager.nodes["10.0.0.1"] = NodeMetadata(
        pod_ip="192.168.1.1",
        node_name="node_0",
        instance_id=1
    )
    manager.nodes["10.0.0.2"] = NodeMetadata(
        pod_ip="192.168.1.2",
        node_name="node_1",
        instance_id=1
    )
    mock_monitor1, mock_monitor2 = MagicMock(), MagicMock()
    manager.resource_monitors.update({"node_0": mock_monitor1, "node_1": mock_monitor2})

    new_config = ControllerConfig()
    new_config.fault_tolerance_config.configmap_prefix = "new-prefix"
    new_config.fault_tolerance_config.configmap_namespace = "new-namespace"

    with patch.object(manager, '_create_resource_monitor_for_node') as mock_create_monitor, \
         patch('motor.controller.fault_tolerance.fault_manager.logger') as mock_logger:
        manager.update_config(new_config)

        assert (manager.configmap_prefix, manager.configmap_namespace) == ("new-prefix", "new-namespace")

        mock_monitor1.stop_monitoring.assert_called_once()
        mock_monitor2.stop_monitoring.assert_called_once()
        assert manager.resource_monitors == {}

        assert mock_create_monitor.call_count == 2
        mock_create_monitor.assert_any_call("node_0")
        mock_create_monitor.assert_any_call("node_1")

        assert mock_logger.info.call_count >= 4  # multiple log calls


def test_update_config_without_configmap_changes():
    """Test update_config method when ConfigMap configuration doesn't change"""
    with patch('motor.controller.fault_tolerance.fault_manager.EtcdClient') as mock_etcd_class:
        mock_client = MagicMock()
        mock_client.persist_data.return_value = True
        mock_client.restore_data.return_value = None
        mock_etcd_class.return_value = mock_client

        config = ControllerConfig()
        manager = FaultManager(config)

        mock_monitor = MagicMock()
        manager.resource_monitors["node_0"] = mock_monitor

        new_config = ControllerConfig()
        new_config.fault_tolerance_config.configmap_prefix = manager.configmap_prefix
        new_config.fault_tolerance_config.configmap_namespace = manager.configmap_namespace

        with patch.object(manager, '_create_resource_monitor_for_node') as mock_create_monitor:
            with patch('motor.controller.fault_tolerance.fault_manager.logger') as mock_logger:
                manager.update_config(new_config)

                mock_monitor.stop_monitoring.assert_not_called()
                assert len(manager.resource_monitors) == 1
                assert manager.resource_monitors["node_0"] is mock_monitor

                mock_create_monitor.assert_not_called()


def test_update_instances(fault_manager):
    """Test update_instances method adds new instances and updates existing ones"""
    manager = fault_manager

    def mk_instance(iid, job, nodes):
        inst = Mock(spec=Instance)
        inst.id = iid
        inst.job_name = job
        inst.get_node_managers.return_value = [NodeManagerInfo(**node) for node in nodes]
        return inst

    mock_instance1 = mk_instance(1, "job1", [
        {"pod_ip": "192.168.1.1", "port": "8080"},
        {"pod_ip": "192.168.1.2", "port": "8080"},
    ])
    mock_instance2 = mk_instance(2, "job2", [
        {"pod_ip": "192.168.1.3", "port": "8080"},
    ])

    # Mapping from pod_ip to node_name used in this test
    pod_to_node = {
        "192.168.1.1": "node_0",
        "192.168.1.2": "node_1",
        "192.168.1.3": "node_2",
        "192.168.1.4": "node_1",
    }

    # Test 1: Add new instances
    with patch.object(manager, 'k8s_client') as mock_k8s_client, \
         patch.object(manager, '_create_resource_monitor_for_node') as mock_create_monitor:
        mock_k8s_client.get_node_hostname_by_pod_ip.side_effect = lambda ip: pod_to_node.get(ip)
        manager.update_instances([mock_instance1, mock_instance2])

        assert set(manager.instances.keys()) == {1, 2}
        assert set(manager.nodes.keys()) == {"node_0", "node_1", "node_2"}

        # Verify ResourceMonitors were created for all nodes in new instances
        assert mock_create_monitor.call_count == 3
        mock_create_monitor.assert_any_call("node_0")
        mock_create_monitor.assert_any_call("node_1")
        mock_create_monitor.assert_any_call("node_2")

    # Test 2: Update existing instance with changed node managers
    mock_instance1.get_node_managers.return_value = [
        NodeManagerInfo(pod_ip="192.168.1.1", port="8080"),
        NodeManagerInfo(pod_ip="192.168.1.4", port="8080"),
    ]
    with patch.object(manager, 'k8s_client') as mock_k8s_client, \
         patch.object(manager, '_stop_resource_monitor_for_node') as mock_stop_monitor, \
         patch.object(manager, '_create_resource_monitor_for_node') as mock_create_monitor:
        mock_k8s_client.get_node_hostname_by_pod_ip.side_effect = lambda ip: pod_to_node.get(ip)
        manager.update_instances([mock_instance1])

        assert set(manager.instances.keys()) == {1, 2}
        assert set(manager.nodes.keys()) == {"node_0", "node_1", "node_2"}
        mock_stop_monitor.assert_not_called()
        mock_create_monitor.assert_not_called()

        # Verify that node_1's pod_ip has been updated to the new pod_ip
        assert manager.nodes["node_1"].pod_ip == "192.168.1.4"

    # Test 3: Empty instance list should not cause issues
    manager.update_instances([])
    assert set(manager.instances.keys()) == {1, 2}
    assert set(manager.nodes.keys()) == {"node_0", "node_1", "node_2"}


# =============================================================================
# 5. Resource Monitoring and Update
# =============================================================================

def test_create_resource_monitor_for_node(fault_manager):
    """Test creating Resource monitor for a node"""
    with patch('motor.controller.fault_tolerance.fault_manager.ResourceMonitor') as mock_monitor_class:
        mock_monitor = MagicMock()
        mock_monitor_class.return_value = mock_monitor

        fault_manager._create_resource_monitor_for_node("node_0")

        # Verify ResourceMonitor was created with correct parameters
        mock_monitor_class.assert_called_once()
        _, kwargs = mock_monitor_class.call_args
        assert kwargs['node_name'] == "node_0"
        assert 'node_change_handler' in kwargs
        assert 'configmap_change_handler' in kwargs

        # Verify monitor was stored and started
        assert "node_0" in fault_manager.resource_monitors
        assert fault_manager.resource_monitors["node_0"] is mock_monitor
        mock_monitor.start_monitoring.assert_called_once()


def test_stop_resource_monitor_for_node(fault_manager):
    """Test stopping Resource monitor for a node"""
    with patch('motor.controller.fault_tolerance.fault_manager.ResourceMonitor') as mock_monitor_class:
        mock_monitor = MagicMock()
        mock_monitor_class.return_value = mock_monitor

        # First create a monitor
        fault_manager._create_resource_monitor_for_node("node_0")
        assert "node_0" in fault_manager.resource_monitors

        # Now stop it
        fault_manager._stop_resource_monitor_for_node("node_0")

        # Verify monitor was stopped and removed
        mock_monitor.stop_monitoring.assert_called_once()
        assert "node_0" not in fault_manager.resource_monitors


@pytest.mark.parametrize("fault", [FAULT_CM_DEVICE_L3_0x1234, FAULT_CM_SWITCH_L2_0x5678,],)
def test_handle_configmap_update_with_faults_parametrized(fault_manager, fault):
    """Test handling ConfigMap update with device/switch faults (parametrized)."""
    node_name = "node_0"
    fault_manager.nodes[node_name] = NodeMetadata(pod_ip="192.168.1.1", node_name=node_name, instance_id=1)

    fault_manager._handle_fault_info_update([fault], node_name)
    node = fault_manager.nodes[node_name]
    assert len(node.fault_infos) == 1
    _assert_fault_info(
        next(iter(node.fault_infos.values())),
        fault_level=fault.fault_level,
        fault_code=fault.fault_code,
        fault_type=fault.fault_type,
    )


# =============================================================================
# 6. Instance and Node status Updating
# =============================================================================

def test_handle_node_status_update_adds_node_reboot_fault_with_L6(fault_manager):
    """Test that node NOT_READY adds a NODE_REBOOT fault with level L6"""
    # Setup: Add a node to the manager
    node_name = "node_0"
    fault_manager.nodes[node_name] = NodeMetadata(
        pod_ip="192.168.1.1", node_name=node_name, instance_id=1
    )
    fault_manager._handle_node_status_update(NodeStatus.NOT_READY, node_name)

    # Verify NODE_REBOOT fault exists and has level L6
    node = fault_manager.nodes[node_name]
    assert SpecialFaultCode.NODE_REBOOT in node.fault_infos
    reboot_fault = node.fault_infos[SpecialFaultCode.NODE_REBOOT]
    assert reboot_fault.fault_level == FaultLevel.L6


def test_refresh_instance_fault_level_instance_not_found(fault_manager):
    """Test _refresh_instance_fault_level when instance is not found"""
    with patch('motor.controller.fault_tolerance.fault_manager.logger') as mock_logger:
        fault_manager._refresh_instance_fault_level(999)

        mock_logger.warning.assert_called_once_with(
            "Instance %d not found, skipping fault level refresh", 999
        )


def test_refresh_instance_fault_level_instance_not_found(fault_manager_with_instances):
    """Test _refresh_instance_fault_level when instance is not found"""
    manager = fault_manager_with_instances

    with patch('motor.controller.fault_tolerance.fault_manager.logger') as mock_logger:
        manager._refresh_instance_fault_level(999)

        mock_logger.warning.assert_called_once_with(
            "Instance %d not found, skipping fault level refresh", 999
        )


def test_refresh_instance_fault_level_no_device_faults(fault_manager_with_instances):
    """Test _refresh_instance_fault_level when instance has no device faults"""
    manager = fault_manager_with_instances
    instance = manager.instances[1]
    instance.fault_level = FaultLevel.L3  # Set to unhealthy initially

    with patch('motor.controller.fault_tolerance.fault_manager.InstanceManager') as mock_im_class, \
         patch('motor.controller.fault_tolerance.fault_manager.logger') as mock_logger:
        mock_im = MagicMock()
        mock_im_class.return_value = mock_im

        manager._refresh_instance_fault_level(1)

        # Should reset to healthy state
        assert instance.fault_level == FaultLevel.HEALTHY
        assert instance.fault_code == 0x0
        mock_logger.info.assert_called_once_with("Instance %d reset to healthy state", 1)

        # Should recover instance from forced separation
        mock_im.recover_instance.assert_called_once_with(1)


def test_refresh_instance_fault_level_with_device_faults(fault_manager_with_instances):
    """Test _refresh_instance_fault_level when instance has device faults"""
    manager = fault_manager_with_instances

    # Set up node with device fault
    node = manager.nodes["node_0"]
    node.fault_infos = {FAULT_DEVICE_L3.fault_code: FAULT_DEVICE_L3}

    instance = manager.instances[1]
    instance.fault_level = FaultLevel.HEALTHY  # Initially healthy

    with patch.object(manager, '_eval_node_status', return_value=next(iter(node.fault_infos.values()))) as mock_eval:
        with patch('motor.controller.fault_tolerance.fault_manager.InstanceManager') as mock_im_class:
            mock_im = MagicMock()
            mock_im_class.return_value = mock_im

            with patch('motor.controller.fault_tolerance.fault_manager.logger') as mock_logger:
                manager._refresh_instance_fault_level(1)

                # Should update instance fault level
                _assert_instance_fault(instance, fault_level=FaultLevel.L3, fault_code=0x2000)

                # Should separate instance
                mock_im.separate_instance.assert_called_once_with(1)

                mock_logger.info.assert_called_once_with(
                    "Updated instance %d fault level to %s with code %s",
                    1, FaultLevel.L3, hex(0x2000)
                )


@pytest.mark.parametrize("is_separated,expect_recover", [
    (True, True),   # Instance is separated, should call recover_instance
    (False, False)  # Instance is not separated, should not call recover_instance
])
def test_refresh_instance_fault_level_with_l2_faults(fault_manager_with_instances, is_separated, expect_recover):
    """Test _refresh_instance_fault_level when instance has L2 level faults"""
    manager = fault_manager_with_instances

    # Set up node with L2 fault
    node = manager.nodes["node_0"]
    node.fault_infos = {FAULT_DEVICE_L2.fault_code: FAULT_DEVICE_L2}

    instance = manager.instances[1]
    instance.fault_level = FaultLevel.HEALTHY  # Initially healthy

    with patch.object(manager, '_eval_node_status', return_value=next(iter(node.fault_infos.values()))) as mock_eval:
        with patch('motor.controller.fault_tolerance.fault_manager.InstanceManager') as mock_im_class:
            mock_im = MagicMock()
            mock_im_class.return_value = mock_im
            # Mock instance separation status
            mock_im.is_instance_separated.return_value = is_separated

            with patch('motor.controller.fault_tolerance.fault_manager.logger') as mock_logger:
                manager._refresh_instance_fault_level(1)

                # Should update instance fault level
                _assert_instance_fault(instance, fault_level=FaultLevel.L2, fault_code=0x2000)

                mock_im.is_instance_separated.assert_called_once_with(1)
                mock_im.separate_instance.assert_not_called()

                # Recovery behavior depends on separation status
                if expect_recover:
                    mock_im.recover_instance.assert_called_once_with(1)
                else:
                    mock_im.recover_instance.assert_not_called()

                mock_logger.info.assert_called_once_with(
                    "Updated instance %d fault level to %s with code %s",
                    1, FaultLevel.L2, hex(0x2000)
                )


def test_refresh_instance_fault_level_multiple_nodes(fault_manager_with_instances):
    """Test _refresh_instance_fault_level with multiple nodes having different fault levels"""
    manager = fault_manager_with_instances

    # Set up node 1 with L2 fault
    node1 = manager.nodes["node_0"]
    node1.fault_infos = {FAULT_DEVICE_L2.fault_code: FAULT_DEVICE_L2}

    # Set up node 2 with L3 fault (higher level)
    node2 = manager.nodes["node_1"]
    node2.fault_infos = {FAULT_NODE_L3.fault_code: FAULT_NODE_L3}

    instance = manager.instances[1]

    def mock_eval_node_status(node_name):
        if node_name == "node_0":
            return next(iter(node1.fault_infos.values()))
        elif node_name == "node_1":
            return next(iter(node2.fault_infos.values()))
        return None

    with patch.object(manager, '_eval_node_status', side_effect=mock_eval_node_status):
        with patch('motor.controller.fault_tolerance.fault_manager.InstanceManager') as mock_im_class:
            mock_im = MagicMock()
            mock_im_class.return_value = mock_im

            with patch('motor.controller.fault_tolerance.fault_manager.logger') as mock_logger:
                manager._refresh_instance_fault_level(1)
                # Should use the highest fault level (L3)
                _assert_instance_fault(instance, fault_level=FaultLevel.L3, fault_code=0x3000)
                mock_im.separate_instance.assert_called_once_with(1)


def test_eval_node_status_node_not_found(fault_manager):
    """Test _eval_node_status when node is not found"""
    result = fault_manager._eval_node_status("nonexistent_node_name")
    assert result is None


def test_eval_node_status_healthy_node(fault_manager_with_instances):
    """Test _eval_node_status for a healthy node"""
    manager = fault_manager_with_instances
    node = manager.nodes["node_0"]
    node.node_status = NodeStatus.READY

    result = manager._eval_node_status("node_0")
    assert result is None


def test_eval_node_status_unhealthy_no_device_faults(fault_manager_with_instances):
    """Test _eval_node_status for unhealthy node with no device faults"""
    manager = fault_manager_with_instances
    node = manager.nodes["node_0"]
    node.node_status = NodeStatus.READY  # Node is ready, but has no device faults
    node.fault_infos = {}

    result = manager._eval_node_status("node_0")
    assert result is None


def test_eval_node_status_with_device_faults(fault_manager_with_instances):
    """Test _eval_node_status for unhealthy node with device faults"""
    manager = fault_manager_with_instances
    node = manager.nodes["node_0"]
    node.node_status = NodeStatus.READY  # Node is ready, evaluate device faults
    fault_infos = [
        FAULT_DEVICE_L1_0x1000,
        FAULT_NODE_L3,
        FAULT_SWITCH_L2,
    ]
    node.fault_infos = {fault.fault_code: fault for fault in fault_infos}

    result = manager._eval_node_status("node_0")

    # Should return the highest fault level (L3)
    _assert_fault_info(result, fault_level=FaultLevel.L3, fault_code=0x3000, fault_type=FaultType.NODE_UNHEALTHY)


def test_eval_node_status_single_device_fault(fault_manager_with_instances):
    """Test _eval_node_status for unhealthy node with single device fault"""
    manager = fault_manager_with_instances
    node = manager.nodes["node_0"]
    node.node_status = NodeStatus.READY  # Node is ready, evaluate device fault
    node.fault_infos = {FAULT_DEVICE_L2.fault_code: FAULT_DEVICE_L2}

    result = manager._eval_node_status("node_0")

    _assert_fault_info(result, fault_level=FaultLevel.L2, fault_code=0x2000, fault_type=FaultType.CARD_UNHEALTHY)


# =============================================================================
# 7. Strategy Center Processing
# =============================================================================

def test_ft_strategy_center_initialization(fault_manager):
    """Test fault tolerance strategy center initialization"""
    # The strategy center thread should be initialized
    assert hasattr(fault_manager, 'ft_strategy_center_thread')
    assert fault_manager.ft_strategy_center_thread is None  # Initially None, started later


def test_process_instance_strategy_with_healthy_instance(fault_manager_with_instances):
    """Test processing strategy for a healthy instance"""
    manager = fault_manager_with_instances

    # Set instance 1 to healthy state
    manager.instances[1].fault_level = FaultLevel.HEALTHY

    with patch('motor.controller.fault_tolerance.fault_manager.InstanceManager') as mock_im_class:
        mock_im = MagicMock()
        mock_im_class.return_value = mock_im

        manager._process_instance_strategy(1)

        # For healthy instances, no recovery action should be taken
        mock_im.recover_instance.assert_not_called()
        mock_im.separate_instance.assert_not_called()


def test_process_instance_strategy_with_unhealthy_instance(fault_manager_with_instances):
    """Test processing strategy for an unhealthy instance"""
    manager = fault_manager_with_instances

    # Set instance 1 to unhealthy state with L4 fault level
    manager.instances[1].fault_level = FaultLevel.L4

    # Mock InstanceManager to return a decode instance for L4 strategy lookup
    with patch('motor.controller.core.instance_manager.InstanceManager') as mock_im_class:
        mock_im = MagicMock()
        mock_im_class.return_value = mock_im
        mock_instance = MagicMock()
        mock_instance.role = "decode"
        mock_im.get_instance.return_value = mock_instance
        manager.config.fault_tolerance_config.enable_scale_p2d = True

        # Process instance strategy - this should set up a strategy for L4 faults
        manager._process_instance_strategy(1)

        # Check that a strategy was set for the instance (L4 decode instance should get ScaleP2DStrategy)
        assert manager.instances[1].strategy is not None
        assert manager.instances[1].fault_level == FaultLevel.L4


def test_ft_strategy_center_processing(fault_manager_with_instances):
    """Test _ft_strategy_center processes instances correctly"""
    manager = fault_manager_with_instances

    # Mock the time.sleep to avoid actual sleeping
    with patch('time.sleep') as mock_sleep:
        # Mock _process_instance_strategy to track calls
        with patch.object(manager, '_process_instance_strategy') as mock_process:
            # Simulate the loop by raising KeyboardInterrupt after first iteration
            mock_sleep.side_effect = KeyboardInterrupt()

            with pytest.raises(KeyboardInterrupt):
                manager._ft_strategy_center()

            # Verify instances were processed
            assert mock_process.call_count == 2  # Two instances in the fixture
            mock_process.assert_any_call(1)
            mock_process.assert_any_call(2)

            # Verify sleep was called with check interval
            mock_sleep.assert_called_once_with(manager.strategy_center_check_interval)


def test_ft_strategy_center_with_empty_instances(fault_manager):
    """Test _ft_strategy_center with no instances"""
    # Mock the time.sleep to avoid actual sleeping and interrupt the loop
    with patch('time.sleep', side_effect=KeyboardInterrupt()):
        with patch.object(fault_manager, '_process_instance_strategy') as mock_process:
            with pytest.raises(KeyboardInterrupt):
                fault_manager._ft_strategy_center()

            mock_process.assert_not_called()


def test_ft_strategy_center_stop_event_handling(fault_manager_with_instances):
    """Test _ft_strategy_center respects stop event"""
    manager = fault_manager_with_instances
    manager.stop_event.set()

    with patch('time.sleep') as mock_sleep:
        with patch.object(manager, '_process_instance_strategy') as mock_process:
            # Should exit immediately due to stop_event being set
            manager._ft_strategy_center()

            # Should not process any instances or sleep
            mock_process.assert_not_called()
            mock_sleep.assert_not_called()