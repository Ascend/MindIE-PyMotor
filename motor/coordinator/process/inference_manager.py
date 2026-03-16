# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import asyncio
import os
import socket
from multiprocessing import connection
from multiprocessing.process import BaseProcess
from typing import Any

import uvloop
import uvicorn
from fastapi import FastAPI, Request

from motor.common.utils.cert_util import CertUtil
from motor.common.utils.config_watcher import ConfigWatcher
from motor.common.utils.http_client import HTTPClientPool
from motor.common.utils.logger import get_logger, reconfigure_logging
from motor.config.coordinator import CoordinatorConfig, DeployMode
from motor.coordinator.api_server.inference_server import InferenceServer
from motor.coordinator.domain.request_manager import RequestManager
from motor.coordinator.process.base import BaseProcessManager
from motor.coordinator.process.utils import set_process_title
from motor.coordinator.scheduler.policy.kv_cache_affinity import TokenizerManager

logger = get_logger(__name__)


def run_inference_worker_proc(
    listen_address: tuple[str, int],
    sock: socket.socket,
    config: CoordinatorConfig,
    worker_index: int,
    **uvicorn_kwargs: Any
) -> None:
    """Entrypoint for individual Inference worker processes.

    Args:
        listen_address: Address to listen for client connections
        sock: Socket for client connections (shared between processes)
        config: Coordinator configuration
        worker_index: Index of this worker process
        **uvicorn_kwargs: Additional uvicorn configuration
    """
    inference_server = None  # Set before use so finally can safely disconnect

    # Reconfigure logging so this child process writes to the same log_file as daemon and Scheduler processes
    reconfigure_logging(config.logging_config)

    # Set process title
    set_process_title(name=str(worker_index))

    logger.info(f"Inference worker process {worker_index} starting (PID: {os.getpid()})")

    # D direct Worker metaserver: set worker port when CDP/PD separate and worker_metaserver_base_port > 0.
    # Single or multi worker: metaserver port is used to receive feedback from D instances.
    mp_cfg = config.inference_workers_config
    base_port = mp_cfg.worker_metaserver_base_port
    deploy_mode = config.scheduler_config.deploy_mode
    if (
        base_port > 0
        and deploy_mode in (DeployMode.CDP_SEPARATE, DeployMode.PD_SEPARATE, \
                DeployMode.PD_DISAGGREGATION_SINGLE_CONTAINER)
    ):
        config.worker_index = worker_index
        config.worker_metaserver_port = base_port + worker_index
        logger.info(
            "Worker %s: metaserver port enabled: %s (base=%s)",
            worker_index, config.worker_metaserver_port, base_port,
        )

    # Create RequestManager first, then InferenceServer (business plane only)
    request_manager = RequestManager(config)
    inference_server = InferenceServer(config, request_manager=request_manager)
    inference_server.setup_rate_limiting(config.rate_limit_config)

    worker_config_watcher = None

    # In multi-process: Worker watches config file so hot-reload reaches this process
    if config.config_path and os.path.exists(config.config_path):
        try:
            def _worker_config_updated() -> None:
                inference_server.update_config(config)
                request_manager.update_config(config)

            worker_config_watcher = ConfigWatcher(
                config_path=config.config_path,
                reload_callback=config.reload,
                config_update_callback=_worker_config_updated,
            )
            worker_config_watcher.start()
            logger.info(
                "Worker %s: config watcher started for hot-reload: %s",
                worker_index, config.config_path,
            )
        except Exception as e:
            logger.warning(
                "Worker %s: failed to start config watcher (hot-reload disabled): %s",
                worker_index, e,
            )

    # init TokenizerManager
    TokenizerManager(config)

    # Get the inference app and configure uvicorn
    app = inference_server.app
    config_kwargs = InferenceServer.create_base_uvicorn_config(
        app,
        config.http_config.coordinator_api_host,
        config.http_config.coordinator_api_infer_port,
    )
    inference_server.apply_timeout_to_config(config_kwargs)

    # Add SSL configuration if needed
    if config.infer_tls_config.enable_tls:
        ssl_context = CertUtil.create_ssl_context(tls_config=config.infer_tls_config)
        if ssl_context:
            config_kwargs["ssl"] = ssl_context

    # Create uvicorn config
    uvicorn_config = uvicorn.Config(**config_kwargs)
    uvicorn_config.load()

    # Create and run server(s)
    server = uvicorn.Server(uvicorn_config)
    metaserver_server = None
    if getattr(config, "worker_metaserver_port", None) is not None:
        #minimal metaserver app (only POST /v1/metaserver) on dedicated port
        metaserver_app = FastAPI(title="Inference Worker Metaserver")
        metaserver_app.state.request_manager = request_manager
        host_metaserver = config.http_config.coordinator_api_host  # so D (e.g. same pod) can reach this Worker
        port_metaserver = config.worker_metaserver_port

        @metaserver_app.post("/v1/metaserver")
        @inference_server.timeout_handler()
        async def metaserver_endpoint(request: Request):
            return await inference_server.handle_metaserver_request(request)

        metaserver_config_kwargs = InferenceServer.create_base_uvicorn_config(
            metaserver_app,
            host_metaserver,
            port_metaserver,
        )
        inference_server.apply_timeout_to_config(metaserver_config_kwargs)
        metaserver_uvicorn_config = uvicorn.Config(**metaserver_config_kwargs)
        metaserver_uvicorn_config.load()
        metaserver_server = uvicorn.Server(metaserver_uvicorn_config)
        logger.info(
            "Worker %s: metaserver listening on %s:%s (Scheme 3)",
            worker_index, host_metaserver, port_metaserver,
        )

    async def _run_servers():
        if metaserver_server is not None:
            await asyncio.gather(
                server.serve(sockets=[sock] if sock else None),
                metaserver_server.serve(),
            )
        else:
            await server.serve(sockets=[sock] if sock else None)

    try:
        # Run server with shared socket (and optionally metaserver on dedicated port)
        # Note: Multiple processes can share the same socket with SO_REUSEPORT
        # The OS kernel will distribute connections among processes
        # Each process will handle requests independently with its own engine clients
        uvloop.run(_run_servers())
    except KeyboardInterrupt:
        logger.info(f"Inference worker process {worker_index} received interrupt signal")
    except Exception as e:
        logger.error(f"Inference worker process {worker_index} error: {e}", exc_info=True)
        raise
    finally:
        # Stop config watcher if started
        if worker_config_watcher is not None:
            try:
                worker_config_watcher.stop()
            except Exception as e:
                logger.warning("Ignored error stopping config watcher in worker %s: %s", worker_index, e)
        # Disconnect SchedulerClient so ZMQ connections are closed cleanly
        if inference_server is not None:
            conn = getattr(inference_server, "_scheduler_connection", None)
            if conn is not None:
                try:
                    asyncio.run(conn.disconnect())
                except Exception as e:
                    logger.warning(
                        "Ignored error disconnecting scheduler client in worker %s: %s",
                        worker_index, e
                    )

        # Close HTTP client pool connections
        try:
            client_pool = HTTPClientPool()
            try:
                asyncio.run(client_pool.close_all())
                logger.info(f"HTTP client pool closed in worker process {worker_index}")
            except Exception as loop_error:
                logger.warning(
                    "Failed to close HTTP client pool in worker %s: %s",
                    worker_index, loop_error, exc_info=True
                )
        except Exception as e:
            logger.warning(f"Failed to close HTTP client pool in worker {worker_index}: {e}", exc_info=True)

        if sock:
            try:
                sock.close()
            except Exception as e:
                logger.warning("Ignored error closing socket in worker %s: %s", worker_index, e)
        logger.info(f"Inference worker process {worker_index} stopped")


class InferenceProcessManager(BaseProcessManager):
    """
    Manages a group of Inference API server worker processes.

    Similar to vllm's APIServerProcessManager, handles creation,
    monitoring, and termination of API server worker processes.
    Uses start()/stop() so it can be registered in main.processes and
    started/stopped by start_all_processes()/stop_all_processes().
    """

    def __init__(
        self,
        config: CoordinatorConfig,
        listen_address: tuple[str, int],
        sock: socket.socket,
        num_workers: int,
    ):
        super().__init__(config, process_name="InferenceWorkers")
        self.listen_address = listen_address
        self.sock = sock
        self.num_workers = num_workers

    def wait_for_completion(self) -> None:
        """Wait for all processes to complete or detect if any fail"""
        try:
            logger.info("Waiting for Inference API server processes to complete...")
            sentinel_to_proc: dict[Any, BaseProcess] = {
                proc.sentinel: proc
                for proc in self._processes
            }
            # Wait for any process to terminate (loop until all sentinels are consumed)
            while sentinel_to_proc:
                # Wait for any process to terminate
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
                    else:
                        logger.info(f"Process {proc.name} (PID: {proc.pid}) exited normally")

        except KeyboardInterrupt:
            logger.info("Received KeyboardInterrupt, shutting down Inference API servers...")
        except Exception as e:
            logger.exception("Exception occurred while running Inference API servers: %s", e)
            raise
        finally:
            logger.info("Terminating remaining processes...")
            self.stop()

    def close(self) -> None:
        """Alias for stop(); kept for backward compatibility."""
        self.stop()

    def is_running(self) -> bool:
        """True only if all worker processes are alive. Any single exit triggers restart."""
        if not self._processes:
            return False
        return all(p.is_alive() for p in self._processes)

    def restart_dead_workers(self) -> bool:
        """Replace and start only dead worker process(es). Leaves alive workers running."""
        dead_indices = [i for i, p in enumerate(self._processes) if not p.is_alive()]
        if not dead_indices:
            return True
        logger.warning("Restarting %s dead worker(s) at index(es) %s", self.process_name, dead_indices)
        for i in dead_indices:
            try:
                proc = self._create_process(i)
                proc.start()
                self._processes[i] = proc
                logger.info("Started %s process %s (PID: %s) replacing dead worker", self.process_name, i, proc.pid)
            except Exception as e:
                logger.error("Failed to restart %s worker %s: %s", self.process_name, i, e, exc_info=True)
        return self.is_running()

    def _create_process(self, index: int) -> BaseProcess:
        return self._spawn_context.Process(
            target=run_inference_worker_proc,
            name=f"InferenceWorker-{index}",
            args=(self.listen_address, self.sock, self.config, index),
        )

    def _get_process_count(self) -> int:
        return self.num_workers


def create_shared_socket(host: str, port: int) -> socket.socket | None:
    """Create a socket that can be shared between multiple processes

    Args:
        host: Host address
        port: Port number

    Returns:
        Socket that can be shared between processes, or None if not supported
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # SO_REUSEPORT allows multiple processes to bind to the same port (coordinator is Linux-only).
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        logger.warning("SO_REUSEPORT not available on this platform")
        sock.close()
        return None

    try:
        sock.bind((host, port))
        sock.listen(128)  # Backlog
        logger.info(f"Created shared socket on {host}:{port}")
        return sock
    except Exception as e:
        logger.error(f"Failed to bind socket on {host}:{port}: {e}")
        sock.close()
        return None
