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
from unittest import mock
import sys


@pytest.fixture(scope="module")
def mock_dependencies():
    """Mock all dependencies needed for testing ServerCoreFactory."""
    # Store original modules to restore later
    original_modules = {}
    modules_to_mock = [
        'motor.engine_server.utils.logger',
        'motor.engine_server.core.data_controller',
        'motor.engine_server.core.endpoint',
        'motor.engine_server.core.service',
        'vllm',
        'motor.engine_server.core.vllm.vllm_engine_proc_mgr',
        'motor.engine_server.core.vllm.vllm_core'
    ]

    # Save original modules if they exist
    for module_name in modules_to_mock:
        if module_name in sys.modules:
            original_modules[module_name] = sys.modules[module_name]

    # Create mock objects
    mock_logger = mock.MagicMock()
    mock_logger.get_logger = mock.MagicMock(return_value=mock.MagicMock())
    mock_data_controller_class = mock.MagicMock()
    mock_endpoint_class = mock.MagicMock()
    mock_metrics_service_class = mock.MagicMock()
    mock_health_service_class = mock.MagicMock()
    mock_vllm = mock.MagicMock()
    mock_proc_manager_class = mock.MagicMock()
    mock_vllm_core_class = mock.MagicMock()

    # Set up the mock module structure
    mock_data_controller_module = mock.MagicMock()
    mock_data_controller_module.DataController = mock_data_controller_class

    mock_endpoint_module = mock.MagicMock()
    mock_endpoint_module.Endpoint = mock_endpoint_class

    mock_service_module = mock.MagicMock()
    mock_service_module.MetricsService = mock_metrics_service_class
    mock_service_module.HealthService = mock_health_service_class

    mock_vllm_engine_proc_mgr_module = mock.MagicMock()
    mock_vllm_engine_proc_mgr_module.ProcManager = mock_proc_manager_class

    mock_vllm_core_module = mock.MagicMock()
    mock_vllm_core_module.VLLMServerCore = mock_vllm_core_class

    # Replace modules in sys.modules
    sys.modules['motor.engine_server.utils.logger'] = mock_logger
    sys.modules['motor.engine_server.core.data_controller'] = mock_data_controller_module
    sys.modules['motor.engine_server.core.endpoint'] = mock_endpoint_module
    sys.modules['motor.engine_server.core.service'] = mock_service_module
    sys.modules['vllm'] = mock_vllm
    sys.modules['motor.engine_server.core.vllm.vllm_engine_proc_mgr'] = mock_vllm_engine_proc_mgr_module
    sys.modules['motor.engine_server.core.vllm.vllm_core'] = mock_vllm_core_module

    # Yield the mock objects for use in tests
    yield {
        'mock_logger': mock_logger,
        'mock_data_controller_class': mock_data_controller_class,
        'mock_endpoint_class': mock_endpoint_class,
        'mock_metrics_service_class': mock_metrics_service_class,
        'mock_health_service_class': mock_health_service_class,
        'mock_vllm': mock_vllm,
        'mock_proc_manager_class': mock_proc_manager_class,
        'mock_vllm_core_class': mock_vllm_core_class
    }

    # Restore original modules
    for module_name in modules_to_mock:
        if module_name in original_modules:
            sys.modules[module_name] = original_modules[module_name]
        elif module_name in sys.modules:
            del sys.modules[module_name]


# Import classes inside a function to be called after mocks are set up
def get_classes():
    """Import the classes needed for testing."""
    from motor.engine_server.factory.core_factory import ServerCoreFactory
    from motor.engine_server.config.base import IConfig, ServerConfig
    from motor.engine_server.core.base_core import IServerCore
    return ServerCoreFactory, IConfig, ServerConfig, IServerCore


class TestServerCoreFactory:
    def setup_method(self, mock_dependencies):
        # Get required classes
        ServerCoreFactory, _, _, _ = get_classes()
        # Create factory instance
        self.factory = ServerCoreFactory()

    def test_initialization(self, mock_dependencies):
        # Verify ENGINE_CORE_MAP is properly defined as class variable
        assert isinstance(self.factory._ENGINE_CORE_MAP, dict)
        assert "vllm" in self.factory._ENGINE_CORE_MAP
        assert self.factory._ENGINE_CORE_MAP["vllm"] == "motor.engine_server.core.vllm.vllm_core.VLLMServerCore"

    @mock.patch('importlib.import_module')
    def test_create_server_core_with_valid_engine_type(self, mock_import_module, mock_dependencies):
        # Get required classes
        _, IConfig, ServerConfig, IServerCore = get_classes()

        # Create mock ServerConfig
        mock_server_config = mock.MagicMock(spec=ServerConfig)
        mock_server_config.engine_type = "vllm"

        # Create mock IConfig
        mock_config = mock.MagicMock(spec=IConfig)
        mock_config.get_server_config.return_value = mock_server_config

        # Create mock return value
        expected_core = mock.MagicMock(spec=IServerCore)

        # Mock module import and class retrieval
        mock_module = mock.MagicMock()
        mock_vllm_core_class = mock.MagicMock()
        mock_vllm_core_class.return_value = expected_core
        mock_module.VLLMServerCore = mock_vllm_core_class
        mock_import_module.return_value = mock_module

        # Call method
        result = self.factory.create_server_core(mock_config)

        # Verify result
        assert result == expected_core
        mock_import_module.assert_called_once_with("motor.engine_server.core.vllm.vllm_core")
        mock_vllm_core_class.assert_called_once_with(mock_config)
        mock_config.get_server_config.assert_called_once()

    def test_create_server_core_with_unsupported_engine_type(self, mock_dependencies):
        # Get required classes
        _, IConfig, ServerConfig, _ = get_classes()

        # Create mock ServerConfig with unsupported engine_type
        mock_server_config = mock.MagicMock(spec=ServerConfig)
        mock_server_config.engine_type = "unsupported_engine"

        # Create mock IConfig
        mock_config = mock.MagicMock(spec=IConfig)
        mock_config.get_server_config.return_value = mock_server_config

        # Verify ValueError is raised with updated error message
        with pytest.raises(ValueError, match=r"Unsupported engine type: unsupported_engine.*Supported types are: \['vllm'\]"):
            self.factory.create_server_core(mock_config)

        # Verify calls
        mock_config.get_server_config.assert_called_once()

    def test_create_server_core_case_sensitive(self, mock_dependencies):
        # Get required classes
        _, IConfig, ServerConfig, _ = get_classes()

        # Create mock ServerConfig with uppercase engine_type
        mock_server_config = mock.MagicMock(spec=ServerConfig)
        mock_server_config.engine_type = "VLLM"

        # Create mock IConfig
        mock_config = mock.MagicMock(spec=IConfig)
        mock_config.get_server_config.return_value = mock_server_config

        # Verify ValueError is raised (case sensitive matching)
        with pytest.raises(ValueError, match="Unsupported engine type: VLLM"):
            self.factory.create_server_core(mock_config)

    def test_engine_core_map_class_variable(self, mock_dependencies):
        # Get required classes
        ServerCoreFactory, _, _, _ = get_classes()

        # Verify ENGINE_CORE_MAP is class variable
        factory1 = ServerCoreFactory()
        factory2 = ServerCoreFactory()

        # Modifying instance's reference to ENGINE_CORE_MAP should not affect class variable
        # This is testing that we're working with the class variable
        assert factory1._ENGINE_CORE_MAP is factory2._ENGINE_CORE_MAP
        assert factory1._ENGINE_CORE_MAP is ServerCoreFactory._ENGINE_CORE_MAP
        
    @mock.patch('importlib.import_module')
    def test_create_server_core_with_import_error(self, mock_import_module, mock_dependencies):
        # Get required classes
        _, IConfig, ServerConfig, _ = get_classes()

        # Create mock ServerConfig
        mock_server_config = mock.MagicMock(spec=ServerConfig)
        mock_server_config.engine_type = "vllm"

        # Create mock IConfig
        mock_config = mock.MagicMock(spec=IConfig)
        mock_config.get_server_config.return_value = mock_server_config

        # Mock import failure
        mock_import_module.side_effect = ImportError("Module not found")

        # Verify ValueError is raised
        with pytest.raises(ValueError) as excinfo:
            self.factory.create_server_core(mock_config)
        
        # Check main error message
        assert "Failed to load core class for vllm" in str(excinfo.value)
        # With raise from, the original exception is preserved as __cause__
        assert "Module not found" in str(excinfo.value.__cause__)

    @mock.patch('importlib.import_module')
    def test_create_server_core_with_attribute_error(self, mock_import_module, mock_dependencies):
        # Get required classes
        _, IConfig, ServerConfig, _ = get_classes()

        # Create mock ServerConfig
        mock_server_config = mock.MagicMock(spec=ServerConfig)
        mock_server_config.engine_type = "vllm"

        # Create mock IConfig
        mock_config = mock.MagicMock(spec=IConfig)
        mock_config.get_server_config.return_value = mock_server_config

        # Mock module import but missing class
        mock_module = mock.MagicMock()
        del mock_module.VLLMServerCore  # Remove VLLMServerCore attribute
        mock_import_module.return_value = mock_module

        # Verify ValueError is raised
        with pytest.raises(ValueError, match="Failed to load core class for vllm"):
            self.factory.create_server_core(mock_config)
