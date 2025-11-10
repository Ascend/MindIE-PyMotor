#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import pytest
import psutil
from unittest.mock import Mock, patch
from motor.engine_server.core.worker import WorkerManager


@pytest.fixture
def mock_processes():
    """Create multiple mocked psutil.Process objects"""
    proc1 = Mock(spec=psutil.Process)
    proc1.pid = 100
    proc1.is_running.return_value = True

    proc2 = Mock(spec=psutil.Process)
    proc2.pid = 200
    proc2.is_running.return_value = True

    proc3 = Mock(spec=psutil.Process)
    proc3.pid = 300
    proc3.is_running.return_value = True

    return [proc1, proc2, proc3]


@pytest.fixture
def worker_manager(mock_processes):
    """Create a WorkerManager instance fixture"""
    return WorkerManager(process=mock_processes)


def test_initialization(worker_manager, mock_processes):
    """test WorkerManager should correctly set process list during initialization"""
    assert worker_manager.processes == mock_processes
    assert len(worker_manager.processes) == 3


def test_add_processes(worker_manager, mock_processes):
    """test add_processes should append new processes to existing process list when called"""
    new_proc1 = Mock(spec=psutil.Process)
    new_proc1.pid = 400
    new_proc2 = Mock(spec=psutil.Process)
    new_proc2.pid = 500
    new_procs = [new_proc1, new_proc2]

    worker_manager.add_processes(new_procs)
    assert len(worker_manager.processes) == 5  # 3 initial + 2 new
    assert all(proc in worker_manager.processes for proc in new_procs)


def test_get_exited_processes_running(worker_manager):
    """test get_exited_processes should return empty list when all processes are running"""
    exited = worker_manager.get_exited_processes()
    assert len(exited) == 0


def test_get_exited_processes_not_running(worker_manager, mock_processes):
    """test get_exited_processes should include processes that are not running when checked"""
    # Mark first process as not running
    mock_processes[0].is_running.return_value = False

    exited = worker_manager.get_exited_processes()
    assert len(exited) == 1
    assert exited[0] == mock_processes[0]


def test_get_exited_processes_no_such_process(worker_manager, mock_processes):
    """test get_exited_processes should include processes that throw NoSuchProcess exception when checked"""
    # Make second process throw NoSuchProcess
    mock_processes[1].is_running.side_effect = psutil.NoSuchProcess(200)

    exited = worker_manager.get_exited_processes()
    assert len(exited) == 1
    assert exited[0] == mock_processes[1]


def test_get_exited_processes_access_denied(worker_manager, mock_processes):
    """test get_exited_processes should exclude processes that throw AccessDenied exception when checked"""
    # Make third process throw AccessDenied
    mock_processes[2].is_running.side_effect = psutil.AccessDenied

    exited = worker_manager.get_exited_processes()
    assert len(exited) == 0  # Access denied processes should not be considered exited


def test_remove_exited_processes(worker_manager, mock_processes):
    """test remove_exited_processes should remove all exited processes from process list when called"""
    proc1, proc2, proc3 = mock_processes
    # Prepare two exited processes
    proc1.is_running.return_value = False
    proc2.is_running.side_effect = psutil.NoSuchProcess(200)

    # 3 processes before removal
    assert len(worker_manager.processes) == 3

    worker_manager.remove_exited_processes()

    # Should only have 1 process left (third one running normally)
    assert len(worker_manager.processes) == 1
    assert worker_manager.processes[0] == proc3


@patch("motor.engine_server.core.worker.run_log")
def test_close_processes_graceful(mock_run_log, worker_manager, mock_processes):
    """test close should terminate processes gracefully and log success when termination completes within timeout"""
    # Mock successful process wait
    for proc in mock_processes:
        proc.wait.return_value = None

    worker_manager.close()

    # Verify all processes were terminated
    for proc in mock_processes:
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once_with(timeout=30)
        assert proc.kill.call_count == 0  # Force kill should not be called

    # Verify log output
    expected_logs = [
        f"Process (PID: {proc.pid}) terminated gracefully"
        for proc in mock_processes
    ]
    for log in expected_logs:
        mock_run_log.info.assert_any_call(log)
    assert mock_run_log.info.call_count == 3

    # Verify process list is cleared
    assert len(worker_manager.processes) == 0


@patch("motor.engine_server.core.worker.run_log")
def test_close_processes_timeout(mock_run_log, worker_manager, mock_processes):
    """test close should force kill processes and log accordingly when termination exceeds timeout"""
    proc1, proc2, proc3 = mock_processes
    # Mock timeout for first process
    proc1.wait.side_effect = psutil.TimeoutExpired(30)
    # Mock successful wait for other processes
    proc2.wait.return_value = None
    proc3.wait.return_value = None

    worker_manager.close()

    # Verify first process was force killed
    proc1.kill.assert_called_once()
    # Verify other processes were terminated normally
    proc2.terminate.assert_called_once()
    proc3.terminate.assert_called_once()

    # Verify log output
    mock_run_log.info.assert_any_call("Process (PID: 100) did not terminate in time, attempting force kill")
    mock_run_log.info.assert_any_call("Process (PID: 100) force killed")
    assert mock_run_log.info.call_count >= 4  # Timeout logs + 2 normal termination logs


@patch("motor.engine_server.core.worker.run_log")
def test_close_processes_no_such_process(mock_run_log, worker_manager, mock_processes):
    """test close should log appropriate message when trying to terminate a non-existent process"""
    proc1, proc2, proc3 = mock_processes
    # Mock non-existent process for second process
    proc2.terminate.side_effect = psutil.NoSuchProcess(200)
    # Mock successful termination for other processes
    proc1.wait.return_value = None
    proc3.wait.return_value = None

    worker_manager.close()

    # Verify log output
    mock_run_log.info.assert_any_call("Process already exited (PID: 200)")
    # Verify other processes were terminated
    proc1.terminate.assert_called_once()
    proc3.terminate.assert_called_once()


@patch("motor.engine_server.core.worker.run_log")
def test_close_processes_access_denied(mock_run_log, worker_manager, mock_processes):
    """test close should log appropriate message when permission is denied to terminate a process"""
    proc1, proc2, proc3 = mock_processes
    # Mock access denied for third process
    proc3.terminate.side_effect = psutil.AccessDenied
    # Mock successful termination for other processes
    proc1.wait.return_value = None
    proc2.wait.return_value = None

    worker_manager.close()

    # Verify log output
    mock_run_log.info.assert_any_call("Permission denied to terminate process (PID: 300)")
    # Verify other processes were terminated
    proc1.terminate.assert_called_once()
    proc2.terminate.assert_called_once()


@patch("motor.engine_server.core.worker.run_log")
def test_close_processes_generic_exception(mock_run_log, worker_manager, mock_processes):
    """test close should log error message when unexpected exception occurs during termination"""
    proc1, proc2, proc3 = mock_processes
    # Mock unexpected exception for first process
    proc1.terminate.side_effect = Exception("Unexpected error")
    # Mock successful termination for other processes
    proc2.wait.return_value = None
    proc3.wait.return_value = None

    worker_manager.close()

    # Verify log output
    mock_run_log.info.assert_any_call("Error terminating process (PID: 100): Unexpected error")
    # Verify other processes were terminated
    proc2.terminate.assert_called_once()
    proc3.terminate.assert_called_once()


@patch("motor.engine_server.core.worker.run_log")
def test_close_empty_processes(mock_run_log):
    """test close should do nothing and log nothing when process list is empty"""
    manager = WorkerManager(process=[])
    manager.close()

    # No log calls should be made
    mock_run_log.info.assert_not_called()
    assert len(manager.processes) == 0


@patch("motor.engine_server.core.worker.run_log")
def test_close_with_exited_processes(mock_run_log, worker_manager, mock_processes):
    """test close should remove exited processes first before attempting termination when some processes have exited"""
    proc1, proc2, proc3 = mock_processes
    # Prepare one exited process
    proc1.is_running.return_value = False
    # Mock successful termination for running processes
    proc2.wait.return_value = None
    proc3.wait.return_value = None

    worker_manager.close()

    # Exited process should not be terminated
    proc1.terminate.assert_not_called()
    # Remaining two processes should be terminated normally
    proc2.terminate.assert_called_once()
    proc3.terminate.assert_called_once()

    # Verify process list is cleared
    assert len(worker_manager.processes) == 0