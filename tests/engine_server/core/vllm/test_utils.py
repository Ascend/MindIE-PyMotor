#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import os
import tempfile
import shutil
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock

from motor.engine_server.core.vllm.utils import get_control_socket, clean_socket_file, build_socket_file
from motor.engine_server.constants import constants
from motor.engine_server.utils.validators import DirectoryValidator


@pytest.fixture
def temp_environment():
    temp_dir = tempfile.mkdtemp()
    original_temp_dir = os.environ.get('TEMP')
    os.environ['TEMP'] = temp_dir
    
    yield temp_dir
    
    if original_temp_dir is not None:
        os.environ['TEMP'] = original_temp_dir
    shutil.rmtree(temp_dir)


class TestUtils:
    """Tests for vLLM utils functions"""

    def test_get_control_socket(self):
        """Test get_control_socket function with different dp_rank values"""
        # Test with different dp_rank values
        assert get_control_socket(0) == "ipc:///tmp/pymotor/zmq/vllm_engine_ctl/dp_0_zmq_ipc.sock"
        assert get_control_socket(15) == "ipc:///tmp/pymotor/zmq/vllm_engine_ctl/dp_15_zmq_ipc.sock"
        

    @patch('os.path.exists')
    @patch('os.unlink')
    def test_clean_socket_file(self, mock_unlink, mock_exists):
        """Test clean_socket_file function with different scenarios"""
        # Test case 1: Socket file exists, should be deleted
        mock_exists.return_value = True
        clean_socket_file("ipc:///tmp/test_socket.sock")
        mock_exists.assert_called_once_with("/tmp/test_socket.sock")
        mock_unlink.assert_called_once_with("/tmp/test_socket.sock")
        
        # Test case 2: Socket file doesn't exist, should not be deleted
        mock_exists.reset_mock()
        mock_unlink.reset_mock()
        mock_exists.return_value = False
        clean_socket_file("ipc:///tmp/test_socket.sock")
        mock_exists.assert_called_once_with("/tmp/test_socket.sock")
        mock_unlink.assert_not_called()
        
        # Test case 3: Multiple calls with different paths
        mock_exists.reset_mock()
        mock_unlink.reset_mock()
        mock_exists.side_effect = [True, False]
        clean_socket_file("ipc:///tmp/socket1.sock")
        clean_socket_file("ipc:///tmp/socket2.sock")
        assert mock_exists.call_count == 2
        assert mock_unlink.call_count == 1
        mock_unlink.assert_called_once_with("/tmp/socket1.sock")


    def test_build_socket_file_integration(self, temp_environment):
        """Integration test for build_socket_file function"""
        # Integration test using temporary directory
        # This test actually creates directories and files on disk
        temp_dir = temp_environment
        temp_ipc_path = f"ipc://{temp_dir}/test_zmq/test_socket.sock"
        temp_sock_file = temp_ipc_path.replace("ipc://", "")
        temp_sock_path = os.path.dirname(temp_sock_file)
        
        try:
            # Test that the function creates the directory and file
            build_socket_file(temp_ipc_path)
            
            # Verify directory exists
            assert os.path.exists(temp_sock_path)
            # Verify file exists
            assert os.path.exists(temp_sock_file)
            
        finally:
            # Clean up
            if os.path.exists(temp_sock_file):
                os.unlink(temp_sock_file)
            if os.path.exists(temp_sock_path):
                os.rmdir(temp_sock_path)