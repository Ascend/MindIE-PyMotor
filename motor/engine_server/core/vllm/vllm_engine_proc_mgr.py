#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import argparse
from multiprocessing.process import BaseProcess
from multiprocessing import connection
from typing import Optional, Any

import vllm
from vllm.entrypoints.cli.serve import run_api_server_worker_proc
from vllm.entrypoints.openai.api_server import setup_server
from vllm.entrypoints.utils import cli_env_setup
from vllm.v1.executor.abstract import Executor
from vllm.v1.utils import APIServerProcessManager
from vllm.v1.engine.coordinator import DPCoordinator
from vllm.v1.engine.utils import CoreEngineProcManager
from vllm.v1.engine.utils import launch_core_engines
from vllm.usage.usage_lib import UsageContext

from motor.common.utils.logger import get_logger
from motor.engine_server.core.worker import WorkerManager
from motor.engine_server.utils.proc import get_child_processes
from motor.engine_server.constants import constants


logger = get_logger("engine_server")


class ProcManager:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.api_server_manager: Optional[APIServerProcessManager] = None
        self.worker_manager: Optional[WorkerManager] = None
        self.core_manager: Optional[CoreEngineProcManager] = None
        self.coordinator: Optional[DPCoordinator] = None
        self.status: str = constants.INIT_STATUS

    def initialize(self):
        cli_env_setup()
        self._apply_request_adaptor()

    def run(self):
        self._run_multi_server()
        self.status = constants.NORMAL_STATUS

    def join(self):
        sentinel_to_proc: dict[Any, BaseProcess] = {}
        for proc in self.api_server_manager.processes:
            sentinel_to_proc[proc.sentinel] = proc

        if self.coordinator:
            sentinel_to_proc[self.coordinator.proc.sentinel] = self.coordinator.proc

        for proc in self.core_manager.processes:
            sentinel_to_proc[proc.sentinel] = proc

        try:
            while sentinel_to_proc:
                ready_sentinels: list[Any] = connection.wait(sentinel_to_proc, timeout=5)
                # Process any terminated processes
                for sentinel in ready_sentinels:
                    proc = sentinel_to_proc.pop(sentinel)

                    # Check if process exited with error
                    if proc.exitcode != 0:
                        raise RuntimeError(
                            f"Process {proc.name} (PID: {proc.pid}) "
                            f"died with exit code {proc.exitcode}"
                        )

                if not self.worker_manager:
                    continue
                exited_processes = self.worker_manager.get_exited_processes()
                if len(exited_processes) > 0:
                    raise RuntimeError(f"some worker process exited, {exited_processes}")

        except KeyboardInterrupt:
            logger.warning("Received KeyboardInterrupt, shutting down all processes...")
        except Exception as e:
            logger.warning("Exception occurred while engine server: %s", str(e))
            raise
        finally:
            self.status = constants.ABNORMAL_STATUS
            logger.info("Terminating remaining processes ...")
            self.shutdown()

    def shutdown(self):
        logger.info("shutting down...")
        if self.api_server_manager:
            self.api_server_manager.close()
        if self.coordinator:
            self.coordinator.close()
        if self.core_manager:
            self.core_manager.close()
        if self.worker_manager:
            self.worker_manager.close()
        logger.info("shutdown complete.")

    def _init_worker_manager(self, size: int):
        if size <= 1:
            return # when size == 1, enginecore and worker in same process
        child_processes = get_child_processes(self.core_manager.processes)
        logger.info(f"worker processes is: {child_processes}")
        self.worker_manager = WorkerManager(child_processes)

    def _run_multi_server(self):
        server_instance_count = self.args.api_server_count
        bind_address, listening_socket = setup_server(self.args)
        
        engine_config = vllm.AsyncEngineArgs.from_cli_args(self.args)
        setattr(engine_config, "_api_process_count", server_instance_count)
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
        
        with launch_core_engines(vllm_server_config, selected_executor, enable_statistics, server_instance_count
                                  ) as (self.core_manager, self.coordinator, server_addresses):
            api_server_settings = dict(
                target_server_fn=run_api_server_worker_proc,
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
            
        worker_total_count = parallel_setup.pipeline_parallel_size * parallel_setup.tensor_parallel_size
        self._init_worker_manager(worker_total_count)

    def _apply_request_adaptor(self):
        if self.args.api_server_count > 0:
            self.args.middleware.append("motor.engine_server.core.vllm.vllm_adaptor.VllmMiddleware")
