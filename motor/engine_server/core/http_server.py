# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import multiprocessing

import uvicorn
from fastapi import FastAPI, Request

from motor.engine_server.factory.lifespan_factory import LifespanFactory
from motor.engine_server.factory.protocol_factory import ProtocolFactory
from motor.engine_server.config.base import IConfig
from motor.common.utils.cert_util import CertUtil
from motor.common.utils.logger import get_logger

logger = get_logger("engine_server")


class HttpServer:
    def __init__(self, config: IConfig, init_params: dict):
        self.host = config.get_server_config().server_host
        self.port = config.get_server_config().engine_port
        self.infer_tls_config = config.get_server_config().deploy_config.infer_tls_config

        lifespan_factory = LifespanFactory()
        self.lifespan = lifespan_factory.get_lifespan(config, init_params)
        self.app = FastAPI(title="EngineServer HttpServer", lifespan=self.lifespan)

        self._stop_event = multiprocessing.Event()
        self._server: uvicorn.Server | None = None
        self._server_process = multiprocessing.Process(
            target=self._run_server,
            name="http_server_process",
            daemon=True
        )
        self.engine_type = config.get_server_config().engine_type
        self._load_protocol_classes()
        self._register_routes()

    def run(self):
        if self._server_process and not self._server_process.is_alive():
            self._server_process.start()
            logger.info(f"HttpServer started in process: http://{self.host}:{self.port}")

    def shutdown(self):
        if self._server:
            self._server.should_exit = True
            logger.info("HttpServer: Uvicorn server exit triggered")
        self._stop_event.set()
        logger.info("HttpServer stopped completely")

    def _load_protocol_classes(self):
        protocol_factory = ProtocolFactory()
        self.chat_completion_request, self.completion_request = protocol_factory.load_protocol_classes(self.engine_type)

    def _register_routes(self):
        @self.app.post("/v1/chat/completions")
        async def create_chat_completion(raw_request: Request):
            request_dict = await raw_request.json()
            request = self.chat_completion_request.model_validate(request_dict)
            return await self.app.state.openai_serving_chat.handle_request(request, raw_request)

        @self.app.post("/v1/completions")
        async def create_completion(raw_request: Request):
            request_dict = await raw_request.json()
            request = self.completion_request.model_validate(request_dict)
            return await self.app.state.openai_serving_completion.handle_request(request, raw_request)

        @self.app.get("/health")
        async def health(raw_request: Request):
            is_healthy = await self.app.state.health_checker(self.app.state.engine_client)
            return is_healthy

    def _run_server(self):
        config_kwargs = {
            "app": self.app,
            "host": self.host,
            "port": self.port,
            "log_level": "warning",
            "workers": 1,
            "loop": "uvloop",
            "http": "httptools"
        }
        config = uvicorn.Config(**config_kwargs)

        config.load()
        if self.infer_tls_config and self.infer_tls_config.enable_tls:
            ssl_context = CertUtil.create_ssl_context(self.infer_tls_config)
            if ssl_context:
                config.ssl = ssl_context
            else:
                raise RuntimeError("Failed to create ssl context")
            logger.info(f"HttpServer started: https://{self.host}:{self.port}")
        else:
            logger.info(f"HttpServer started: http://{self.host}:{self.port}")

        self._server = uvicorn.Server(config)
        if not self._stop_event.is_set():
            self._server.run()
