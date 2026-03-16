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
    """Mock all dependencies needed for testing ProtocolFactory."""
    # Store original modules to restore later
    original_modules = {}
    modules_to_mock = [
        'motor.common.utils.logger',
        'vllm.entrypoints.openai.chat_completion.protocol',
        'vllm.entrypoints.openai.completion.protocol'
    ]

    # Save original modules if they exist
    for module_name in modules_to_mock:
        if module_name in sys.modules:
            original_modules[module_name] = sys.modules[module_name]

    # Create mock objects
    mock_logger = mock.MagicMock()
    mock_logger.get_logger = mock.MagicMock(return_value=mock.MagicMock())

    # Mock protocol classes
    mock_chat_completion_request = mock.MagicMock()
    mock_completion_request = mock.MagicMock()

    # Set up mock chat completion protocol module
    mock_chat_protocol_module = mock.MagicMock()
    mock_chat_protocol_module.ChatCompletionRequest = mock_chat_completion_request

    # Set up mock completion protocol module
    mock_completion_protocol_module = mock.MagicMock()
    mock_completion_protocol_module.CompletionRequest = mock_completion_request

    # Replace modules in sys.modules
    sys.modules['motor.common.utils.logger'] = mock_logger
    sys.modules['vllm.entrypoints.openai.chat_completion.protocol'] = mock_chat_protocol_module
    sys.modules['vllm.entrypoints.openai.completion.protocol'] = mock_completion_protocol_module

    # Yield the mock objects for use in tests
    yield {
        'mock_logger': mock_logger,
        'mock_chat_protocol_module': mock_chat_protocol_module,
        'mock_completion_protocol_module': mock_completion_protocol_module,
        'mock_chat_completion_request': mock_chat_completion_request,
        'mock_completion_request': mock_completion_request
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
    from motor.engine_server.factory.protocol_factory import ProtocolFactory
    return ProtocolFactory


class TestProtocolFactory:
    def setup_method(self):
        """Setup test environment before each method."""
        # Get required classes
        ProtocolFactory = get_classes()
        # Create factory instance
        self.factory = ProtocolFactory()

    def test_initialization(self, mock_dependencies):
        """Test ProtocolFactory initialization and _PROTOCOL_MAP setup."""
        # Verify _PROTOCOL_MAP is properly defined as class variable
        assert isinstance(self.factory._PROTOCOL_MAP, dict)
        assert "vllm" in self.factory._PROTOCOL_MAP
        assert isinstance(self.factory._PROTOCOL_MAP["vllm"], dict)
        assert self.factory._PROTOCOL_MAP["vllm"] == {
            "ChatCompletionRequest": "vllm.entrypoints.openai.chat_completion.protocol.ChatCompletionRequest",
            "CompletionRequest": "vllm.entrypoints.openai.completion.protocol.CompletionRequest",
        }

    @mock.patch('importlib.import_module')
    def test_import_class_from_string(self, mock_import_module, mock_dependencies):
        """Test private _import_class_from_string method."""
        # Mock module import and class retrieval
        mock_module = mock.MagicMock()
        mock_class = mock.MagicMock()
        mock_module.TestClass = mock_class
        mock_import_module.return_value = mock_module

        result = self.factory._import_class_from_string("some.module.TestClass")

        # Verify results
        assert result == mock_class
        mock_import_module.assert_called_once_with("some.module")

        with pytest.raises(ValueError):
            self.factory._import_class_from_string("invalid_path")

    @mock.patch('importlib.import_module')
    def test_load_protocol_classes_with_valid_engine_type(self, mock_import_module, mock_dependencies):
        """Test load_protocol_classes with valid vllm engine type."""
        # Mock module import (override sys.modules mock for explicit verification)
        mock_chat_module = mock.MagicMock()
        mock_chat_module.ChatCompletionRequest = mock_dependencies['mock_chat_completion_request']
        mock_completion_module = mock.MagicMock()
        mock_completion_module.CompletionRequest = mock_dependencies['mock_completion_request']

        def side_effect(module_name):
            if module_name == "vllm.entrypoints.openai.chat_completion.protocol":
                return mock_chat_module
            elif module_name == "vllm.entrypoints.openai.completion.protocol":
                return mock_completion_module
            return mock.MagicMock()

        mock_import_module.side_effect = side_effect

        # Call method
        chat_cls, completion_cls = self.factory.load_protocol_classes("vllm")

        # Verify results
        assert chat_cls == mock_dependencies['mock_chat_completion_request']
        assert completion_cls == mock_dependencies['mock_completion_request']

    def test_load_protocol_classes_with_unsupported_engine_type(self, mock_dependencies):
        """Test load_protocol_classes with unsupported engine type raises ValueError."""
        # Verify ValueError is raised with correct error message
        with pytest.raises(ValueError,
                           match=r"Unsupported engine type: unsupported_engine.*Supported types are: \['vllm'\]"):
            self.factory.load_protocol_classes("unsupported_engine")

    def test_load_protocol_classes_case_sensitive(self, mock_dependencies):
        """Test load_protocol_classes with uppercase engine type (case sensitive matching)."""
        # Verify ValueError is raised (case sensitive matching)
        with pytest.raises(ValueError, match="Unsupported engine type: VLLM"):
            self.factory.load_protocol_classes("VLLM")

    def test_protocol_map_class_variable(self, mock_dependencies):
        """Test _PROTOCOL_MAP is a class variable (shared across instances)."""
        # Get required classes
        ProtocolFactory = get_classes()

        # Create multiple factory instances
        factory1 = ProtocolFactory()
        factory2 = ProtocolFactory()

        # Verify _PROTOCOL_MAP is shared (class variable)
        assert factory1._PROTOCOL_MAP is factory2._PROTOCOL_MAP
        assert factory1._PROTOCOL_MAP is ProtocolFactory._PROTOCOL_MAP

    @mock.patch('importlib.import_module')
    def test_load_protocol_classes_with_import_error(self, mock_import_module, mock_dependencies):
        """Test load_protocol_classes with ImportError during module loading."""
        # Mock import failure
        mock_import_module.side_effect = ImportError("Module not found")

        # Verify ValueError is raised with original exception as cause
        with pytest.raises(ValueError) as excinfo:
            self.factory.load_protocol_classes("vllm")

        # Check main error message and original exception
        assert "Failed to load protocol classes for vllm" in str(excinfo.value)
        assert "Module not found" in str(excinfo.value.__cause__)

    @mock.patch('importlib.import_module')
    def test_load_protocol_classes_with_attribute_error(self, mock_import_module, mock_dependencies):
        """Test load_protocol_classes with AttributeError (missing class in module)."""
        # Mock module import but missing ChatCompletionRequest class
        mock_chat_module = mock.MagicMock()
        del mock_chat_module.ChatCompletionRequest  # Remove the class attribute
        mock_completion_module = mock.MagicMock()

        mock_import_module.side_effect = [mock_chat_module, mock_completion_module]

        # Verify ValueError is raised
        with pytest.raises(ValueError, match="Failed to load protocol classes for vllm"):
            self.factory.load_protocol_classes("vllm")

    @mock.patch('motor.engine_server.factory.protocol_factory.ProtocolFactory._import_class_from_string')
    def test_load_protocol_classes_with_key_error(self, mock_import_class, mock_dependencies):
        """Test load_protocol_classes with KeyError (missing protocol key)."""
        with mock.patch.dict(self.factory._PROTOCOL_MAP["vllm"], {"ChatCompletionRequest": "path"}, clear=True):
            # Verify ValueError is raised
            with pytest.raises(ValueError, match="Failed to load protocol classes for vllm"):
                self.factory.load_protocol_classes("vllm")

    @mock.patch('motor.engine_server.factory.protocol_factory.ProtocolFactory._import_class_from_string')
    def test_load_protocol_classes_with_value_error(self, mock_import_class, mock_dependencies):
        """Test load_protocol_classes with ValueError from _import_class_from_string."""
        # Mock _import_class_from_string to raise ValueError
        mock_import_class.side_effect = ValueError("Invalid class path")

        # Verify ValueError is raised with original exception as cause
        with pytest.raises(ValueError) as excinfo:
            self.factory.load_protocol_classes("vllm")

        assert "Failed to load protocol classes for vllm" in str(excinfo.value)
        assert "Invalid class path" in str(excinfo.value.__cause__)