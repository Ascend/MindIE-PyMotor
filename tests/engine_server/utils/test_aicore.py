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

import os
import subprocess
from unittest import mock
import pytest
from motor.engine_server.utils.aicore import get_aicore_usage, get_device_info_from_rank_table


def test_get_device_info_from_rank_table_success(monkeypatch):
    mock_rank_table_path = "test_rank_table.json"
    monkeypatch.setenv("RANKTABLE_PATH", mock_rank_table_path)
    mock_rank_table = {
        "server_list": [
            {
                "device": [
                    {
                        "device_id": "3",
                        "rank_id": "0"
                    }
                ]
            }
        ]
    }
    with mock.patch('builtins.open', mock.mock_open(read_data='{"server_list":[{"device":[{"device_id":"3","rank_id":"0"}]}]}')):
        with mock.patch('json.load', return_value=mock_rank_table):
            device_id, chip_id = get_device_info_from_rank_table()
            assert device_id == 1  # 3 // 2 = 1
            assert chip_id == 1     # 3 % 2 = 1


def test_get_device_info_from_rank_table_no_env(monkeypatch):
    if "RANKTABLE_PATH" in os.environ:
        monkeypatch.delenv("RANKTABLE_PATH")
    with pytest.raises(ValueError) as cm:
        get_device_info_from_rank_table()
    assert "Environment variable RANKTABLE_PATH is not set" in str(cm.value)


def test_get_device_info_from_rank_table_file_error(monkeypatch):
    mock_rank_table_path = "test_rank_table.json"
    monkeypatch.setenv("RANKTABLE_PATH", mock_rank_table_path)
    with mock.patch('builtins.open', side_effect=IOError("File open failed")):
        with pytest.raises(RuntimeError) as cm:
            get_device_info_from_rank_table()
        assert "Error reading RANK_TABLE_PATH file" in str(cm.value)


def test_get_device_info_from_rank_table_no_device_id(monkeypatch):
    mock_rank_table_path = "test_rank_table.json"
    monkeypatch.setenv("RANKTABLE_PATH", mock_rank_table_path)
    mock_rank_table = {
        "server_list": [
            {
                "device": [
                    {
                        "rank_id": "0"
                    }
                ]
            }
        ]
    }
    with mock.patch('builtins.open', mock.mock_open(read_data='{"server_list":[{"device":[{"rank_id":"0"}]}]}')):
        with mock.patch('json.load', return_value=mock_rank_table):
            with pytest.raises(ValueError) as cm:
                get_device_info_from_rank_table()
            assert "device_id field not found in RANK_TABLE_PATH file" in str(cm.value)


def test_get_device_info_from_rank_table_invalid_device_id(monkeypatch):
    mock_rank_table_path = "test_rank_table.json"
    monkeypatch.setenv("RANKTABLE_PATH", mock_rank_table_path)
    mock_rank_table = {
        "server_list": [
            {
                "device": [
                    {
                        "device_id": "invalid",
                        "rank_id": "0"
                    }
                ]
            }
        ]
    }
    with mock.patch('builtins.open', mock.mock_open(read_data='{"server_list":[{"device":[{"device_id":"invalid","rank_id":"0"}]}]}')):
        with mock.patch('json.load', return_value=mock_rank_table):
            with pytest.raises(ValueError) as cm:
                get_device_info_from_rank_table()
            assert f"device_id field value is not a valid integer" in str(cm.value)


def test_get_aicore_usage_success():
    with mock.patch('motor.engine_server.utils.aicore.get_device_info_from_rank_table', return_value=(0, 0)):
        mock_result = mock.MagicMock()
        mock_result.stdout = """+-------------------+-----------------+\n| Device ID         | 0               |\n+===================+=================+\n| Chip ID           | 0               |\n+-------------------+-----------------+\n| Aicore Usage Rate(%)           : 50\n| Memory Usage Rate(%)           : 30\n+-------------------+-----------------+\n"""
        mock_result.stderr = ""

        with mock.patch('subprocess.run', return_value=mock_result):
            usage = get_aicore_usage()
            assert usage == 50


def test_get_aicore_usage_npu_smi_failure():
    with mock.patch('motor.engine_server.utils.aicore.get_device_info_from_rank_table', return_value=(0, 0)):
        with mock.patch('subprocess.run', side_effect=subprocess.CalledProcessError(
            returncode=1, cmd=["npu-smi", "info"], stderr="Command execution failed"
        )):
            with pytest.raises(RuntimeError) as cm:
                get_aicore_usage()
            assert "npu-smi execution failed from subprocess" in str(cm.value)


def test_get_aicore_usage_no_match():
    with mock.patch('motor.engine_server.utils.aicore.get_device_info_from_rank_table', return_value=(0, 0)):
        mock_result = mock.MagicMock()
        mock_result.stdout = """+-------------------+-----------------+\n| Device ID         | 0               |\n+===================+=================+\n| Chip ID           | 0               |\n+-------------------+-----------------+\n| Memory Usage Rate(%)           : 30\n+-------------------+-----------------+\n"""
        mock_result.stderr = ""

        with mock.patch('subprocess.run', return_value=mock_result):
            with pytest.raises(ValueError) as cm:
                get_aicore_usage()
            assert "Aicore Usage Rate not found" in str(cm.value)