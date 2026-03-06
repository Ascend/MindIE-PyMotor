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

import signal
import time
from typing import Optional

import requests
import vllm
from vllm.entrypoints.utils import cli_env_setup
from vllm.entrypoints.openai.api_server import setup_server
from vllm.usage.usage_lib import UsageContext
from vllm.v1.executor.abstract import Executor
from vllm.v1.utils import APIServerProcessManager
from vllm.v1.engine.coordinator import DPCoordinator
from vllm.v1.engine.utils import CoreEngineProcManager

from motor.common.utils.http_client import SafeHTTPSClient
from motor.engine_server.config.base import IConfig
from motor.engine_server.core.base_core import BaseServerCore
from motor.common.utils.logger import get_logger
from motor.engine_server.constants import constants
from motor.engine_server.core.vllm.launch_engine import engine_server_launch_vllm_core_engines
from motor.engine_server.core.vllm.launch_server import engine_server_run_api_server_worker_proc
from motor.engine_server.utils.proc import ProcManager

logger = get_logger("engine_server")


class VLLMServerCore(BaseServerCore):
    def __init__(self, config: IConfig):
        super().__init__(config)
        self.args = config.get_args()
        self.api_server_manager: Optional[APIServerProcessManager] = None
        self.core_manager: Optional[CoreEngineProcManager] = None
        self.coordinator: Optional[DPCoordinator] = None
        self._status: str = constants.INIT_STATUS
        self.infer_tls_config = config.get_server_config().deploy_config.infer_tls_config

    def initialize(self) -> None:
        self._register_signal_handlers()
        super().initialize()
        cli_env_setup()
        self.data_controller.set_server_core(self)

    def run(self) -> None:
        super().run()
        self._run_multi_server()

    def join(self) -> None:
        super().join()

    def shutdown(self) -> None:
        self._status = constants.ABNORMAL_STATUS
        super().shutdown()
        logger.info(f"[VLLMServerCore] vLLM shutdown completed")

    def status(self) -> str:
        if super().is_shutting_down():
            return constants.ABNORMAL_STATUS
        return self._status

    def _signal_handler(self, sig: int, frame) -> None:
        logger.info(f"[VLLMServerCore] Received signal {sig} (SIGINT/SIGTERM), initiating shutdown")
        self.shutdown()

    def _register_signal_handlers(self) -> None:
        def handle_signal(signum, frame):
            self._signal_handler(signum, frame)

        for sig in [signal.SIGINT, signal.SIGTERM, signal.SIGQUIT]:
            signal.signal(sig, handle_signal)

    def _run_multi_server(self):
        server_instance_count = self.args.api_server_count
        bind_address, listening_socket = setup_server(self.args)

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

        with engine_server_launch_vllm_core_engines(
                vllm_server_config,
                selected_executor,
                enable_statistics,
                server_instance_count) as (self.core_manager, self.coordinator, server_addresses):
            api_server_settings = dict(
                target_server_fn=engine_server_run_api_server_worker_proc,
                listen_address=bind_address,
                sock=listening_socket,
                args=self.args,
                num_servers=server_instance_count,
                input_addresses=server_addresses.inputs,
                output_addresses=server_addresses.outputs,
                stats_update_address=self.coordinator.get_stats_publish_address()
                if self.coordinator
                else None,
            )
            should_initialize_manager = (dp_rank_value == 0 or
                                         not (use_external_load_balancing or use_hybrid_load_balancing))
            if should_initialize_manager:
                self.api_server_manager = APIServerProcessManager(**api_server_settings)

        if self.api_server_manager is None:
            api_server_settings["stats_update_address"] = (
                server_addresses.frontend_stats_publish_address
            )
            self.api_server_manager = APIServerProcessManager(**api_server_settings)

        if len(self.api_server_manager.processes) < 0:
            logger.error(f"Failed to start API server process, exit")
            self.status = constants.ABNORMAL_STATUS
            self.shutdown()
            return

        # triggered when weights loaded
        api_server_pid = self.api_server_manager.processes[0].pid
        logger.info(f"API server process pid: {api_server_pid}")

        retry = 0
        while self._status == constants.INIT_STATUS and retry < constants.API_READY_CHECK_TIMES:
            if super().is_shutting_down():
                self._status = constants.ABNORMAL_STATUS
                break
            self._check_api_server_ready()

            # check api server process
            if retry > constants.API_SERVER_PROC_CHECK_TIMES and not ProcManager.is_process_exist(api_server_pid):
                message = f"API server process {api_server_pid} is not alive"
                raise Exception(message)

            retry += 1
            time.sleep(1)

    def _check_api_server_ready(self):
        try:
            address = f"{self.args.host}:{self.args.port}"
            with SafeHTTPSClient(address=address, tls_config=self.infer_tls_config) as client:
                response = client.do_get("/health")
                response.raise_for_status()
            self._status = constants.NORMAL_STATUS
            logger.info(f"API server health check passed, status change to: {self._status}")
        except Exception as e:
            logger.debug(f"Failed to check API server health: {e}, try again")
