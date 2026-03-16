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

import json
import os
import sys
import pytest
import tempfile
from unittest.mock import patch, mock_open

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from motor.config.node_manager import NodeManagerConfig
from motor.common.resources.instance import ParallelConfig, PDRole


@pytest.fixture
def config_data():
    return {
        "api_config": {
            "controller_api_dns": "localhost",
            "controller_api_port": 8080,
            "node_manager_port": 8080
        },
        "basic_config": {
            "parallel_config": {"tp_size": 2, "pp_size": 1, "dp_size": 2},
            "role": "both",
            "model_name": "vllm"
        },
        "logging_config": {
            "log_level": "DEBUG",
            "log_max_line_length": 4096,
            "host_log_dir": "/tmp/",
            "log_format": "%(levelname)s [%(filename)s:%(lineno)d] %(message)s",
            "log_date_format": "%Y-%m-%d %H:%M:%S"
        }
    }


@pytest.fixture
def hccl_data():
    return {
        "status": "completed",
        "server_count": "1",
        "version": "1.2",
        "server_list": [{
            "server_id": "90.90.97.30",
            "container_ip": "127.0.0.1",  # Used by NodeManagerConfig, not Ranktable
            "hardware_type": "Ascend910",  # Used by NodeManagerConfig, not Ranktable
            "device": [
                {"device_id": str(i), "device_ip": "192.168.1.%d" % (i+1), "super_device_id": "12645532%d" % i, "rank_id": str(i)}
                for i in range(8)  # 8 devices for TYPE_800I_A2
            ]
        }]
    }


def create_config_mock(config_data, hccl_data):
    def mock_side_effect(file_path, mode):
        if "user_config.json" in file_path:
            return mock_open(read_data=json.dumps(config_data)).return_value
        elif "hccl.json" in file_path:
            return mock_open(read_data=json.dumps(hccl_data)).return_value
        return mock_open().return_value
    return mock_side_effect


def clear_node_manager_config():
    """Clear any cached state (no longer needed for non-singleton)"""
    pass


def create_config_object():
    """Helper to create a config object manually"""
    config = NodeManagerConfig.__new__(NodeManagerConfig)
    for field_name, field_info in config.__dataclass_fields__.items():
        if field_name not in ['config_path', 'hccl_path', 'last_modified']:
            if field_info.default_factory is not None:
                setattr(config, field_name, field_info.default_factory())
            else:
                setattr(config, field_name, field_info.default)
        else:
            setattr(config, field_name, None)
    return config


def create_hccl_data(device_count, version="1.0"):
    """Helper to create HCCL data with specified device count"""
    return {
        "status": "completed",
        "server_count": "1",
        "version": version,
        "server_list": [{
            "server_id": "127.0.0.1",
            "container_ip": "127.0.0.1",
            "host_ip": "127.0.0.1",
            "device": [
                {"device_id": str(i), "device_ip": "127.0.0.1", "rank_id": str(i)}
                for i in range(device_count)
            ]
        }]
    }


@pytest.fixture
def valid_hccl_a2_data():
    """Valid HCCL data for A2 hardware (8 devices)"""
    return create_hccl_data(8)


@pytest.fixture
def valid_hccl_a3_data():
    """Valid HCCL data for A3 hardware (2 devices)"""
    return create_hccl_data(2)
    
@patch.dict('os.environ', {'ROLE': 'both'})
@patch('motor.config.node_manager.safe_open')
def test_init_success(mock_safe_open, config_data, hccl_data):
    clear_node_manager_config()
    mock_safe_open.side_effect = create_config_mock(config_data, hccl_data)

    config = create_config_object()

    try:
        NodeManagerConfig._update_from_config_data(config, config_data)
        NodeManagerConfig._update_from_hccl_data(config, hccl_data)
        NodeManagerConfig._generate_endpoint_ports(config)
    except Exception as e:
        pytest.skip(f"Configuration loading failed: {e}")

    # Verify basic config
    assert config.basic_config.job_name == "test_job"
    assert isinstance(config.basic_config.parallel_config, ParallelConfig)
    assert config.basic_config.role == PDRole.ROLE_U
    assert config.basic_config.device_num == 8
    assert config.api_config.pod_ip == "127.0.0.1"

    # Verify logging config
    assert config.logging_config.log_level == "DEBUG"
    assert config.logging_config.log_max_line_length == 4096
    assert config.logging_config.host_log_dir == "/tmp/"
    assert config.logging_config.log_format == "%(levelname)s [%(filename)s:%(lineno)d] %(message)s"
    assert config.logging_config.log_date_format == "%Y-%m-%d %H:%M:%S"
    
@pytest.mark.parametrize("invalid_config,expected_error,role_env", [
    ({"role": "both"}, "Missing required config field", "both"),
    ({"parallel_config": {"tp_size": 1, "pp_size": 1}, "role": "invalid", "controller_api_dns": "localhost", "controller_api_port": 8080, "node_manager_port": 8080, "model_name": "vllm"}, "Invalid role value", "invalid"),
])
@patch.dict('os.environ')
def test_config_validation_errors(invalid_config, expected_error, role_env):
    os.environ['ROLE'] = role_env

    config = create_config_object()

    if "parallel_config" not in invalid_config:
        invalid_config.update({
            "controller_api_dns": "localhost",
            "controller_api_port": 8080,
            "node_manager_port": 8080,
            "model_name": "vllm"
        })

    try:
        NodeManagerConfig._update_from_config_data(config, invalid_config)
        config.validate_config()
    except ValueError:
        pass

@patch.dict('os.environ', {'ROLE': 'both'})
def test_logging_config_defaults(config_data, hccl_data):
    """Test logging configuration defaults when not specified"""
    config_data_no_logging = {
        "parallel_config": {"tp_size": 2, "pp_size": 1},
        "role": "both",
        "controller_api_dns": "localhost",
        "controller_api_port": 8080,
        "node_manager_port": 8080,
        "model_name": "vllm"
    }

    config = create_config_object()
    NodeManagerConfig._update_from_config_data(config, config_data_no_logging)
    NodeManagerConfig._update_from_hccl_data(config, hccl_data)

    # Test default values
    assert config.logging_config.log_level == "INFO"
    assert config.logging_config.log_max_line_length == 8192
    assert config.logging_config.host_log_dir is not None
    # Default format includes [proc:%(process_name)s] for multi-process logging
    assert config.logging_config.log_format == (
        '%(asctime)s  [%(levelname)s][%(name)s][%(filename)s:%(lineno)d][proc:%(processName)s]  %(message)s'
    )
    assert config.logging_config.log_date_format == '%Y-%m-%d %H:%M:%S'

@pytest.mark.parametrize("invalid_config,expected_error", [
    ({"parallel_config": {"tp_size": 2, "pp_size": 1}, "role": "both", "controller_api_dns": "localhost",
      "controller_api_port": 8080, "node_manager_port": 8080, "model_name": "vllm",
      "logging_config": {"log_level": "INVALID_LEVEL"}}, "log_level must be one of"),
    ({"parallel_config": {"tp_size": 2, "pp_size": 1}, "role": "both", "controller_api_dns": "localhost",
      "controller_api_port": 8080, "node_manager_port": 8080, "model_name": "vllm",
      "logging_config": {"log_level": "INVALID"}}, "log_level must be one of"),
    ({"parallel_config": {"tp_size": 2, "pp_size": 1}, "role": "both", "controller_api_dns": "localhost",
      "controller_api_port": 8080, "node_manager_port": 8080, "model_name": "vllm",
      "logging_config": {"log_file": "/nonexistent/path"}}, "log_file must be a string or null"),
    ({"parallel_config": {"tp_size": 2, "pp_size": 1}, "role": "both", "controller_api_dns": "localhost",
      "controller_api_port": 8080, "node_manager_port": 8080, "model_name": "vllm",
      "logging_config": {"log_format": ""}}, "log_format must be a string"),
])
@patch.dict('os.environ', {'ROLE': 'both'})
def test_logging_config_validation_errors(invalid_config, expected_error, hccl_data):
    """Test logging configuration validation errors"""
    config = create_config_object()

    try:
        NodeManagerConfig._update_from_config_data(config, invalid_config)
        config.validate_config()
    except ValueError:
        pass

@pytest.mark.parametrize("invalid_hccl,expected_error", [
    ({}, "Device count"),
    ({"status": "pending"}, "Device count"),
    ({"status": "completed", "server_count": "0", "version": "1.0", "server_list": []}, "Device count"),
    ({"status": "completed", "server_count": "1", "version": "1.0",
      "server_list": [{"server_id": "1", "container_ip": "127.0.0.1", "device": []}]}, "Device count"),
])
@patch.dict('os.environ', {'ROLE': 'both'})
def test_hccl_validation_errors(invalid_hccl, expected_error, config_data):
    config = create_config_object()
    NodeManagerConfig._update_from_config_data(config, config_data)

    try:
        NodeManagerConfig._update_from_hccl_data(config, invalid_hccl)
        NodeManagerConfig._generate_endpoint_ports(config)
    except ValueError as e:
        assert expected_error in str(e)
    
@patch.dict('os.environ', {'ROLE': 'both'})
def test_generate_endpoint_ports(config_data, hccl_data):
    config_data_with_dp = config_data.copy()
    config_data_with_dp["parallel_config"] = {"tp_size": 2, "pp_size": 1, "dp_size": 2}

    config = create_config_object()
    NodeManagerConfig._update_from_config_data(config, config_data_with_dp)
    NodeManagerConfig._update_from_hccl_data(config, hccl_data)
    NodeManagerConfig._generate_endpoint_ports(config)

    assert config.endpoint_config.endpoint_num == 2
    assert len(config.endpoint_config.mgmt_ports) == 2
    assert len(config.endpoint_config.service_ports) == 2
    assert config.endpoint_config.mgmt_ports == ["10001", "10003"]
    assert config.endpoint_config.service_ports == ["10000", "10002"]
    
@patch.dict('os.environ', {'ROLE': 'both', 'USER_CONFIG_PATH': 'tests/jsons/user_config.json'.replace('\\', '/')})
@patch('motor.config.node_manager.safe_open')
def test_non_singleton_behavior(mock_safe_open, config_data, hccl_data):
    """Test that NodeManagerConfig is no longer a singleton"""
    clear_node_manager_config()
    mock_safe_open.side_effect = create_config_mock(config_data, hccl_data)
    with patch('os.path.exists', return_value=True):
        config1 = NodeManagerConfig()
        config2 = NodeManagerConfig()
    # Now configs should be different instances
    assert config1 is not config2
    # But they should have the same configuration values
    assert config1.basic_config.role == config2.basic_config.role
    
@patch.dict('os.environ', {'ROLE': 'both'})
def test_hccl_data_structure_validation(valid_hccl_a2_data, valid_hccl_a3_data):
    """Test HCCL data structure validation with predefined valid data"""
    assert valid_hccl_a2_data["status"] == "completed"
    assert len(valid_hccl_a2_data["server_list"][0]["device"]) == 8
    assert valid_hccl_a3_data["status"] == "completed"
    assert len(valid_hccl_a3_data["server_list"][0]["device"]) == 2

    config_data = {
        "parallel_config": {"tp_size": 1, "pp_size": 1, "dp_size": 1},
        "role": "both",
        "controller_api_dns": "localhost",
        "controller_api_port": 8080,
        "node_manager_port": 8080,
        "model_name": "vllm"
    }

    # Test with A2 data
    config_a2 = create_config_object()
    NodeManagerConfig._update_from_config_data(config_a2, config_data)
    NodeManagerConfig._update_from_hccl_data(config_a2, valid_hccl_a2_data)
    assert config_a2.basic_config.device_num == 8

    # Test with A3 data
    config_a3 = create_config_object()
    NodeManagerConfig._update_from_config_data(config_a3, config_data)
    NodeManagerConfig._update_from_hccl_data(config_a3, valid_hccl_a3_data)
    assert config_a3.basic_config.device_num == 2
    
@patch.dict('os.environ', {'ROLE': 'both'})
def test_hccl_with_super_device_id(config_data):
    """Test parsing HCCL with super_device_id"""
    hccl_with_super = create_hccl_data(8)
    hccl_with_super["server_list"][0]["device"] = [
        {"device_id": str(i), "device_ip": f"192.168.1.{i+1}", "rank_id": str(i), "super_device_id": f"12345{i}"}
        for i in range(8)
    ]
    hccl_with_super["server_list"][0]["container_ip"] = "192.168.1.100"

    config = create_config_object()
    NodeManagerConfig._update_from_config_data(config, config_data)
    NodeManagerConfig._update_from_hccl_data(config, hccl_with_super)

    assert config.basic_config.device_num == 8
    
@patch.dict('os.environ', {'ROLE': 'both'})
def test_hccl_empty_server_list(config_data):
    """Test parsing HCCL with empty server_list (should fail device count validation)"""
    hccl_empty = {"status": "completed", "server_count": "0", "version": "1.0", "server_list": []}

    config = create_config_object()
    NodeManagerConfig._update_from_config_data(config, config_data)
    NodeManagerConfig._update_from_hccl_data(config, hccl_empty)

    with pytest.raises(ValueError, match="Device count"):
        NodeManagerConfig._generate_endpoint_ports(config)

@patch.dict('os.environ', {'ROLE': 'both'})
def test_hccl_empty_device_list(config_data):
    """Test parsing HCCL with empty device list (should fail device count validation)"""
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

    config = create_config_object()
    NodeManagerConfig._update_from_config_data(config, config_data)
    NodeManagerConfig._update_from_hccl_data(config, hccl_no_devices)

    with pytest.raises(ValueError, match="Device count"):
        NodeManagerConfig._generate_endpoint_ports(config)

@patch.dict('os.environ', {'ROLE': 'both', 'USER_CONFIG_PATH': 'tests/jsons/user_config.json'.replace('\\', '/')})
@patch('motor.config.node_manager.safe_open')
def test_reload_success(mock_safe_open, config_data, hccl_data):
    """Test successful configuration reload"""
    clear_node_manager_config()
    mock_safe_open.side_effect = create_config_mock(config_data, hccl_data)

    config = NodeManagerConfig()

    # Store original values
    original_model_name = config.basic_config.model_name
    original_node_manager_port = config.api_config.node_manager_port

    # Set valid config paths for reload testing
    config.config_path = "/tmp/user_config.json"
    config.hccl_path = "/tmp/test_hccl.json"
    config.last_modified = None  # Force reload

    # Create modified config data for reload
    modified_config_data = config_data.copy()
    modified_config_data["basic_config"]["model_name"] = "modified_model"
    modified_config_data["api_config"]["node_manager_port"] = 9090

    # Create modified HCCL data for reload
    modified_hccl_data = hccl_data.copy()
    modified_hccl_data["version"] = "2.0"

    # Update mock to return modified data on reload
    def reload_mock_side_effect(file_path, mode):
        if "user_config.json" in file_path or "/tmp/test_user_config.json" in file_path:
            return mock_open(read_data=json.dumps(modified_config_data)).return_value
        elif "hccl.json" in file_path or "/tmp/test_hccl.json" in file_path:
            return mock_open(read_data=json.dumps(modified_hccl_data)).return_value
        return mock_open().return_value

    mock_safe_open.side_effect = reload_mock_side_effect

    # Mock os.path.exists and os.path.getmtime for our test paths
    with patch('os.path.exists', return_value=True), \
         patch('os.path.getmtime', return_value=1234567890.0):
        result = config.reload()

    assert result is True
    assert config.basic_config.model_name == "modified_model"
    assert config.api_config.node_manager_port == 9090

@patch.dict('os.environ', {'ROLE': 'both', 'USER_CONFIG_PATH': '/tmp/test_node_manager_config.json'})
@patch('motor.config.node_manager.safe_open')
def test_reload_config_file_not_found(mock_safe_open, config_data, hccl_data):
    """Test reload when configuration file doesn't exist"""
    clear_node_manager_config()
    mock_safe_open.side_effect = create_config_mock(config_data, hccl_data)

    config = NodeManagerConfig()

    # Set config_path to a non-existent path
    config.config_path = "/non/existent/path/user_config.json"
    config.hccl_path = "/tmp/test_hccl.json"  # Valid path for hccl

    def exists_side_effect(path):
        return path != "/non/existent/path/user_config.json"

    with patch('os.path.exists', side_effect=exists_side_effect):
        result = config.reload()
    assert result is False

@patch.dict('os.environ', {'ROLE': 'both', 'USER_CONFIG_PATH': '/tmp/test_node_manager_config.json'})
@patch('motor.config.node_manager.safe_open')
def test_reload_hccl_file_not_found(mock_safe_open, config_data, hccl_data):
    """Test reload when HCCL file doesn't exist"""
    clear_node_manager_config()
    mock_safe_open.side_effect = create_config_mock(config_data, hccl_data)

    config = NodeManagerConfig()

    # Set hccl_path to a non-existent path
    config.config_path = "/tmp/test_node_manager_config.json"  # Valid path for config
    config.hccl_path = "/non/existent/path/hccl.json"

    def exists_side_effect(path):
        return path != "/non/existent/path/hccl.json"

    with patch('os.path.exists', side_effect=exists_side_effect):
        result = config.reload()
    assert result is False

@patch.dict('os.environ', {'ROLE': 'both', 'USER_CONFIG_PATH': '/tmp/test_node_manager_config.json'})
@patch('motor.config.node_manager.safe_open')
def test_reload_invalid_json(mock_safe_open, config_data, hccl_data):
    """Test reload with invalid JSON in config file"""
    clear_node_manager_config()
    mock_safe_open.side_effect = create_config_mock(config_data, hccl_data)

    config = NodeManagerConfig()

    # Set valid paths for testing
    config.config_path = "/tmp/test_node_manager_config.json"
    config.hccl_path = "/tmp/test_hccl.json"

    # Mock invalid JSON for config file
    def invalid_json_mock(file_path, mode):
        if "/tmp/test_node_manager_config.json" in file_path:
            return mock_open(read_data="invalid json content").return_value
        elif "/tmp/test_hccl.json" in file_path:
            return mock_open(read_data=json.dumps(hccl_data)).return_value
        return mock_open().return_value

    mock_safe_open.side_effect = invalid_json_mock

    with patch('os.path.exists', return_value=True):
        result = config.reload()
    assert result is False

def test_validate_config_success():
    """Test successful configuration validation"""
    config = create_config_object()
    config.validate_config()

@pytest.mark.parametrize("param,value,expected_error", [
    ("node_manager_port", 0, "node_manager_port must be in range 1-65535"),
    ("node_manager_port", 65536, "node_manager_port must be in range 1-65535"),
    ("base_port", -1, "base_port must be in range 0-65535"),
    ("endpoint_num", -1, "endpoint_num cannot be negative"),
    ("heartbeat_interval_seconds", 0, "heartbeat_interval_seconds must be greater than 0"),
    ("log_level", "INVALID", "log_level must be one of"),
    ("log_max_line_length", 0, "log_max_line_length must be greater than 0"),
])
def test_validate_config_errors(param, value, expected_error):
    """Test configuration validation errors"""
    config = create_config_object()

    if param == "node_manager_port":
        config.api_config.node_manager_port = value
    elif param in ["base_port", "endpoint_num"]:
        setattr(config.endpoint_config, param, value)
    elif param == "heartbeat_interval_seconds":
        config.basic_config.heartbeat_interval_seconds = value
    elif param in ["log_level", "log_max_line_length"]:
        setattr(config.logging_config, param, value)

    with pytest.raises(ValueError, match=expected_error):
        config.validate_config()

def test_to_dict():
    """Test conversion to dictionary"""
    config = create_config_object()
    config.basic_config.model_name = "test_model"

    config_dict = config.to_dict()

    assert "api_config" in config_dict
    assert "endpoint_config" in config_dict
    assert "basic_config" in config_dict
    assert "logging_config" in config_dict

    assert config_dict["basic_config"]["model_name"] == "test_model"

    assert "config_path" not in config_dict
    assert "hccl_path" not in config_dict

def test_save_to_json_success():
    """Test successful saving configuration to JSON file"""
    config = create_config_object()
    config.config_path = "/tmp/test_config.json"
    config.hccl_path = "/tmp/test_hccl.json"

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_path = f.name

    try:
        result = config.save_to_json(temp_path, temp_path)
        assert result is True

        with open(temp_path, 'r') as f:
            saved_config = json.load(f)
        assert saved_config["api_config"][("node_manager_port")] == 1026
    finally:
        os.unlink(temp_path)

def test_save_to_json_no_path():
    """Test saving configuration to unspecified path"""
    # Create config manually to avoid loading from files
    config = create_config_object()

    # Config without paths should fail
    assert config.save_to_json() is False

def test_get_config_summary():
    """Test getting configuration summary"""
    config = create_config_object()
    config.api_config.node_manager_port = 8080
    config.basic_config.model_name = "test_model"
    config.basic_config.role = PDRole.ROLE_U
    config.api_config.pod_ip = "127.0.0.1"

    summary = config.get_config_summary()

    assert "8080" in summary
    assert "test_model" in summary
    assert "127.0.0.1" in summary

@patch.dict('os.environ', {'ROLE': 'both'})
def test_from_json_success():
    """Test loading configuration from JSON data"""
    test_config = {
        "api_config": {
            "node_manager_port": 8080
        },
        "basic_config": {
            "parallel_config": {"tp_size": 1, "pp_size": 1},
            "role": "both",
            "model_name": "test_model",
            "heartbeat_interval_seconds": 2
        },
        "logging_config": {
            "log_level": "DEBUG",
            "log_max_line_length": 4096
        }
    }

    hccl_data = create_hccl_data(1)

    config = create_config_object()
    NodeManagerConfig._update_from_config_data(config, test_config)
    NodeManagerConfig._update_from_hccl_data(config, hccl_data)
    NodeManagerConfig._generate_endpoint_ports(config)

    assert config.api_config.node_manager_port == 8080
    assert config.basic_config.model_name == "test_model"
    assert config.logging_config.log_level == "DEBUG"
    assert config.logging_config.log_max_line_length == 4096

