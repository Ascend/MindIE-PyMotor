# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import sys
import pytest
from unittest.mock import MagicMock, Mock, patch


@pytest.fixture(autouse=True, scope="function")
def mock_modules():
    """Mock all necessary modules at the module level"""
    # First check and save original modules (if they exist)
    original_modules = {}
    modules_to_mock = [
        'motor.engine_server.config.base',
        'motor.engine_server.utils.config_parser',
        'motor.engine_server.factory.core_factory',
        'motor.common.utils.logger',
        'motor.engine_server.utils.prometheus'
    ]

    for module_name in modules_to_mock:
        if module_name in sys.modules:
            original_modules[module_name] = sys.modules[module_name]

    # Create mock module structure
    mock_base = Mock()
    mock_server_config = Mock()
    mock_base.ServerConfig = mock_server_config
    
    # Create a proper mock object with engine_type attribute
    mock_config_obj = Mock()
    mock_config_obj.engine_type = "vllm"  # Add engine_type attribute
    mock_server_config.init_engine_server_config = MagicMock(return_value=mock_config_obj)

    mock_config_parser = Mock()
    mock_parser_class = Mock()
    mock_config_parser.ConfigParser = mock_parser_class
    mock_parser_instance = Mock()
    mock_parser_class.return_value = mock_parser_instance
    mock_parser_instance.parse = MagicMock(return_value="mock_parsed_config")

    mock_core_factory = Mock()
    mock_factory_class = Mock()
    mock_core_factory.ServerCoreFactory = mock_factory_class
    mock_factory_instance = Mock()
    mock_factory_class.return_value = mock_factory_instance
    mock_server_core = Mock()
    mock_factory_instance.create_server_core = MagicMock(return_value=mock_server_core)

    mock_logger = Mock()
    mock_logger_module = Mock()
    mock_logger_module.get_logger = MagicMock(return_value=mock_logger)

    # Mock prometheus module
    mock_prometheus = Mock()
    mock_prometheus.setup_multiprocess_prometheus = MagicMock()

    # Replace modules in sys.modules
    sys.modules['motor.engine_server.config.base'] = mock_base
    sys.modules['motor.engine_server.utils.config_parser'] = mock_config_parser
    sys.modules['motor.engine_server.factory.core_factory'] = mock_core_factory
    sys.modules['motor.common.utils.logger'] = mock_logger_module
    sys.modules['motor.engine_server.utils.prometheus'] = mock_prometheus

    # Build dictionary of mock objects to return
    mock_objects = {
        'server_config_class': mock_server_config,
        'mock_config_obj': mock_config_obj,
        'parser_class': mock_parser_class,
        'parser_instance': mock_parser_instance,
        'factory_class': mock_factory_class,
        'factory_instance': mock_factory_instance,
        'server_core': mock_server_core,
        'logger': mock_logger,
        'prometheus_module': mock_prometheus
    }

    yield mock_objects

    # Cleanup: restore original modules or remove mock modules
    for module_name in modules_to_mock:
        if module_name in original_modules:
            sys.modules[module_name] = original_modules[module_name]
        elif module_name in sys.modules:
            del sys.modules[module_name]


def test_main(mock_modules):
    """Test the main function of engine_server cli"""
    # Import main after mocks are set up
    from motor.engine_server.cli.main import main

    # Ensure initialize doesn't raise an exception in this test
    mock_modules['server_core'].initialize.side_effect = None

    # Reset mocks before test
    mock_modules['prometheus_module'].setup_multiprocess_prometheus.reset_mock()
    mock_modules['server_config_class'].init_engine_server_config.reset_mock()
    mock_modules['parser_class'].reset_mock()
    mock_modules['parser_instance'].parse.reset_mock()
    mock_modules['factory_class'].reset_mock()
    mock_modules['factory_instance'].create_server_core.reset_mock()
    mock_modules['server_core'].initialize.reset_mock()
    mock_modules['server_core'].run.reset_mock()
    mock_modules['server_core'].join.reset_mock()
    mock_modules['logger'].info.reset_mock()

    # Call the main function
    main()

    # Verify all expected calls were made
    mock_modules['prometheus_module'].setup_multiprocess_prometheus.assert_called_once()
    mock_modules['server_config_class'].init_engine_server_config.assert_called_once()
    mock_modules['parser_class'].assert_called_once_with(server_config=mock_modules['mock_config_obj'])
    mock_modules['parser_instance'].parse.assert_called_once()
    mock_modules['logger'].info.assert_called_once()
    mock_modules['factory_class'].assert_called_once()
    mock_modules['factory_instance'].create_server_core.assert_called_once_with(config="mock_parsed_config")
    mock_modules['server_core'].initialize.assert_called_once()
    mock_modules['server_core'].run.assert_called_once()
    mock_modules['server_core'].join.assert_called_once()
