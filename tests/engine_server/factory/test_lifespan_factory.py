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
    """Mock all dependencies needed for testing LifespanFactory."""
    # Store original modules to restore later
    original_modules = {}
    modules_to_mock = [
        'motor.common.utils.logger',
        'motor.engine_server.config.base',
        'motor.engine_server.core.vllm.vllm_httpserver_init'
    ]

    # Save original modules if they exist
    for module_name in modules_to_mock:
        if module_name in sys.modules:
            original_modules[module_name] = sys.modules[module_name]

    # Create mock objects
    mock_logger = mock.MagicMock()
    mock_logger.get_logger = mock.MagicMock(return_value=mock.MagicMock())
    mock_config_module = mock.MagicMock()
    mock_vllm_httpserver_init_module = mock.MagicMock()
    
    # Mock the create_vllm_lifespan function
    mock_create_vllm_lifespan = mock.MagicMock()
    mock_vllm_httpserver_init_module.create_vllm_lifespan = mock_create_vllm_lifespan

    # Replace modules in sys.modules
    sys.modules['motor.common.utils.logger'] = mock_logger
    sys.modules['motor.engine_server.config.base'] = mock_config_module
    sys.modules['motor.engine_server.core.vllm.vllm_httpserver_init'] = mock_vllm_httpserver_init_module

    # Yield the mock objects for use in tests
    yield {
        'mock_logger': mock_logger,
        'mock_config_module': mock_config_module,
        'mock_vllm_httpserver_init_module': mock_vllm_httpserver_init_module,
        'mock_create_vllm_lifespan': mock_create_vllm_lifespan
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
    from motor.engine_server.factory.lifespan_factory import LifespanFactory
    from motor.engine_server.config.base import IConfig, ServerConfig
    return LifespanFactory, IConfig, ServerConfig


class TestLifespanFactory:
    def setup_method(self, mock_dependencies):
        # Get required classes
        LifespanFactory, _, _ = get_classes()
        # Create factory instance
        self.factory = LifespanFactory()

    def test_initialization(self, mock_dependencies):
        """Test LifespanFactory initialization and _LIFESPAN_CREATOR_MAP setup."""
        # Verify _LIFESPAN_CREATOR_MAP is properly defined as class variable
        assert isinstance(self.factory._LIFESPAN_CREATOR_MAP, dict)
        assert "vllm" in self.factory._LIFESPAN_CREATOR_MAP
        assert self.factory._LIFESPAN_CREATOR_MAP["vllm"] == "motor.engine_server.core.vllm.vllm_httpserver_init.create_vllm_lifespan"

    @mock.patch('importlib.import_module')
    def test_get_lifespan_with_valid_engine_type(self, mock_import_module, mock_dependencies):
        """Test get_lifespan with valid vllm engine type."""
        # Get required classes
        _, IConfig, ServerConfig = get_classes()

        # Create mock ServerConfig
        mock_server_config = mock.MagicMock()
        mock_server_config.engine_type = "vllm"

        # Create mock IConfig and init_params
        mock_config = mock.MagicMock()
        mock_config.get_server_config.return_value = mock_server_config
        mock_init_params = {"input_address": "ipc:///tmp/input", "output_address": "ipc:///tmp/output"}

        # Create mock return value for lifespan creator
        expected_lifespan = mock.MagicMock()
        mock_dependencies['mock_create_vllm_lifespan'].return_value = expected_lifespan

        # Mock module import (override the sys.modules mock for explicit verification)
        mock_module = mock.MagicMock()
        mock_module.create_vllm_lifespan = mock_dependencies['mock_create_vllm_lifespan']
        mock_import_module.return_value = mock_module

        # Call method
        result = self.factory.get_lifespan(mock_config, mock_init_params)

        # Verify results
        assert result == expected_lifespan
        
        # Verify import and function calls
        mock_import_module.assert_called_once_with("motor.engine_server.core.vllm.vllm_httpserver_init")
        mock_dependencies['mock_create_vllm_lifespan'].assert_called_once_with(mock_config, mock_init_params)
        mock_config.get_server_config.assert_called_once()

    def test_get_lifespan_with_unsupported_engine_type(self, mock_dependencies):
        """Test get_lifespan with unsupported engine type raises ValueError."""
        # Get required classes
        _, IConfig, ServerConfig = get_classes()

        # Create mock ServerConfig with unsupported engine_type
        mock_server_config = mock.MagicMock()
        mock_server_config.engine_type = "unsupported_engine"

        # Create mock IConfig
        mock_config = mock.MagicMock()
        mock_config.get_server_config.return_value = mock_server_config
        mock_init_params = {}

        # Verify ValueError is raised with correct error message
        with pytest.raises(ValueError, match=r"Unsupported engine type: unsupported_engine.*Supported types are: \['vllm'\]"):
            self.factory.get_lifespan(mock_config, mock_init_params)

        # Verify config method was called
        mock_config.get_server_config.assert_called_once()

    def test_get_lifespan_case_sensitive(self, mock_dependencies):
        """Test get_lifespan with uppercase engine type (case sensitive matching)."""
        # Get required classes
        _, IConfig, ServerConfig = get_classes()

        # Create mock ServerConfig with uppercase engine_type
        mock_server_config = mock.MagicMock()
        mock_server_config.engine_type = "VLLM"

        # Create mock IConfig
        mock_config = mock.MagicMock()
        mock_config.get_server_config.return_value = mock_server_config
        mock_init_params = {}

        # Verify ValueError is raised (case sensitive matching)
        with pytest.raises(ValueError, match="Unsupported engine type: VLLM"):
            self.factory.get_lifespan(mock_config, mock_init_params)

    def test_lifespan_creator_map_class_variable(self, mock_dependencies):
        """Test _LIFESPAN_CREATOR_MAP is a class variable (shared across instances)."""
        # Get required classes
        LifespanFactory, _, _ = get_classes()

        # Create multiple factory instances
        factory1 = LifespanFactory()
        factory2 = LifespanFactory()

        # Verify _LIFESPAN_CREATOR_MAP is shared (class variable)
        assert factory1._LIFESPAN_CREATOR_MAP is factory2._LIFESPAN_CREATOR_MAP
        assert factory1._LIFESPAN_CREATOR_MAP is LifespanFactory._LIFESPAN_CREATOR_MAP
        
    @mock.patch('importlib.import_module')
    def test_get_lifespan_with_import_error(self, mock_import_module, mock_dependencies):
        """Test get_lifespan with ImportError during module loading."""
        # Get required classes
        _, IConfig, ServerConfig = get_classes()

        # Create mock ServerConfig
        mock_server_config = mock.MagicMock()
        mock_server_config.engine_type = "vllm"

        # Create mock IConfig
        mock_config = mock.MagicMock()
        mock_config.get_server_config.return_value = mock_server_config
        mock_init_params = {}

        # Mock import failure
        mock_import_module.side_effect = ImportError("Module not found")

        # Verify ValueError is raised with original exception as cause
        with pytest.raises(ValueError) as excinfo:
            self.factory.get_lifespan(mock_config, mock_init_params)
        
        # Check main error message and original exception
        assert "Failed to load lifespan creator for vllm" in str(excinfo.value)
        assert "Module not found" in str(excinfo.value.__cause__)

    @mock.patch('importlib.import_module')
    def test_get_lifespan_with_attribute_error(self, mock_import_module, mock_dependencies):
        """Test get_lifespan with AttributeError (missing function in module)."""
        # Get required classes
        _, IConfig, ServerConfig = get_classes()

        # Create mock ServerConfig
        mock_server_config = mock.MagicMock()
        mock_server_config.engine_type = "vllm"

        # Create mock IConfig
        mock_config = mock.MagicMock()
        mock_config.get_server_config.return_value = mock_server_config
        mock_init_params = {}

        # Mock module import but missing create_vllm_lifespan function
        mock_module = mock.MagicMock()
        del mock_module.create_vllm_lifespan  # Remove the function attribute
        mock_import_module.return_value = mock_module

        # Verify ValueError is raised
        with pytest.raises(ValueError, match="Failed to load lifespan creator for vllm"):
            self.factory.get_lifespan(mock_config, mock_init_params)