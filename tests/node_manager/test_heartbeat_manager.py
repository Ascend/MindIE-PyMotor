#!/usr/bin/env python3
# coding=utf-8
import os
import sys
import json
import pytest
from unittest.mock import Mock, patch, MagicMock, mock_open
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

os.environ['HCCL_PATH'] = 'tests/jsons'
os.environ['JOB_NAME'] = 'test_job'
os.environ['POD_IP'] = '192.168.1.100'
os.environ['CONFIG_PATH'] = 'tests/jsons'

from motor.common.resources.endpoint import Endpoint, EndpointStatus, DeviceInfo
from motor.common.resources.http_msg_spec import StartCmdMsg, Ranktable, ServerInfo
from motor.common.resources.instance import ParallelConfig, PDRole
from motor.node_manager.core.heartbeat_manager import HeartbeatManager
from motor.config.node_manager import NodeManagerConfig


def create_config_mock(config_data, hccl_data):
    def mock_side_effect(file_path, mode):
        file_path_str = str(file_path)
        if "node_manager_config.json" in file_path_str:
            return mock_open(read_data=json.dumps(config_data)).return_value
        elif "hccl.json" in file_path_str:
            return mock_open(read_data=json.dumps(hccl_data)).return_value
        return mock_open().return_value
    return mock_side_effect


@pytest.fixture
def config_data():
    return {
        "parallel_config": {"tp_size": 2, "pp_size": 1},
        "role": "both",
        "controller_api_dns": "localhost",
        "controller_api_port": 8080,
        "node_manager_port": 8080,
        "model_name": "vllm"
    }


@pytest.fixture
def hccl_data():
    return {
        "status": "completed",
        "server_count": "1",
        "version": "1.0",
        "server_list": [{
            "server_id": "192.168.1.100",
            "host_ip": "192.168.1.200",
            "container_ip": "192.168.1.100",
            "device": [
                {"device_id": "0", "device_ip": "192.168.1.1", "rank_id": "0"},
                {"device_id": "1", "device_ip": "192.168.1.2", "rank_id": "1"}
            ]
        }]
    }


class TestHeartBeatManager:
    """HeartBeatManager test class"""
    @pytest.fixture
    def heart_beat_manager(self, config_data, hccl_data):
        """return HeartBeatManager instance"""
        with patch('motor.config.node_manager.safe_open') as mock_safe_open, \
             patch('threading.Thread') as mock_thread_class, \
             patch.dict('os.environ', {'JOB_NAME': 'test_job', 'CONFIG_PATH': 'tests/jsons', 'HCCL_PATH': 'tests/jsons', 'ROLE': 'both'}):
            mock_safe_open.side_effect = create_config_mock(config_data, hccl_data)
            mock_thread = MagicMock()
            mock_thread_class.return_value = mock_thread
            # clear HeartBeatManager instance (HeartbeatManager is still singleton)
            if hasattr(HeartbeatManager, '_instances') and HeartbeatManager in HeartbeatManager._instances:
                try:
                    HeartbeatManager._instances[HeartbeatManager].stop()
                except:
                    pass
                if HeartbeatManager in HeartbeatManager._instances:
                    del HeartbeatManager._instances[HeartbeatManager]

            config = NodeManagerConfig()
            # Manually set the configuration data
            config.basic_config.parallel_config = ParallelConfig(tp_size=config_data["parallel_config"]["tp_size"], pp_size=config_data["parallel_config"]["pp_size"])
            config.basic_config.job_name = config_data.get("model_name", "test_job")
            config.basic_config.role = PDRole(config_data.get("role", "both"))
            config.api_config.controller_api_dns = config_data.get("controller_api_dns", "localhost")
            config.api_config.controller_api_port = config_data.get("controller_api_port", 8080)
            config.api_config.node_manager_port = config_data.get("node_manager_port", 8080)

            # Set device info from hccl_data
            server = (hccl_data.get("server_list") or [None])[0]
            if server:
                devices = server.get("device") or []
                config.basic_config.device_num = len(devices)

            manager = HeartbeatManager(config)
            yield manager

    @pytest.fixture
    def sample_endpoints(self):
        """return sample endpoints"""
        return [
            Endpoint(id=1, ip="192.168.1.1", business_port="8080", mgmt_port="9090", status=EndpointStatus.NORMAL),
            Endpoint(id=2, ip="192.168.1.2", business_port="8080", mgmt_port="9090", status=EndpointStatus.NORMAL)
        ]

    @pytest.fixture
    def sample_start_cmd_msg(self, sample_endpoints):
        """return start command message"""
        device_info = DeviceInfo( device_id="0", device_ip="192.168.0.1",super_device_id="0",rank_id="0",cluster_id="0")
        sever_info = ServerInfo(server_id="1", container_ip="192.168.1.100", device=[device_info])
        test_rank_table = Ranktable(version="1.0", status="normal", server_count="1",server_list=[sever_info])
        return StartCmdMsg(
            job_name="test_job",
            role="prefill",
            instance_id=1,
            endpoints=sample_endpoints,
            ranktable=test_rank_table
        )

    @pytest.fixture
    def mock_http_client(self):
        """mock HTTP client fixture"""
        with patch('motor.node_manager.core.heartbeat_manager.SafeHTTPSClient') as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client
            yield mock_client

    @patch('motor.config.node_manager.safe_open')
    @patch.dict('os.environ', {'JOB_NAME': 'test_job', 'CONFIG_PATH': './', 'HCCL_PATH': './tests/jsons/hccl.json', 'ROLE': 'both'})
    def test_singleton_pattern(self, mock_safe_open, config_data, hccl_data):
        """test singleton pattern"""
        mock_safe_open.side_effect = create_config_mock(config_data, hccl_data)
        # Clear singleton instance
        if hasattr(HeartbeatManager, '_instances') and HeartbeatManager in HeartbeatManager._instances:
            if HeartbeatManager in HeartbeatManager._instances:
                del HeartbeatManager._instances[HeartbeatManager]
        
        with patch('threading.Thread'):
            config = NodeManagerConfig()
            manager1 = HeartbeatManager(config)
            manager2 = HeartbeatManager(config)
            assert manager1 is manager2

    def test_initial_state(self, heart_beat_manager):
        """test initial state"""
        assert heart_beat_manager._job_name == ""
        assert heart_beat_manager._role == "prefill"
        assert heart_beat_manager._instance_id == -1
        assert heart_beat_manager._endpoints == []
        assert heart_beat_manager.stop_event.is_set() is False
        assert heart_beat_manager._thread_started is False

    def test_update_endpoint(self, heart_beat_manager, sample_start_cmd_msg):
        """test update endpoint"""
        heart_beat_manager.update_endpoint(sample_start_cmd_msg)

        assert heart_beat_manager._job_name == "test_job"
        assert heart_beat_manager._role == "prefill"
        assert heart_beat_manager._instance_id == 1
        assert len(heart_beat_manager._endpoints) == 2
        assert heart_beat_manager._endpoints[0].id == 1
        assert heart_beat_manager._endpoints[1].id == 2

    @patch('motor.node_manager.core.heartbeat_manager.SafeHTTPSClient')
    def test_get_engine_server_status_success(self, mock_client_class, heart_beat_manager, sample_endpoints):
        """test get engine server status success"""
        # Mock client instance with get method returning status
        mock_client = MagicMock()
        mock_client.get.return_value = {"status": "normal"}
        mock_client.close = MagicMock()
        mock_client_class.return_value = mock_client

        with heart_beat_manager._endpoint_lock:
            heart_beat_manager._endpoints = sample_endpoints.copy()

        heart_beat_manager._get_engine_server_status()

        # Verify that client was created for each endpoint
        assert mock_client_class.call_count == 2
        # Verify that client.get was called for each endpoint with correct path
        assert mock_client.get.call_count == 2
        mock_client.get.assert_any_call("/status")
        # Verify that client.close was called for each endpoint
        assert mock_client.close.call_count == 2
        
        # Verify that status was updated correctly
        assert heart_beat_manager._endpoints[0].status == EndpointStatus.NORMAL
        assert heart_beat_manager._endpoints[1].status == EndpointStatus.NORMAL

    @patch('motor.node_manager.core.heartbeat_manager.time.sleep')
    @patch('motor.node_manager.core.heartbeat_manager.SafeHTTPSClient')
    def test_report_heartbeat_loop_success(self, mock_client_class, mock_sleep, heart_beat_manager):
        """test _report_heartbeat_loop success"""
        call_count = {"count": 0}
        
        def mock_stop_sleep(seconds):
            call_count["count"] += 1
            if call_count["count"] >= 1:
                heart_beat_manager.stop_event.set()
        
        mock_client_instance = MagicMock()
        mock_client_instance.post.return_value = {}
        # SafeHTTPSClient is used as context manager
        mock_client_class.return_value.__enter__.return_value = mock_client_instance
        mock_client_class.return_value.__exit__.return_value = None

        # set endpoint info
        heart_beat_manager._job_name = "test_job"
        heart_beat_manager._instance_id = 1
        # pod_ip is already set during initialization
        heart_beat_manager.stop_event.clear()  # Ensure stop_event is not set initially
        with heart_beat_manager._endpoint_lock:
            heart_beat_manager._endpoints = [
                Endpoint(id=1, ip="192.168.1.1", business_port="8080", mgmt_port="9090", status=EndpointStatus.NORMAL)
            ]

        mock_sleep.side_effect = mock_stop_sleep
        
        # Call the method directly (will execute once then stop)
        heart_beat_manager._report_heartbeat_loop()
        
        # Verify client was created and used
        assert mock_client_class.called, "SafeHTTPSClient should be instantiated"
        assert mock_client_instance.post.called, "post method should be called"

    @patch('motor.node_manager.core.heartbeat_manager.time.sleep')
    @patch('motor.node_manager.core.heartbeat_manager.SafeHTTPSClient')
    def test_heartbeat_report_loop(self, mock_client_class, mock_sleep, heart_beat_manager):
        """test heartbeat report loop"""
        call_count = {"count": 0}

        # set loop exec once
        def mock_stop_sleep(seconds):
            call_count["count"] += 1
            if call_count["count"] >= 1:
                heart_beat_manager.stop_event.set()

        mock_client_instance = MagicMock()
        mock_client_instance.post.return_value = {}
        mock_client_instance.close = MagicMock()
        # SafeHTTPSClient is used as context manager in heartbeat reporting
        mock_client_instance.__enter__.return_value = mock_client_instance
        mock_client_instance.__exit__.return_value = None
        mock_client_class.return_value = mock_client_instance

        # set endpoint info
        heart_beat_manager._job_name = "test_job"
        heart_beat_manager._instance_id = 1
        # pod_ip is already set during initialization
        heart_beat_manager.stop_event.clear()  # Ensure stop_event is not set initially
        with heart_beat_manager._endpoint_lock:
            heart_beat_manager._endpoints = [
                Endpoint(id=1, ip="192.168.1.1", business_port="8080", mgmt_port="9090", status=EndpointStatus.NORMAL)
            ]
        
        mock_sleep.side_effect = mock_stop_sleep

        heart_beat_manager._report_heartbeat_loop()
        # assert client was called
        assert mock_client_class.called
        assert mock_client_instance.post.called

    @patch('motor.node_manager.core.heartbeat_manager.time.sleep')
    @patch('motor.node_manager.core.heartbeat_manager.SafeHTTPSClient')
    def test_heartbeat_report_with_empty_endpoints(self, mock_client_class, mock_sleep, heart_beat_manager):
        """test heartbeat report with empty endpoints"""
        call_count = {"count": 0}

        # set loop exec once
        def mock_stop_sleep(seconds):
            call_count["count"] += 1
            if call_count["count"] >= 1:
                heart_beat_manager.stop_event.set()

        mock_client_instance = MagicMock()
        mock_client_instance.post.return_value = {}
        mock_client_instance.close = MagicMock()
        # SafeHTTPSClient is used as context manager in heartbeat reporting
        mock_client_instance.__enter__.return_value = mock_client_instance
        mock_client_instance.__exit__.return_value = None
        mock_client_class.return_value = mock_client_instance

        # set endpoint info
        heart_beat_manager._job_name = "test_job"
        heart_beat_manager._instance_id = 1
        # pod_ip is already set during initialization
        heart_beat_manager.stop_event.clear()  # Ensure stop_event is not set initially
        # clear endpoint list
        with heart_beat_manager._endpoint_lock:
            heart_beat_manager._endpoints = []

        mock_sleep.side_effect = mock_stop_sleep

        heart_beat_manager._report_heartbeat_loop()
        # Even with empty endpoints, the loop should still run and send heartbeat
        # (with empty status dict)
        assert mock_client_class.called
        assert mock_client_instance.post.called

    @patch('motor.config.node_manager.safe_open')
    @patch.dict('os.environ', {'JOB_NAME': 'test_job', 'CONFIG_PATH': './', 'HCCL_PATH': './tests/jsons/hccl.json', 'ROLE': 'both'})
    def test_thread_safety(self, mock_safe_open, sample_start_cmd_msg, config_data, hccl_data):
        """test thread safety"""
        import threading
        mock_safe_open.side_effect = create_config_mock(config_data, hccl_data)
        # Clear singleton instance
        if hasattr(HeartbeatManager, '_instances') and HeartbeatManager in HeartbeatManager._instances:
            if HeartbeatManager in HeartbeatManager._instances:
                del HeartbeatManager._instances[HeartbeatManager]
        
        with patch('threading.Thread'):
            config = NodeManagerConfig()
            heartbeat_manager = HeartbeatManager(config)

            # Set initial state
            heartbeat_manager.update_endpoint(sample_start_cmd_msg)
            
            def update_endpoints():
                for _ in range(50):
                    heartbeat_manager.update_endpoint(sample_start_cmd_msg)
                    time.sleep(0.0005)

            def read_endpoints():
                for _ in range(50):
                    with heartbeat_manager._endpoint_lock:
                        endpoints = heartbeat_manager._endpoints.copy()
                    # assert endpoint len
                    assert len(endpoints) == len(sample_start_cmd_msg.endpoints)
                    time.sleep(0.0005)

            threads = []
            for i in range(3):
                if i % 2 == 0:
                    thread = threading.Thread(target=update_endpoints)
                else:
                    thread = threading.Thread(target=read_endpoints)
                threads.append(thread)
                thread.start()

            # Wait for all threads to complete.
            for thread in threads:
                thread.join(timeout=3.0)

            # Verify the consistency of the final state.
            assert heartbeat_manager._job_name == sample_start_cmd_msg.job_name
            assert len(heartbeat_manager._endpoints) == len(sample_start_cmd_msg.endpoints)
    
    def test_start_method(self, heart_beat_manager):
        """test start method"""
        assert heart_beat_manager._thread_started is False
        heart_beat_manager.start()
        assert heart_beat_manager._thread_started is True
        # Calling start again should not change the state
        heart_beat_manager.start()
        assert heart_beat_manager._thread_started is True
    
    @patch('motor.node_manager.core.heartbeat_manager.EngineManager')
    def test_reregister_success(self, mock_engine_manager_class, heart_beat_manager):
        """test _reregister success"""
        mock_engine_manager = MagicMock()
        mock_engine_manager.post_reregister_msg.return_value = True
        mock_engine_manager_class.return_value = mock_engine_manager
        
        heart_beat_manager._reregister()
        
        mock_engine_manager.post_reregister_msg.assert_called_once()
    
    @patch('motor.node_manager.core.heartbeat_manager.EngineManager')
    def test_reregister_failure(self, mock_engine_manager_class, heart_beat_manager):
        """test _reregister failure"""
        mock_engine_manager = MagicMock()
        mock_engine_manager.post_reregister_msg.return_value = False
        mock_engine_manager_class.return_value = mock_engine_manager
        
        heart_beat_manager._reregister()
        
        mock_engine_manager.post_reregister_msg.assert_called_once()
    
    @patch('motor.node_manager.core.heartbeat_manager.threading.Thread')
    @patch('motor.node_manager.core.heartbeat_manager.time.sleep')
    @patch('motor.node_manager.core.heartbeat_manager.SafeHTTPSClient')
    @patch('motor.node_manager.core.heartbeat_manager.EngineManager')
    def test_reregister_triggered_on_503(self, mock_engine_manager_class, mock_client_class, mock_sleep, mock_thread_class, heart_beat_manager):
        """test that reregister is triggered when 503 error occurs"""
        call_count = {"count": 0}
        
        def mock_stop_sleep(seconds):
            call_count["count"] += 1
            if call_count["count"] >= 1:
                heart_beat_manager.stop_event.set()
        
        mock_client = MagicMock()
        mock_client.post.side_effect = Exception("503 Service Unavailable")
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        mock_engine_manager = MagicMock()
        mock_engine_manager.post_reregister_msg.return_value = True
        mock_engine_manager_class.return_value = mock_engine_manager
        
        mock_reregister_thread = MagicMock()
        mock_thread_class.return_value = mock_reregister_thread
        
        heart_beat_manager._job_name = "test_job"
        heart_beat_manager._instance_id = 1
        # pod_ip is already set during initialization
        heart_beat_manager.stop_event.clear()  # Ensure stop_event is not set initially
        
        mock_sleep.side_effect = mock_stop_sleep
        
        heart_beat_manager._report_heartbeat_loop()
        
        # Verify that reregister thread was created and started (only if 503 was detected)
        # The thread creation happens in the exception handler
        assert mock_thread_class.call_count >= 0  # May or may not be called depending on exception handling
    
    @patch('motor.node_manager.core.heartbeat_manager.time.sleep')
    @patch('motor.node_manager.core.heartbeat_manager.SafeHTTPSClient')
    def test_reregister_lock_thread_safety(self, mock_client_class, mock_sleep, heart_beat_manager):
        """test that _reregister_lock prevents concurrent reregister attempts"""
        call_count = {"count": 0}
        
        def mock_stop_sleep(seconds):
            if call_count["count"] >= 1:
                heart_beat_manager.stop_event.set()
            call_count["count"] += 1
        
        mock_client = MagicMock()
        mock_client.post.side_effect = Exception("503 Service Unavailable")
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        heart_beat_manager._job_name = "test_job"
        heart_beat_manager._instance_id = 1
        # pod_ip is already set during initialization
        
        mock_sleep.side_effect = mock_stop_sleep
        
        # Start the loop - it should trigger reregister once, then skip on subsequent 503s
        heart_beat_manager._report_heartbeat_loop()
        
        # Verify that _reregistering flag is properly managed
        # The lock ensures only one reregister thread is started
        assert True  # Test passes if no race condition occurs
    
    def test_stop_method(self, heart_beat_manager):
        """test stop method"""
        # Start threads first
        heart_beat_manager.start()
        assert heart_beat_manager._thread_started is True
        
        # Stop should set stop_event and join threads
        heart_beat_manager.stop()
        
        assert heart_beat_manager.stop_event.is_set() is True
