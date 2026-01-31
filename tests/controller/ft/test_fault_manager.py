#!/usr/bin/env python3
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

import pytest
import threading
import sys
from unittest.mock import Mock, patch, MagicMock

# mock pb
cluster_fault_pb2 = MagicMock()
mock_pb2_grpc = MagicMock()
cluster_fault_pb2.ClientInfo = Mock
cluster_fault_pb2.FaultMsgSignal = Mock
# add cluster pb to system
sys.modules['cluster_fault_pb2'] = cluster_fault_pb2
sys.modules['cluster_fault_pb2_grpc'] = mock_pb2_grpc

from motor.controller.ft.cluster_grpc import cluster_fault_pb2
from motor.controller.core.observer import ObserverEvent
from motor.common.resources.instance import Instance, NodeManagerInfo
from motor.common.utils.singleton import ThreadSafeSingleton

# Import FaultManager and related classes after mocking
from motor.controller.ft.fault_manager import (
    FaultManager,
    ServerMetadata,
    InstanceMetadata,
    DeviceFaultInfo,
    Status,
    FaultLevel
)

# Test constants
TEST_IPS = ["192.168.1.1", "192.168.1.2", "192.168.1.99"]
TEST_PORT = "8080"
TEST_NODE_NAMES = ["node_0", "node_1"]
TEST_SERIAL_NUMBERS = ["SN0:06d", "SN1:06d"]
TEST_FAULT_CODES = [0x1234, 0x2000, 0x3000, 0x3001, 0x4000, 0x00f1fef5]


@pytest.fixture(autouse=True)
def mock_etcd_client():
    """Mock EtcdClient to avoid real ETCD operations in tests"""
    with patch('motor.controller.ft.fault_manager.EtcdClient') as mock_etcd_class:
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
    """Create fault manager with proper mocking"""
    # Clear singleton instance before creating new one
    if FaultManager in ThreadSafeSingleton._instances:
        del ThreadSafeSingleton._instances[FaultManager]

    manager, _ , _ = FaultManagerTestHelper.create_mock_fault_manager()
    yield manager
    # cleanup
    if manager is not None and not manager.stop_event.is_set():
        manager.stop()


@pytest.fixture
def fault_manager_with_instances():
    """Create fault manager with test instances and servers"""
    manager, _ , _ = FaultManagerTestHelper.create_fault_manager_with_instances()
    yield manager
    if not manager.stop_event.is_set():
        manager.stop()


@pytest.fixture
def mock_instance():
    """Create mock instance"""
    return FaultManagerTestHelper.create_mock_instance()


@pytest.fixture
def mock_instance_manager(mock_instance):
    """Create mock instance manager"""
    with patch('motor.controller.ft.fault_manager.InstanceManager') as mock_cls:
        instance_manager = Mock()
        mock_cls.return_value = instance_manager
        instance_manager.get_instance_by_podip = Mock(return_value=mock_instance)
        instance_manager.get_instance = Mock(return_value=mock_instance)
        instance_manager.notify = Mock()
        instance_manager.separate_instance = Mock()
        instance_manager.recover_instance = Mock()
        yield instance_manager


class FaultManagerTestHelper:
    """Helper class for common FaultManager test setup"""

    @staticmethod
    def create_mock_instance(instance_id=100, job_name="test_job", role="prefill",
                           pod_ip=TEST_IPS[0], host_ip=TEST_IPS[0], port=TEST_PORT):
        """Create a mock instance with specified parameters"""
        mock_instance = Mock(spec=Instance)
        mock_instance.id = instance_id
        mock_instance.job_name = job_name
        mock_instance.role = role
        mock_instance.update_instance_status = Mock()
        mock_instance.get_node_managers.return_value = [
            NodeManagerInfo(pod_ip=pod_ip, host_ip=host_ip, port=port)
        ]
        mock_instance.get_endpoints.return_value = {}
        return mock_instance

    @staticmethod
    def create_device_fault_info(device_type="npu", rank_id=0, fault_code=TEST_FAULT_CODES[0],
                                fault_level=FaultLevel.L3, fault_type="HARDWARE", fault_reason="Memory failure"):
        """Create a DeviceFaultInfo object"""
        return DeviceFaultInfo(
            device_type=device_type,
            rank_id=rank_id,
            fault_code=fault_code,
            fault_level=fault_level,
            fault_type=fault_type,
            fault_reason=fault_reason
        )

    @staticmethod
    def create_server_metadata(pod_ip=TEST_IPS[0], host_ip=TEST_IPS[0], status=Status.HEALTHY,
                              device_fault_infos=None):
        """Create ServerMetadata object"""
        return ServerMetadata(
            pod_ip=pod_ip,
            host_ip=host_ip,
            status=status,
            device_fault_infos=device_fault_infos or []
        )

    @staticmethod
    def create_instance_metadata(instance_id=100, status=Status.HEALTHY, node_managers=None):
        """Create InstanceMetadata object"""
        if node_managers is None:
            node_managers = [NodeManagerInfo(pod_ip=TEST_IPS[0], host_ip=TEST_IPS[0], port=TEST_PORT)]
        return InstanceMetadata(
            instance_id=instance_id,
            status=status,
            node_managers=node_managers
        )

    @staticmethod
    def create_fault_message(signal_type="fault", node_ip=TEST_IPS[0], node_name=TEST_NODE_NAMES[0],
                           node_sn=TEST_SERIAL_NUMBERS[0], fault_level="unhealthy",
                           device_type="SERVER", fault_codes=None, fault_types=None, fault_reasons=None):
        """Create a mock fault message"""
        fault_msg = cluster_fault_pb2.FaultMsgSignal()
        fault_msg.signalType = signal_type

        if signal_type == "fault":
            node_fault_info = fault_msg.nodeFaultInfo.add()
            node_fault_info.nodeName = node_name
            node_fault_info.nodeIP = node_ip
            node_fault_info.nodeSN = node_sn
            node_fault_info.faultLevel = fault_level

            if device_type and fault_codes:
                device_fault = node_fault_info.faultDevice.add()
                device_fault.deviceId = f"device_{node_ip.split('.')[-1]}"
                device_fault.deviceType = device_type
                device_fault.faultCodes.extend(fault_codes or ["ERR001"])
                device_fault.faultLevel = "L5"
                device_fault.faultType.extend(fault_types or ["HARDWARE"])
                device_fault.faultReason.extend(fault_reasons or ["Memory failure"])

        return fault_msg

    @staticmethod
    def create_mock_fault_manager():
        """Create a mock fault manager with all necessary mocks"""
        with patch('motor.controller.ft.fault_manager.ClusterNodeClient') as mock_client_class:
            with patch('motor.controller.ft.fault_manager.generate_strategy_map') as mock_strategy_map:
                with patch('concurrent.futures.ThreadPoolExecutor') as mockexecutor_class:
                    # Mock cluster client
                    mock_client = Mock()
                    mock_client_class.return_value = mock_client
                    mock_client.register = Mock(return_value=True)
                    mock_client.subscribe_fault_messages = Mock()
                    mock_client.close = Mock()

                    # Mock strategy map
                    def create_mock_strategy_class():
                        mock_strategy = Mock()
                        # make sure the strategy will not finished immediately.
                        mock_strategy.is_finished.return_value = False
                        return mock_strategy

                    mock_strategy_map.return_value = {
                        "L0": Mock(return_value=None),  # Healthy instances
                        "L1": Mock(return_value=None),
                        "L2": Mock(return_value=create_mock_strategy_class),
                        "L3": Mock(return_value=create_mock_strategy_class),
                        "L4": Mock(return_value=create_mock_strategy_class),
                        "L5": Mock(return_value=create_mock_strategy_class),
                        "L6": Mock(return_value=create_mock_strategy_class),
                    }

                    # Mock executor
                    mockexecutor = Mock()
                    mockexecutor_class.return_value = mockexecutor

                    from motor.config.controller import ControllerConfig
                    config = ControllerConfig()
                    manager = FaultManager(config)

                    return manager, mockexecutor, mock_strategy_map

    @staticmethod
    def create_fault_manager_with_instances():
        """Create fault manager with test instances and servers"""
        manager, mockexecutor, mock_strategy_map = FaultManagerTestHelper.create_mock_fault_manager()

        # Create test instances using helper methods
        manager.instances[1] = FaultManagerTestHelper.create_instance_metadata(
            instance_id=1,
            node_managers=[NodeManagerInfo(pod_ip=TEST_IPS[0], host_ip=TEST_IPS[0], port=TEST_PORT)]
        )
        manager.instances[2] = FaultManagerTestHelper.create_instance_metadata(
            instance_id=2,
            node_managers=[NodeManagerInfo(pod_ip=TEST_IPS[1], host_ip=TEST_IPS[1], port=TEST_PORT)]
        )

        # Create test servers using helper methods
        manager.servers[TEST_IPS[0]] = FaultManagerTestHelper.create_server_metadata(
            pod_ip=TEST_IPS[0], host_ip=TEST_IPS[0]
        )
        manager.servers[TEST_IPS[1]] = FaultManagerTestHelper.create_server_metadata(
            pod_ip=TEST_IPS[1], host_ip=TEST_IPS[1]
        )

        return manager, mockexecutor, mock_strategy_map


def test_initialization(fault_manager):
    """Test FaultManager initialization"""
    assert isinstance(fault_manager.stop_event, threading.Event)
    assert isinstance(fault_manager.lock, type(threading.Lock()))
    assert isinstance(fault_manager.servers, dict)
    assert isinstance(fault_manager.instances, dict)

    # Check threads are None before start() is called
    assert fault_manager.server_status_subscriber_thread is None
    assert fault_manager.ft_strategy_center_thread is None

@pytest.mark.parametrize("dataclass_name,expected_attrs", [
    ("DeviceFaultInfo", {
        "device_type": "npu",
        "rank_id": 0,
        "fault_code": TEST_FAULT_CODES[0],
        "fault_level": "L3",
        "fault_type": "HARDWARE",
        "fault_reason": "Memory failure"
    }),
    ("ServerMetadata", {
        "pod_ip": TEST_IPS[0],
        "host_ip": TEST_IPS[0],
        "status": Status.HEALTHY,
        "device_fault_infos": []
    }),
    ("InstanceMetadata", {
        "instance_id": 100,
        "status": Status.HEALTHY,
        "node_managers": [NodeManagerInfo(pod_ip=TEST_IPS[0], host_ip=TEST_IPS[0], port=TEST_PORT)],
        "fault_level": "L0",
        "fault_code": 0x0,
        "strategy": None
    })
])
def test_dataclass_creation(dataclass_name, expected_attrs):
    """Test dataclass creation for various metadata classes"""
    if dataclass_name == "DeviceFaultInfo":
        obj = DeviceFaultInfo(**{k: v for k, v in expected_attrs.items()
                                 if k in ["device_type", "rank_id", "fault_code",
                                         "fault_level", "fault_type", "fault_reason"]})
    elif dataclass_name == "ServerMetadata":
        obj = ServerMetadata(**{k: v for k, v in expected_attrs.items()
                                if k in ["pod_ip", "host_ip", "status", "device_fault_infos"]})
    elif dataclass_name == "InstanceMetadata":
        obj = InstanceMetadata(**{k: v for k, v in expected_attrs.items()
                                  if k in ["instance_id", "status", "node_managers"]})

    for attr, expected_value in expected_attrs.items():
        assert getattr(obj, attr) == expected_value

@pytest.mark.parametrize("signal_type,fault_level,expected_server_status,should_update_instances", [
    ("normal", None, None, False),  # Normal signal should return early
    ("fault", "unhealthy", Status.UNHEALTHY, True),  # Unhealthy fault should update server and instances
    ("fault", "healthy", Status.HEALTHY, True),  # Healthy fault should update server and instances
])
def test_process_cluster_fault_message_basic(
        fault_manager, signal_type, fault_level,
        expected_server_status, should_update_instances):
    """Test basic fault message processing for different signal types and fault levels"""
    # Setup server metadata for fault tests
    if signal_type == "fault":
        initial_status = Status.UNHEALTHY if fault_level == "healthy" else Status.HEALTHY
        fault_manager.servers[TEST_IPS[0]] = FaultManagerTestHelper.create_server_metadata(
            status=initial_status
        )

    # Setup instance metadata to test _update_instances_status call
    if should_update_instances:
        fault_manager.instances[1] = FaultManagerTestHelper.create_instance_metadata(instance_id=1)

    # Create fault message using helper
    fault_msg = FaultManagerTestHelper.create_fault_message(
        signal_type=signal_type,
        fault_level=fault_level,
        fault_codes=["ERR001"] if signal_type == "fault" else None
    )

    # Mock update_instances_status to verify it's called
    with patch.object(fault_manager, '_update_instances_status') as mock_update_status:
        fault_manager._process_cluster_fault_message(fault_msg)

        # Verify _update_instances_status was called for fault messages
        if should_update_instances:
            mock_update_status.assert_called_once()
        else:
            mock_update_status.assert_not_called()

    # Verify server status updated for fault messages
    if expected_server_status:
        assert fault_manager.servers[TEST_IPS[0]].status == expected_server_status
        if fault_level == "unhealthy":
            assert len(fault_manager.servers[TEST_IPS[0]].device_fault_infos) == 1

@pytest.mark.parametrize("test_case,expected_behavior", [
    ("unknown_server", {"creates_server": False, "raises_exception": False}),
    ("none_input", {"raises_exception": False}),
    ("missing_signalType", {"raises_exception": False}),
    ("missing_nodeFaultInfo", {"raises_exception": False}),
])
def test_process_cluster_fault_message_edge_cases(fault_manager, test_case, expected_behavior):
    """Test fault message processing edge cases"""
    if test_case == "unknown_server":
        fault_msg = FaultManagerTestHelper.create_fault_message(
            node_ip=TEST_IPS[2],  # Unknown server IP
            fault_level="healthy"
        )
        fault_manager._process_cluster_fault_message(fault_msg)
        assert TEST_IPS[2] not in fault_manager.servers

    elif test_case == "none_input":
        fault_manager._process_cluster_fault_message(None)

    elif test_case == "missing_signalType":
        fault_msg = cluster_fault_pb2.FaultMsgSignal()
        # Don't set signalType
        fault_manager._process_cluster_fault_message(fault_msg)

    elif test_case == "missing_nodeFaultInfo":
        fault_msg = cluster_fault_pb2.FaultMsgSignal()
        fault_msg.signalType = "fault"
        # Don't set nodeFaultInfo
        fault_manager._process_cluster_fault_message(fault_msg)

def test_process_cluster_fault_message_large_device_faults(fault_manager):
    """Test processing fault message with large number of device faults - should truncate"""
    # Setup server metadata using helper
    fault_manager.servers[TEST_IPS[0]] = FaultManagerTestHelper.create_server_metadata()

    # Create fault message with many device faults
    fault_msg = cluster_fault_pb2.FaultMsgSignal()
    fault_msg.signalType = "fault"

    # Create node fault info with many device faults
    node_fault_info = fault_msg.nodeFaultInfo.add()
    node_fault_info.nodeName = TEST_NODE_NAMES[0]
    node_fault_info.nodeIP = TEST_IPS[0]
    node_fault_info.nodeSN = TEST_SERIAL_NUMBERS[0]
    node_fault_info.faultLevel = "unhealthy"

    # Create many device faults (more than 1000)
    for i in range(1500):
        device_fault = node_fault_info.faultDevice.add()
        device_fault.deviceId = f"device_{i}"
        device_fault.deviceType = "SERVER"
        device_fault.faultCodes.append(f"ERR{i:03d}")
        device_fault.faultLevel = "L5"
        device_fault.faultType.append("HARDWARE")
        device_fault.faultReason.append(f"Fault reason {i}")

    fault_manager._process_cluster_fault_message(fault_msg)

    # Verify device faults were truncated to 1000
    assert len(fault_manager.servers[TEST_IPS[0]].device_fault_infos) == 1000

def test_process_cluster_fault_message_invalid_nodeinfo(fault_manager):
    """Test processing fault message with invalid node info - should handle gracefully"""
    fault_msg = cluster_fault_pb2.FaultMsgSignal()
    fault_msg.signalType = "fault"

    # Create node info without required fields (nodeIP, faultLevel)
    node_info = fault_msg.nodeFaultInfo.add()
    # Don't set nodeIP or faultLevel to simulate invalid node info

    # Should handle invalid node info gracefully without raising exceptions
    # The method should log warnings and skip processing invalid entries
    # This tests defensive programming - method should be robust against malformed input
    fault_manager._process_cluster_fault_message(fault_msg)  # Should not raise any exception

def test_process_cluster_fault_message_external_call_failure(fault_manager, mock_instance_manager):
    """Test processing fault message when external calls fail - should handle gracefully"""
    # Setup server metadata
    fault_manager.servers[TEST_IPS[0]] = FaultManagerTestHelper.create_server_metadata()

    # Create fault message with device faults to meet unhealthy condition
    fault_msg = FaultManagerTestHelper.create_fault_message(
        fault_level="unhealthy",
        fault_codes=["ERR001"]
    )

    # Mock instance manager to raise exception
    mock_instance_manager.get_instance_by_podip.side_effect = Exception("Database connection failed")

    # Should handle external call failure gracefully
    fault_manager._process_cluster_fault_message(fault_msg)

    # Verify server status was still updated despite external call failure
    assert fault_manager.servers[TEST_IPS[0]].status == Status.UNHEALTHY

@pytest.mark.parametrize("server_status,device_faults,expected_result", [
    (Status.HEALTHY, [], None),  # Healthy server returns None
    (Status.UNHEALTHY, [  # Unhealthy server with faults returns highest level fault
        FaultManagerTestHelper.create_device_fault_info(fault_level=FaultLevel.L2, fault_code=0x1000),
        FaultManagerTestHelper.create_device_fault_info(fault_level=FaultLevel.L4, fault_code=0x2000),
        FaultManagerTestHelper.create_device_fault_info(fault_level=FaultLevel.L3, fault_code=0x3000)
    ], (FaultLevel.L4, 0x2000)),
])
def test_eval_server_status(fault_manager, server_status, device_faults, expected_result):
    """Test _eval_server_status with different server states"""
    # Setup server metadata using helper
    fault_manager.servers[TEST_IPS[0]] = FaultManagerTestHelper.create_server_metadata(
        status=server_status,
        device_fault_infos=device_faults
    )

    result = fault_manager._eval_server_status(TEST_IPS[0])

    if expected_result is None:
        assert result is None
    else:
        assert result is not None
        assert result.fault_level == expected_result[0]
        assert result.fault_code == expected_result[1]

def test_eval_server_status_unknown_server(fault_manager):
    """Test _eval_server_status with unknown server"""
    with pytest.raises(ValueError, match=f"Server {TEST_IPS[2]} not found"):
        fault_manager._eval_server_status(TEST_IPS[2])

@pytest.mark.parametrize("test_case,server_configs,expected_fault_level,expected_fault_code", [
    ("healthy_instance", [
        {"ip": TEST_IPS[0], "status": Status.HEALTHY, "faults": []}
    ], FaultLevel.L0, 0x0),
    ("unhealthy_instance", [
        {"ip": TEST_IPS[0], "status": Status.UNHEALTHY, "faults": [
            FaultManagerTestHelper.create_device_fault_info(fault_level=FaultLevel.L2, fault_code=0x1000),
            FaultManagerTestHelper.create_device_fault_info(fault_level=FaultLevel.L4, fault_code=0x2000),
            FaultManagerTestHelper.create_device_fault_info(fault_level=FaultLevel.L3, fault_code=0x3000)
        ]}
    ], FaultLevel.L4, 0x2000),
    ("multiple_servers", [
        {"ip": TEST_IPS[0], "status": Status.UNHEALTHY, "faults": [
            FaultManagerTestHelper.create_device_fault_info(fault_level=FaultLevel.L2, fault_code=0x1000)
        ]},
        {"ip": TEST_IPS[1], "status": Status.UNHEALTHY, "faults": [
            FaultManagerTestHelper.create_device_fault_info(fault_level=FaultLevel.L5, fault_code=0x3000)
        ]}
    ], FaultLevel.L5, 0x3000),
    ("mixed_servers", [
        {"ip": TEST_IPS[0], "status": Status.HEALTHY, "faults": []},
        {"ip": TEST_IPS[1], "status": Status.UNHEALTHY, "faults": [
            FaultManagerTestHelper.create_device_fault_info(fault_level=FaultLevel.L3, fault_code=0x2000)
        ]}
    ], FaultLevel.L3, 0x2000),
])
def test_update_instances_status(fault_manager, test_case, server_configs, expected_fault_level, expected_fault_code):
    """Test _update_instances_status with different server configurations"""
    # Setup servers
    node_managers = []
    for config in server_configs:
        fault_manager.servers[config["ip"]] = FaultManagerTestHelper.create_server_metadata(
            pod_ip=config["ip"], host_ip=config["ip"],
            status=config["status"], device_fault_infos=config["faults"]
        )
        node_managers.append(NodeManagerInfo(pod_ip=config["ip"], host_ip=config["ip"], port=TEST_PORT))

    # Setup instance metadata
    instance_metadata = FaultManagerTestHelper.create_instance_metadata(
        instance_id=1, node_managers=node_managers
    )
    fault_manager.instances[1] = instance_metadata

    fault_manager._update_instances_status()

    # Verify instance metadata updated correctly
    assert instance_metadata.fault_level == expected_fault_level
    assert instance_metadata.fault_code == expected_fault_code

def test_update_instance_added(fault_manager):
    """Test update method with INSTANCE_INITIAL event"""
    # Create mock instance using helper
    mock_instance = FaultManagerTestHelper.create_mock_instance(
        instance_id=100, role="prefill", pod_ip=TEST_IPS[0], host_ip=TEST_IPS[0]
    )

    fault_manager.update(mock_instance, ObserverEvent.INSTANCE_INITIAL)

    # Check instance registration
    assert 100 in fault_manager.instances
    instance_metadata = fault_manager.instances[100]
    assert instance_metadata.instance_id == 100
    assert len(instance_metadata.node_managers) == 1

    # Check server metadata creation
    assert TEST_IPS[0] in fault_manager.servers
    server = fault_manager.servers[TEST_IPS[0]]
    assert server.pod_ip == TEST_IPS[0]
    assert server.status == Status.HEALTHY

def test_update_instance_added_duplicate(fault_manager):
    """Test update method with INSTANCE_INITIAL event for duplicate instance"""
    # Create first mock instance
    mock_instance1 = FaultManagerTestHelper.create_mock_instance(
        instance_id=100, role="prefill", pod_ip=TEST_IPS[0], host_ip=TEST_IPS[0]
    )

    # Add the instance first time
    fault_manager.update(mock_instance1, ObserverEvent.INSTANCE_INITIAL)

    # Check instance registration
    assert 100 in fault_manager.instances
    instance_metadata = fault_manager.instances[100]
    assert instance_metadata.instance_id == 100
    assert len(instance_metadata.node_managers) == 1
    assert TEST_IPS[0] in fault_manager.servers

    # Create another instance with same ID but different pod_ip
    mock_instance2 = FaultManagerTestHelper.create_mock_instance(
        instance_id=100, role="prefill", pod_ip=TEST_IPS[1], host_ip=TEST_IPS[1]
    )

    # Try to add the same instance again
    fault_manager.update(mock_instance2, ObserverEvent.INSTANCE_INITIAL)

    # Check that the original instance data is preserved (not overwritten)
    assert len(fault_manager.instances) == 1  # Still only one instance
    assert fault_manager.instances[100].instance_id == 100
    assert len(fault_manager.instances[100].node_managers) == 1
    # Original pod_ip should still be there, new one should not be added due to duplicate check
    assert TEST_IPS[0] in fault_manager.servers
    assert TEST_IPS[1] not in fault_manager.servers  # New server should not be added

def test_update_instance_separated(fault_manager, mock_instance):
    """Test update method with INSTANCE_SEPERATED event"""
    # First add the instance
    fault_manager.update(mock_instance, ObserverEvent.INSTANCE_INITIAL)

    # Then separate it
    fault_manager.update(mock_instance, ObserverEvent.INSTANCE_SEPERATED)

    # Check that instance is marked as separated but not removed
    assert 100 in fault_manager.instances
    assert fault_manager.instances[100].status == Status.UNHEALTHY
    assert TEST_IPS[0] in fault_manager.servers

def test_update_instance_removed(fault_manager, mock_instance):
    """Test update method with INSTANCE_REMOVED event"""
    # First add the instance
    fault_manager.update(mock_instance, ObserverEvent.INSTANCE_INITIAL)

    # Then remove it
    fault_manager.update(mock_instance, ObserverEvent.INSTANCE_REMOVED)

    # Check removal
    assert 100 not in fault_manager.instances
    assert TEST_IPS[0] not in fault_manager.servers

def test_update_invalid_event(fault_manager, mock_instance):
    """Test update method with invalid event"""
    with pytest.raises(ValueError, match="Invalid event"):
        fault_manager.update(mock_instance, "INVALID_EVENT")

@pytest.fixture
def fault_manager_with_instances():
    """Create fault manager with test instances and servers"""
    manager, _ , _ = FaultManagerTestHelper.create_fault_manager_with_instances()
    yield manager
    if not manager.stop_event.is_set():
        manager.stop()

@pytest.mark.parametrize("test_case,server_setup,expected_strategy_calls,expected_instance_states", [
    ("healthy_instances", {}, 0, {"strategy": None, "fault_level": FaultLevel.L0, "fault_code": 0x0}),
    ("unhealthy_instances", {
        TEST_IPS[0]: {"status": Status.UNHEALTHY, "faults": [
            FaultManagerTestHelper.create_device_fault_info(fault_level=FaultLevel.L3, fault_code=TEST_FAULT_CODES[0])
        ]}
    }, 1, {"strategy": "not_none", "fault_level": FaultLevel.L3, "fault_code": TEST_FAULT_CODES[0]}),
    ("multiple_instances", {
        TEST_IPS[0]: {"status": Status.UNHEALTHY, "faults": [
            FaultManagerTestHelper.create_device_fault_info(fault_level=FaultLevel.L3, fault_code=0x3000)
        ]}
    }, 1, {"strategy": "not_none", "fault_level": FaultLevel.L3, "fault_code": 0x3000}),
])
def test_ft_strategy_center_basic(
        fault_manager_with_instances, test_case, server_setup,
        expected_strategy_calls, expected_instance_states):
    """Test ft_strategy_center with different scenarios"""
    manager = fault_manager_with_instances

    # Setup server states
    for ip, config in server_setup.items():
        manager.servers[ip].status = config["status"]
        manager.servers[ip].device_fault_infos = config["faults"]

    # Update instance statuses to reflect server faults
    if server_setup:
        manager._update_instances_status()

    # Mock time.sleep to control the loop execution
    def mock_sleep(seconds):
        raise StopIteration

    with patch('motor.controller.ft.fault_manager.time') as mock_time:
        mock_time.sleep.side_effect = mock_sleep
        try:
            manager._ft_strategy_center()
        except StopIteration:
            pass

    # Verify strategy submission count
    assert manager.executor.submit.call_count == expected_strategy_calls

    # Verify instance states - only check instances that should be affected
    if test_case == "healthy_instances":
        # All instances should be healthy
        for ins_metadata in manager.instances.values():
            assert ins_metadata.strategy is None
            assert ins_metadata.fault_level == FaultLevel.L0
            assert ins_metadata.fault_code == 0x0
    else:
        # Only instance 1 should be affected in unhealthy cases
        assert manager.instances[1].strategy is not None
        assert manager.instances[1].fault_level == expected_instance_states["fault_level"]
        assert manager.instances[1].fault_code == expected_instance_states["fault_code"]

        # Instance 2 should remain healthy
        assert manager.instances[2].strategy is None
        assert manager.instances[2].fault_level == FaultLevel.L0
        assert manager.instances[2].fault_code == 0x0

def test_ft_strategy_center_strategy_levels(fault_manager_with_instances):
    """Test different error levels trigger appropriate strategies"""
    manager = fault_manager_with_instances

    # Test L2 level error
    manager.servers[TEST_IPS[0]].status = Status.UNHEALTHY
    manager.servers[TEST_IPS[0]].device_fault_infos = [
        FaultManagerTestHelper.create_device_fault_info(fault_level=FaultLevel.L2, fault_code=TEST_FAULT_CODES[4])
    ]

    # First update instance status to reflect the server fault
    manager._update_instances_status()

    # Mock time.sleep to control the loop execution
    def mock_sleep(seconds):
        raise StopIteration

    with patch('motor.controller.ft.fault_manager.time') as mock_time:
        mock_time.sleep.side_effect = mock_sleep
        try:
            manager._ft_strategy_center()
        except StopIteration:
            pass

    # Verify L2 strategy was called
    manager.strategies["L2"].assert_called_once_with(TEST_FAULT_CODES[4], 1, manager.config)

def test_ft_strategy_center_strategy_finished(fault_manager_with_instances):
    """Test that finished strategies are cleaned up"""
    manager = fault_manager_with_instances

    # Setup instance with finished strategy
    mock_strategy = Mock()
    mock_strategy.is_finished.return_value = True
    manager.instances[1].strategy = mock_strategy

    # Mock time.sleep to control the loop execution
    def mock_sleep(seconds):
        raise StopIteration

    with patch('motor.controller.ft.fault_manager.time') as mock_time:
        mock_time.sleep.side_effect = mock_sleep
        try:
            manager._ft_strategy_center()
        except StopIteration:
            pass

    # Verify strategy was cleaned up
    assert manager.instances[1].strategy is None

@pytest.mark.parametrize("test_case,initial_strategy,new_fault,expected_stop_calls,expected_submit_calls", [
    ("fault_upgrade", {"level": "L2", "code": 0x2000}, {"level": "L4", "code": 0x4000}, 1, 1),
    ("fault_downgrade", {"level": "L4", "code": 0x4000}, {"level": "L2", "code": 0x2000}, 1, 1),
    ("same_level_same_code", {"level": "L3", "code": 0x3000}, {"level": "L3", "code": 0x3000}, 0, 0),
    ("same_level_different_code", {"level": "L3", "code": 0x3000}, {"level": "L3", "code": 0x3001}, 1, 1),
])
def test_ft_strategy_center_fault_transitions(fault_manager_with_instances, test_case, initial_strategy,
                                             new_fault, expected_stop_calls, expected_submit_calls):
    """Test fault level transitions and strategy changes"""
    manager = fault_manager_with_instances

    # Setup initial strategy
    mock_strategy = Mock()
    mock_strategy.is_finished.return_value = False
    mock_strategy.stop = Mock()

    # Set instance with existing strategy
    manager.instances[1].strategy = mock_strategy
    manager.instances[1].fault_level = initial_strategy["level"]
    manager.instances[1].fault_code = initial_strategy["code"]

    # Make server unhealthy with new fault level
    manager.servers[TEST_IPS[0]].status = Status.UNHEALTHY
    manager.servers[TEST_IPS[0]].device_fault_infos = [
        FaultManagerTestHelper.create_device_fault_info(
            fault_level=new_fault["level"], fault_code=new_fault["code"]
        )
    ]

    # Update instance status to reflect the server fault
    manager._update_instances_status()

    # For same level same code test, mock strategy factory to return None
    if test_case == "same_level_same_code":
        original_l3_strategy_factory = manager.strategies["L3"]
        def same_strategy_factory(fault_code, instance_id, config):
            if fault_code == 0x3000:  # Same fault code
                return None  # No new strategy needed
            else:
                return original_l3_strategy_factory(fault_code, instance_id, config)
        manager.strategies["L3"] = same_strategy_factory

    # Mock time.sleep to control the loop execution
    def mock_sleep(seconds):
        raise StopIteration

    with patch('motor.controller.ft.fault_manager.time') as mock_time:
        mock_time.sleep.side_effect = mock_sleep
        try:
            manager._ft_strategy_center()
        except StopIteration:
            pass

    # Verify strategy stop calls
    assert mock_strategy.stop.call_count == expected_stop_calls

    # Verify strategy submission calls
    assert manager.executor.submit.call_count == expected_submit_calls

    # Verify instance metadata was updated
    ins_metadata = manager.instances[1]
    assert ins_metadata.fault_level == new_fault["level"]
    assert ins_metadata.fault_code == new_fault["code"]

    # For cases where new strategy should be created
    if expected_submit_calls > 0:
        assert ins_metadata.strategy is not None
        # Verify correct strategy was called
        expected_level = new_fault["level"]
        manager.strategies[expected_level].assert_called_once_with(new_fault["code"], 1, manager.config)
    elif test_case == "same_level_same_code":
        # Strategy should remain the same
        assert ins_metadata.strategy is mock_strategy


def test_concurrent_updates(fault_manager):
    """Test batch updates to FaultManager"""
    # Create multiple mock instances using helper
    instances = []
    for i in range(5):
        mock_instance = FaultManagerTestHelper.create_mock_instance(
            instance_id=i * 100,
            job_name=f"job_{i}",
            role="prefill" if i % 2 == 0 else "decode",
            pod_ip=f"192.168.1.{i+1}",
            host_ip=f"192.168.1.{i+1}"
        )
        instances.append(mock_instance)

    # Add all instances
    for instance in instances:
        fault_manager.update(instance, ObserverEvent.INSTANCE_INITIAL)

    # Verify all instances are registered
    assert len(fault_manager.instances) == 5

    # Remove all instances
    for instance in instances:
        fault_manager.update(instance, ObserverEvent.INSTANCE_REMOVED)

    # Verify all instances are removed
    assert len(fault_manager.instances) == 0

def test_triggered_update_workflow(fault_manager):
    """Test the complete triggered update workflow"""
    # Setup instance using helper
    mock_instance = FaultManagerTestHelper.create_mock_instance()

    # Add instance
    fault_manager.update(mock_instance, ObserverEvent.INSTANCE_INITIAL)

    # Verify initial state
    assert 100 in fault_manager.instances
    assert TEST_IPS[0] in fault_manager.servers
    assert fault_manager.instances[100].fault_level == FaultLevel.L0
    assert fault_manager.instances[100].fault_code == 0x0

    # Simulate fault message processing with _update_instances_status call
    fault_manager.servers[TEST_IPS[0]].status = Status.UNHEALTHY
    fault_manager.servers[TEST_IPS[0]].device_fault_infos = [
        FaultManagerTestHelper.create_device_fault_info(fault_code=TEST_FAULT_CODES[0], fault_level=FaultLevel.L3)
    ]

    # Call _update_instances_status to simulate triggered update
    fault_manager._update_instances_status()

    # Verify instance metadata was updated
    assert fault_manager.instances[100].fault_level == FaultLevel.L3
    assert fault_manager.instances[100].fault_code == TEST_FAULT_CODES[0]

    # Now test strategy center with updated fault level
    with patch('motor.controller.ft.fault_manager.time') as mock_time:
        def mock_sleep(seconds):
            raise StopIteration

        mock_time.sleep.side_effect = mock_sleep
        try:
            fault_manager._ft_strategy_center()
        except StopIteration:
            pass

    # Verify strategy was submitted for the updated fault level
    fault_manager.executor.submit.assert_called_once()
    call_args = fault_manager.executor.submit.call_args

    # Verify the strategy execution parameters
    execute_method = call_args[0][0]  # First argument is the execute method
    instance_id = call_args[0][1]  # Second argument is the instance id

    assert execute_method is not None
    assert instance_id == 100

    # Verify L3 strategy was called with correct parameters
    fault_manager.strategies["L3"].assert_called_once_with(TEST_FAULT_CODES[0], 100, fault_manager.config)


# @pytest.mark.parametrize("device_faults,fault_level,expected_status,expected_separate_called,expected_recover_called", [
#     # Case 1: Has device faults AND fault_level is unhealthy -> unhealthy (separate)
#     ([FaultManagerTestHelper.create_device_fault_info()], "unhealthy", Status.UNHEALTHY, True, False),
#     # Case 2: Has device faults BUT fault_level is healthy -> healthy (recover)
#     ([FaultManagerTestHelper.create_device_fault_info()], "healthy", Status.HEALTHY, False, True),
#     # Case 3: No device faults AND fault_level is unhealthy -> healthy (recover)
#     ([], "unhealthy", Status.HEALTHY, False, True),
#     # Case 4: No device faults AND fault_level is healthy -> healthy (recover)
#     ([], "healthy", Status.HEALTHY, False, True),
# ])
# def test_comprehensive_fault_determination_logic(fault_manager, mock_instance_manager, device_faults, fault_level, expected_status, expected_separate_called, expected_recover_called):
#     """Test comprehensive fault determination logic combining device faults and fault level"""
#     # Setup server metadata
#     fault_manager.servers[TEST_IPS[0]] = FaultManagerTestHelper.create_server_metadata(
#         device_fault_infos=device_faults
#     )

#     # Create fault message
#     fault_codes = ["ERR001"] if device_faults else None
#     fault_msg = FaultManagerTestHelper.create_fault_message(
#         fault_level=fault_level,
#         fault_codes=fault_codes
#     )

#     # Process fault message
#     fault_manager._process_cluster_fault_message(fault_msg)

#     # Verify server status
#     assert fault_manager.servers[TEST_IPS[0]].status == expected_status

#     # Verify instance manager calls
#     if expected_separate_called:
#         mock_instance_manager.separate_instances_by_pod_ips.assert_called_once_with([TEST_IPS[0]])
#     else:
#         mock_instance_manager.separate_instances_by_pod_ips.assert_not_called()

#     if expected_recover_called:
#         mock_instance_manager.recover_instances_by_pod_ips.assert_called_once_with([TEST_IPS[0]])
#     else:
#         mock_instance_manager.recover_instances_by_pod_ips.assert_not_called()


def test_fault_manager_bidirectional_instance_management(fault_manager_with_instances, mock_instance_manager):
    """Test that fault manager handles both isolation and recovery in a single message"""
    # Use the fault_manager_with_instances which already has instances set up
    manager = fault_manager_with_instances

    # Setup instance 1 with two servers initially healthy (fault_level = L0)
    manager.instances[1].fault_level = FaultLevel.L0
    manager.instances[1].fault_code = 0x0

    # Modify server configurations for this test - make server unhealthy
    manager.servers[TEST_IPS[0]].device_fault_infos = [FaultManagerTestHelper.create_device_fault_info()]
    manager.servers[TEST_IPS[0]].status = Status.UNHEALTHY

    # Create fault message with unhealthy node
    fault_msg = cluster_fault_pb2.FaultMsgSignal()
    fault_msg.signalType = "fault"

    # Add unhealthy node (with device faults and unhealthy fault_level)
    unhealthy_node = fault_msg.nodeFaultInfo.add()
    unhealthy_node.nodeIP = TEST_IPS[0]
    unhealthy_node.nodeName = TEST_NODE_NAMES[0]
    unhealthy_node.nodeSN = TEST_SERIAL_NUMBERS[0]
    unhealthy_node.faultLevel = "unhealthy"
    device_fault = unhealthy_node.faultDevice.add()
    device_fault.deviceId = f"device_{TEST_IPS[0].split('.')[-1]}"
    device_fault.deviceType = "SERVER"
    device_fault.faultCodes.extend(["ERR001"])
    device_fault.faultLevel = "L5"

    # Process fault message
    manager._process_cluster_fault_message(fault_msg)

    # Verify server gets correct status
    assert manager.servers[TEST_IPS[0]].status == Status.UNHEALTHY

    # Verify instance manager calls - instance 1 should be isolated (became unhealthy)
    mock_instance_manager.separate_instance.assert_called_once_with(1)  # instance_id 1


def test_fault_manager_device_faults_with_healthy_fault_level(fault_manager, mock_instance_manager):
    """Test server with device faults but healthy fault_level gets recovered"""
    # Setup server with device faults but healthy fault_level
    fault_manager.servers[TEST_IPS[0]] = FaultManagerTestHelper.create_server_metadata(
        device_fault_infos=[FaultManagerTestHelper.create_device_fault_info()]
    )

    # Setup instance initially as unhealthy to test recovery
    fault_manager.instances[100] = FaultManagerTestHelper.create_instance_metadata(
        instance_id=100,
        node_managers=[NodeManagerInfo(pod_ip=TEST_IPS[0], host_ip=TEST_IPS[0], port=TEST_PORT)]
    )
    # Set instance initially as unhealthy to test recovery
    fault_manager.instances[100].fault_level = FaultLevel.L1

    # Create fault message with healthy fault_level
    fault_msg = FaultManagerTestHelper.create_fault_message(
        fault_level="healthy",
        fault_codes=["ERR001"]  # But has device faults
    )

    # Process fault message
    fault_manager._process_cluster_fault_message(fault_msg)

    # Verify server status is HEALTHY (despite having device faults)
    assert fault_manager.servers[TEST_IPS[0]].status == Status.HEALTHY

    # Verify recovery is called (instance became healthy)
    mock_instance_manager.separate_instance.assert_not_called()
    mock_instance_manager.recover_instance.assert_called_once_with(100)  # instance_id


def test_fault_manager_unhealthy_fault_level_without_device_faults(fault_manager, mock_instance_manager):
    """Test server with unhealthy fault_level but no device faults gets recovered"""
    # Setup server without device faults
    fault_manager.servers[TEST_IPS[0]] = FaultManagerTestHelper.create_server_metadata(
        device_fault_infos=[]  # No device faults
    )

    # Setup instance initially as unhealthy to test recovery
    fault_manager.instances[100] = FaultManagerTestHelper.create_instance_metadata(
        instance_id=100,
        node_managers=[NodeManagerInfo(pod_ip=TEST_IPS[0], host_ip=TEST_IPS[0], port=TEST_PORT)]
    )
    # Set instance initially as unhealthy to test recovery
    fault_manager.instances[100].fault_level = FaultLevel.L1

    # Create fault message with unhealthy fault_level but no device faults
    fault_msg = FaultManagerTestHelper.create_fault_message(
        fault_level="unhealthy"
        # No fault_codes, so no device faults in message
    )

    # Process fault message
    fault_manager._process_cluster_fault_message(fault_msg)

    # Verify server status is HEALTHY (despite unhealthy fault_level)
    assert fault_manager.servers[TEST_IPS[0]].status == Status.HEALTHY

    # Verify recovery is called (instance became healthy)
    mock_instance_manager.separate_instance.assert_not_called()
    mock_instance_manager.recover_instance.assert_called_once_with(100)  # instance_id


def test_persist_data_success(fault_manager_with_instances, mock_etcd_client):
    """Test successful data persistence to ETCD"""
    manager = fault_manager_with_instances

    # Mock the persist_data calls to return True
    mock_etcd_client.persist_data.return_value = True

    # Call persist_data
    result = manager.persist_data()

    # Verify success
    assert result is True

    # Verify persist_data was called twice (for servers and instances)
    assert mock_etcd_client.persist_data.call_count == 2

    # Verify the call arguments
    calls = mock_etcd_client.persist_data.call_args_list

    # First call should be for servers
    servers_call = calls[0]
    assert servers_call[0][0] == "/controller/fault/servers"
    servers_data = servers_call[0][1]
    assert isinstance(servers_data, dict)
    assert len(servers_data) == 2  # Two servers in test setup

    # Verify server data structure
    server_data = servers_data[TEST_IPS[0]]
    assert server_data['pod_ip'] == TEST_IPS[0]
    assert server_data['host_ip'] == TEST_IPS[0]
    assert server_data['status'] == Status.HEALTHY.value
    assert 'device_fault_infos' in server_data

    # Second call should be for instances
    instances_call = calls[1]
    assert instances_call[0][0] == "/controller/fault/instances"
    instances_data = instances_call[0][1]
    assert isinstance(instances_data, dict)
    assert len(instances_data) == 2  # Two instances in test setup

    # Verify instance data structure
    instance_data = instances_data['1']  # instance_id 1
    assert instance_data['instance_id'] == 1
    assert instance_data['status'] == Status.HEALTHY.value
    assert 'node_managers' in instance_data
    assert 'fault_level' in instance_data
    assert 'fault_code' in instance_data


def test_persist_data_with_device_faults(fault_manager_with_instances, mock_etcd_client):
    """Test data persistence with device fault information"""
    manager = fault_manager_with_instances

    # Add device faults to a server
    device_fault = FaultManagerTestHelper.create_device_fault_info(
        fault_level=FaultLevel.L3, fault_code=TEST_FAULT_CODES[0]
    )
    manager.servers[TEST_IPS[0]].device_fault_infos = [device_fault]
    manager.servers[TEST_IPS[0]].status = Status.UNHEALTHY

    # Update instance status to reflect the fault
    manager._update_instances_status()

    # Mock the persist_data calls to return True
    mock_etcd_client.persist_data.return_value = True

    # Call persist_data
    result = manager.persist_data()

    # Verify success
    assert result is True

    # Get the persisted data
    servers_call = mock_etcd_client.persist_data.call_args_list[0]
    servers_data = servers_call[0][1]

    # Verify device fault information is persisted
    server_data = servers_data[TEST_IPS[0]]
    assert len(server_data['device_fault_infos']) == 1
    persisted_fault = server_data['device_fault_infos'][0]
    assert persisted_fault['fault_level'] == FaultLevel.L3
    assert persisted_fault['fault_code'] == TEST_FAULT_CODES[0]


def test_persist_data_etcd_failure(fault_manager_with_instances, mock_etcd_client):
    """Test data persistence when ETCD operations fail"""
    manager = fault_manager_with_instances

    # Mock the persist_data calls to return False (failure)
    mock_etcd_client.persist_data.return_value = False

    # Call persist_data
    result = manager.persist_data()

    # Verify failure
    assert result is False


def test_persist_data_exception_handling(fault_manager_with_instances, mock_etcd_client):
    """Test data persistence exception handling"""
    manager = fault_manager_with_instances

    # Mock the persist_data calls to raise an exception
    mock_etcd_client.persist_data.side_effect = Exception("ETCD connection error")

    # Call persist_data
    result = manager.persist_data()

    # Verify failure
    assert result is False


def test_persist_data_empty_data(fault_manager, mock_etcd_client):
    """Test data persistence with empty data"""
    manager = fault_manager

    # Ensure no data exists
    manager.servers.clear()
    manager.instances.clear()

    # Mock the persist_data calls to return True
    mock_etcd_client.persist_data.return_value = True

    # Call persist_data
    result = manager.persist_data()

    # Verify success
    assert result is True

    # Verify empty data was persisted
    calls = mock_etcd_client.persist_data.call_args_list
    servers_data = calls[0][0][1]
    instances_data = calls[1][0][1]

    assert servers_data == {}
    assert instances_data == {}


def test_restore_data_success(fault_manager, mock_etcd_client):
    """Test successful data restoration from ETCD"""
    manager = fault_manager

    # Prepare test data to be restored
    servers_data = {
        TEST_IPS[0]: {
            'pod_ip': TEST_IPS[0],
            'host_ip': TEST_IPS[0],
            'status': Status.HEALTHY.value,
            'device_fault_infos': [{
                'device_type': 'npu',
                'rank_id': 0,
                'fault_code': TEST_FAULT_CODES[0],
                'fault_level': FaultLevel.L3,
                'fault_type': 'HARDWARE',
                'fault_reason': 'Memory failure'
            }]
        }
    }

    instances_data = {
        '1': {
            'instance_id': 1,
            'status': Status.HEALTHY.value,
            'node_managers': [{
                'pod_ip': TEST_IPS[0],
                'host_ip': TEST_IPS[0],
                'port': TEST_PORT
            }],
            'fault_level': FaultLevel.L0,
            'fault_code': 0x0
        }
    }

    # Mock the restore_data calls
    mock_etcd_client.restore_data.side_effect = [servers_data, instances_data]

    # Call restore_data
    result = manager.restore_data()

    # Verify success
    assert result is True

    # Verify data was restored
    assert len(manager.servers) == 1
    assert TEST_IPS[0] in manager.servers

    server = manager.servers[TEST_IPS[0]]
    assert server.pod_ip == TEST_IPS[0]
    assert server.host_ip == TEST_IPS[0]
    assert server.status == Status.HEALTHY
    assert len(server.device_fault_infos) == 1
    assert server.device_fault_infos[0].fault_level == FaultLevel.L3
    assert server.device_fault_infos[0].fault_code == TEST_FAULT_CODES[0]

    assert len(manager.instances) == 1
    assert 1 in manager.instances

    instance = manager.instances[1]
    assert instance.instance_id == 1
    assert instance.status == Status.HEALTHY
    assert instance.fault_level == FaultLevel.L0
    assert instance.fault_code == 0x0
    assert len(instance.node_managers) == 1


def test_restore_data_none_data(fault_manager, mock_etcd_client):
    """Test data restoration when ETCD returns None (no data)"""
    manager = fault_manager

    # Mock the restore_data calls to return None
    mock_etcd_client.restore_data.return_value = None

    # Call restore_data
    result = manager.restore_data()

    # Verify success (graceful handling of None data)
    assert result is True

    # Verify no data was restored
    assert len(manager.servers) == 0
    assert len(manager.instances) == 0


def test_restore_data_etcd_failure(fault_manager, mock_etcd_client):
    """Test data restoration when ETCD operations fail"""
    manager = fault_manager

    # Mock the restore_data calls to raise an exception
    mock_etcd_client.restore_data.side_effect = Exception("ETCD connection error")

    # Call restore_data
    result = manager.restore_data()

    # Verify failure
    assert result is False


def test_restore_data_corrupted_data(fault_manager, mock_etcd_client):
    """Test data restoration with corrupted/invalid data"""
    manager = fault_manager

    # Prepare corrupted test data
    corrupted_servers_data = {
        TEST_IPS[0]: {
            'pod_ip': TEST_IPS[0],
            'host_ip': TEST_IPS[0],
            'status': 'invalid_status',  # Invalid status enum value
            'device_fault_infos': []
        }
    }

    corrupted_instances_data = {
        '1': {
            'instance_id': 'not_an_int',  # Invalid instance_id type
            'status': Status.HEALTHY.value,
            'node_managers': [],
            'fault_level': FaultLevel.L0,
            'fault_code': 0x0
        }
    }

    # Mock the restore_data calls
    mock_etcd_client.restore_data.side_effect = [corrupted_servers_data, corrupted_instances_data]

    # Call restore_data - should handle corruption gracefully
    result = manager.restore_data()

    # Verify failure due to corrupted data
    assert result is False

    # Verify data structures remain empty due to failed restoration
    assert len(manager.servers) == 0
    assert len(manager.instances) == 0


def test_restore_data_partial_failure(fault_manager, mock_etcd_client):
    """Test data restoration when one ETCD operation fails"""
    manager = fault_manager

    # Mock servers data restoration to succeed, instances to fail
    servers_data = {
        TEST_IPS[0]: {
            'pod_ip': TEST_IPS[0],
            'host_ip': TEST_IPS[0],
            'status': Status.HEALTHY.value,
            'device_fault_infos': []
        }
    }

    mock_etcd_client.restore_data.side_effect = [servers_data, Exception("Instances ETCD error")]

    # Call restore_data
    result = manager.restore_data()

    # Verify failure
    assert result is False

    # Since restoration failed, data should be cleared even if partial success
    assert len(manager.servers) == 0
    assert len(manager.instances) == 0


def test_update_config():
    """Test update_config method updates configuration and recreates ETCD client"""
    # Create FaultManager with mocked dependencies
    with patch('motor.controller.ft.fault_manager.EtcdClient') as mock_etcd_class:
        mock_client = MagicMock()
        mock_client.persist_data.return_value = True
        mock_client.restore_data.return_value = None
        mock_etcd_class.return_value = mock_client

        # Create FaultManager instance
        from motor.config.controller import ControllerConfig
        config = ControllerConfig()
        manager = FaultManager(config)

        # Store original config
        original_config = manager.config

        # Create new config with different ETCD settings
        new_config = ControllerConfig()
        new_config.etcd_config.etcd_host = "new-etcd-host"
        new_config.etcd_config.etcd_port = 2380
        new_config.etcd_config.etcd_timeout = 30.0
        new_config.etcd_config.enable_etcd_persistence = True

        # Clear the mock call history to track new calls
        mock_etcd_class.reset_mock()

        # Update config
        manager.update_config(new_config)

        # Verify config was updated
        assert manager.config is new_config
        assert manager.config.etcd_config.etcd_host == "new-etcd-host"
        assert manager.config.etcd_config.etcd_port == 2380
        assert manager.config.etcd_config.etcd_timeout == 30.0

        # Verify ETCD client constructor was called with new config
        # Note: update_config updates etcd_tls_config, so it uses the new one
        assert mock_etcd_class.call_count == 1
        call_args = mock_etcd_class.call_args
        # Verify the call was made with correct etcd_config and tls_config
        # The call_args might be a call object with kwargs attribute
        if hasattr(call_args, 'kwargs') and call_args.kwargs:
            kwargs = call_args.kwargs
        else:
            # Fallback: check if it's a tuple (args, kwargs)
            kwargs = call_args[1] if len(call_args) > 1 else {}
        assert 'etcd_config' in kwargs
        assert 'tls_config' in kwargs
        assert kwargs['etcd_config'].etcd_host == "new-etcd-host"
        assert kwargs['etcd_config'].etcd_port == 2380
        assert kwargs['etcd_config'].etcd_timeout == 30.0
        assert kwargs['tls_config'] == new_config.etcd_tls_config


def test_update_instances():
    """Test update_instances method adds new instances and updates existing ones"""
    # Create FaultManager with mocked dependencies using the helper
    # The helper properly sets up all necessary mocks and returns a fully initialized manager
    manager, _, _ = FaultManagerTestHelper.create_mock_fault_manager()

    # Create mock instances
    mock_instance1 = Mock(spec=Instance)
    mock_instance1.id = 1
    mock_instance1.job_name = "job1"
    mock_instance1.get_node_managers.return_value = [
        NodeManagerInfo(pod_ip="192.168.1.1", host_ip="10.0.0.1", port="8080"),
        NodeManagerInfo(pod_ip="192.168.1.2", host_ip="10.0.0.2", port="8080")
    ]

    mock_instance2 = Mock(spec=Instance)
    mock_instance2.id = 2
    mock_instance2.job_name = "job2"
    mock_instance2.get_node_managers.return_value = [
        NodeManagerInfo(pod_ip="192.168.1.3", host_ip="10.0.0.3", port="8080")
    ]

    # Test 1: Add new instances
    manager.update_instances([mock_instance1, mock_instance2])

    # Verify instances were added
    assert 1 in manager.instances
    assert 2 in manager.instances
    assert len(manager.instances) == 2

    # Verify servers were added
    assert "192.168.1.1" in manager.servers
    assert "192.168.1.2" in manager.servers
    assert "192.168.1.3" in manager.servers
    assert len(manager.servers) == 3

    # Test 2: Update existing instance with changed node managers
    mock_instance1.get_node_managers.return_value = [
        NodeManagerInfo(pod_ip="192.168.1.1", host_ip="10.0.0.1", port="8080"),
        NodeManagerInfo(pod_ip="192.168.1.4", host_ip="10.0.0.4", port="8080")  # Changed node2 to node4
    ]

    manager.update_instances([mock_instance1])

    # Verify instance 1 still exists
    assert 1 in manager.instances
    assert 2 in manager.instances

    # Verify old server was removed and new server was added
    assert "192.168.1.2" not in manager.servers  # Old server removed
    assert "192.168.1.4" in manager.servers     # New server added
    assert "192.168.1.1" in manager.servers     # Existing server kept
    assert "192.168.1.3" in manager.servers     # Unchanged server kept
    assert len(manager.servers) == 3  # Total servers should still be 3

    # Test 3: Empty instance list should not cause issues
    manager.update_instances([])
    assert len(manager.instances) == 2  # No change
    assert len(manager.servers) == 3    # No change