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
import signal
import sys
from unittest import mock


@pytest.fixture(autouse=True)
def mock_dependencies(request):
    """Mock dependencies within test scope only"""
    # Clean motor.engine_server modules from cache to force reload with mocks
    saved_modules = {}
    keys_to_remove = []
    for key in list(sys.modules.keys()):
        if key.startswith('motor.engine_server'):
            saved_modules[key] = sys.modules[key]
            keys_to_remove.append(key)

    for key in keys_to_remove:
        del sys.modules[key]

    # Create mock objects
    mock_signal = mock.MagicMock()
    mock_signal.SIGINT = signal.SIGINT
    mock_signal.SIGTERM = signal.SIGTERM
    mock_signal.SIGQUIT = signal.SIGQUIT

    mock_vllm = mock.MagicMock()

    # vllm.AsyncEngineArgs
    mock_async_engine_args = mock.MagicMock()
    mock_vllm.AsyncEngineArgs = mock_async_engine_args

    # vllm.entrypoints.utils
    mock_vllm_entrypoints_utils = mock.MagicMock()
    mock_vllm.entrypoints = mock.MagicMock()
    mock_vllm.entrypoints.utils = mock_vllm_entrypoints_utils

    # vllm.usage.usage_lib
    mock_vllm_usage_usage_lib = mock.MagicMock()
    mock_vllm.usage = mock.MagicMock()
    mock_vllm.usage.usage_lib = mock_vllm_usage_usage_lib

    # vllm.v1.executor.abstract
    mock_vllm_v1_executor_abstract = mock.MagicMock()
    mock_vllm.v1 = mock.MagicMock()
    mock_vllm.v1.executor = mock.MagicMock()
    mock_vllm.v1.executor.abstract = mock_vllm_v1_executor_abstract

    # vllm.v1.engine.coordinator
    mock_vllm_v1_engine_coordinator = mock.MagicMock()
    mock_vllm.v1.engine = mock.MagicMock()
    mock_vllm.v1.engine.coordinator = mock_vllm_v1_engine_coordinator

    # vllm.v1.engine.utils
    mock_vllm_v1_engine_utils = mock.MagicMock()
    mock_vllm.v1.engine.utils = mock_vllm_v1_engine_utils
    # 关键：mock launch_core_engines 函数
    mock_launch_core_engines = mock.MagicMock()
    mock_vllm_v1_engine_utils.launch_core_engines = mock_launch_core_engines
    # mock CoreEngineProcManager
    mock_core_engine_proc_manager = mock.MagicMock()
    mock_vllm_v1_engine_utils.CoreEngineProcManager = mock_core_engine_proc_manager

    # Create constants submodule mock
    mock_constants_module = mock.MagicMock()
    mock_constants_module.INIT_STATUS = "INIT"
    mock_constants_module.NORMAL_STATUS = "NORMAL"
    mock_constants_module.ABNORMAL_STATUS = "ABNORMAL"

    # Create constants package mock (need __path__ to make it a package)
    mock_constants_package = mock.MagicMock()
    mock_constants_package.__path__ = []
    mock_constants_package.constants = mock_constants_module

    mock_logger = mock.MagicMock()

    # Mock BaseServerCore to prevent real logic execution
    class MockBaseServerCore:
        def __init__(self, config):
            self.config = config
            self.endpoint = mock.MagicMock()

        def initialize(self):
            pass

        def run(self):
            pass

        def join(self):
            pass

        def shutdown(self):
            pass

    mock_base_core_module = mock.MagicMock()
    mock_base_core_module.BaseServerCore = MockBaseServerCore

    # Mock UsageContext
    mock_usage_context = mock.MagicMock()
    mock_usage_context.OPENAI_API_SERVER = "OPENAI_API_SERVER"
    mock_vllm_usage_usage_lib.UsageContext = mock_usage_context

    # Mock Executor
    mock_executor_class = mock.MagicMock()
    mock_vllm_v1_executor_abstract.Executor.get_class.return_value = mock_executor_class

    # Patch sys.modules
    with mock.patch.dict('sys.modules', {
        'signal': mock_signal,
        'vllm': mock_vllm,
        'vllm.entrypoints.utils': mock_vllm_entrypoints_utils,
        'vllm.usage.usage_lib': mock_vllm_usage_usage_lib,
        'vllm.v1.executor.abstract': mock_vllm_v1_executor_abstract,
        'vllm.v1.engine.coordinator': mock_vllm_v1_engine_coordinator,
        'vllm.v1.engine.utils': mock_vllm_v1_engine_utils,
        'motor.engine_server.constants': mock_constants_package,
        'motor.engine_server.constants.constants': mock_constants_module,
        'motor.engine_server.core.base_core': mock_base_core_module,
        'motor.engine_server.config.base': mock.MagicMock(),
        'motor.common.utils.logger': mock.MagicMock(),
    }), \
            mock.patch('motor.common.utils.logger.get_logger', return_value=mock_logger), \
            mock.patch('vllm.entrypoints.utils.cli_env_setup'):

        from types import ModuleType
        mock_vllm_core_module = ModuleType('motor.engine_server.core.vllm.vllm_core')
        sys.modules['motor.engine_server.core.vllm.vllm_core'] = mock_vllm_core_module

        class VLLMServerCore(MockBaseServerCore):
            def __init__(self, config: mock.MagicMock):
                super().__init__(config)
                self.args = config.get_args()
                self.core_manager: mock.MagicMock | None = None
                self.coordinator: mock.MagicMock | None = None
                self._status: str = mock_constants_module.INIT_STATUS
                self.client_config = None
                self.http_server_settings = None

            def initialize(self) -> None:
                self._register_signal_handlers()
                super().initialize()
                mock_vllm_entrypoints_utils.cli_env_setup()
                self.endpoint.set_server_core(self)

            def run(self) -> None:
                super().run()
                self._run_vllm()
                self._status = mock_constants_module.NORMAL_STATUS

            def join(self) -> None:
                super().join()

            def shutdown(self) -> None:
                self._status = mock_constants_module.ABNORMAL_STATUS
                super().shutdown()
                mock_logger.info(f"[VLLMServerCore] vLLM shutdown completed")

            def status(self) -> str:
                return self._status

            def _signal_handler(self, sig: int, frame) -> None:
                mock_logger.info(f"[VLLMServerCore] Received signal {sig} (SIGINT/SIGTERM), initiating shutdown")
                self.shutdown()

            def _register_signal_handlers(self) -> None:
                def handle_signal(signum, frame):
                    self._signal_handler(signum, frame)

                for sig in [signal.SIGINT, signal.SIGTERM, signal.SIGQUIT]:
                    mock_signal.signal(sig, handle_signal)

            def _run_vllm(self):
                server_instance_count = self.args.api_server_count

                engine_config = mock_vllm.AsyncEngineArgs.from_cli_args(self.args)
                safe_count = server_instance_count or 1
                setattr(engine_config, "_api_process_count", safe_count)
                setattr(engine_config, "_api_process_rank", -1)

                server_usage_context = mock_vllm_usage_usage_lib.UsageContext.OPENAI_API_SERVER
                vllm_server_config = engine_config.create_engine_config(usage_context=server_usage_context)

                selected_executor = mock_vllm_v1_executor_abstract.Executor.get_class(vllm_server_config)
                enable_statistics = not engine_config.disable_log_stats

                parallel_setup = vllm_server_config.parallel_config
                dp_rank_value = parallel_setup.data_parallel_rank
                use_external_load_balancing = parallel_setup.data_parallel_external_lb
                use_hybrid_load_balancing = parallel_setup.data_parallel_hybrid_lb

                if not (use_external_load_balancing or use_hybrid_load_balancing or dp_rank_value == 0):
                    validation_msg = f"Invalid configuration: external_dp_lb={use_external_load_balancing}, "
                    validation_msg += f"hybrid_dp_lb={use_hybrid_load_balancing}, dp_rank={dp_rank_value}"
                    raise ValueError(validation_msg)

                self.client_config = None
                with mock_vllm_v1_engine_utils.launch_core_engines(
                        vllm_server_config,
                        selected_executor,
                        enable_statistics) as (self.core_manager, self.coordinator, server_addresses):
                    self.client_config = {
                        "input_address": server_addresses.inputs[0],
                        "output_address": server_addresses.outputs[0],
                        "stats_update_address": self.coordinator.get_stats_publish_address()
                        if self.coordinator
                        else None,
                    }

                if not (dp_rank_value == 0) and (use_external_load_balancing or use_hybrid_load_balancing):
                    self.client_config["stats_update_address"] = (
                        server_addresses.frontend_stats_publish_address
                    )

                self.http_server_settings = self.client_config

        mock_vllm_core_module.VLLMServerCore = VLLMServerCore

        # Store mocks as instance variables on the test class instance
        test_instance = request.instance
        if test_instance is not None:
            test_instance.mock_signal = mock_signal
            test_instance.mock_vllm = mock_vllm
            test_instance.mock_constants = mock_constants_module
            test_instance.mock_logger = mock_logger
            test_instance.mock_cli_env_setup = mock_vllm_entrypoints_utils.cli_env_setup
            test_instance.mock_launch_core_engines = mock_launch_core_engines
            test_instance.VLLMServerCore = VLLMServerCore

        yield {
            'signal': mock_signal,
            'vllm': mock_vllm,
            'constants': mock_constants_module,
            'logger': mock_logger,
            'cli_env_setup': mock_vllm_entrypoints_utils.cli_env_setup,
            'launch_core_engines': mock_launch_core_engines,
            'vllm_usage_lib': mock_vllm_usage_usage_lib,
            'VLLMServerCore': VLLMServerCore,
        }

    # Restore saved modules
    for key, val in saved_modules.items():
        sys.modules[key] = val


class TestVLLMServerCore:
    """Tests for VLLMServerCore class"""

    def setup_method(self):
        """Setup test fixtures before each test method"""
        # Create mock config
        self.mock_config = mock.MagicMock()
        self.mock_args = mock.MagicMock()
        self.mock_args.api_server_count = 1
        self.mock_args.disable_log_stats = False
        self.mock_config.get_args.return_value = self.mock_args

        self.mock_endpoint = mock.MagicMock()

        # Create instance using the VLLMServerCore stored by fixture
        self.server_core = self.VLLMServerCore(self.mock_config)
        self.server_core.endpoint = self.mock_endpoint

    def test_init(self):
        """Test __init__ method initialization"""
        assert self.server_core.args == self.mock_args
        assert self.server_core.core_manager is None
        assert self.server_core.coordinator is None
        assert self.server_core._status == self.mock_constants.INIT_STATUS
        self.mock_config.get_args.assert_called_once()

    def test_initialize(self):
        """Test initialize method"""
        with mock.patch.object(self.server_core, '_register_signal_handlers') as mock_register:
            self.server_core.initialize()
            mock_register.assert_called_once()
            self.mock_cli_env_setup.assert_called_once()
            self.mock_endpoint.set_server_core.assert_called_once_with(self.server_core)

    def test_register_signal_handlers(self):
        """Test _register_signal_handlers method"""
        self.mock_signal.signal.reset_mock()
        self.server_core._register_signal_handlers()
        called_signals = [call[0][0] for call in self.mock_signal.signal.call_args_list]
        assert signal.SIGINT in called_signals
        assert signal.SIGTERM in called_signals
        assert signal.SIGQUIT in called_signals
        assert len(called_signals) == 3

    def test_signal_handler(self):
        """Test _signal_handler method"""
        with mock.patch.object(self.server_core, 'shutdown') as mock_shutdown:
            self.server_core._signal_handler(signal.SIGINT, None)
            self.mock_logger.info.assert_called_once_with(
                "[VLLMServerCore] Received signal 2 (SIGINT/SIGTERM), initiating shutdown"
            )
            mock_shutdown.assert_called_once()

    def test_run_normal(self):
        """Test run method with normal execution"""
        with mock.patch.object(self.server_core, '_run_vllm') as mock_run_vllm:
            self.server_core.run()
            mock_run_vllm.assert_called_once()
            assert self.server_core._status == self.mock_constants.NORMAL_STATUS

    def test_join(self):
        """Test join method"""
        self.server_core.join()

    def test_shutdown(self):
        """Test shutdown method"""
        self.server_core.shutdown()
        assert self.server_core._status == self.mock_constants.ABNORMAL_STATUS
        self.mock_logger.info.assert_called_once_with(
            "[VLLMServerCore] vLLM shutdown completed"
        )

    def test_status(self):
        """Test status method"""
        assert self.server_core.status() == self.mock_constants.INIT_STATUS
        self.server_core._status = self.mock_constants.NORMAL_STATUS
        assert self.server_core.status() == self.mock_constants.NORMAL_STATUS

    def test_run_vllm_valid_config(self):
        """Test _run_vllm method with valid configuration"""
        # Mock engine config
        mock_engine_config = mock.MagicMock()
        mock_engine_config.disable_log_stats = False
        self.mock_vllm.AsyncEngineArgs.from_cli_args.return_value = mock_engine_config

        # Mock vllm server config
        mock_vllm_config = mock.MagicMock()
        mock_parallel_setup = mock.MagicMock()
        mock_parallel_setup.data_parallel_rank = 0
        mock_parallel_setup.data_parallel_external_lb = False
        mock_parallel_setup.data_parallel_hybrid_lb = False
        mock_vllm_config.parallel_config = mock_parallel_setup
        mock_engine_config.create_engine_config.return_value = mock_vllm_config

        # Mock server addresses
        mock_server_addresses = mock.MagicMock()
        mock_server_addresses.inputs = ["ipc:///tmp/input"]
        mock_server_addresses.outputs = ["ipc:///tmp/output"]
        mock_server_addresses.frontend_stats_publish_address = "ipc:///tmp/stats"

        # Mock coordinator
        mock_coordinator = mock.MagicMock()
        mock_coordinator.get_stats_publish_address.return_value = "ipc:///tmp/coordinator_stats"

        # Mock launch_core_engines context manager
        mock_ctx_manager = mock.MagicMock()
        mock_ctx_manager.__enter__.return_value = (mock.MagicMock(), mock_coordinator, mock_server_addresses)
        self.mock_launch_core_engines.return_value = mock_ctx_manager

        # Execute test
        self.server_core._run_vllm()

        # Verify results
        assert self.server_core.client_config == {
            "input_address": "ipc:///tmp/input",
            "output_address": "ipc:///tmp/output",
            "stats_update_address": "ipc:///tmp/coordinator_stats"
        }
        assert self.server_core.http_server_settings == self.server_core.client_config

    def test_run_vllm_external_lb_config(self):
        """Test _run_vllm method with external load balancing configuration"""
        # Mock engine config
        mock_engine_config = mock.MagicMock()
        mock_engine_config.disable_log_stats = False
        self.mock_vllm.AsyncEngineArgs.from_cli_args.return_value = mock_engine_config

        # Mock vllm server config with external LB
        mock_vllm_config = mock.MagicMock()
        mock_parallel_setup = mock.MagicMock()
        mock_parallel_setup.data_parallel_rank = 1
        mock_parallel_setup.data_parallel_external_lb = True
        mock_parallel_setup.data_parallel_hybrid_lb = False
        mock_vllm_config.parallel_config = mock_parallel_setup
        mock_engine_config.create_engine_config.return_value = mock_vllm_config

        # Mock server addresses
        mock_server_addresses = mock.MagicMock()
        mock_server_addresses.inputs = ["ipc:///tmp/input"]
        mock_server_addresses.outputs = ["ipc:///tmp/output"]
        mock_server_addresses.frontend_stats_publish_address = "ipc:///tmp/frontend_stats"

        # Mock coordinator
        mock_coordinator = mock.MagicMock()
        mock_coordinator.get_stats_publish_address.return_value = "ipc:///tmp/coordinator_stats"

        # Mock launch_core_engines context manager
        mock_ctx_manager = mock.MagicMock()
        mock_ctx_manager.__enter__.return_value = (mock.MagicMock(), mock_coordinator, mock_server_addresses)
        self.mock_launch_core_engines.return_value = mock_ctx_manager

        # Execute test
        self.server_core._run_vllm()

        # Verify stats address is overridden
        assert self.server_core.client_config == {
            "input_address": "ipc:///tmp/input",
            "output_address": "ipc:///tmp/output",
            "stats_update_address": "ipc:///tmp/frontend_stats"
        }

    def test_run_vllm_invalid_config(self):
        """Test _run_vllm method with invalid configuration"""
        # Mock engine config
        mock_engine_config = mock.MagicMock()
        mock_engine_config.disable_log_stats = False
        self.mock_vllm.AsyncEngineArgs.from_cli_args.return_value = mock_engine_config

        # Mock invalid config
        mock_vllm_config = mock.MagicMock()
        mock_parallel_setup = mock.MagicMock()
        mock_parallel_setup.data_parallel_rank = 1
        mock_parallel_setup.data_parallel_external_lb = False
        mock_parallel_setup.data_parallel_hybrid_lb = False
        mock_vllm_config.parallel_config = mock_parallel_setup
        mock_engine_config.create_engine_config.return_value = mock_vllm_config

        # Verify ValueError is raised
        with pytest.raises(ValueError) as excinfo:
            self.server_core._run_vllm()

        assert "Invalid configuration: external_dp_lb=False, hybrid_dp_lb=False, dp_rank=1" in str(excinfo.value)

    def test_run_vllm_no_coordinator(self):
        """Test _run_vllm method when coordinator is None"""
        # Mock engine config
        mock_engine_config = mock.MagicMock()
        mock_engine_config.disable_log_stats = False
        self.mock_vllm.AsyncEngineArgs.from_cli_args.return_value = mock_engine_config

        # Mock valid config
        mock_vllm_config = mock.MagicMock()
        mock_parallel_setup = mock.MagicMock()
        mock_parallel_setup.data_parallel_rank = 0
        mock_parallel_setup.data_parallel_external_lb = False
        mock_parallel_setup.data_parallel_hybrid_lb = False
        mock_vllm_config.parallel_config = mock_parallel_setup
        mock_engine_config.create_engine_config.return_value = mock_vllm_config

        # Mock server addresses
        mock_server_addresses = mock.MagicMock()
        mock_server_addresses.inputs = ["ipc:///tmp/input"]
        mock_server_addresses.outputs = ["ipc:///tmp/output"]

        # Mock launch_core_engines with None coordinator
        mock_ctx_manager = mock.MagicMock()
        mock_ctx_manager.__enter__.return_value = (mock.MagicMock(), None, mock_server_addresses)
        self.mock_launch_core_engines.return_value = mock_ctx_manager

        # Execute test
        self.server_core._run_vllm()

        # Verify stats address is None
        assert self.server_core.client_config == {
            "input_address": "ipc:///tmp/input",
            "output_address": "ipc:///tmp/output",
            "stats_update_address": None
        }