#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
#
# MindIE is licensed under both the Mulan PSL v2 and the Apache License, Version 2.0.
# You may choose to use this software under the terms of either license.
#
# ---------------------------------------------------------------------------
# Mulan PSL v2:
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
#
# Apache License, Version 2.0:
# You may obtain a copy of the License at:
#         http://www.apache.org/licenses/LICENSE-2.0
# ---------------------------------------------------------------------------
#
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the respective licenses for more details.

import contextlib
import signal
import threading
import time
from collections.abc import Iterator

import zmq
from vllm.config import ParallelConfig, VllmConfig
from vllm.v1.utils import get_engine_client_zmq_addr
from vllm.v1.engine.coordinator import DPCoordinator
from vllm.v1.engine.core import EngineCoreProc, DPEngineCoreProc
from vllm.v1.engine.utils import CoreEngineProcManager
from vllm.v1.engine.utils import CoreEngineActorManager
from vllm.v1.engine.utils import EngineZmqAddresses
from vllm.v1.engine.utils import wait_for_engine_startup
from vllm.v1.engine.utils import CoreEngine
from vllm.v1.executor.abstract import Executor
from vllm.transformers_utils.config import maybe_register_config_serialize_by_value

from motor.common.utils.logger import get_logger
from motor.engine_server.core.vllm.utils import clean_socket_file, get_control_socket, build_socket_file

logger = get_logger("engine_server")


@contextlib.contextmanager
def engine_server_launch_vllm_core_engines(
        vllm_config: VllmConfig,
        executor_class: type[Executor],
        log_stats: bool,
        num_api_servers: int = 1
) -> Iterator[
    tuple[
        CoreEngineProcManager | CoreEngineActorManager | None,
        DPCoordinator | None,
        EngineZmqAddresses
    ]
]:
    parallel_cfg = vllm_config.parallel_config 
    local_engine_num = parallel_cfg.data_parallel_size_local
    local_rank_start = parallel_cfg.data_parallel_rank_local
    master_ip = parallel_cfg.data_parallel_master_ip
    dp_rank_idx = parallel_cfg.data_parallel_rank
    data_parallel_size = parallel_cfg.data_parallel_size
    enable_expert_parallel = parallel_cfg.enable_expert_parallel
    
    is_local_only = (
            parallel_cfg.data_parallel_hybrid_lb
            or parallel_cfg.data_parallel_external_lb
    )

    is_offline = local_rank_start is not None

    client_use_local = (
            is_offline or is_local_only or (local_engine_num == data_parallel_size)
    )

    zmq_addresses = EngineZmqAddresses(
        inputs=[
            get_engine_client_zmq_addr(client_use_local, master_ip)
            for _ in range(num_api_servers)
        ],
        outputs=[
            get_engine_client_zmq_addr(client_use_local, master_ip)
            for _ in range(num_api_servers)
        ],
    )

    need_coordinator = data_parallel_size > 1 and not is_offline and dp_rank_idx == 0
    dp_coordinator = None

    if need_coordinator:
        dp_coordinator = DPCoordinator(parallel_cfg)
        zmq_addresses.coordinator_input, zmq_addresses.coordinator_output = (
            dp_coordinator.get_engine_socket_addresses()
        )
        zmq_addresses.frontend_stats_publish_address = (
            dp_coordinator.get_stats_publish_address()
        )

        logger.info("EngineServer started DP Coordinator process (PID: %d)", dp_coordinator.proc.pid)

    if parallel_cfg.data_parallel_backend == "ray":
        logger.info("EngineServer starting ray-based data parallel backend")

        actor_manager = CoreEngineActorManager(
            vllm_config=vllm_config,
            log_stats=log_stats,
            executor_class=executor_class,
            addresses=zmq_addresses,
        )

        yield actor_manager, dp_coordinator, zmq_addresses
        return

    engines_for_handshake = []
    if is_offline:
        if local_engine_num != 1:
            raise ValueError(f"Expected local_engine_num to be 1 in offline mode, got {local_engine_num}")
        engines_for_handshake = [CoreEngine(index=dp_rank_idx, local=True)]
    elif dp_rank_idx == 0:
        engines_for_handshake = [CoreEngine(index=i, local=(i < local_engine_num)) for i in range(data_parallel_size)]
    else:
        if not is_local_only:
            raise RuntimeError("EngineServer attempting to launch core engines from dp_rank > 0, "
                               "but found internal DPLB, which is incompatible.")
        engines_for_handshake = [
            CoreEngine(index=i, local=True)
            for i in range(dp_rank_idx, dp_rank_idx + local_engine_num)
        ]

    handshake_local = is_offline or local_engine_num == data_parallel_size
    handshake_addr = get_engine_client_zmq_addr(
        handshake_local, master_ip, parallel_cfg.data_parallel_rpc_port
    )

    try:
        from vllm.utils.network_utils import get_open_zmq_ipc_path, zmq_socket_ctx
    except Exception:
        from vllm.utils import get_mp_context, get_open_zmq_ipc_path, zmq_socket_ctx
    client_handshake_addr = None
    local_handshake_addr = handshake_addr
    if is_local_only and dp_rank_idx > 0:
        if handshake_local:
            raise RuntimeError("handshake_local must be False when is_local_only and dp_rank_idx > 0")
        local_handshake_addr = get_open_zmq_ipc_path()
        client_handshake_addr = local_handshake_addr

    with zmq_socket_ctx(local_handshake_addr, zmq.ROUTER, bind=True) as handshake_sock:

        local_engine_mgr = None
        if local_engine_num > 0:
            local_engine_mgr = CoreEngineProcManager(
                EngineServerEngineCoreProc.engine_server_run_engine_core,
                local_start_index=local_rank_start or 0,
                client_handshake_address=client_handshake_addr,
                start_index=dp_rank_idx,
                executor_class=executor_class,
                log_stats=log_stats,
                handshake_address=handshake_addr,
                local_client=True,
                local_engine_count=local_engine_num,
                vllm_config=vllm_config,
            )

        yield local_engine_mgr, dp_coordinator, zmq_addresses

        import pkg_resources
        vllm_version = pkg_resources.get_distribution('vllm').version
        if pkg_resources.parse_version(vllm_version) >= pkg_resources.parse_version("0.15.0"):
            wait_for_engine_startup(
                handshake_sock,
                zmq_addresses,
                engines_for_handshake,
                parallel_cfg,
                True if data_parallel_size > 1 and enable_expert_parallel else False,
                vllm_config.cache_config,
                local_engine_mgr,
                dp_coordinator.proc if dp_coordinator else None,
            )
        else:
            wait_for_engine_startup(
                handshake_sock,
                zmq_addresses,
                engines_for_handshake,
                parallel_cfg,
                vllm_config.cache_config,
                local_engine_mgr,
                dp_coordinator.proc if dp_coordinator else None,
            )


class EngineServerEngineCoreProc(EngineCoreProc):
    def __init__(
            self,
            ctl_zmq_address: str,
            vllm_config: VllmConfig,
            local_client: bool,
            handshake_address: str,
            executor_class: type[Executor],
            log_stats: bool,
            client_handshake_address: str | None = None,
    ):
        self.ctl_zmq_address = ctl_zmq_address
        self._control_thread = threading.Thread(
            target=self._busy_listen,
            name="engine_server_engine_ctl_thread",
            daemon=True
        )
        self._control_thread.start()
        super().__init__(vllm_config, local_client, handshake_address,
                         executor_class, log_stats, client_handshake_address)

    @staticmethod
    def engine_server_run_engine_core(*args, dp_rank: int = 0, local_dp_rank: int = 0, **kwargs):
        need_shutdown = False

        maybe_register_config_serialize_by_value()

        def handle_signal(signum, frame):
            nonlocal need_shutdown
            if not need_shutdown:
                need_shutdown = True
                raise Exception("receive exit signal, will shutdown engine core")

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        engine_instance: EngineCoreProc | None = None
        try:
            try:
                from vllm.utils.system_utils import decorate_logs, set_process_title
            except Exception:
                from vllm.utils import decorate_logs, set_process_title

            kwargs["ctl_zmq_address"] = get_control_socket(dp_rank)

            parallel_cfg: ParallelConfig = kwargs["vllm_config"].parallel_config

            if parallel_cfg.data_parallel_size > 1 or dp_rank > 0:
                set_process_title("EngineCore", f"DP{dp_rank}")
                decorate_logs()

                parallel_cfg.data_parallel_rank = dp_rank
                parallel_cfg.data_parallel_rank_local = local_dp_rank

                engine_instance = EngineServerDPEngineCoreProc(*args, **kwargs)
            else:
                set_process_title("EngineCore")
                decorate_logs()

                engine_instance = EngineServerEngineCoreProc(*args, **kwargs)

            engine_instance.run_busy_loop()

        except Exception as err:
            if engine_instance is None:
                logger.exception("EngineCore failed to start.")
            else:
                logger.exception("EngineCore encountered a fatal error.")
                engine_instance.output_queue.put_nowait(EngineCoreProc.ENGINE_CORE_DEAD)
                engine_instance.output_thread.join(timeout=5.0)
                if engine_instance.output_thread.is_alive():
                    logger.fatal(
                        "vLLM shutdown signal from EngineCore failed "
                        "to send. Please report this issue."
                    )
            raise err
        finally:
            if engine_instance is not None:
                engine_instance.shutdown()

    def _busy_listen(self):
        clean_socket_file(self.ctl_zmq_address)
        build_socket_file(self.ctl_zmq_address)
        context = zmq.Context()
        socket = context.socket(zmq.REP)
        socket.bind(self.ctl_zmq_address)
        logger.info(f"engine core control server listen on %s {self.ctl_zmq_address}")

        try:
            while True:
                cmd = socket.recv_string()
                logger.info(f"dp engine core control server received {cmd}")
                # now just return UN_SUPPORTED response
                socket.send_string("UN_SUPPORTED")
                time.sleep(1)
        except Exception as e:
            logger.exception(f"engine core control server occur exception: {e}")
        finally:
            socket.close()
            context.term()
            clean_socket_file(self.ctl_zmq_address)


class EngineServerDPEngineCoreProc(DPEngineCoreProc):
    def __init__(
            self,
            ctl_zmq_address: str,
            vllm_config: VllmConfig,
            local_client: bool,
            handshake_address: str,
            executor_class: type[Executor],
            log_stats: bool,
            client_handshake_address: str | None = None,
    ):
        self.ctl_zmq_address = ctl_zmq_address
        self._control_thread = threading.Thread(
            target=self._busy_listen,
            name="engine_server_dp_engine_ctl_thread",
            daemon=True
        )
        self._control_thread.start()
        super().__init__(vllm_config, local_client, handshake_address,
                         executor_class, log_stats, client_handshake_address)

    def _busy_listen(self):
        clean_socket_file(self.ctl_zmq_address)
        build_socket_file(self.ctl_zmq_address)
        context = zmq.Context()
        socket = context.socket(zmq.REP)
        socket.bind(self.ctl_zmq_address)
        logger.info(f"dp engine core control server listen on %s {self.ctl_zmq_address}")

        try:
            while True:
                cmd = socket.recv_string()
                logger.info(f"dp engine core control server received {cmd}")
                # now just return UN_SUPPORTED response
                socket.send_string("UN_SUPPORTED")

        except Exception as e:
            logger.exception(f"dp engine core control server occur exception: {e}")
        finally:
            socket.close()
            context.term()
            clean_socket_file(self.ctl_zmq_address)
