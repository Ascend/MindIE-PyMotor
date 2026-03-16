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
import importlib.util


@pytest.fixture(scope="module")
def mock_dependencies():
    """Mock all dependencies needed for testing ConfigParser."""
    # Store original modules to restore later
    original_modules = {}
    modules_to_mock = [
        'motor.engine_server.utils.logger',
        'motor.engine_server.config.vllm'
    ]

    # Save original modules if they exist
    for module_name in modules_to_mock:
        if module_name in sys.modules:
            original_modules[module_name] = sys.modules[module_name]

    # Create mock objects
    mock_logger = mock.MagicMock()
    mock_logger.get_logger = mock.MagicMock(return_value=mock.MagicMock())
    mock_vllm_config_class = mock.MagicMock()

    # Set up the mock module structure
    mock_vllm_module = mock.MagicMock()
    mock_vllm_module.VLLMConfig = mock_vllm_config_class

    # Replace modules in sys.modules
    sys.modules['motor.engine_server.utils.logger'] = mock_logger
    sys.modules['motor.engine_server.config.vllm'] = mock_vllm_module
    
    # Mock importlib.util.find_spec to return a non-None value for 'vllm'
    original_find_spec = importlib.util.find_spec
    mock_find_spec = mock.MagicMock()
    mock_find_spec.return_value = mock.MagicMock()  # Simulate vllm package exists
    importlib.util.find_spec = mock_find_spec

    # Yield the mock objects for use in tests
    yield {
        'mock_logger': mock_logger,
        'mock_vllm_config_class': mock_vllm_config_class,
        'mock_find_spec': mock_find_spec
    }

    # Restore original functions and modules
    importlib.util.find_spec = original_find_spec
    for module_name in modules_to_mock:
        if module_name in original_modules:
            sys.modules[module_name] = original_modules[module_name]
        elif module_name in sys.modules:
            del sys.modules[module_name]


# Import classes inside a function to be called after mocks are set up
def get_classes():
    """Import the classes needed for testing."""
    from motor.engine_server.utils.config_parser import ConfigParser
    from motor.engine_server.config.base import IConfig, ServerConfig
    return ConfigParser, IConfig, ServerConfig


class TestConfigParser:
    def setup_method(self, mock_dependencies):
        # Get required classes
        ConfigParser, _, ServerConfig = get_classes()

        # Create mock ServerConfig
        self.mock_server_config = mock.MagicMock(spec=ServerConfig)

        # Create parser instance
        self.parser = ConfigParser(server_config=self.mock_server_config)

    def test_initialization(self, mock_dependencies):
        # Verify initialization sets up properties correctly
        assert self.parser.server_config == self.mock_server_config
        # Check for new _ENGINE_CONFIG_MAP attribute instead of _config_class_map
        assert hasattr(self.parser, '_ENGINE_CONFIG_MAP')
        assert "vllm" in self.parser._ENGINE_CONFIG_MAP
        assert self.parser._ENGINE_CONFIG_MAP["vllm"] == "motor.engine_server.config.vllm.VLLMConfig"

    @mock.patch('importlib.import_module')
    def test_parse_success(self, mock_import_module, mock_dependencies):
        # Get required classes
        _, IConfig, _ = get_classes()
        
        # Set up mock server_config with valid engine_type
        self.mock_server_config.engine_type = "vllm"
        
        # Mock module import and config class retrieval
        mock_module = mock.MagicMock()
        mock_config_class = mock.MagicMock(spec=IConfig)
        mock_module.VLLMConfig = mock_config_class
        mock_import_module.return_value = mock_module
        
        # Mock config instance and its methods
        mock_config_instance = mock.MagicMock(spec=IConfig)
        mock_config_class.return_value = mock_config_instance
        
        # Call parse method
        result = self.parser.parse()
        
        # Verify module import and class retrieval
        mock_import_module.assert_called_once_with("motor.engine_server.config.vllm")
        
        # Verify config class is instantiated and methods are called
        mock_config_class.assert_called_once_with(server_config=self.mock_server_config)
        mock_config_instance.initialize.assert_called_once()
        mock_config_instance.convert.assert_called_once()
        mock_config_instance.validate.assert_called_once()
        
        # Verify return value
        assert result == mock_config_instance

    @mock.patch('importlib.import_module')
    def test_parse_with_import_error(self, mock_import_module, mock_dependencies):
        # Set up mock server_config with valid engine_type
        self.mock_server_config.engine_type = "vllm"
        
        # Mock import failure
        mock_import_module.side_effect = ImportError("Module not found")
        
        # Verify ValueError is raised
        with pytest.raises(ValueError) as excinfo:
            self.parser.parse()
        
        # Verify error message contains expected part
        error_message = str(excinfo.value)
        assert "Failed to load config class for vllm" in error_message
        # With raise from, the original exception is preserved as __cause__
        assert "Module not found" in str(excinfo.value.__cause__)

    @mock.patch('importlib.import_module')
    def test_parse_with_attribute_error(self, mock_import_module, mock_dependencies):
        # Set up mock server_config with valid engine_type
        self.mock_server_config.engine_type = "vllm"
        
        # Mock successful module import but missing class
        mock_module = mock.MagicMock()
        del mock_module.VLLMConfig  # Remove VLLMConfig attribute to trigger AttributeError
        mock_import_module.return_value = mock_module
        
        # Verify ValueError is raised
        with pytest.raises(ValueError) as excinfo:
            self.parser.parse()
        
        # Verify error message contains attribute error info
        error_message = str(excinfo.value)
        assert "Failed to load config class for vllm" in error_message

    def test_parse_with_unsupported_engine_type(self, mock_dependencies):
        # Set up mock server_config with unsupported engine_type
        self.mock_server_config.engine_type = "unsupported_engine"

        # Verify ValueError is raised
        with pytest.raises(ValueError) as excinfo:
            self.parser.parse()

        # Check error message contains expected parts
        error_message = str(excinfo.value)
        assert "Unsupported engine type: unsupported_engine" in error_message
        assert "vllm" in error_message

    def test_parse_case_sensitive(self, mock_dependencies):
        # Set up mock server_config with uppercase engine_type
        self.mock_server_config.engine_type = "VLLM"

        # Verify ValueError is raised (case sensitive matching)
        with pytest.raises(ValueError) as excinfo:
            self.parser.parse()

        # Check error message contains expected parts
        error_message = str(excinfo.value)
        assert "Unsupported engine type: VLLM" in error_message
        assert "vllm" in error_message
