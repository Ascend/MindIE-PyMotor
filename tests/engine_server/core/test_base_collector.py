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
from abc import ABC
from unittest.mock import Mock
from motor.engine_server.core.base_collector import Collector, BaseCollector
from motor.engine_server.config.base import IConfig


@pytest.fixture
def mock_config():
    """Create mocked IConfig with server config and engine_type"""
    mock_cfg = Mock(spec=IConfig)
    # Mock server config with engine_type attribute
    mock_server_config = Mock()
    mock_server_config.engine_type = "vllm"
    mock_cfg.get_server_config.return_value = mock_server_config
    return mock_cfg


@pytest.fixture
def mock_config_with_custom_engine():
    """Create mocked IConfig with custom engine_type for edge case testing"""
    mock_cfg = Mock(spec=IConfig)
    mock_server_config = Mock()
    mock_server_config.engine_type = "tensorrt"
    mock_cfg.get_server_config.return_value = mock_server_config
    return mock_cfg


def test_collector_is_abstract():
    """test Collector should be an abstract class that cannot be instantiated directly"""
    assert issubclass(Collector, ABC)

    mock_cfg = Mock(spec=IConfig)
    with pytest.raises(TypeError, match=r"Can't instantiate abstract class Collector.*abstract method.*collect"):
        Collector(config=mock_cfg)


def test_base_collector_initialization(mock_config):
    """test BaseCollector should initialize with correct name derived from config when created"""
    base_collector = BaseCollector(config=mock_config)

    assert isinstance(base_collector, Collector)
    expected_name = "vllm_metrics_and_health_collector"
    assert base_collector.name == expected_name
    mock_config.get_server_config.assert_called_once()


def test_base_collector_initialization_with_custom_engine(mock_config_with_custom_engine):
    """test BaseCollector should generate correct name for custom engine_type when initialized"""
    base_collector = BaseCollector(config=mock_config_with_custom_engine)

    expected_name = "tensorrt_metrics_and_health_collector"
    assert base_collector.name == expected_name


def test_base_collector_collect_calls_internal_collect(mock_config):
    """test BaseCollector.collect() should call _collect() and return its result when executed"""

    class TestCollector(BaseCollector):
        def _collect(self) -> dict:
            return {"metrics": {}, "health": {}}

    test_collector = TestCollector(config=mock_config)
    result = test_collector.collect()

    assert result == {"metrics": {}, "health": {}}


def test_base_collector_internal_collect_returns_none(mock_config):
    """test BaseCollector._collect() should return None (no-op implementation)"""
    base_collector = BaseCollector(config=mock_config)
    result = base_collector._collect()

    assert result is None


def test_base_collector_with_empty_engine_type():
    """test BaseCollector should handle empty engine_type in config when initialized"""
    # Mock config with empty engine_type
    mock_cfg = Mock(spec=IConfig)
    mock_server_config = Mock()
    mock_server_config.engine_type = ""
    mock_cfg.get_server_config.return_value = mock_server_config

    base_collector = BaseCollector(config=mock_cfg)
    expected_name = "_metrics_and_health_collector"
    assert base_collector.name == expected_name


def test_base_collector_with_special_char_engine_type():
    """test BaseCollector should handle engine_type with special characters when initialized"""
    # Mock config with special characters in engine_type
    mock_cfg = Mock(spec=IConfig)
    mock_server_config = Mock()
    mock_server_config.engine_type = "llm-7b#v2"
    mock_cfg.get_server_config.return_value = mock_server_config

    base_collector = BaseCollector(config=mock_cfg)
    expected_name = "llm-7b#v2_metrics_and_health_collector"
    assert base_collector.name == expected_name


def test_base_collector_config_get_server_config_failure():
    """test BaseCollector should propagate exception when config.get_server_config() fails"""
    mock_cfg = Mock(spec=IConfig)
    mock_cfg.get_server_config.side_effect = Exception("Server config not found")

    with pytest.raises(Exception, match="Server config not found"):
        BaseCollector(config=mock_cfg)
