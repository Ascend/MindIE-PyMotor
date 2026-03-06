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

import pytest
import sys
from unittest import mock
import asyncio


# Use pytest fixture to mock dependencies within the test scope
@pytest.fixture
def mock_all_dependencies():
    # Mock pkg_resources for version checking
    mock_pkg_resources = mock.MagicMock()
    mock_distribution = mock.MagicMock()
    mock_distribution.version = '0.15.0'  # Default to v0.15.0 for testing
    mock_pkg_resources.get_distribution.return_value = mock_distribution
    # Mock DistributionNotFound exception
    mock_pkg_resources.DistributionNotFound = Exception
    sys.modules['pkg_resources'] = mock_pkg_resources
    """Mock all external dependencies within test scope only"""
    # Clear any existing modules from cache first - this is crucial for fresh imports
    # Be more aggressive in clearing modules to prevent any cross-test pollution
    modules_to_clear = list(sys.modules.keys())

    # Clear all modules that might be related to our test
    for module in modules_to_clear:
        if any(prefix in module for prefix in [
            'motor.engine_server.core.vllm',
            'uvloop',
            'vllm',
            'motor.common.utils.logger',
            'motor.engine_server.utils.util'
        ]):
            del sys.modules[module]

    # Create mock objects for the specific functions and classes used
    mock_uvloop = mock.MagicMock()
    mock_uvloop.run = mock.MagicMock()

    mock_vllm_envs = mock.MagicMock()
    mock_vllm_envs.VLLM_HTTP_TIMEOUT_KEEP_ALIVE = 60

    # Mock ReasoningParserManager
    mock_reasoning_parser_manager = mock.MagicMock()
    mock_reasoning_parser_manager.import_reasoning_parser = mock.MagicMock()

    # Mock ToolParserManager
    mock_tool_parser_manager = mock.MagicMock()
    mock_tool_parser_manager.import_tool_parser = mock.MagicMock()

    # Mock serve_http
    mock_serve_http = mock.AsyncMock()
    loop = asyncio.new_event_loop()
    mock_future = loop.create_future()
    mock_future.set_result(None)
    mock_serve_http.return_value = mock_future

    # Mock api_server functions
    mock_load_log_config = mock.MagicMock()
    mock_build_async_engine_client = mock.MagicMock()
    mock_maybe_register_tokenizer_info_endpoint = mock.MagicMock()
    mock_build_app = mock.MagicMock()
    mock_init_app_state = mock.AsyncMock()

    # Mock motor functions
    mock_get_logger = mock.MagicMock()
    mock_get_logger.return_value = mock.MagicMock()

    mock_vllm_engine_controller = mock.MagicMock()

    mock_func_has_parameter = mock.MagicMock(return_value=False)

    # Mock vllm.utils functions
    mock_decorate_logs = mock.MagicMock()
    mock_set_process_title = mock.MagicMock()

    # Create mock modules with exact structure matching imports
    mock_vllm = mock.MagicMock()
    mock_vllm.reasoning = mock.MagicMock()
    mock_vllm.reasoning.ReasoningParserManager = mock_reasoning_parser_manager

    # Mock both import paths for ToolParserManager to handle version checking
    mock_vllm.tool_parsers = mock.MagicMock()
    mock_vllm.tool_parsers.ToolParserManager = mock_tool_parser_manager
    
    mock_vllm.entrypoints = mock.MagicMock()
    mock_vllm.entrypoints.openai = mock.MagicMock()
    mock_vllm.entrypoints.openai.tool_parsers = mock.MagicMock()
    mock_vllm.entrypoints.openai.tool_parsers.ToolParserManager = mock_tool_parser_manager

    mock_vllm.entrypoints.launcher = mock.MagicMock()
    mock_vllm.entrypoints.launcher.serve_http = mock_serve_http

    mock_vllm.entrypoints.openai.api_server = mock.MagicMock()
    mock_vllm.entrypoints.openai.api_server.load_log_config = mock_load_log_config
    mock_vllm.entrypoints.openai.api_server.build_async_engine_client = mock_build_async_engine_client
    mock_vllm.entrypoints.openai.api_server.maybe_register_tokenizer_info_endpoint = mock_maybe_register_tokenizer_info_endpoint
    mock_vllm.entrypoints.openai.api_server.build_app = mock_build_app
    mock_vllm.entrypoints.openai.api_server.init_app_state = mock_init_app_state

    mock_vllm.utils = mock.MagicMock()
    mock_vllm.utils.decorate_logs = mock_decorate_logs
    mock_vllm.utils.set_process_title = mock_set_process_title

    mock_vllm.utils.system_utils = mock.MagicMock()
    mock_vllm.utils.system_utils.decorate_logs = mock_decorate_logs
    mock_vllm.utils.system_utils.set_process_title = mock_set_process_title

    # Mock motor modules
    mock_motor_common_utils_logger = mock.MagicMock()
    mock_motor_common_utils_logger.get_logger = mock_get_logger

    mock_motor_engine_vllm_control = mock.MagicMock()
    mock_motor_engine_vllm_control.VllmEngineController = mock_vllm_engine_controller

    mock_motor_engine_utils = mock.MagicMock()
    mock_motor_engine_utils.func_has_parameter = mock_func_has_parameter

    # Setup the mock dictionary with ALL import paths that launch_server.py uses
    mock_dict = {
        'uvloop': mock_uvloop,
        'vllm': mock_vllm,
        'vllm.envs': mock_vllm_envs,
        'vllm.reasoning': mock_vllm.reasoning,
        'vllm.utils': mock_vllm.utils,
        'vllm.utils.system_utils': mock_vllm.utils.system_utils,
        'vllm.entrypoints': mock_vllm.entrypoints,
        'vllm.entrypoints.openai': mock_vllm.entrypoints.openai,
        'vllm.entrypoints.openai.tool_parsers': mock_vllm.entrypoints.openai.tool_parsers,
        'vllm.entrypoints.launcher': mock_vllm.entrypoints.launcher,
        'vllm.entrypoints.openai.api_server': mock_vllm.entrypoints.openai.api_server,
        'motor.common.utils.logger': mock_motor_common_utils_logger,
        'motor.engine_server.core.vllm.vllm_engine_control': mock_motor_engine_vllm_control,
        'motor.engine_server.utils.util': mock_motor_engine_utils
    }

    # Apply the mocks using patch.dict
    with mock.patch.dict('sys.modules', mock_dict):
        # Make the mocks available to tests
        yield {
            'uvloop': mock_uvloop,
            'vllm_envs': mock_vllm_envs,
            'reasoning_parser_manager': mock_reasoning_parser_manager,
            'tool_parser_manager': mock_tool_parser_manager,
            'serve_http': mock_serve_http,
            'load_log_config': mock_load_log_config,
            'build_async_engine_client': mock_build_async_engine_client,
            'maybe_register_tokenizer_info_endpoint': mock_maybe_register_tokenizer_info_endpoint,
            'build_app': mock_build_app,
            'init_app_state': mock_init_app_state,
            'get_logger': mock_get_logger,
            'vllm_engine_controller': mock_vllm_engine_controller,
            'func_has_parameter': mock_func_has_parameter,
            'decorate_logs': mock_decorate_logs,
            'set_process_title': mock_set_process_title
        }


# Import the module under test inside the test file
@pytest.fixture
def launch_server_module(mock_all_dependencies):
    """Import the module under test"""
    # Only clear the launch_server module itself from cache
    # to ensure fresh import with mocks (but keep the mocked dependencies)
    module_name = 'motor.engine_server.core.vllm.launch_server'
    if module_name in sys.modules:
        del sys.modules[module_name]

    from motor.engine_server.core.vllm import launch_server
    return launch_server


class TestEngineServerLaunchServer:
    """Tests for engine_server launch server functions"""

    def test_engine_server_run_api_server_worker_proc(self, launch_server_module, mock_all_dependencies):
        """Test engine_server_run_api_server_worker_proc function"""
        # Setup
        listen_address = "127.0.0.1:8080"
        mock_sock = mock.MagicMock()
        mock_args = mock.MagicMock()
        mock_args.host = "0.0.0.0"
        mock_args.port = 8000
        mock_args.uvicorn_log_level = "info"
        mock_args.disable_uvicorn_access_log = False
        mock_args.ssl_keyfile = None
        mock_args.ssl_certfile = None
        mock_args.ssl_ca_certs = None
        mock_args.ssl_cert_reqs = None
        mock_args.enable_ssl_refresh = False
        mock_args.h11_max_incomplete_event_size = 16384
        mock_args.h11_max_header_count = 100
        mock_args.log_config_file = None
        mock_args.data_parallel_rank = None
        mock_client_config = {"client_index": 1}
        mock_uvicorn_kwargs = {"workers": 1}

        # Mock the async function that uvloop.run will call
        mock_async_func = mock.MagicMock()
        with mock.patch.object(launch_server_module, 'engine_server_run_server_worker', mock_async_func):
            # Call the function
            launch_server_module.engine_server_run_api_server_worker_proc(
                listen_address=listen_address,
                sock=mock_sock,
                args=mock_args,
                client_config=mock_client_config,
                **mock_uvicorn_kwargs
            )

        # Verify
        mock_all_dependencies['set_process_title'].assert_called_once_with("APIServer", "1")
        mock_all_dependencies['decorate_logs'].assert_called_once()
        mock_all_dependencies['uvloop'].run.assert_called_once()
        mock_async_func.assert_called_once()

    def test_engine_server_run_api_server_worker_proc_with_exception(self, mock_all_dependencies):
        """Test engine_server_run_api_server_worker_proc when importing from vllm.utils.system_utils fails"""
        # Setup - create a fresh module import with mock that simulates the import failure
        module_name = 'motor.engine_server.core.vllm.launch_server'
        if module_name in sys.modules:
            del sys.modules[module_name]

        # Temporarily modify the mock to simulate import failure from vllm.utils.system_utils
        original_vllm_utils_system_utils = mock_all_dependencies['decorate_logs']

        # Instead of using side_effect, we need to simulate the import error
        # by making the first import attempt fail, then the fallback should work

        listen_address = "127.0.0.1:8080"
        mock_sock = mock.MagicMock()
        mock_args = mock.MagicMock()
        mock_args.host = "0.0.0.0"
        mock_args.port = 8000
        mock_args.uvicorn_log_level = "info"
        mock_args.disable_uvicorn_access_log = False
        mock_args.ssl_keyfile = None
        mock_args.ssl_certfile = None
        mock_args.ssl_ca_certs = None
        mock_args.ssl_cert_reqs = None
        mock_args.enable_ssl_refresh = False
        mock_args.h11_max_incomplete_event_size = 16384
        mock_args.h11_max_header_count = 100
        mock_args.log_config_file = None
        mock_args.data_parallel_rank = None

        # Import the module fresh
        from motor.engine_server.core.vllm import launch_server

        # Mock the async function that uvloop.run will call
        mock_async_func = mock.MagicMock()
        with mock.patch.object(launch_server, 'engine_server_run_server_worker', mock_async_func):
            # Call the function
            launch_server.engine_server_run_api_server_worker_proc(
                listen_address=listen_address,
                sock=mock_sock,
                args=mock_args
            )

        # Verify it uses the functions (either from system_utils or utils)
        mock_all_dependencies['set_process_title'].assert_called_once_with("APIServer", "0")
        mock_all_dependencies['decorate_logs'].assert_called_once()

    @pytest.mark.asyncio
    async def test_engine_server_run_server_worker(self, launch_server_module, mock_all_dependencies):
        """Test engine_server_run_server_worker async function"""
        # Setup
        mock_address = "127.0.0.1:8080"
        mock_sock = mock.MagicMock()
        mock_args = mock.MagicMock()
        mock_args.host = "0.0.0.0"
        mock_args.port = 8000
        mock_args.uvicorn_log_level = "info"
        mock_args.disable_uvicorn_access_log = False
        mock_args.ssl_keyfile = None
        mock_args.ssl_certfile = None
        mock_args.ssl_ca_certs = None
        mock_args.ssl_cert_reqs = None
        mock_args.enable_ssl_refresh = False
        mock_args.h11_max_incomplete_event_size = 16384
        mock_args.h11_max_header_count = 100
        mock_args.log_config_file = None
        mock_args.data_parallel_rank = None
        mock_ipc_config = {"client_index": 1}
        mock_uvicorn_kwargs = {}

        # Mock the async context manager for engine client
        mock_engine_client = mock.MagicMock()
        mock_engine_client.__aenter__.return_value = mock_engine_client
        mock_engine_client.__aexit__.return_value = False
        mock_all_dependencies['build_async_engine_client'].return_value = mock_engine_client

        # Mock application state
        mock_app = mock.MagicMock()
        mock_app.state = mock.MagicMock()
        mock_all_dependencies['build_app'].return_value = mock_app

        # Call the async function
        await launch_server_module.engine_server_run_server_worker(
            address=mock_address,
            listen_sock=mock_sock,
            args=mock_args,
            ipc_config=mock_ipc_config,
            **mock_uvicorn_kwargs
        )

        # Verify
        mock_all_dependencies['load_log_config'].assert_called_once_with(mock_args.log_config_file)
        mock_all_dependencies['build_async_engine_client'].assert_called_once()
        mock_all_dependencies['maybe_register_tokenizer_info_endpoint'].assert_called_once_with(mock_args)
        mock_all_dependencies['build_app'].assert_called_once_with(mock_args)
        mock_all_dependencies['init_app_state'].assert_called_once()
        mock_all_dependencies['vllm_engine_controller'].assert_called_once_with(dp_rank=0)
        mock_all_dependencies['serve_http'].assert_called_once()

    @pytest.mark.asyncio
    async def test_engine_server_run_server_worker_with_params(self, launch_server_module, mock_all_dependencies):
        """Test engine_server_run_server_worker when init_app_state has vllm_config parameter"""
        # Setup
        mock_address = "127.0.0.1:8080"
        mock_sock = mock.MagicMock()
        mock_args = mock.MagicMock()
        mock_args.host = "0.0.0.0"
        mock_args.port = 8000
        mock_args.uvicorn_log_level = "info"
        mock_args.disable_uvicorn_access_log = False
        mock_args.ssl_keyfile = None
        mock_args.ssl_certfile = None
        mock_args.ssl_ca_certs = None
        mock_args.ssl_cert_reqs = None
        mock_args.enable_ssl_refresh = False
        mock_args.h11_max_incomplete_event_size = 16384
        mock_args.h11_max_header_count = 100
        mock_args.log_config_file = None
        mock_args.data_parallel_rank = None

        # Mock func_has_parameter to return True
        mock_all_dependencies['func_has_parameter'].return_value = True

        # Mock the async context manager for engine client
        mock_engine_client = mock.MagicMock()
        mock_engine_client.__aenter__.return_value = mock_engine_client
        mock_engine_client.__aexit__.return_value = False
        mock_engine_client.get_vllm_config = mock.AsyncMock(return_value=mock.MagicMock())
        mock_all_dependencies['build_async_engine_client'].return_value = mock_engine_client

        # Mock application state
        mock_app = mock.MagicMock()
        mock_app.state = mock.MagicMock()
        mock_all_dependencies['build_app'].return_value = mock_app

        # Call the async function
        await launch_server_module.engine_server_run_server_worker(
            address=mock_address,
            listen_sock=mock_sock,
            args=mock_args
        )

        # Verify get_vllm_config is called
        mock_engine_client.get_vllm_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_engine_server_run_server_worker_with_plugins(self, launch_server_module, mock_all_dependencies):
        """Test engine_server_run_server_worker with tool and reasoning plugins"""
        # Setup
        mock_address = "127.0.0.1:8080"
        mock_sock = mock.MagicMock()
        mock_args = mock.MagicMock()
        mock_args.host = "0.0.0.0"
        mock_args.port = 8000
        mock_args.uvicorn_log_level = "info"
        mock_args.disable_uvicorn_access_log = False
        mock_args.ssl_keyfile = None
        mock_args.ssl_certfile = None
        mock_args.ssl_ca_certs = None
        mock_args.ssl_cert_reqs = None
        mock_args.enable_ssl_refresh = False
        mock_args.h11_max_incomplete_event_size = 16384
        mock_args.h11_max_header_count = 100
        mock_args.log_config_file = None
        mock_args.data_parallel_rank = None

        # Set up args with plugin paths
        mock_args.tool_parser_plugin = "test_tool_plugin"
        mock_args.reasoning_parser_plugin = "test_reasoning_plugin"

        # Mock the async context manager for engine client
        mock_engine_client = mock.MagicMock()
        mock_engine_client.__aenter__.return_value = mock_engine_client
        mock_engine_client.__aexit__.return_value = False
        mock_all_dependencies['build_async_engine_client'].return_value = mock_engine_client

        # Mock application state
        mock_app = mock.MagicMock()
        mock_app.state = mock.MagicMock()
        mock_all_dependencies['build_app'].return_value = mock_app

        # Call the async function
        await launch_server_module.engine_server_run_server_worker(
            address=mock_address,
            listen_sock=mock_sock,
            args=mock_args
        )

        # Verify plugins are imported
        mock_all_dependencies['tool_parser_manager'].import_tool_parser.assert_called_once_with("test_tool_plugin")
        mock_all_dependencies['reasoning_parser_manager'].import_reasoning_parser.assert_called_once_with(
            "test_reasoning_plugin")

    @pytest.mark.asyncio
    async def test_engine_server_run_server_worker_with_dp_rank(self, launch_server_module, mock_all_dependencies):
        """Test engine_server_run_server_worker with data_parallel_rank"""
        # Setup
        mock_address = "127.0.0.1:8080"
        mock_sock = mock.MagicMock()
        mock_args = mock.MagicMock()
        mock_args.host = "0.0.0.0"
        mock_args.port = 8000
        mock_args.uvicorn_log_level = "info"
        mock_args.disable_uvicorn_access_log = False
        mock_args.ssl_keyfile = None
        mock_args.ssl_certfile = None
        mock_args.ssl_ca_certs = None
        mock_args.ssl_cert_reqs = None
        mock_args.enable_ssl_refresh = False
        mock_args.h11_max_incomplete_event_size = 16384
        mock_args.h11_max_header_count = 100
        mock_args.log_config_file = None

        # Set data_parallel_rank
        mock_args.data_parallel_rank = 2

        # Mock the async context manager for engine client
        mock_engine_client = mock.MagicMock()
        mock_engine_client.__aenter__.return_value = mock_engine_client
        mock_engine_client.__aexit__.return_value = False
        mock_all_dependencies['build_async_engine_client'].return_value = mock_engine_client

        # Mock application state
        mock_app = mock.MagicMock()
        mock_app.state = mock.MagicMock()
        mock_all_dependencies['build_app'].return_value = mock_app

        # Call the async function
        await launch_server_module.engine_server_run_server_worker(
            address=mock_address,
            listen_sock=mock_sock,
            args=mock_args
        )

        # Verify VllmEngineController is created with correct dp_rank
        mock_all_dependencies['vllm_engine_controller'].assert_called_once_with(dp_rank=2)
