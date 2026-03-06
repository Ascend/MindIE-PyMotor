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
from motor.engine_server.core.service import Service, BaseService, MetricsService, HealthService


@pytest.fixture
def mock_data_controller():
    """Create mocked DataController with get_metrics_data and get_health_data methods"""
    mock_dc = Mock()
    # Mock return values for data methods
    mock_dc.get_metrics_data.return_value = {
        "latest_metrics": {"cpu_usage": 0.3},
        "collector_name": "test_collector"
    }
    mock_dc.get_health_data.return_value = {
        "latest_health": {"status": "healthy"},
        "collector_name": "test_collector"
    }
    return mock_dc


def test_service_is_abstract():
    """test Service should be an abstract class that cannot be instantiated directly"""
    # Verify Service inherits from ABC
    assert issubclass(Service, ABC)

    # Verify instantiation raises TypeError (missing abstract method implementation)
    with pytest.raises(TypeError, match=r"Can't instantiate abstract class Service.*abstract method.*get_data"):
        Service()


def test_base_service_initialization():
    """test BaseService should initialize with correct name when created"""
    test_name = "test_service"
    base_service = BaseService(name=test_name)

    assert base_service.name == test_name


def test_base_service_get_data_returns_none():
    """test BaseService.get_data() should return None (no-op implementation)"""
    base_service = BaseService(name="test_service")
    result = base_service.get_data()

    assert result is None


def test_metrics_service_initialization(mock_data_controller):
    """test MetricsService should initialize with correct name and data_controller when created"""
    metrics_service = MetricsService(data_controller=mock_data_controller)

    # Verify name is set correctly
    assert metrics_service.name == "metrics_service"
    # Verify data_controller is assigned
    assert metrics_service.data_controller == mock_data_controller


def test_metrics_service_get_data_returns_metrics(mock_data_controller):
    """test MetricsService.get_data() should return data from data_controller.get_metrics_data() when called"""
    metrics_service = MetricsService(data_controller=mock_data_controller)
    result = metrics_service.get_data()

    # Verify data_controller method is called
    mock_data_controller.get_metrics_data.assert_called_once()
    # Verify returned data matches mock's return value
    assert result == mock_data_controller.get_metrics_data.return_value


def test_metrics_service_get_data_propagates_exception(mock_data_controller):
    """test MetricsService.get_data() should propagate exceptions from data_controller.get_metrics_data() when they occur"""
    # Mock exception in get_metrics_data
    test_exception = Exception("Metrics collection failed")
    mock_data_controller.get_metrics_data.side_effect = test_exception

    metrics_service = MetricsService(data_controller=mock_data_controller)

    # Verify exception is raised
    with pytest.raises(Exception) as excinfo:
        metrics_service.get_data()

    assert str(excinfo.value) == str(test_exception)
    mock_data_controller.get_metrics_data.assert_called_once()


def test_health_service_initialization(mock_data_controller):
    """test HealthService should initialize with correct name and data_controller when created"""
    health_service = HealthService(data_controller=mock_data_controller)

    # Verify name is set correctly
    assert health_service.name == "health_service"
    # Verify data_controller is assigned
    assert health_service.data_controller == mock_data_controller


def test_health_service_get_data_returns_health(mock_data_controller):
    """test HealthService.get_data() should return data from data_controller.get_health_data() when called"""
    health_service = HealthService(data_controller=mock_data_controller)
    result = health_service.get_data()

    # Verify data_controller method is called
    mock_data_controller.get_health_data.assert_called_once()
    # Verify returned data matches mock's return value
    assert result == mock_data_controller.get_health_data.return_value


def test_health_service_get_data_propagates_exception(mock_data_controller):
    """test HealthService.get_data() should propagate exceptions from data_controller.get_health_data() when they occur"""
    # Mock exception in get_health_data
    test_exception = Exception("Health check failed")
    mock_data_controller.get_health_data.side_effect = test_exception

    health_service = HealthService(data_controller=mock_data_controller)

    # Verify exception is raised
    with pytest.raises(Exception) as excinfo:
        health_service.get_data()

    assert str(excinfo.value) == str(test_exception)
    mock_data_controller.get_health_data.assert_called_once()


def test_metrics_service_with_empty_data_controller():
    """test MetricsService should handle empty data_controller responses when get_data() is called"""
    # Mock data_controller with empty return value
    empty_mock_dc = Mock()
    empty_mock_dc.get_metrics_data.return_value = {}

    metrics_service = MetricsService(data_controller=empty_mock_dc)
    result = metrics_service.get_data()

    assert result == {}
    empty_mock_dc.get_metrics_data.assert_called_once()


def test_health_service_with_none_data_controller():
    """test HealthService should handle None data_controller responses when get_data() is called"""
    # Mock data_controller with None return value
    none_mock_dc = Mock()
    none_mock_dc.get_health_data.return_value = None

    health_service = HealthService(data_controller=none_mock_dc)
    result = health_service.get_data()

    assert result is None
    none_mock_dc.get_health_data.assert_called_once()
