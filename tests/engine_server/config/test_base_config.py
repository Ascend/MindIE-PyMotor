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

from unittest.mock import patch, MagicMock
import pytest

from motor.engine_server.config.base import ServerConfig, BaseConfig


class TestServerConfig:

    def test_default_values(self):
        config = ServerConfig()
        assert config.engine_type == "vllm"
        assert config.server_host == "127.0.0.1"
        assert config.role == "union"
        assert config.server_port == 9001
        assert config.engine_port == 8000
        assert config.instance_id == 0
        assert config.dp_rank == 0
        assert config.config_path is None
        assert config.deploy_config is None

    def test_custom_values(self):
        config = ServerConfig(
            server_host="192.168.1.1",
            role="prefill",
            server_port=8080,
            engine_port=9000,
            instance_id=1,
            config_path="/path/to/config.json",
            dp_rank=2
        )
        assert config.server_host == "192.168.1.1"
        assert config.role == "prefill"
        assert config.server_port == 8080
        assert config.engine_port == 9000
        assert config.instance_id == 1
        assert config.config_path == "/path/to/config.json"
        assert config.dp_rank == 2

    @patch('os.path.exists', return_value=True)
    @patch('motor.engine_server.config.base.FileValidator')
    @patch('motor.engine_server.config.base.ip_valid_check')
    @patch('motor.engine_server.config.base.port_valid_check')
    def test_validate_success(self, mock_port_valid, mock_ip_valid, mock_file_validator, mock_exists):
        # Setup mocks
        mock_validator = MagicMock()
        mock_validator.check_not_soft_link.return_value = mock_validator
        mock_validator.check_file_size.return_value = mock_validator
        mock_validator.check.return_value = mock_validator
        mock_validator.is_valid.return_value = True
        mock_file_validator.return_value = mock_validator
        # Execute test
        config = ServerConfig(
            server_host="127.0.0.1",
            role="union",
            server_port=9001,
            engine_port=8000,
            instance_id=0,
            config_path="/valid/path.json",
            dp_rank=0
        )
        # Should not raise exception
        config.validate()

        # Verify calls
        mock_ip_valid.assert_called_once_with("127.0.0.1")
        mock_port_valid.assert_any_call(9001)
        mock_port_valid.assert_any_call(8000)

    def test_validate_invalid_role(self):
        config = ServerConfig(role="invalid_role")
        with pytest.raises(ValueError, match="role invalid_role is not supported"):
            config.validate()

    def test_validate_negative_instance_id(self):
        config = ServerConfig(instance_id=-1)
        with pytest.raises(ValueError, match="instance_id -1 illegal"):
            config.validate()

    @patch('os.path.exists')
    def test_validate_config_path_not_exist(self, mock_exists):
        mock_exists.return_value = False
        config = ServerConfig(config_path="/non/existent/path.json")
        with pytest.raises(ValueError, match="config file /non/existent/path.json does not exist"):
            config.validate()

    @patch('motor.engine_server.config.config_loader.DeployConfig.load')
    @patch('os.path.exists', return_value=True)
    @patch('motor.engine_server.config.base.FileValidator')
    @patch('motor.engine_server.config.base.ip_valid_check')
    @patch('motor.engine_server.config.base.port_valid_check')
    def test_load_deploy_config(self, mock_port_valid, mock_ip_valid, mock_file_validator, mock_exists, mock_load):
        # Setup mocks
        mock_validator = MagicMock()
        mock_validator.check_not_soft_link.return_value = mock_validator
        mock_validator.check_file_size.return_value = mock_validator
        mock_validator.check.return_value = mock_validator
        mock_validator.is_valid.return_value = True
        mock_file_validator.return_value = mock_validator
        # Setup mock deploy config
        mock_deploy_config = MagicMock()
        mock_deploy_config.engine_type = "vllm"
        mock_load.return_value = mock_deploy_config

        # Execute test
        config = ServerConfig(config_path="/valid/path.json")
        config.validate()
        config.load_deploy_config()

        # Verify results
        assert config.deploy_config == mock_deploy_config
        assert config.engine_type == "vllm"
        mock_load.assert_called_once_with("/valid/path.json", role="union")

    @patch('argparse.ArgumentParser.parse_args')
    def test_parse_cli_args(self, mock_parse_args):
        # Setup mocks
        mock_args = MagicMock()
        mock_args.host = "test-host"
        mock_args.role = "test-role"
        mock_args.port = 1234
        mock_args.server_port = 5678
        mock_args.instance_id = 9
        mock_args.dp_rank = 10
        mock_args.config_path = "/test/path.json"
        mock_parse_args.return_value = mock_args

        # Execute test
        args = ServerConfig.parse_cli_args()

        # Verify results
        assert args == mock_args
        mock_parse_args.assert_called_once()

    @patch('motor.engine_server.config.base.ServerConfig.parse_cli_args')
    @patch('motor.engine_server.config.base.ServerConfig.validate')
    @patch('motor.engine_server.config.base.ServerConfig.load_deploy_config')
    def test_init_engine_server_config(self, mock_load_deploy, mock_validate, mock_parse_cli):
        # Setup mocks
        mock_args = MagicMock()
        mock_args.host = "cli-host"
        mock_args.role = "cli-role"
        mock_args.port = 8888
        mock_args.server_port = 9999
        mock_args.instance_id = 1
        mock_args.dp_rank = 2
        mock_args.config_path = "/cli/config.json"
        mock_parse_cli.return_value = mock_args

        # Execute test
        config = ServerConfig.init_engine_server_config()

        # Verify results
        assert config.server_host == "cli-host"
        assert config.role == "cli-role"
        assert config.engine_port == 8888
        assert config.server_port == 9999
        assert config.instance_id == 1
        assert config.dp_rank == 2
        assert config.config_path == "/cli/config.json"
        mock_validate.assert_called_once()
        mock_load_deploy.assert_called_once()


class TestBaseConfig:

    def setup_method(self):
        self.server_config = ServerConfig(
            server_host="test-host",
            server_port=1234,
            engine_type="vllm"
        )
        self.base_config = BaseConfig(server_config=self.server_config)

    def test_initialization(self):
        assert self.base_config.server_config == self.server_config

    def test_initialize(self):
        # This method is empty in base class, just confirm it can be called without exception
        self.base_config.initialize()

    def test_validate(self):
        # This method is empty in base class, just confirm it can be called without exception
        self.base_config.validate()

    def test_convert(self):
        # This method is empty in base class, just confirm it can be called without exception
        self.base_config.convert()

    def test_get_args(self):
        assert self.base_config.get_args() is None

    def test_get_server_config(self):
        assert self.base_config.get_server_config() == self.server_config


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
