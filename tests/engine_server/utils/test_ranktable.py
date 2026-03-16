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
import pytest
from unittest import mock

from motor.engine_server.utils import ranktable


class TestRankTable:

    def test_get_data_parallel_address_success(self, monkeypatch, tmpdir):
        # Create a temporary rank table file
        rank_table_content = {
            "server_list": [
                {
                    "container_ip": "192.168.1.100",
                    "device": [
                        {"rank_id": "0"},
                        {"rank_id": "1"}
                    ]
                },
                {
                    "container_ip": "192.168.1.101",
                    "device": [
                        {"rank_id": "2"},
                        {"rank_id": "3"}
                    ]
                }
            ]
        }

        # Create temp file
        rank_table_file = tmpdir.join("rank_table.json")
        with open(rank_table_file, "w", encoding="utf-8") as f:
            json.dump(rank_table_content, f)

        # Mock environment variable
        monkeypatch.setenv("RANKTABLE_PATH", str(rank_table_file))

        # Mock FileValidator
        mock_validator = mock.Mock()
        mock_validator.check_not_soft_link.return_value = mock_validator
        mock_validator.check_file_size.return_value = mock_validator
        mock_validator.check.return_value = mock_validator
        mock_validator.is_valid.return_value = True

        with mock.patch('motor.engine_server.utils.ranktable.FileValidator', return_value=mock_validator):
            result = ranktable.get_data_parallel_address()

            # Verify result
            assert result == "192.168.1.100"

    def test_get_data_parallel_address_env_not_set(self, monkeypatch):
        # Remove environment variable if exists
        if "RANKTABLE_PATH" in os.environ:
            monkeypatch.delenv("RANKTABLE_PATH")

        # Test missing environment variable
        with pytest.raises(ValueError, match="Environment variable RANKTABLE_PATH is not set"):
            ranktable.get_data_parallel_address()

    def test_get_data_parallel_address_invalid_file(self, monkeypatch, tmpdir):
        # Mock environment variable
        rank_table_file = tmpdir.join("rank_table.json")
        monkeypatch.setenv("RANKTABLE_PATH", str(rank_table_file))

        # Mock FileValidator to return invalid
        mock_validator = mock.Mock()
        mock_validator.check_not_soft_link.return_value = mock_validator
        mock_validator.check_file_size.return_value = mock_validator
        mock_validator.check.return_value = mock_validator
        mock_validator.is_valid.return_value = False

        with mock.patch('motor.engine_server.utils.ranktable.FileValidator', return_value=mock_validator):
            with pytest.raises(ValueError) as exc_info:
                ranktable.get_data_parallel_address()
            # Check if the error message contains the file path
            assert str(rank_table_file) in str(exc_info.value)
            assert "is not a valid file path" in str(exc_info.value)

    def test_get_data_parallel_address_file_not_found(self, monkeypatch):
        # Mock non-existent file
        non_existent_file = "non_existent_rank_table.json"
        monkeypatch.setenv("RANKTABLE_PATH", non_existent_file)

        # Mock FileValidator
        mock_validator = mock.Mock()
        mock_validator.check_not_soft_link.return_value = mock_validator
        mock_validator.check_file_size.return_value = mock_validator
        mock_validator.check.return_value = mock_validator
        mock_validator.is_valid.return_value = True

        with mock.patch('motor.engine_server.utils.ranktable.FileValidator', return_value=mock_validator):
            with pytest.raises(FileNotFoundError, match=f"File {non_existent_file} not found"):
                ranktable.get_data_parallel_address()

    def test_get_data_parallel_address_invalid_json(self, monkeypatch, tmpdir):
        # Create a temporary file with invalid JSON
        rank_table_file = tmpdir.join("invalid_rank_table.json")
        with open(rank_table_file, "w", encoding="utf-8") as f:
            f.write("{invalid json}")

        # Mock environment variable
        monkeypatch.setenv("RANKTABLE_PATH", str(rank_table_file))

        # Mock FileValidator
        mock_validator = mock.Mock()
        mock_validator.check_not_soft_link.return_value = mock_validator
        mock_validator.check_file_size.return_value = mock_validator
        mock_validator.check.return_value = mock_validator
        mock_validator.is_valid.return_value = True

        with mock.patch('motor.engine_server.utils.ranktable.FileValidator', return_value=mock_validator):
            with pytest.raises(json.JSONDecodeError):
                ranktable.get_data_parallel_address()

    def test_get_data_parallel_address_no_rank_zero(self, monkeypatch, tmpdir):
        # Create a temporary rank table file without rank_id 0
        rank_table_content = {
            "server_list": [
                {
                    "container_ip": "192.168.1.100",
                    "device": [
                        {"rank_id": "1"},
                        {"rank_id": "2"}
                    ]
                },
                {
                    "container_ip": "192.168.1.101",
                    "device": [
                        {"rank_id": "3"},
                        {"rank_id": "4"}
                    ]
                }
            ]
        }

        # Create temp file
        rank_table_file = tmpdir.join("rank_table_no_zero.json")
        with open(rank_table_file, "w", encoding="utf-8") as f:
            json.dump(rank_table_content, f)

        # Mock environment variable
        monkeypatch.setenv("RANKTABLE_PATH", str(rank_table_file))

        # Mock FileValidator
        mock_validator = mock.Mock()
        mock_validator.check_not_soft_link.return_value = mock_validator
        mock_validator.check_file_size.return_value = mock_validator
        mock_validator.check.return_value = mock_validator
        mock_validator.is_valid.return_value = True

        with mock.patch('motor.engine_server.utils.ranktable.FileValidator', return_value=mock_validator):
            with pytest.raises(ValueError, match="No device with rank_id=0 found"):
                ranktable.get_data_parallel_address()

    def test_get_data_parallel_address_unexpected_error(self, monkeypatch):
        # Mock environment variable
        monkeypatch.setenv("RANKTABLE_PATH", "test_rank_table.json")

        # Mock FileValidator
        mock_validator = mock.Mock()
        mock_validator.check_not_soft_link.return_value = mock_validator
        mock_validator.check_file_size.return_value = mock_validator
        mock_validator.check.return_value = mock_validator
        mock_validator.is_valid.return_value = True

        # Mock open to raise an unexpected exception
        with mock.patch('motor.engine_server.utils.ranktable.FileValidator', return_value=mock_validator):
            with mock.patch('builtins.open', side_effect=PermissionError("Permission denied")):
                with pytest.raises(RuntimeError, match="Error reading file: Permission denied"):
                    ranktable.get_data_parallel_address()

    def test_get_data_parallel_address_rank_zero_in_second_server(self, monkeypatch, tmpdir):
        # Create a temporary rank table file with rank_id 0 in the second server
        rank_table_content = {
            "server_list": [
                {
                    "container_ip": "192.168.1.100",
                    "device": [
                        {"rank_id": "2"},
                        {"rank_id": "3"}
                    ]
                },
                {
                    "container_ip": "192.168.1.101",
                    "device": [
                        {"rank_id": "0"},
                        {"rank_id": "1"}
                    ]
                }
            ]
        }

        # Create temp file
        rank_table_file = tmpdir.join("rank_table_second_server.json")
        with open(rank_table_file, "w", encoding="utf-8") as f:
            json.dump(rank_table_content, f)

        # Mock environment variable
        monkeypatch.setenv("RANKTABLE_PATH", str(rank_table_file))

        # Mock FileValidator
        mock_validator = mock.Mock()
        mock_validator.check_not_soft_link.return_value = mock_validator
        mock_validator.check_file_size.return_value = mock_validator
        mock_validator.check.return_value = mock_validator
        mock_validator.is_valid.return_value = True

        with mock.patch('motor.engine_server.utils.ranktable.FileValidator', return_value=mock_validator):
            result = ranktable.get_data_parallel_address()

            # Verify result
            assert result == "192.168.1.101"

