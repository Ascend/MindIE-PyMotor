#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import asyncio
import threading
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import Response
import uvicorn

from motor.node_manager.core.heartbeat_manager import HeartbeatManager
from motor.utils.logger import get_logger
from motor.resources.http_msg_spec import StartCmdMsg
from motor.node_manager.core.engine_manager import EngineManager
from motor.node_manager.core.daemon import Daemon


logger = get_logger(__name__)
app = FastAPI()

MAX_CONCURRENT_THREADS = 10
thread_semaphore = asyncio.Semaphore(MAX_CONCURRENT_THREADS)


@app.post("/node-manager/start")
async def start_instance(request: Request):
    """ post instance and role、ranktable info """
    try:
        payload = await request.json()
        start_msg = StartCmdMsg(**payload)

        engine_manager = EngineManager()
        if engine_manager is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail="Engine manager not initialized")

        async with thread_semaphore:
            try:
                parsed_ok = await asyncio.to_thread(engine_manager.parse_start_cmd, start_msg)
            except Exception as inner_err:
                logger.error(f"Failed to parse start command: {inner_err}")
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"Invalid start command payload") from inner_err

        if not parsed_ok:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="Start command validation failed")

        try:
            await asyncio.to_thread(Daemon().pull_engine,
                                    engine_manager.config.role,
                                    start_msg.endpoints,
                                    start_msg.instance_id)
        except Exception as pull_err:
            logger.error(f"Failed to pull engine: {pull_err}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail=f"Failed to start engine server") from pull_err

        HeartbeatManager().update_endpoint(start_msg)
        HeartbeatManager().start()
        return {}

    except HTTPException as http_err:
        raise http_err
    except Exception as err:
        # Catch other unexpected exceptions to avoid returning unfriendly internal errors
        logger.error(f"Unexpected error: {err}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"An internal server error occurred") from err


@app.post("/node-manager/stop")
async def stop_instance(request: Request):
    """
    Stop all engine processes by invoking Daemon.exit_daemon().
    """
    try:
        await asyncio.to_thread(Daemon().exit_daemon)

        return Response(status_code=status.HTTP_200_OK, content="All engine processes stopped successfully.")
    except Exception as err:
        logger.error(f"Failed to stop engines via daemon: {err}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to stop engine processes"
        ) from err


class NodeManagerAPI:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.server = None
        self.serve_task = None
        self._thread = None

        self._thread = threading.Thread(target=self._serve_in_thread, daemon=True, name="nm_api_server")
        self._thread.start()

    async def stop(self):
        self.stop_sync()

    def stop_sync(self):
        if self.server:
            self.server.should_exit = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _serve_in_thread(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        config = uvicorn.Config(app, host=self.host, port=self.port, loop="asyncio")
        self.server = uvicorn.Server(config)
        try:
            loop.run_until_complete(self.server.serve())
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception as e:
                logger.error(f"Failed to shutdown server: {e}")
            loop.close()