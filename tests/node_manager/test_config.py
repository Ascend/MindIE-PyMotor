#!/usr/bin/env python3
# coding=utf-8

import json
import os
import sys
import pytest
from unittest.mock import patch, mock_open

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from motor.config.node_manager import NodeManagerConfig
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
        "version": "1.2",
        "server_list": [{
            "server_id": "90.90.97.30",
            "host_ip": "90.90.97.30",
            "container_ip": "127.0.0.1",  # Used by NodeManagerConfig, not Ranktable
            "hardware_type": "Ascend910",  # Used by NodeManagerConfig, not Ranktable
            "device": [
                {"device_id": "0", "device_ip": "192.168.1.1", "super_device_id": "126455325", "rank_id": "0"},
                {"device_id": "1", "device_ip": "192.168.1.2", "super_device_id": "398754302", "rank_id": "1"}
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


def clear_node_manager_config():
    """Clear singleton instance and reset class variables"""
    # Clear singleton instance
    if hasattr(NodeManagerConfig, '_instances'):
        if NodeManagerConfig in NodeManagerConfig._instances:
            del NodeManagerConfig._instances[NodeManagerConfig]
    
    # Reset class variables to initial state
    # Important: clear lists before reassigning to ensure no reference issues
    NodeManagerConfig.device_info.clear()
    NodeManagerConfig.mgmt_ports.clear()
    NodeManagerConfig.service_ports.clear()
    
    NodeManagerConfig.pod_ip = None
    NodeManagerConfig.host_ip = None
    NodeManagerConfig.parallel_config = None
    NodeManagerConfig.endpoint_num = 0
    NodeManagerConfig.role = None
    NodeManagerConfig.model_name = None
    NodeManagerConfig.hardware_type = None
    NodeManagerConfig.controller_api_dns = None
    NodeManagerConfig.controller_api_port = None
    NodeManagerConfig.ranktable = None


class TestNodeManagerConfig:
    
    @patch.dict('os.environ')  
    @patch('motor.config.node_manager.safe_open')
    def test_init_success(self, mock_safe_open, config_data, hccl_data):
        clear_node_manager_config()
        mock_safe_open.side_effect = create_config_mock(config_data, hccl_data)
        
        config = NodeManagerConfig()
        
        assert config.job_name == "test_job"
        assert isinstance(config.parallel_config, ParallelConfig)
        assert config.role == PDRole.ROLE_U
        assert len(config.device_info) == 2  # Matches hccl_data fixture
        assert config.pod_ip == "127.0.0.1"
        assert config.host_ip == "90.90.97.30"
        assert config.ranktable is not None
        assert config.ranktable.status == "completed"
        assert config.ranktable.version == "1.2"
    
    @pytest.mark.parametrize("invalid_config,expected_error", [
        ({"role": "both"}, "Invalid config json"),  
        ({"parallel_config": {"tp": 1, "pp": 1}, "role": "invalid"}, "Invalid role value"),  # 无效role
    ])
    @patch.dict('os.environ')
    @patch('motor.config.node_manager.safe_open')
    def test_config_validation_errors(self, mock_safe_open, invalid_config, expected_error):
        clear_node_manager_config()
        
        invalid_config.update({
            "controller_api_dns": "localhost", 
            "controller_api_port": 8080,
            "node_manager_port": 8080,
            "model_name": "vllm"
        })
        
        mock_safe_open.side_effect = create_config_mock(
            invalid_config, 
            {"status": "completed", "server_list": []}
        )
        
        with pytest.raises(ValueError, match=expected_error):
            NodeManagerConfig()

    @pytest.mark.parametrize("invalid_hccl,expected_error", [
        # Invalid Ranktable structure (missing required fields)
        ({}, "Invalid HCCL json"),
        ({"status": "pending"}, "Invalid HCCL json"),  # Missing required Ranktable fields
        # Empty server_list is allowed (server will be None)
        ({"status": "completed", "server_count": "0", "version": "1.0", "server_list": []}, None),
        # Empty device list is allowed (no devices will be added)
        ({"status": "completed", "server_count": "1", "version": "1.0", 
          "server_list": [{"server_id": "1", "host_ip": "192.168.1.100", "container_ip": "127.0.0.1", "device": []}]}, None),
    ])
    @patch.dict('os.environ')
    @patch('motor.config.node_manager.safe_open')
    def test_hccl_validation_errors(self, mock_safe_open, invalid_hccl, expected_error, config_data):
        clear_node_manager_config()
        mock_safe_open.side_effect = create_config_mock(config_data, invalid_hccl)
        
        if expected_error:
            with pytest.raises(ValueError, match=expected_error):
                NodeManagerConfig()
        else:
            # These cases should not raise errors with the new implementation
            config = NodeManagerConfig()
            if invalid_hccl.get("server_list") == []:
                assert config.pod_ip is None
                assert config.host_ip is None
                assert len(config.device_info) == 0
            elif invalid_hccl.get("server_list", [{}])[0].get("device") == []:
                assert len(config.device_info) == 0
    
    @patch.dict('os.environ')
    @patch('motor.config.node_manager.safe_open')
    def test_calculate_endpoint_num(self, mock_safe_open, config_data, hccl_data):
        clear_node_manager_config()
        mock_safe_open.side_effect = create_config_mock(config_data, hccl_data)
        config = NodeManagerConfig()

        expected_num = max(1, len(config.device_info) //
                           (config.parallel_config.tp_size * config.parallel_config.pp_size))
        assert config.endpoint_num == expected_num
        assert len(config.mgmt_ports) == config.endpoint_num
        assert len(config.service_ports) == config.endpoint_num
    
    @patch.dict('os.environ')
    @patch('motor.config.node_manager.safe_open')
    def test_singleton_behavior(self, mock_safe_open, config_data, hccl_data):
        clear_node_manager_config()
        mock_safe_open.side_effect = create_config_mock(config_data, hccl_data)
        config1 = NodeManagerConfig()
        config2 = NodeManagerConfig()
        assert config1 is config2
    
    def test_real_hccl_files_exist(self):
        current_dir = os.path.dirname(__file__)
        project_root = os.path.join(current_dir, '..', '..')
        
        hccl_files = [
            os.path.join(project_root, "tests", "jsons", "hccl_a2.json"),
            os.path.join(project_root, "tests", "jsons", "hccl_a3.json")
        ]
        
        for file_path in hccl_files:
            assert os.path.exists(file_path), f"测试文件不存在: {file_path}"
            
            with open(file_path, 'r') as f:
                data = json.load(f)
                assert data["status"] == "completed"
    
    @patch.dict('os.environ')
    @patch('motor.config.node_manager.safe_open')
    def test_hccl_ranktable_creation(self, mock_safe_open, config_data, hccl_data):
        """Test that ranktable is properly created from HCCL data"""
        clear_node_manager_config()
        mock_safe_open.side_effect = create_config_mock(config_data, hccl_data)
        config = NodeManagerConfig()
        
        # Verify ranktable was created
        assert config.ranktable is not None
        assert config.ranktable.status == "completed"
        assert config.ranktable.version == "1.2"
        assert config.ranktable.server_count == "1"
        assert len(config.ranktable.server_list) == 1
        assert config.ranktable.server_list[0].server_id == "90.90.97.30"
        assert config.ranktable.server_list[0].host_ip == "90.90.97.30"
    
    @patch.dict('os.environ')
    @patch('motor.config.node_manager.safe_open')
    def test_hccl_with_super_device_id(self, mock_safe_open, config_data):
        """Test parsing HCCL with super_device_id"""
        clear_node_manager_config()
        
        # Need at least 2 devices (tp=2, pp=1) for config_data's parallel_config
        hccl_with_super = {
            "status": "completed",
            "server_count": "1",
            "version": "1.0",
            "server_list": [{
                "server_id": "1",
                "host_ip": "192.168.1.100",
                "container_ip": "192.168.1.100",
                "device": [
                    {"device_id": "0", "device_ip": "192.168.1.1", "rank_id": "0", "super_device_id": "12345"},
                    {"device_id": "1", "device_ip": "192.168.1.2", "rank_id": "1", "super_device_id": "67890"}
                ]
            }]
        }
        
        mock_safe_open.side_effect = create_config_mock(config_data, hccl_with_super)
        config = NodeManagerConfig()
        
        assert len(config.device_info) == 2
        assert config.device_info[0].super_device_id == "12345"
        assert config.device_info[1].super_device_id == "67890"
    
    @patch.dict('os.environ')
    @patch('motor.config.node_manager.safe_open')
    def test_hccl_empty_server_list(self, mock_safe_open, config_data):
        """Test parsing HCCL with empty server_list (should handle None gracefully)"""
        clear_node_manager_config()
        
        hccl_empty = {
            "status": "completed",
            "server_count": "0",
            "version": "1.0",
            "server_list": []
        }
        
        mock_safe_open.side_effect = create_config_mock(config_data, hccl_empty)
        config = NodeManagerConfig()
        
        # Should handle None server gracefully
        assert config.pod_ip is None
        assert config.host_ip is None
        assert len(config.device_info) == 0
        assert config.ranktable is not None  # Ranktable should still be created
    
    @patch.dict('os.environ')
    @patch('motor.config.node_manager.safe_open')
    def test_hccl_empty_device_list(self, mock_safe_open, config_data):
        """Test parsing HCCL with empty device list"""
        clear_node_manager_config()
        
        hccl_no_devices = {
            "status": "completed",
            "server_count": "1",
            "version": "1.0",
            "server_list": [{
                "server_id": "1",
                "host_ip": "192.168.1.100",
                "container_ip": "192.168.1.100",
                "device": []
            }]
        }
        
        mock_safe_open.side_effect = create_config_mock(config_data, hccl_no_devices)
        config = NodeManagerConfig()
        
        # Should handle empty device list gracefully
        assert len(config.device_info) == 0
        assert config.pod_ip == "192.168.1.100"
        assert config.host_ip == "192.168.1.100"
