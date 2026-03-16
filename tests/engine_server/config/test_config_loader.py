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
from pathlib import Path
import json

from motor.engine_server.config.config_loader import (
    ParallelConfig,
    ModelConfig,
    EngineConfig,
    DeployConfig
)


class TestParallelConfig:
    def test_from_dict(self):
        """Test creating ParallelConfig from dictionary"""
        data = {
            "dp_size": 2,
            "tp_size": 4,
            "pp_size": 1,
            "enable_ep": True,
            "dp_rpc_port": 9000
        }
        config = ParallelConfig.from_dict(data)

        assert config.dp_size == 2
        assert config.tp_size == 4
        assert config.pp_size == 1
        assert config.enable_ep is True
        assert config.dp_rpc_port == 9000

    def test_equality(self):
        """Test equality of ParallelConfig instances"""
        config1 = ParallelConfig(2, 4, 1, True, 9000)
        config2 = ParallelConfig(2, 4, 1, True, 9000)
        config3 = ParallelConfig(1, 2, 1, False, 9001)

        assert config1 == config2
        assert config1 != config3


class TestModelConfig:
    def test_from_dict(self):
        """Test creating ModelConfig from dictionary"""
        data = {
            "model_name": "test_model",
            "model_path": "/path/to/model",
            "npu_mem_utils": 0.8,
            "prefill_parallel_config": {
                "dp_size": 2,
                "tp_size": 4,
                "pp_size": 1,
                "enable_ep": True,
                "dp_rpc_port": 9000
            },
            "decode_parallel_config": {
                "dp_size": 1,
                "tp_size": 2,
                "pp_size": 1,
                "enable_ep": False,
                "dp_rpc_port": 9001
            }
        }

        config = ModelConfig.from_dict(data)

        assert config.model_name == "test_model"
        assert config.model_path == "/path/to/model"
        assert config.npu_mem_utils == 0.8

        # Test prefill_parallel_config
        assert config.prefill_parallel_config.dp_size == 2
        assert config.prefill_parallel_config.tp_size == 4
        assert config.prefill_parallel_config.dp_rpc_port == 9000

        # Test decode_parallel_config
        assert config.decode_parallel_config.dp_size == 1
        assert config.decode_parallel_config.tp_size == 2
        assert config.decode_parallel_config.dp_rpc_port == 9001


class TestEngineConfig:
    def test_from_dict(self):
        """Test creating EngineConfig from dictionary"""
        data = {
            "max_batch_size": 128,
            "gpu_memory_utilization": 0.9,
            "tensor_parallel_size": 4
        }

        config = EngineConfig.from_dict(data)

        assert config.configs == data
        assert len(config.configs) == 3

    def test_get_method(self):
        """Test EngineConfig.get method"""
        data = {"key1": "value1", "key2": 2}
        config = EngineConfig(configs=data)

        assert config.get("key1") == "value1"
        assert config.get("key2") == 2
        assert config.get("key3") is None
        assert config.get("key3", "default") == "default"

    def test_set_method(self):
        """Test EngineConfig.set method"""
        config = EngineConfig(configs={})

        config.set("new_key", "new_value")
        assert config.get("new_key") == "new_value"

        config.set("existing_key", 123)
        assert config.get("existing_key") == 123

        # Test updating existing key
        config.set("new_key", "updated_value")
        assert config.get("new_key") == "updated_value"


class TestDeployConfig:
    @pytest.fixture
    def test_config_path(self):
        """Return path to test configuration file"""
        return Path(__file__).parent / "test_deploy_config.json"

    def test_load_from_file(self, test_config_path):
        """Test loading DeployConfig from JSON file"""
        config = DeployConfig.load(test_config_path)

        # Test top-level fields
        assert config.engine_type == "vllm"

        # Test model_config
        assert config.model_config.model_name == "test_model"
        assert config.model_config.model_path == "/path/to/model"
        assert config.model_config.npu_mem_utils == 0.8

        # Test engine_config
        assert config.engine_config.get("max_batch_size") == 128
        assert config.engine_config.get("gpu_memory_utilization") == 0.9
        assert config.engine_config.get("tensor_parallel_size") == 4

    def test_get_parallel_config_union(self, test_config_path):
        """Test get_parallel_config with 'union' role"""
        config = DeployConfig.load(test_config_path)
        parallel_config = config.get_parallel_config(role="union")

        # Should return prefill_parallel_config
        assert parallel_config.dp_size == 2
        assert parallel_config.tp_size == 4
        assert parallel_config.dp_rpc_port == 9000

    def test_get_parallel_config_prefill(self, test_config_path):
        """Test get_parallel_config with 'prefill' role"""
        config = DeployConfig.load(test_config_path)
        parallel_config = config.get_parallel_config(role="prefill")

        # Should return prefill_parallel_config
        assert parallel_config.dp_size == 2
        assert parallel_config.tp_size == 4
        assert parallel_config.dp_rpc_port == 9000

    def test_get_parallel_config_decode(self, test_config_path):
        """Test get_parallel_config with 'decode' role"""
        config = DeployConfig.load(test_config_path)
        parallel_config = config.get_parallel_config(role="decode")

        # Should return decode_parallel_config
        assert parallel_config.dp_size == 1
        assert parallel_config.tp_size == 2
        assert parallel_config.dp_rpc_port == 9001

    def test_get_parallel_config_default(self, test_config_path):
        """Test get_parallel_config with default role"""
        config = DeployConfig.load(test_config_path)
        parallel_config = config.get_parallel_config()

        # Default role should be 'union'
        assert parallel_config.dp_size == 2
        assert parallel_config.tp_size == 4
        assert parallel_config.dp_rpc_port == 9000

    def test_get_parallel_config_invalid_role(self, test_config_path):
        """Test get_parallel_config with invalid role"""
        config = DeployConfig.load(test_config_path)

        with pytest.raises(ValueError) as excinfo:
            config.get_parallel_config(role="invalid_role")

        assert "Unsupported role: invalid_role" in str(excinfo.value)
        assert "Allowed values: 'union', 'prefill', 'decode'" in str(excinfo.value)

    def test_load_nonexistent_file(self):
        """Test loading from nonexistent file raises FileNotFoundError"""
        nonexistent_path = Path(__file__).parent / "nonexistent_config.json"

        with pytest.raises(FileNotFoundError):
            DeployConfig.load(nonexistent_path)

    def test_load_invalid_json(self, tmp_path):
        """Test loading from invalid JSON file raises json.JSONDecodeError"""
        invalid_json_path = tmp_path / "invalid.json"
        invalid_json_path.write_text("{invalid json}", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            DeployConfig.load(invalid_json_path)

    def test_load_missing_required_fields(self, tmp_path):
        """Test loading from JSON file with missing required fields raises KeyError"""
        incomplete_json_path = tmp_path / "incomplete.json"
        incomplete_json_path.write_text('{"engine_type": "vllm"}', encoding="utf-8")

        with pytest.raises(KeyError):
            DeployConfig.load(incomplete_json_path)
