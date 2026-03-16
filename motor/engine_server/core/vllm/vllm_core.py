# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import signal

import vllm
from vllm.entrypoints.utils import cli_env_setup
from vllm.usage.usage_lib import UsageContext
from vllm.v1.executor.abstract import Executor
from vllm.v1.engine.coordinator import DPCoordinator
from vllm.v1.engine.utils import CoreEngineProcManager, launch_core_engines

from motor.engine_server.config.base import IConfig
from motor.engine_server.core.base_core import BaseServerCore
from motor.engine_server.constants import constants
from motor.common.utils.logger import get_logger

logger = get_logger("engine_server")


class VLLMServerCore(BaseServerCore):
    def __init__(self, config: IConfig):
        super().__init__(config)
        self.args = config.get_args()
        self.core_manager: CoreEngineProcManager | None = None
        self.coordinator: DPCoordinator | None = None
        self._status: str = constants.INIT_STATUS

    def initialize(self) -> None:
        self._register_signal_handlers()
        super().initialize()
        cli_env_setup()
        self.endpoint.set_server_core(self)

    def run(self) -> None:
        super().run()
        self._run_vllm()
        self._status = constants.NORMAL_STATUS

    def join(self) -> None:
        super().join()

    def shutdown(self) -> None:
        self._status = constants.ABNORMAL_STATUS
        super().shutdown()
        logger.info(f"[VLLMServerCore] vLLM shutdown completed")

    def status(self) -> str:
        return self._status

    def _signal_handler(self, sig: int, frame) -> None:
        logger.info(f"[VLLMServerCore] Received signal {sig} (SIGINT/SIGTERM), initiating shutdown")
        self.shutdown()

    def _register_signal_handlers(self) -> None:
        def handle_signal(signum, frame):
            self._signal_handler(signum, frame)

        for sig in [signal.SIGINT, signal.SIGTERM, signal.SIGQUIT]:
            signal.signal(sig, handle_signal)

    def _run_vllm(self):
        server_instance_count = self.args.api_server_count

        engine_config = vllm.AsyncEngineArgs.from_cli_args(self.args)
        safe_count = server_instance_count or 1
        setattr(engine_config, "_api_process_count", safe_count)
        setattr(engine_config, "_api_process_rank", -1)

        server_usage_context = UsageContext.OPENAI_API_SERVER
        vllm_server_config = engine_config.create_engine_config(usage_context=server_usage_context)

        selected_executor = Executor.get_class(vllm_server_config)
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
        with launch_core_engines(
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
