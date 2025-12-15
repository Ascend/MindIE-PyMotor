#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import pytest
import time
from unittest.mock import Mock, patch
from motor.engine_server.utils.proc import ProcManager


class TestProcManager:
    """Test ProcManager class"""

    @pytest.fixture
    def mock_psutil(self):
        """Mock psutil module"""
        with patch('motor.engine_server.utils.proc.psutil') as mock:
            yield mock

    @pytest.fixture
    def mock_time(self):
        """Mock time module"""
        with patch('motor.engine_server.utils.proc.time') as mock:
            yield mock

    @pytest.fixture
    def mock_logger(self):
        """Mock logger module"""
        with patch('motor.engine_server.utils.proc.logger') as mock:
            yield mock

    def test_initialization_with_non_existent_process(self, mock_psutil):
        """Test ProcManager initialization with non-existent process"""
        # Mock process does not exist
        mock_psutil.Process.side_effect = Exception("Process not found")

        main_pid = 9999

        # Verify ValueError is raised
        with pytest.raises(ValueError, match=f"process {main_pid} does not exist"):
            ProcManager(main_pid)

    def test_initialization(self, mock_psutil):
        """Test ProcManager initialization"""
        # Mock process exists
        mock_psutil.Process.return_value.is_running.return_value = True
        mock_psutil.Process.return_value.status.return_value = 'running'

        main_pid = 1234
        proc_manager = ProcManager(main_pid)

        # Verify _update_child_pids is not called during initialization
        assert mock_psutil.Process.return_value.children.call_count == 0
        # Verify initial state
        assert proc_manager.main_pid == main_pid
        assert proc_manager.child_pids == set()
        assert proc_manager._shutdown_triggered is False

    def test_is_process_exist(self, mock_psutil):
        """Test _is_process_exist method"""
        # Mock process exists
        mock_psutil.Process.return_value.is_running.return_value = True
        mock_psutil.Process.return_value.status.return_value = 'running'

        main_pid = 1234
        proc_manager = ProcManager(main_pid)

        # Test existing process
        assert proc_manager._is_process_exist(5678) is True

        # Test non-existent process
        mock_psutil.Process.side_effect = Exception("Process not found")
        assert proc_manager._is_process_exist(9999) is False

        # Test zombie process
        mock_psutil.Process.side_effect = None
        mock_psutil.Process.return_value.is_running.return_value = True
        mock_psutil.Process.return_value.status.return_value = mock_psutil.STATUS_ZOMBIE
        assert proc_manager._is_process_exist(5678) is False

    def test_get_all_children_pids(self, mock_psutil):
        """Test _get_all_children_pids method"""
        # Mock process with children
        mock_child1 = Mock(pid=1001)
        mock_child2 = Mock(pid=1002)
        mock_psutil.Process.return_value.children.return_value = [mock_child1, mock_child2]

        main_pid = 1234
        proc_manager = ProcManager(main_pid)

        # Test getting children pids
        children = proc_manager._get_all_children_pids(5678)
        assert len(children) == 2
        assert 1001 in children  # Check if PID is in the set
        assert 1002 in children  # Check if PID is in the set

        # Test exception handling
        mock_psutil.Process.side_effect = Exception("Error getting children")
        children = proc_manager._get_all_children_pids(5678)
        assert children == set()

    def test_update_child_pids(self, mock_psutil):
        """Test _update_child_pids method"""
        # Mock process with children
        mock_child1 = Mock(pid=1001)
        mock_child2 = Mock(pid=1002)
        mock_psutil.Process.return_value.children.return_value = [mock_child1, mock_child2]
        mock_psutil.Process.return_value.is_running.return_value = True
        mock_psutil.Process.return_value.status.return_value = 'running'

        main_pid = 1234
        proc_manager = ProcManager(main_pid)

        # Clear initial child_pids
        proc_manager.child_pids.clear()

        # Test updating child pids
        proc_manager._update_child_pids()
        assert len(proc_manager.child_pids) == 2

        # Test with shutdown triggered
        proc_manager._shutdown_triggered = True
        proc_manager.child_pids.clear()
        proc_manager._update_child_pids()
        assert len(proc_manager.child_pids) == 0

    def test_shutdown(self, mock_psutil, mock_logger):
        """Test shutdown method"""
        # Mock process exists
        mock_psutil.Process.return_value.is_running.return_value = True
        mock_psutil.Process.return_value.status.return_value = 'running'

        main_pid = 1234
        proc_manager = ProcManager(main_pid)

        # Reset mock call count after initialization
        mock_psutil.Process.reset_mock()
        
        # Add some child pids
        proc_manager.child_pids = {1001, 1002}

        # Test shutdown
        proc_manager.shutdown()

        # Verify shutdown is triggered
        assert proc_manager._shutdown_triggered is True
        # Verify kill process is called for each child and main pid
        assert mock_psutil.Process.call_count == 3  # 1 for main pid, 2 for children
        # Verify terminate is called
        assert mock_psutil.Process.return_value.terminate.call_count == 3
        # Verify wait is called
        assert mock_psutil.Process.return_value.wait.call_count == 3
        # Verify info log is written
        mock_logger.info.assert_called_once_with(f"Shutting down process manager {main_pid}")

        # Test shutdown is idempotent
        mock_psutil.Process.reset_mock()
        mock_logger.info.reset_mock()
        proc_manager.shutdown()
        assert mock_psutil.Process.call_count == 0
        mock_logger.info.assert_not_called()

    def test_kill_process(self, mock_psutil, mock_logger):
        """Test _kill_process method"""
        # Mock process exists
        mock_psutil.Process.return_value.is_running.return_value = True
        mock_psutil.Process.return_value.status.return_value = 'running'

        main_pid = 1234
        proc_manager = ProcManager(main_pid)

        # Test normal termination
        proc_manager._kill_process(5678)
        mock_psutil.Process.return_value.terminate.assert_called_once()
        mock_psutil.Process.return_value.wait.assert_called_once_with(timeout=3)
        mock_psutil.Process.return_value.kill.assert_not_called()

        # Test termination with timeout
        mock_psutil.Process.reset_mock()
        
        # Set up mock to raise TimeoutExpired when wait is called
        timeout_expired = Exception("TimeoutExpired")
        mock_psutil.TimeoutExpired = type('TimeoutExpired', (Exception,), {})
        mock_psutil.Process.return_value.wait.side_effect = mock_psutil.TimeoutExpired(3)
        
        proc_manager._kill_process(5678)
        mock_psutil.Process.return_value.terminate.assert_called_once()
        mock_psutil.Process.return_value.wait.assert_called_once_with(timeout=3)
        mock_psutil.Process.return_value.kill.assert_called_once()

        # Test exception handling
        mock_psutil.Process.reset_mock()
        mock_psutil.Process.side_effect = Exception("Error killing process")
        proc_manager._kill_process(5678)
        # Verify warning log is written
        mock_logger.warning.assert_called_once_with("process 5678 exited with error: Error killing process")

    def test_join(self, mock_psutil, mock_time, mock_logger):
        """Test join method"""
        # Mock process with children
        mock_child1 = Mock(pid=1001)
        mock_child2 = Mock(pid=1002)
        mock_psutil.Process.return_value.children.return_value = [mock_child1, mock_child2]
        mock_psutil.Process.return_value.is_running.return_value = True
        mock_psutil.Process.return_value.status.return_value = 'running'

        main_pid = 1234
        proc_manager = ProcManager(main_pid)

        # Test join with no child processes
        proc_manager._update_child_pids = Mock()
        proc_manager.child_pids = set()
        proc_manager.join()
        proc_manager._update_child_pids.assert_called_once()

        # Reset for next test
        proc_manager._shutdown_triggered = False
        proc_manager._update_child_pids.reset_mock()

        # Test join with dead children
        # Patch _update_child_pids to add some child pids
        with patch.object(proc_manager, '_update_child_pids') as mock_update:
            # Add child pids directly
            proc_manager.child_pids.add(1001)
            proc_manager.child_pids.add(1002)

            # Make _is_process_exist return False for child pids
            with patch.object(proc_manager, '_is_process_exist', return_value=False):
                with patch.object(proc_manager, 'shutdown') as mock_shutdown:
                    proc_manager.join()
                    mock_shutdown.assert_called_once()
                    # Verify logger.warning is called for each dead process
                    assert mock_logger.warning.call_count == 2
                    # Verify the log message contains the process IDs
                    mock_logger.warning.assert_any_call("process 1001 exited, prepare to shutdown")
                    mock_logger.warning.assert_any_call("process 1002 exited, prepare to shutdown")

        # Test join with shutdown already triggered
        proc_manager._shutdown_triggered = True

        with patch.object(proc_manager, 'shutdown') as mock_shutdown:
            proc_manager.join()
            mock_shutdown.assert_not_called()

        # Reset for next test
        proc_manager._shutdown_triggered = False
        proc_manager.child_pids.clear()

        # Test join with generic exception (instead of KeyboardInterrupt)
        with patch.object(proc_manager, 'shutdown') as mock_shutdown:
            with patch.object(proc_manager, '_is_process_exist', return_value=True):
                # Make _update_child_pids add child pids instead of clearing them
                def mock_update_child_pids():
                    # Instead of clearing, add child pids
                    proc_manager.child_pids.add(1001)
                    proc_manager.child_pids.add(1002)

                with patch.object(proc_manager, '_update_child_pids',
                                  side_effect=mock_update_child_pids) as mock_update:
                    # Make time.sleep raise a generic Exception
                    mock_time.sleep.side_effect = Exception("Some unexpected error")

                    proc_manager.join()

                    # Verify _update_child_pids is called at the beginning of join
                    mock_update.assert_called_once()
                    # Verify shutdown is called when any exception is raised
                    mock_shutdown.assert_called_once()
                    # Verify error log is written
                    mock_logger.error.assert_called_once_with("exception occur while join: Some unexpected error")

        # Test join with KeyboardInterrupt by mocking it as a custom exception
        # This avoids interrupting pytest itself
        proc_manager._shutdown_triggered = False
        proc_manager.child_pids.clear()
        mock_logger.reset_mock()
        mock_time.reset_mock()

        with patch.object(proc_manager, 'shutdown') as mock_shutdown:
            with patch.object(proc_manager, '_is_process_exist', return_value=True):
                # Make _update_child_pids add child pids instead of clearing them
                def mock_update_child_pids():
                    proc_manager.child_pids.add(1001)
                    proc_manager.child_pids.add(1002)

                with patch.object(proc_manager, '_update_child_pids',
                                  side_effect=mock_update_child_pids) as mock_update:
                    # Mock KeyboardInterrupt as a custom exception to avoid pytest interruption
                    class MockKeyboardInterrupt(Exception):
                        pass
                    
                    # Replace KeyboardInterrupt with our mock class in the module
                    with patch('motor.engine_server.utils.proc.KeyboardInterrupt', MockKeyboardInterrupt):
                        # Make time.sleep raise our mock KeyboardInterrupt
                        mock_time.sleep.side_effect = MockKeyboardInterrupt()

                        proc_manager.join()

                        # Verify _update_child_pids is called at the beginning of join
                        mock_update.assert_called_once()
                        # Verify shutdown is called when KeyboardInterrupt is raised
                        mock_shutdown.assert_called_once()
                        # Verify error log is written
                        mock_logger.error.assert_called_once()
