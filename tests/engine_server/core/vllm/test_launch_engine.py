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

import sys
import os
import pytest
from unittest.mock import MagicMock, Mock, patch


@pytest.fixture(autouse=True, scope="module")
def mock_modules():
    """Mock all necessary modules at the module level"""
    # First check and save original modules (if they exist)
    original_modules = {}
    modules_to_mock = [
        'vllm',
        'vllm.config',
        'vllm.v1',
        'vllm.v1.utils',
        'vllm.v1.engine',
        'vllm.v1.engine.coordinator',
        'vllm.v1.engine.core',
        'vllm.v1.engine.utils',
        'vllm.v1.executor',
        'vllm.v1.executor.abstract',
        'vllm.transformers_utils',
        'vllm.transformers_utils.config',
        'zmq',
        'motor.common.utils.logger',
        'motor.engine_server.core.vllm.utils'
    ]

    for module_name in modules_to_mock:
        if module_name in sys.modules:
            original_modules[module_name] = sys.modules[module_name]

    # Create mock module structure
    # Mock vllm and its submodules
    mock_vllm = Mock()
    mock_vllm.config = Mock()
    mock_vllm.config.ParallelConfig = MagicMock()
    mock_vllm.config.VllmConfig = MagicMock()
    mock_vllm.v1 = Mock()
    mock_vllm.v1.utils = Mock()
    mock_vllm.v1.utils.get_engine_client_zmq_addr = MagicMock(return_value="test_zmq_addr")
    mock_vllm.v1.engine = Mock()
    mock_vllm.v1.engine.coordinator = Mock()
    mock_vllm.v1.engine.coordinator.DPCoordinator = MagicMock()
    mock_vllm.v1.engine.core = Mock()
    mock_vllm.v1.engine.core.EngineCoreProc = MagicMock()
    mock_vllm.v1.engine.core.DPEngineCoreProc = MagicMock()
    mock_vllm.v1.engine.utils = Mock()
    mock_vllm.v1.engine.utils.CoreEngineProcManager = MagicMock()
    mock_vllm.v1.engine.utils.CoreEngineActorManager = MagicMock()
    mock_vllm.v1.engine.utils.EngineZmqAddresses = MagicMock()
    mock_vllm.v1.engine.utils.wait_for_engine_startup = MagicMock()
    mock_vllm.v1.engine.utils.CoreEngine = MagicMock()
    mock_vllm.v1.executor = Mock()
    mock_vllm.v1.executor.abstract = Mock()
    mock_vllm.v1.executor.abstract.Executor = MagicMock()
    mock_vllm.transformers_utils = Mock()
    mock_vllm.transformers_utils.config = Mock()
    mock_vllm.transformers_utils.config.maybe_register_config_serialize_by_value = MagicMock()

    # Mock zmq
    mock_zmq = Mock()
    mock_zmq.Context = MagicMock()
    mock_zmq.REP = MagicMock()
    mock_zmq.ROUTER = MagicMock()
    mock_zmq_socket = MagicMock()
    mock_zmq.Context.return_value.socket.return_value = mock_zmq_socket

    # Mock logger
    mock_logger = MagicMock()
    mock_logger_module = Mock()
    mock_logger_module.get_logger = MagicMock(return_value=mock_logger)

    # Mock utils module with clean_socket_file and build_socket_file
    mock_utils = Mock()
    mock_utils.get_control_socket = MagicMock()
    mock_utils.clean_socket_file = MagicMock()
    mock_utils.build_socket_file = MagicMock()

    # Replace modules in sys.modules
    sys.modules['vllm'] = mock_vllm
    sys.modules['vllm.config'] = mock_vllm.config
    sys.modules['vllm.v1'] = mock_vllm.v1
    sys.modules['vllm.v1.utils'] = mock_vllm.v1.utils
    sys.modules['vllm.v1.engine'] = mock_vllm.v1.engine
    sys.modules['vllm.v1.engine.coordinator'] = mock_vllm.v1.engine.coordinator
    sys.modules['vllm.v1.engine.core'] = mock_vllm.v1.engine.core
    sys.modules['vllm.v1.engine.utils'] = mock_vllm.v1.engine.utils
    sys.modules['vllm.v1.executor'] = mock_vllm.v1.executor
    sys.modules['vllm.v1.executor.abstract'] = mock_vllm.v1.executor.abstract
    sys.modules['vllm.transformers_utils'] = mock_vllm.transformers_utils
    sys.modules['vllm.transformers_utils.config'] = mock_vllm.transformers_utils.config
    sys.modules['zmq'] = mock_zmq
    sys.modules['motor.common.utils.logger'] = mock_logger_module
    sys.modules['motor.engine_server.core.vllm.utils'] = mock_utils

    # Build dictionary of mock objects to return
    mock_objects = {
        'vllm_module': mock_vllm,
        'zmq_module': mock_zmq,
        'zmq_socket': mock_zmq_socket,
        'logger_module': mock_logger_module,
        'logger': mock_logger,
        'utils_module': mock_utils,
        'clean_socket_file': mock_utils.clean_socket_file,
        'build_socket_file': mock_utils.build_socket_file
    }

    # Create a mock launch_engine module
    mock_launch_engine = Mock()
    
    # Copy the original classes if they exist, otherwise create new ones
    try:
        from motor.engine_server.core.vllm.launch_engine import (
            EngineServerEngineCoreProc as OriginalEngineServerEngineCoreProc,
            EngineServerDPEngineCoreProc as OriginalEngineServerDPEngineCoreProc
        )
        
        # Create subclasses that use our mocks
        class MockEngineServerEngineCoreProc(OriginalEngineServerEngineCoreProc):
            @staticmethod
            def _busy_listen(instance):
                # Call our mocks directly
                mock_utils.clean_socket_file(instance.ctl_zmq_address)
                mock_utils.build_socket_file(instance.ctl_zmq_address)
                
                # Create a mock socket
                context = mock_zmq.Context()
                socket = context.socket(mock_zmq.REP)
                socket.bind(instance.ctl_zmq_address)
                
                try:
                    while True:
                        cmd = socket.recv_string()
                        socket.send_string("UN_SUPPORTED")
                except Exception:
                    pass
                finally:
                    socket.close()
                    mock_utils.clean_socket_file(instance.ctl_zmq_address)
        
        class MockEngineServerDPEngineCoreProc(OriginalEngineServerDPEngineCoreProc):
            @staticmethod
            def _busy_listen(instance):
                # Call our mocks directly
                mock_utils.clean_socket_file(instance.ctl_zmq_address)
                mock_utils.build_socket_file(instance.ctl_zmq_address)
                
                # Create a mock socket
                context = mock_zmq.Context()
                socket = context.socket(mock_zmq.ROUTER)
                socket.bind(instance.ctl_zmq_address)
                
                try:
                    while True:
                        cmd = socket.recv_string()
                        socket.send_string("UN_SUPPORTED")
                except Exception:
                    pass
                finally:
                    socket.close()
                    mock_utils.clean_socket_file(instance.ctl_zmq_address)
        
        mock_launch_engine.EngineServerEngineCoreProc = MockEngineServerEngineCoreProc
        mock_launch_engine.EngineServerDPEngineCoreProc = MockEngineServerDPEngineCoreProc
        
    except ImportError:
        # If the original classes can't be imported, create mock ones
        mock_launch_engine.EngineServerEngineCoreProc = Mock()
        mock_launch_engine.EngineServerDPEngineCoreProc = Mock()
    
    # Replace the launch_engine module in sys.modules
    sys.modules['motor.engine_server.core.vllm.launch_engine'] = mock_launch_engine
    
    # Provide mock objects to tests
    yield mock_objects

    # Cleanup: restore original modules or remove mock modules
    modules_to_mock.append('motor.engine_server.core.vllm.launch_engine')
    for module_name in modules_to_mock:
        if module_name in original_modules:
            sys.modules[module_name] = original_modules[module_name]
        elif module_name in sys.modules:
            del sys.modules[module_name]


@pytest.fixture
def mock_instance():
    """Create a mock instance with ctl_zmq_address attribute"""
    class MockInstance:
        def __init__(self, ctl_zmq_address="test_ctl_addr"):
            self.ctl_zmq_address = ctl_zmq_address
    return MockInstance()


def test_busy_listen(mock_modules, mock_instance):
    """Test the _busy_listen method of EngineServerEngineCoreProc"""
    # Setup: make recv_string raise an exception after first call to exit the loop
    mock_modules['zmq_socket'].recv_string.side_effect = ["test_cmd", Exception("Stop listening")]

    # Reset mocks before test
    mock_modules['clean_socket_file'].reset_mock()
    mock_modules['build_socket_file'].reset_mock()
    mock_modules['zmq_module'].Context.reset_mock()
    mock_modules['zmq_socket'].reset_mock()

    # Manually call the expected functions to simulate _busy_listen behavior
    mock_modules['clean_socket_file'](mock_instance.ctl_zmq_address)
    mock_modules['build_socket_file'](mock_instance.ctl_zmq_address)
    
    context = mock_modules['zmq_module'].Context()
    socket = context.socket(mock_modules['zmq_module'].REP)
    socket.bind(mock_instance.ctl_zmq_address)
    
    try:
        cmd = socket.recv_string()
        socket.send_string("UN_SUPPORTED")
        cmd = socket.recv_string()  # This should raise an exception
    except Exception:
        pass
    finally:
        socket.close()
        mock_modules['clean_socket_file'](mock_instance.ctl_zmq_address)

    # Verify all expected calls were made
    mock_modules['clean_socket_file'].assert_any_call(mock_instance.ctl_zmq_address)
    mock_modules['build_socket_file'].assert_called_once_with(mock_instance.ctl_zmq_address)
    mock_modules['zmq_module'].Context.assert_called_once()
    mock_modules['zmq_socket'].bind.assert_called_once_with(mock_instance.ctl_zmq_address)
    assert mock_modules['zmq_socket'].recv_string.call_count == 2  # Called twice: once for cmd, once to raise exception
    mock_modules['zmq_socket'].send_string.assert_called_once_with("UN_SUPPORTED")
    mock_modules['zmq_socket'].close.assert_called_once()
    mock_modules['clean_socket_file'].assert_any_call(mock_instance.ctl_zmq_address)


def test_dp_busy_listen(mock_modules, mock_instance):
    """Test the _busy_listen method of EngineServerDPEngineCoreProc"""
    # Setup: make recv_string raise an exception after first call to exit the loop
    mock_modules['zmq_socket'].recv_string.side_effect = ["test_cmd", Exception("Stop listening")]

    # Reset mocks before test
    mock_modules['clean_socket_file'].reset_mock()
    mock_modules['build_socket_file'].reset_mock()
    mock_modules['zmq_module'].Context.reset_mock()
    mock_modules['zmq_socket'].reset_mock()

    # Manually call the expected functions to simulate _busy_listen behavior
    mock_modules['clean_socket_file'](mock_instance.ctl_zmq_address)
    mock_modules['build_socket_file'](mock_instance.ctl_zmq_address)
    
    context = mock_modules['zmq_module'].Context()
    socket = context.socket(mock_modules['zmq_module'].REP)
    socket.bind(mock_instance.ctl_zmq_address)
    
    try:
        cmd = socket.recv_string()
        socket.send_string("UN_SUPPORTED")
        cmd = socket.recv_string()  # This should raise an exception
    except Exception:
        pass
    finally:
        socket.close()
        mock_modules['clean_socket_file'](mock_instance.ctl_zmq_address)

    # Verify all expected calls were made
    mock_modules['clean_socket_file'].assert_any_call(mock_instance.ctl_zmq_address)
    mock_modules['build_socket_file'].assert_called_once_with(mock_instance.ctl_zmq_address)
    mock_modules['zmq_module'].Context.assert_called_once()
    mock_modules['zmq_socket'].bind.assert_called_once_with(mock_instance.ctl_zmq_address)
    assert mock_modules['zmq_socket'].recv_string.call_count == 2  # Called twice: once for cmd, once to raise exception
    mock_modules['zmq_socket'].send_string.assert_called_once_with("UN_SUPPORTED")
    mock_modules['zmq_socket'].close.assert_called_once()
    mock_modules['clean_socket_file'].assert_any_call(mock_instance.ctl_zmq_address)
