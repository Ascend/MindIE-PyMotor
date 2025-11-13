#!/usr/bin/env python3
# coding=utf-8

import os
import sys
import json
import pytest
import tempfile
import shutil
import signal
from unittest.mock import patch, MagicMock, Mock, mock_open
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from motor.node_manager.core.engine_manager import EngineManager
from motor.resources.http_msg_spec import StartCmdMsg, Ranktable, ServerInfo, RegisterMsg, ReregisterMsg
from motor.resources.endpoint import Endpoint, DeviceInfo, EndpointStatus
from motor.resources.instance import ParallelConfig, PDRole


@pytest.fixture
def config_data():
    return {
        "parallel_config": {"tp": 2, "pp": 1},
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
            "hardware_type": "Ascend910",
            "device": [
                {"device_id": "0", "device_ip": "192.168.1.1", "rank_id": "0"},
                {"device_id": "1", "device_ip": "192.168.1.2", "rank_id": "1"}
            ]
        }]
    }


def create_config_mock(config_data, hccl_data):
    def mock_side_effect(file_path, mode):
        if "node_manager_config.json" in file_path:
            return mock_open(read_data=json.dumps(config_data)).return_value
        elif "hccl.json" in file_path:
            return mock_open(read_data=json.dumps(hccl_data)).return_value
        return mock_open().return_value
    return mock_side_effect


@pytest.fixture
def engine_manager(config_data, hccl_data):
    """Create EngineManager instance with mocked config"""
    with patch('motor.config.node_manager.safe_open') as mock_safe_open, \
         patch('threading.Thread') as mock_thread_class, \
         patch.dict('os.environ', {'JOB_NAME': 'test_job', 'CONFIG_PATH': './', 'HOME_HCCL_PATH': './tests/jsons'}):
        
        mock_safe_open.side_effect = create_config_mock(config_data, hccl_data)
        mock_thread = MagicMock()
        mock_thread_class.return_value = mock_thread
        
        # Clear singleton instance
        if hasattr(EngineManager, '_instances') and EngineManager in EngineManager._instances:
            if EngineManager in EngineManager._instances:
                del EngineManager._instances[EngineManager]
        
        manager = EngineManager()
        yield manager


@pytest.fixture
def sample_endpoints():
    """Create sample endpoints"""
    return [
        Endpoint(id=0, ip="192.168.1.100", business_port="8080", mgmt_port="9090"),
        Endpoint(id=1, ip="192.168.1.100", business_port="8081", mgmt_port="9091")
    ]


@pytest.fixture
def sample_ranktable():
    """Create sample ranktable"""
    device_info = DeviceInfo(device_id="0", device_ip="192.168.0.1", rank_id="0")
    server_info = ServerInfo(server_id="1", host_ip="192.168.1.200", device=[device_info])
    return Ranktable(version="1.0", status="normal", server_count="1", server_list=[server_info])


@pytest.fixture
def sample_start_cmd_msg(sample_endpoints, sample_ranktable):
    """Create sample StartCmdMsg"""
    return StartCmdMsg(
        job_name="test_job",
        role="both",
        instance_id=1,
        endpoints=sample_endpoints,
        ranktable=sample_ranktable
    )


class TestEngineManager:
    
    @patch('motor.config.node_manager.safe_open')
    @patch('threading.Thread')
    @patch.dict('os.environ', {'JOB_NAME': 'test_job', 'CONFIG_PATH': './', 'HOME_HCCL_PATH': './tests/jsons'})
    def test_init_success(self, mock_thread_class, mock_safe_open, config_data, hccl_data):
        """Test EngineManager initialization"""
        mock_safe_open.side_effect = create_config_mock(config_data, hccl_data)
        mock_thread = MagicMock()
        mock_thread_class.return_value = mock_thread
        
        # Clear singleton instance
        if hasattr(EngineManager, '_instances') and EngineManager in EngineManager._instances:
            if EngineManager in EngineManager._instances:
                del EngineManager._instances[EngineManager]
        
        manager = EngineManager()
        
        assert manager.endpoints == []
        assert manager.instance_id == 0
        assert manager.is_working is False
        assert hasattr(manager, 'config')
        mock_thread_class.assert_called_once()
    
    @patch('motor.config.node_manager.safe_open')
    @patch('threading.Thread')
    @patch.dict('os.environ', {'JOB_NAME': 'test_job', 'CONFIG_PATH': './', 'HOME_HCCL_PATH': './tests/jsons'})
    def test_singleton_pattern(self, mock_thread_class, mock_safe_open, config_data, hccl_data):
        """Test singleton pattern"""
        mock_safe_open.side_effect = create_config_mock(config_data, hccl_data)
        mock_thread = MagicMock()
        mock_thread_class.return_value = mock_thread
        
        # Clear singleton instance
        if hasattr(EngineManager, '_instances') and EngineManager in EngineManager._instances:
            if EngineManager in EngineManager._instances:
                del EngineManager._instances[EngineManager]
        
        manager1 = EngineManager()
        manager2 = EngineManager()
        assert manager1 is manager2
    
    def test_check_config_paras_success(self, engine_manager):
        """Test _check_config_paras with valid config"""
        engine_manager.config.job_name = "test_job"
        assert engine_manager._check_config_paras() is True
    
    def test_check_config_paras_failure(self, engine_manager):
        """Test _check_config_paras with None job_name"""
        engine_manager.config.job_name = None
        assert engine_manager._check_config_paras() is False
    
    def test_gen_register_msg_success(self, engine_manager):
        """Test _gen_register_msg with valid config"""
        engine_manager.config.job_name = "test_job"
        engine_manager.config.model_name = "test_model"
        engine_manager.config.role = PDRole.ROLE_U
        engine_manager.config.pod_ip = "192.168.1.100"
        engine_manager.config.host_ip = "192.168.1.200"
        engine_manager.config.service_ports = ["8080", "8081"]
        engine_manager.config.node_manager_port = 8080
        engine_manager.config.parallel_config = ParallelConfig(tp=2, pp=1)
        
        device_info = DeviceInfo(device_id="0", device_ip="192.168.0.1", rank_id="0")
        server_info = ServerInfo(server_id="1", host_ip="192.168.1.200", device=[device_info])
        ranktable = Ranktable(version="1.0", status="normal", server_count="1", server_list=[server_info])
        engine_manager.config.ranktable = ranktable
        
        msg = engine_manager._gen_register_msg()
        assert msg is not None
        assert isinstance(msg, RegisterMsg)
        assert msg.job_name == "test_job"
        assert msg.model_name == "test_model"
        assert msg.role == PDRole.ROLE_U
    
    def test_gen_register_msg_failure(self, engine_manager):
        """Test _gen_register_msg with invalid config"""
        engine_manager.config.job_name = None
        msg = engine_manager._gen_register_msg()
        assert msg is None
    
    def test_gen_reregister_msg_success(self, engine_manager, sample_endpoints):
        """Test _gen_reregister_msg with valid data"""
        engine_manager.config.job_name = "test_job"
        engine_manager.config.role = PDRole.ROLE_U
        engine_manager.config.pod_ip = "192.168.1.100"
        engine_manager.config.host_ip = "192.168.1.200"
        engine_manager.config.node_manager_port = 8080
        engine_manager.config.parallel_config = ParallelConfig(tp=2, pp=1)
        engine_manager.endpoints = sample_endpoints
        engine_manager.instance_id = 1
        
        msg = engine_manager._gen_reregister_msg()
        assert msg is not None
        assert isinstance(msg, ReregisterMsg)
        assert msg.job_name == "test_job"
        assert msg.instance_id == 1
        assert len(msg.endpoints) == 2
    
    def test_gen_reregister_msg_failure_no_endpoints(self, engine_manager):
        """Test _gen_reregister_msg with empty endpoints"""
        engine_manager.config.job_name = "test_job"
        engine_manager.config.role = PDRole.ROLE_U
        engine_manager.config.pod_ip = "192.168.1.100"
        engine_manager.config.host_ip = "192.168.1.200"
        engine_manager.config.node_manager_port = 8080
        engine_manager.config.parallel_config = ParallelConfig(tp=2, pp=1)
        engine_manager.endpoints = []
        engine_manager.instance_id = 1
        
        msg = engine_manager._gen_reregister_msg()
        assert msg is None
    
    def test_gen_reregister_msg_failure_no_instance_id(self, engine_manager, sample_endpoints):
        """Test _gen_reregister_msg with None instance_id"""
        engine_manager.config.job_name = "test_job"
        engine_manager.config.role = PDRole.ROLE_U
        engine_manager.config.pod_ip = "192.168.1.100"
        engine_manager.config.host_ip = "192.168.1.200"
        engine_manager.config.node_manager_port = 8080
        engine_manager.config.parallel_config = ParallelConfig(tp=2, pp=1)
        engine_manager.endpoints = sample_endpoints
        engine_manager.instance_id = None
        
        # Should return None before trying to create ReregisterMsg (which would fail validation)
        msg = engine_manager._gen_reregister_msg()
        assert msg is None
    
    @patch('motor.node_manager.core.engine_manager.SafeHTTPSClient')
    def test_post_register_msg_success(self, mock_client_class, engine_manager):
        """Test post_register_msg with successful response"""
        engine_manager.config.job_name = "test_job"
        engine_manager.config.model_name = "test_model"
        engine_manager.config.role = PDRole.ROLE_U
        engine_manager.config.pod_ip = "192.168.1.100"
        engine_manager.config.host_ip = "192.168.1.200"
        engine_manager.config.service_ports = ["8080"]
        engine_manager.config.node_manager_port = 8080
        engine_manager.config.controller_api_dns = "localhost"
        engine_manager.config.controller_api_port = 8080
        engine_manager.config.parallel_config = ParallelConfig(tp=2, pp=1)
        
        device_info = DeviceInfo(device_id="0", device_ip="192.168.0.1", rank_id="0")
        server_info = ServerInfo(server_id="1", host_ip="192.168.1.200", device=[device_info])
        ranktable = Ranktable(version="1.0", status="normal", server_count="1", server_list=[server_info])
        engine_manager.config.ranktable = ranktable
        
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.post.return_value = mock_response
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        result = engine_manager.post_register_msg()
        assert result is True
        mock_client.post.assert_called_once()
    
    @patch('motor.node_manager.core.engine_manager.SafeHTTPSClient')
    def test_post_register_msg_failure(self, mock_client_class, engine_manager):
        """Test post_register_msg with exception"""
        engine_manager.config.job_name = "test_job"
        engine_manager.config.model_name = "test_model"
        engine_manager.config.role = PDRole.ROLE_U
        engine_manager.config.pod_ip = "192.168.1.100"
        engine_manager.config.host_ip = "192.168.1.200"
        engine_manager.config.service_ports = ["8080"]
        engine_manager.config.node_manager_port = 8080
        engine_manager.config.controller_api_dns = "localhost"
        engine_manager.config.controller_api_port = 8080
        engine_manager.config.parallel_config = ParallelConfig(tp=2, pp=1)
        
        device_info = DeviceInfo(device_id="0", device_ip="192.168.0.1", rank_id="0")
        server_info = ServerInfo(server_id="1", host_ip="192.168.1.200", device=[device_info])
        ranktable = Ranktable(version="1.0", status="normal", server_count="1", server_list=[server_info])
        engine_manager.config.ranktable = ranktable
        
        mock_client_class.side_effect = Exception("Connection error")
        
        result = engine_manager.post_register_msg()
        assert result is False
    
    @patch('motor.node_manager.core.engine_manager.SafeHTTPSClient')
    def test_post_reregister_msg_success(self, mock_client_class, engine_manager, sample_endpoints):
        """Test post_reregister_msg with successful response"""
        engine_manager.config.job_name = "test_job"
        engine_manager.config.role = PDRole.ROLE_U
        engine_manager.config.pod_ip = "192.168.1.100"
        engine_manager.config.host_ip = "192.168.1.200"
        engine_manager.config.node_manager_port = 8080
        engine_manager.config.controller_api_dns = "localhost"
        engine_manager.config.controller_api_port = 8080
        engine_manager.config.parallel_config = ParallelConfig(tp=2, pp=1)
        engine_manager.endpoints = sample_endpoints
        engine_manager.instance_id = 1
        
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.post.return_value = mock_response
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        result = engine_manager.post_reregister_msg()
        assert result is True
        mock_client.post.assert_called_once()
    
    @patch('motor.node_manager.core.engine_manager.SafeHTTPSClient')
    def test_post_reregister_msg_failure(self, mock_client_class, engine_manager, sample_endpoints):
        """Test post_reregister_msg with exception"""
        engine_manager.config.job_name = "test_job"
        engine_manager.config.role = PDRole.ROLE_U
        engine_manager.config.pod_ip = "192.168.1.100"
        engine_manager.config.host_ip = "192.168.1.200"
        engine_manager.config.node_manager_port = 8080
        engine_manager.config.controller_api_dns = "localhost"
        engine_manager.config.controller_api_port = 8080
        engine_manager.config.parallel_config = ParallelConfig(tp=2, pp=1)
        engine_manager.endpoints = sample_endpoints
        engine_manager.instance_id = 1
        
        mock_client_class.side_effect = Exception("Connection error")
        
        result = engine_manager.post_reregister_msg()
        assert result is False
    
    def test_check_cmd_para_success(self, engine_manager, sample_start_cmd_msg):
        """Test _check_cmd_para with valid command"""
        engine_manager.config.job_name = "test_job"
        engine_manager.config.endpoint_num = 2
        engine_manager.config.pod_ip = "192.168.1.100"
        
        assert engine_manager._check_cmd_para(sample_start_cmd_msg) is True
    
    @pytest.mark.parametrize("job_name,endpoint_num,pod_ip,expected", [
        ("wrong_job", 2, "192.168.1.100", False),
        ("test_job", 1, "192.168.1.100", False),
        ("test_job", 2, "192.168.1.101", False),
    ])
    def test_check_cmd_para_failure(self, engine_manager, sample_start_cmd_msg, job_name, endpoint_num, pod_ip, expected):
        """Test _check_cmd_para with invalid parameters"""
        engine_manager.config.job_name = job_name
        engine_manager.config.endpoint_num = endpoint_num
        engine_manager.config.pod_ip = pod_ip
        
        assert engine_manager._check_cmd_para(sample_start_cmd_msg) == expected
    
    def test_check_cmd_para_invalid_ranktable_type(self, engine_manager, sample_start_cmd_msg):
        """Test _check_cmd_para with invalid ranktable type"""
        engine_manager.config.job_name = "test_job"
        engine_manager.config.endpoint_num = 2
        engine_manager.config.pod_ip = "192.168.1.100"
        
        # Create invalid StartCmdMsg with non-Ranktable ranktable
        # Note: Pydantic will validate at creation time, so we test with None
        # by directly modifying the attribute after creation
        invalid_msg = StartCmdMsg(
            job_name="test_job",
            role="both",
            instance_id=1,
            endpoints=sample_start_cmd_msg.endpoints,
            ranktable=sample_start_cmd_msg.ranktable
        )
        # Override ranktable with invalid type
        invalid_msg.ranktable = "invalid"  # This will fail isinstance check
        
        result = engine_manager._check_cmd_para(invalid_msg)
        assert result is False
    
    @patch('motor.node_manager.core.engine_manager.EngineManager._write_ranktable_to_file')
    def test_parse_start_cmd_success(self, mock_write, engine_manager, sample_start_cmd_msg):
        """Test parse_start_cmd with valid command"""
        engine_manager.config.job_name = "test_job"
        engine_manager.config.endpoint_num = 2
        engine_manager.config.pod_ip = "192.168.1.100"
        
        result = engine_manager.parse_start_cmd(sample_start_cmd_msg)
        
        assert result is True
        assert engine_manager.instance_id == 1
        assert len(engine_manager.endpoints) == 2
        assert engine_manager.instance_ranktable == sample_start_cmd_msg.ranktable
        mock_write.assert_called_once()
    
    def test_parse_start_cmd_failure(self, engine_manager, sample_start_cmd_msg):
        """Test parse_start_cmd with invalid command"""
        engine_manager.config.job_name = "wrong_job"
        engine_manager.config.endpoint_num = 2
        engine_manager.config.pod_ip = "192.168.1.100"
        
        result = engine_manager.parse_start_cmd(sample_start_cmd_msg)
        assert result is False
    
    def test_write_ranktable_to_file(self, engine_manager, sample_ranktable):
        """Test _write_ranktable_to_file"""
        temp_dir = tempfile.mkdtemp()
        try:
            with patch('os.getcwd', return_value=temp_dir):
                engine_manager.instance_id = 1
                engine_manager.instance_ranktable = sample_ranktable
                
                engine_manager._write_ranktable_to_file()
                
                expected_file = os.path.join(temp_dir, "ranktables", "ranktable_1.json")
                assert os.path.exists(expected_file)
                
                with open(expected_file, 'r') as f:
                    data = json.load(f)
                    assert data["version"] == "1.0"
                    assert data["status"] == "normal"
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_write_ranktable_to_file_no_instance_id(self, engine_manager, sample_ranktable):
        """Test _write_ranktable_to_file without instance_id"""
        temp_dir = tempfile.mkdtemp()
        try:
            with patch('os.getcwd', return_value=temp_dir):
                # Remove instance_id attribute
                if hasattr(engine_manager, 'instance_id'):
                    delattr(engine_manager, 'instance_id')
                engine_manager.instance_ranktable = sample_ranktable
                
                engine_manager._write_ranktable_to_file()
                
                expected_file = os.path.join(temp_dir, "ranktables", "ranktable_unknown.json")
                assert os.path.exists(expected_file)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_stop(self, engine_manager):
        """Test stop method"""
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        engine_manager._register_thread = mock_thread
        
        engine_manager.stop()
        
        # Should call join on the thread object
        mock_thread.join.assert_called_once_with(timeout=0.1)
    
    @patch('motor.node_manager.core.engine_manager.time.sleep')
    @patch('motor.node_manager.core.engine_manager.EngineManager.post_register_msg')
    @patch('motor.node_manager.core.engine_manager.os.kill')
    def test_register_retry_mechanism(self, mock_kill, mock_post_register, mock_sleep, engine_manager):
        """Test registration retry mechanism"""
        # Make all attempts fail
        mock_post_register.return_value = False
        
        # Run _register method
        engine_manager._register()
        
        # Should have retried 5 times
        assert mock_post_register.call_count == 5
        # Should have sent SIGTERM after max retries
        mock_kill.assert_called_once_with(os.getpid(), signal.SIGTERM)
    
    @patch('motor.node_manager.core.engine_manager.EngineManager.post_register_msg')
    @patch('motor.node_manager.core.engine_manager.time.sleep')
    def test_register_success_on_first_attempt(self, mock_sleep, mock_post_register, engine_manager):
        """Test registration succeeds on first attempt"""
        mock_post_register.return_value = True
        
        engine_manager._register()
        
        # Should only try once
        assert mock_post_register.call_count == 1
        # Should not sleep
        mock_sleep.assert_not_called()
    
    @patch('motor.node_manager.core.engine_manager.EngineManager.post_register_msg')
    @patch('motor.node_manager.core.engine_manager.time.sleep')
    def test_register_success_on_retry(self, mock_sleep, mock_post_register, engine_manager):
        """Test registration succeeds on retry"""
        # First attempt fails, second succeeds
        mock_post_register.side_effect = [False, True]
        
        engine_manager._register()
        
        # Should have tried twice
        assert mock_post_register.call_count == 2
        # Should have slept once between attempts
        assert mock_sleep.call_count == 1

