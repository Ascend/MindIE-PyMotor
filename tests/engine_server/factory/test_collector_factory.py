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
from unittest.mock import Mock, patch, MagicMock
import sys


@pytest.fixture(autouse=True)
def mock_logger_module():
    module_name = 'motor.common.utils.logger'
    original_logger = sys.modules.get(module_name)

    mock_logger = MagicMock()
    mock_logger_module = MagicMock()
    mock_logger_module.get_logger = MagicMock(return_value=mock_logger)
    sys.modules[module_name] = mock_logger_module

    try:
        yield
    finally:
        if original_logger is not None:
            sys.modules[module_name] = original_logger
        else:
            if module_name in sys.modules:
                del sys.modules[module_name]


from motor.engine_server.factory.collector_factory import CollectorFactory
from motor.engine_server.config.base import IConfig


@pytest.fixture(scope="function")
def collector_factory():
    with patch('motor.engine_server.factory.collector_factory.VLLMCollector') as mock_vllm_collector:
        factory = CollectorFactory()
        factory._mock_vllm_collector = mock_vllm_collector
        yield factory


@pytest.fixture(scope="function")
def mock_config():
    mock_cfg = Mock(spec=IConfig)
    mock_server_cfg = Mock()
    mock_cfg.get_server_config.return_value = mock_server_cfg
    mock_cfg._mock_server_config = mock_server_cfg
    return mock_cfg


def test_create_collector_success(collector_factory, mock_config):
    """test CollectorFactory should create and return VLLMCollector instance when engine_type is vllm"""
    mock_config._mock_server_config.engine_type = "vllm"
    mock_vllm_instance = Mock()
    collector_factory._mock_vllm_collector.return_value = mock_vllm_instance
    result = collector_factory.create_collector(mock_config)
    mock_config.get_server_config.assert_called_once()
    collector_factory._mock_vllm_collector.assert_called_once_with(mock_config)
    assert result == mock_vllm_instance


def test_create_collector_unknown_type(collector_factory, mock_config):
    """test CollectorFactory should raise ValueError with correct message when engine_type is unknown"""
    mock_config._mock_server_config.engine_type = "unknown_engine"
    with pytest.raises(ValueError) as exc_info:
        collector_factory.create_collector(mock_config)
    expected_msg = "No collector found for config type unknown_engine"
    assert str(exc_info.value) == expected_msg
    mock_config.get_server_config.assert_called_once()


if __name__ == "__main__":
    pytest.main(["-v", __file__])
