#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import pytest
from unittest import mock

# Use pytest fixture to mock dependencies within the test scope
@pytest.fixture(autouse=True)
def mock_dependencies():
    """Mock dependencies within test scope only"""
    # Create mock for zmq
    mock_zmq = mock.MagicMock()
    mock_zmq.REQ = 1
    mock_zmq.SNDTIMEO = 2000
    mock_zmq.RCVTIMEO = 2000
    
    # Mock logger to prevent real logging
    mock_logger = mock.MagicMock()
    
    # Mock the modules only during test execution
    with mock.patch.dict('sys.modules', {
        'zmq': mock_zmq,
    }), \
         mock.patch('motor.common.utils.logger.get_logger', return_value=mock_logger):
        # Import the module under test inside the mock context
        global VllmEngineController, get_control_socket
        from motor.engine_server.core.vllm.vllm_engine_control import VllmEngineController
        from motor.engine_server.core.vllm.utils import get_control_socket
        
        # Make the mocks available to tests
        yield {
            'zmq': mock_zmq,
            'logger': mock_logger
        }


# Test class
class TestVllmEngineController:
    """Tests for VllmEngineController class"""

    @mock.patch("motor.engine_server.core.vllm.vllm_engine_control.get_control_socket")
    def test_init(self, mock_get_ctl_sock, mock_dependencies):
        """Test __init__ method"""
        # Setup
        mock_get_ctl_sock.return_value = "ipc:///tmp/test_ctl_sock"
        mock_zmq = mock_dependencies['zmq']
        
        # Create mock context and socket
        mock_context = mock.MagicMock()
        mock_socket = mock.MagicMock()
        mock_context.socket.return_value = mock_socket
        mock_zmq.Context = mock.MagicMock(return_value=mock_context)
        
        # Create instance
        controller = VllmEngineController(dp_rank=0)
        
        # Verify initialization
        mock_zmq.Context.assert_called_once()
        mock_context.socket.assert_called_once_with(mock_zmq.REQ)
        mock_socket.setsockopt.assert_any_call(mock_zmq.SNDTIMEO, 2)
        mock_socket.setsockopt.assert_any_call(mock_zmq.RCVTIMEO, 2)
        mock_get_ctl_sock.assert_called_once_with(0)
        mock_socket.connect.assert_called_once_with("ipc:///tmp/test_ctl_sock")
        
        # Cleanup
        controller.stop()

    @mock.patch("motor.engine_server.core.vllm.vllm_engine_control.get_control_socket")
    def test_control_success(self, mock_get_ctl_sock, mock_dependencies):
        """Test control method with successful response"""
        # Setup
        mock_get_ctl_sock.return_value = "ipc:///tmp/test_ctl_sock"
        mock_zmq = mock_dependencies['zmq']
        
        # Create mock context and socket
        mock_context = mock.MagicMock()
        mock_socket = mock.MagicMock()
        mock_context.socket.return_value = mock_socket
        mock_socket.recv_string.return_value = "SUCCESS"
        mock_zmq.Context = mock.MagicMock(return_value=mock_context)
        
        # Create instance and call control method
        controller = VllmEngineController(dp_rank=0)
        result = controller.control("TEST_CMD")
        
        # Verify
        mock_socket.send_string.assert_called_once_with("TEST_CMD")
        mock_socket.recv_string.assert_called_once()
        assert result == "SUCCESS"
        
        # Cleanup
        controller.stop()

    @mock.patch("motor.engine_server.core.vllm.vllm_engine_control.get_control_socket")
    def test_control_failure(self, mock_get_ctl_sock, mock_dependencies):
        """Test control method with exception"""
        # Setup
        mock_get_ctl_sock.return_value = "ipc:///tmp/test_ctl_sock"
        mock_zmq = mock_dependencies['zmq']
        
        # Create mock context and socket
        mock_context = mock.MagicMock()
        mock_socket = mock.MagicMock()
        mock_context.socket.return_value = mock_socket
        mock_socket.send_string.side_effect = Exception("Socket error")
        mock_zmq.Context = mock.MagicMock(return_value=mock_context)
        
        # Create instance and call control method
        controller = VllmEngineController(dp_rank=0)
        
        # Verify exception is raised
        with pytest.raises(Exception) as excinfo:
            controller.control("TEST_CMD")
        
        assert "Socket error" in str(excinfo.value)
        
        # Cleanup
        controller.stop()

    @mock.patch("motor.engine_server.core.vllm.vllm_engine_control.get_control_socket")
    def test_stop(self, mock_get_ctl_sock, mock_dependencies):
        """Test stop method"""
        # Setup
        mock_get_ctl_sock.return_value = "ipc:///tmp/test_ctl_sock"
        mock_zmq = mock_dependencies['zmq']
        
        # Create mock context and socket
        mock_context = mock.MagicMock()
        mock_socket = mock.MagicMock()
        mock_context.socket.return_value = mock_socket
        mock_zmq.Context = mock.MagicMock(return_value=mock_context)
        
        # Create instance and call stop method
        controller = VllmEngineController(dp_rank=0)
        controller.stop()
        
        # Verify
        mock_socket.close.assert_called_once()
        mock_context.term.assert_called_once()