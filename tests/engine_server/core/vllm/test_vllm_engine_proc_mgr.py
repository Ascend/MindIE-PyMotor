#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import pytest
import sys
import argparse
from unittest.mock import patch, MagicMock, Mock
from multiprocessing.process import BaseProcess
from typing import Optional, Any

from motor.engine_server.constants import constants


@pytest.fixture(autouse=True, scope="module")
def mock_modules():
    """Mock all necessary modules to intercept imports during testing"""
    # First check and save original modules (if they exist)
    original_modules = {}
    modules_to_mock = [
        # vllm related modules
        'vllm',
        'vllm.envs',
        'vllm.entrypoints.cli.serve',
        'vllm.entrypoints.openai.api_server',
        'vllm.entrypoints.utils',
        'vllm.v1.engine.core',
        'vllm.v1.executor.abstract',
        'vllm.v1.utils',
        'vllm.v1.engine.coordinator',
        'vllm.v1.engine.utils',
        'vllm.usage.usage_lib',
        'vllm.utils',
        # motor related modules
        'motor.engine_server.core.worker',
        'motor.engine_server.utils.proc'
    ]

    # Save original modules
    for module_name in modules_to_mock:
        if module_name in sys.modules:
            original_modules[module_name] = sys.modules[module_name]

    # Create mock module structure
    # vllm main module
    mock_vllm = Mock()
    mock_vllm.AsyncEngineArgs = Mock()
    mock_vllm.AsyncEngineArgs.from_cli_args = MagicMock(return_value=Mock())
    
    # vllm.envs
    mock_vllm_envs = Mock()
    
    # vllm.entrypoints.cli.serve
    mock_cli_serve = Mock()
    mock_cli_serve.run_api_server_worker_proc = MagicMock()
    
    # vllm.entrypoints.openai.api_server
    mock_openai_api_server = Mock()
    mock_openai_api_server.setup_server = MagicMock(return_value=("localhost:8000", MagicMock()))
    
    # vllm.entrypoints.utils
    mock_entrypoints_utils = Mock()
    mock_entrypoints_utils.cli_env_setup = MagicMock()
    
    # vllm.v1.engine.core
    mock_engine_core = Mock()
    
    # vllm.v1.executor.abstract
    mock_executor_abstract = Mock()
    mock_executor_abstract.Executor = Mock()
    mock_executor_abstract.Executor.get_class = MagicMock(return_value=Mock())
    
    # vllm.v1.utils
    mock_v1_utils = Mock()
    mock_v1_utils.APIServerProcessManager = MagicMock()
    
    # vllm.v1.engine.coordinator
    mock_coordinator = Mock()
    mock_coordinator.DPCoordinator = MagicMock()
    
    # vllm.v1.engine.utils
    mock_engine_utils = Mock()
    mock_engine_utils.CoreEngineProcManager = MagicMock()
    
    # Create mock context manager for launch_core_engines
    mock_core_manager = MagicMock()
    mock_coordinator_instance = MagicMock()
    mock_coordinator_instance.get_stats_publish_address = MagicMock(return_value="localhost:9000")
    mock_server_addresses = Mock()
    mock_server_addresses.inputs = ["input1", "input2"]
    mock_server_addresses.outputs = ["output1", "output2"]
    mock_server_addresses.frontend_stats_publish_address = "localhost:9001"
    
    mock_launch_core_engines = MagicMock()
    mock_launch_core_engines.return_value.__enter__.return_value = (
        mock_core_manager, mock_coordinator_instance, mock_server_addresses
    )
    mock_engine_utils.launch_core_engines = mock_launch_core_engines
    
    # vllm.usage.usage_lib
    mock_usage_lib = Mock()
    mock_usage_lib.UsageContext = Mock()
    mock_usage_lib.UsageContext.OPENAI_API_SERVER = "openai_api_server"
    
    # vllm.utils
    mock_vllm_utils = Mock()
    mock_vllm_utils.get_tcp_uri = MagicMock(return_value="tcp://localhost:8000")
    
    # motor.utils.logger
    mock_logger_module = Mock()
    mock_run_log = MagicMock()
    mock_logger_module.get_logger = MagicMock(return_value=mock_run_log)
    
    # motor.engine_server.core.worker
    mock_worker = Mock()
    mock_worker.WorkerManager = MagicMock()
    
    # motor.engine_server.utils.proc
    mock_proc = Mock()
    mock_proc.get_child_processes = MagicMock(return_value=[1, 2, 3])
    
    # Replace modules in sys.modules
    sys.modules['vllm'] = mock_vllm
    sys.modules['vllm.envs'] = mock_vllm_envs
    sys.modules['vllm.entrypoints.cli.serve'] = mock_cli_serve
    sys.modules['vllm.entrypoints.openai.api_server'] = mock_openai_api_server
    sys.modules['vllm.entrypoints.utils'] = mock_entrypoints_utils
    sys.modules['vllm.v1.engine.core'] = mock_engine_core
    sys.modules['vllm.v1.executor.abstract'] = mock_executor_abstract
    sys.modules['vllm.v1.utils'] = mock_v1_utils
    sys.modules['vllm.v1.engine.coordinator'] = mock_coordinator
    sys.modules['vllm.v1.engine.utils'] = mock_engine_utils
    sys.modules['vllm.usage.usage_lib'] = mock_usage_lib
    sys.modules['vllm.utils'] = mock_vllm_utils
    sys.modules['motor.common.utils.logger'] = mock_logger_module
    sys.modules['motor.engine_server.core.worker'] = mock_worker
    sys.modules['motor.engine_server.utils.proc'] = mock_proc
    
    # Build dictionary of mock objects to return
    mock_objects = {
        'mock_vllm': mock_vllm,
        'mock_cli_env_setup': mock_entrypoints_utils.cli_env_setup,
        'mock_setup_server': mock_openai_api_server.setup_server,
        'mock_run_api_server_worker_proc': mock_cli_serve.run_api_server_worker_proc,
        'mock_launch_core_engines': mock_launch_core_engines,
        'mock_APIServerProcessManager': mock_v1_utils.APIServerProcessManager,
        'mock_get_class': mock_executor_abstract.Executor.get_class,
        'mock_run_log': mock_run_log,
        'mock_WorkerManager': mock_worker.WorkerManager,
        'mock_get_child_processes': mock_proc.get_child_processes
    }
    
    # Provide mock objects to tests
    yield mock_objects
    
    # Cleanup: restore original modules or remove mock modules
    for module_name in modules_to_mock:
        if module_name in original_modules:
            sys.modules[module_name] = original_modules[module_name]
        elif module_name in sys.modules:
            del sys.modules[module_name]


@pytest.fixture
def mock_args():
    """Create mock argparse.Namespace for testing"""
    args = argparse.Namespace()
    args.api_server_count = 2
    args.middleware = []
    args.disable_log_stats = False
    return args


@pytest.fixture
def mock_process():
    """Create mock process for testing"""
    mock_proc = MagicMock(spec=BaseProcess)
    mock_proc.sentinel = "test_sentinel"
    mock_proc.name = "test_process"
    mock_proc.pid = 12345
    mock_proc.exitcode = 0
    return mock_proc


@pytest.fixture
def proc_manager(mock_args):
    """Create ProcManager instance for testing"""
    # Import after mocking to avoid actual vllm import
    from motor.engine_server.core.vllm.vllm_engine_proc_mgr import ProcManager
    return ProcManager(args=mock_args)


class TestProcManager:
    def test_initialization(self, proc_manager, mock_args):
        """Test ProcManager initialization"""
        assert proc_manager.args == mock_args
        assert proc_manager.api_server_manager is None
        assert proc_manager.worker_manager is None
        assert proc_manager.core_manager is None
        assert proc_manager.coordinator is None
        assert proc_manager.status == constants.INIT_STATUS
    
    def test_initialize(self, proc_manager, mock_modules):
        """Test ProcManager.initialize method"""
        mock_cli_env_setup = mock_modules['mock_cli_env_setup']
        
        proc_manager.initialize()
        
        mock_cli_env_setup.assert_called_once()
        # Check if middleware was added
        assert "motor.engine_server.core.vllm.vllm_adaptor.VllmMiddleware" in proc_manager.args.middleware
    
    def test_apply_request_adaptor_no_servers(self, mock_args, mock_modules):
        """Test _apply_request_adaptor when no servers are configured"""
        from motor.engine_server.core.vllm.vllm_engine_proc_mgr import ProcManager
        
        mock_args.api_server_count = 0
        mock_args.middleware = []
        proc_manager = ProcManager(args=mock_args)
        
        proc_manager._apply_request_adaptor()
        
        # Middleware should not be added
        assert "motor.engine_server.core.vllm.vllm_adaptor.VllmMiddleware" not in mock_args.middleware
    
    def test_apply_request_adaptor_with_servers(self, mock_args, mock_modules):
        """Test _apply_request_adaptor when servers are configured"""
        from motor.engine_server.core.vllm.vllm_engine_proc_mgr import ProcManager
        
        mock_args.api_server_count = 1
        mock_args.middleware = []
        proc_manager = ProcManager(args=mock_args)
        
        proc_manager._apply_request_adaptor()
        
        # Middleware should be added
        assert "motor.engine_server.core.vllm.vllm_adaptor.VllmMiddleware" in mock_args.middleware
    
        assert proc_manager.worker_manager is None
    
    @patch('motor.engine_server.core.vllm.vllm_engine_proc_mgr.vllm')
    @patch('motor.engine_server.core.vllm.vllm_engine_proc_mgr.UsageContext')
    @patch('motor.engine_server.core.vllm.vllm_engine_proc_mgr.Executor')
    @patch('motor.engine_server.core.vllm.vllm_engine_proc_mgr.launch_core_engines')
    @patch('motor.engine_server.core.vllm.vllm_engine_proc_mgr.setup_server')
    @patch('motor.engine_server.core.vllm.vllm_engine_proc_mgr.APIServerProcessManager')
    def test_run_multi_server_valid_config(
        self,
        mock_APIServerProcessManager,
        mock_setup_server,
        mock_launch_core_engines,
        mock_Executor,
        mock_UsageContext,
        mock_vllm,
        proc_manager,
        mock_modules
    ):
        """Test _run_multi_server with valid configuration"""
        # Setup mocks
        mock_setup_server.return_value = ("localhost:8000", MagicMock())
        
        mock_engine_args = MagicMock()
        mock_engine_args.disable_log_stats = False
        mock_vllm.AsyncEngineArgs.from_cli_args.return_value = mock_engine_args
        
        mock_engine_config = MagicMock()
        mock_engine_args.create_engine_config.return_value = mock_engine_config
        
        mock_parallel_config = MagicMock()
        mock_parallel_config.data_parallel_rank = 0
        mock_parallel_config.data_parallel_external_lb = False
        mock_parallel_config.data_parallel_hybrid_lb = False
        mock_parallel_config.pipeline_parallel_size = 1
        mock_parallel_config.tensor_parallel_size = 2
        mock_engine_config.parallel_config = mock_parallel_config
        
        mock_executor_class = MagicMock()
        mock_Executor.get_class.return_value = mock_executor_class
        
        mock_UsageContext.OPENAI_API_SERVER = "openai_api_server"
        
        # Create mock context manager
        mock_core_manager = MagicMock()
        mock_coordinator = MagicMock()
        mock_coordinator.get_stats_publish_address.return_value = "localhost:9000"
        mock_server_addresses = MagicMock()
        mock_server_addresses.inputs = ["input1"]
        mock_server_addresses.outputs = ["output1"]
        mock_server_addresses.frontend_stats_publish_address = "localhost:9001"
        
        mock_context_manager = MagicMock()
        mock_context_manager.__enter__.return_value = (
            mock_core_manager, mock_coordinator, mock_server_addresses
        )
        mock_launch_core_engines.return_value = mock_context_manager
        
        # Mock _init_worker_manager
        with patch.object(proc_manager, '_init_worker_manager') as mock_init_worker_manager:
            proc_manager._run_multi_server()
            
            # Verify setup_server was called
            mock_setup_server.assert_called_once_with(proc_manager.args)
            
            # Verify AsyncEngineArgs.from_cli_args was called
            mock_vllm.AsyncEngineArgs.from_cli_args.assert_called_once_with(proc_manager.args)
            
            # Verify attributes were set on engine_args
            assert hasattr(mock_engine_args, '_api_process_count')
            assert mock_engine_args._api_process_count == 2
            assert hasattr(mock_engine_args, '_api_process_rank')
            assert mock_engine_args._api_process_rank == -1
            
            # Verify create_engine_config was called
            mock_engine_args.create_engine_config.assert_called_once_with(usage_context="openai_api_server")
            
            # Verify Executor.get_class was called
            mock_Executor.get_class.assert_called_once_with(mock_engine_config)
            
            # Verify launch_core_engines was called
            mock_launch_core_engines.assert_called_once_with(
                mock_engine_config, mock_executor_class, True, 2
            )
            
            # Verify APIServerProcessManager was initialized
            mock_APIServerProcessManager.assert_called_once()
            
            # Verify _init_worker_manager was called
            mock_init_worker_manager.assert_called_once_with(2)
    
    
    @patch('motor.engine_server.core.vllm.vllm_engine_proc_mgr.ProcManager._run_multi_server')
    def test_run(self, mock_run_multi_server, proc_manager):
        """Test ProcManager.run method"""
        proc_manager.run()
        
        mock_run_multi_server.assert_called_once()
        assert proc_manager.status == "normal"
    
    @patch('motor.engine_server.core.vllm.vllm_engine_proc_mgr.ProcManager.shutdown')
    @patch('motor.engine_server.core.vllm.vllm_engine_proc_mgr.connection')
    def test_join_successful(self, mock_connection, mock_shutdown, proc_manager, mock_process, mock_modules):
        """Test ProcManager.join method with successful execution"""
        mock_run_log = mock_modules['mock_run_log']
        
        # Setup mock processes
        mock_connection.wait.return_value = []
        
        # Setup manager attributes
        proc_manager.api_server_manager = MagicMock()
        proc_manager.api_server_manager.processes = [mock_process]
        proc_manager.coordinator = MagicMock()
        proc_manager.coordinator.proc = mock_process
        proc_manager.core_manager = MagicMock()
        proc_manager.core_manager.processes = [mock_process]
        proc_manager.worker_manager = MagicMock()
        proc_manager.worker_manager.get_exited_processes.return_value = []
        
        # Make connection.wait raise KeyboardInterrupt after first call
        def side_effect(waitable, timeout):
            # First call returns empty list, second raises KeyboardInterrupt
            if not hasattr(side_effect, 'called'):
                side_effect.called = True
                return []
            raise KeyboardInterrupt()
        mock_connection.wait.side_effect = side_effect
        
        proc_manager.join()
        
        # Verify connection.wait was called
        assert mock_connection.wait.call_count == 2
        
        
        # Verify shutdown was called
        mock_shutdown.assert_called_once()
        
        # Verify status was updated
        assert proc_manager.status == "abnormal"
    
    
    def test_shutdown(self, proc_manager, mock_modules):
        """Test ProcManager.shutdown method"""
        mock_run_log = mock_modules['mock_run_log']
        
        # Setup mock managers
        mock_api_server_manager = MagicMock()
        mock_coordinator = MagicMock()
        mock_core_manager = MagicMock()
        mock_worker_manager = MagicMock()
        
        proc_manager.api_server_manager = mock_api_server_manager
        proc_manager.coordinator = mock_coordinator
        proc_manager.core_manager = mock_core_manager
        proc_manager.worker_manager = mock_worker_manager
        
        proc_manager.shutdown()
        
        # Verify log messages
        mock_run_log.info.assert_any_call("shutting down...")
        mock_run_log.info.assert_any_call("shutdown complete.")
        
        # Verify close methods were called
        mock_api_server_manager.close.assert_called_once()
        mock_coordinator.close.assert_called_once()
        mock_core_manager.close.assert_called_once()
        mock_worker_manager.close.assert_called_once()
    
    def test_shutdown_none_managers(self, proc_manager, mock_modules):
        """Test ProcManager.shutdown method with None managers"""
        mock_run_log = mock_modules['mock_run_log']
        
        # All managers are None
        proc_manager.api_server_manager = None
        proc_manager.coordinator = None
        proc_manager.core_manager = None
        proc_manager.worker_manager = None
        
        proc_manager.shutdown()
        
        # Verify log messages
        mock_run_log.info.assert_any_call("shutting down...")
        mock_run_log.info.assert_any_call("shutdown complete.")
        
    def test_init_worker_manager_size_less_or_equal_1(self, proc_manager, mock_modules):
        """Test _init_worker_manager method when size is less than or equal to 1"""
        mock_get_child_processes = mock_modules['mock_get_child_processes']
        mock_WorkerManager = mock_modules['mock_WorkerManager']
        
        # Reset mocks
        mock_get_child_processes.reset_mock()
        mock_WorkerManager.reset_mock()
        
        # Setup mock core_manager
        proc_manager.core_manager = MagicMock()
        proc_manager.core_manager.processes = [MagicMock()]
        
        # Call method with size=1
        proc_manager._init_worker_manager(size=1)
        
        # Verify get_child_processes was not called
        mock_get_child_processes.assert_not_called()
        
        # Verify WorkerManager was not initialized
        mock_WorkerManager.assert_not_called()
        
        # Verify worker_manager remains None
        assert proc_manager.worker_manager is None
        
    def test_init_worker_manager_size_greater_than_1(self, proc_manager, mock_modules):
        """Test _init_worker_manager method when size is greater than 1"""
        mock_get_child_processes = mock_modules['mock_get_child_processes']
        mock_WorkerManager = mock_modules['mock_WorkerManager']
        mock_run_log = mock_modules['mock_run_log']
        
        # Reset mocks
        mock_get_child_processes.reset_mock()
        mock_WorkerManager.reset_mock()
        mock_run_log.reset_mock()
        
        # Setup mock core_manager
        proc_manager.core_manager = MagicMock()
        proc_manager.core_manager.processes = [MagicMock(), MagicMock()]
        
        # Mock return value for get_child_processes
        child_processes = [1, 2, 3]
        mock_get_child_processes.return_value = child_processes
        
        # Call method with size=2
        proc_manager._init_worker_manager(size=2)
        
        # Verify get_child_processes was called
        mock_get_child_processes.assert_called_once_with(proc_manager.core_manager.processes)
        
        # Verify log message was logged
        mock_run_log.info.assert_called_once_with(f"worker processes is: {child_processes}")
        
        # Verify WorkerManager was initialized
        mock_WorkerManager.assert_called_once_with(child_processes)
        
        # Verify worker_manager was set
        assert proc_manager.worker_manager is not None
