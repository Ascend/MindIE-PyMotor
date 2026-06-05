# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import json
import multiprocessing
from abc import abstractmethod
from http import HTTPStatus
from typing import Annotated, Any, AsyncGenerator, Callable

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import ValidationError

from motor.engine_server.core.config import IConfig
from motor.common.http.cert_util import CertUtil
from motor.common.logger import get_logger
from motor.engine_server.core.endpoint import Endpoint
from motor.engine_server.utils.cancellation import with_cancellation

logger = get_logger(__name__)

CONFIG_KEY = "_config"


class InferEndpoint(Endpoint):
    def __init__(self, config: IConfig):
        self.config = config
        self.host = config.get_endpoint_config().host
        self.port = config.get_endpoint_config().port
        self.infer_tls_config = (
            config.get_endpoint_config().deploy_config.infer_tls_config
        )

        self.app = FastAPI(
            title="EngineServer InferEndpoint", lifespan=self.get_lifespan()
        )

        self.app.extra[CONFIG_KEY] = self.config

        self._stop_event = multiprocessing.Event()
        self._server: uvicorn.Server | None = None
        self._server_process = multiprocessing.Process(
            target=self._run_server, name="infer_endpoint_process", daemon=False
        )
        self._run_http_in_process = True
        self.engine_type = config.get_endpoint_config().engine_type
        self.init_request_handlers()
        self._register_routes()

    @abstractmethod
    def get_lifespan(self) -> Callable[[FastAPI], AsyncGenerator[None, None]]:
        """Return lifespan async generator; state (openai_serving_chat etc.) must be set in lifespan."""
        pass

    @abstractmethod
    def init_request_handlers(self) -> None:
        """Set protocol classes (chat_completion_request, completion_request). State is set in lifespan."""
        pass

    def run(self):
        if getattr(self, "_run_http_in_process", False):
            logger.info(
                "InferEndpoint running in same process (run_http_in_process=True)."
            )
            self._run_server()
        elif self._server_process and not self._server_process.is_alive():
            self._server_process.start()
            logger.info(
                f"InferEndpoint started in process: http://{self.host}:{self.port}"
            )

    def join(self) -> None:
        self._server_process.join()
        logger.error(
            f"infer_endpoint process exited with code {self._server_process.exitcode}"
        )

    def wait(self) -> None:
        """Block until infer server exits. No-op when HTTP runs in-process (run() already blocks)."""
        if not getattr(self, "_run_http_in_process", True):
            self.join()

    def shutdown(self):
        if self._server:
            self._server.should_exit = True
            logger.info("InferEndpoint: Uvicorn server exit triggered")
        self._stop_event.set()
        logger.info("InferEndpoint stopped completely")

    async def _parse_openai_request(
        self, raw_request: Request, model: type[Any]
    ) -> Any:
        try:
            body = await raw_request.json()
            return model.model_validate(body)
        except (json.JSONDecodeError, ValidationError) as e:
            detail = (
                e.errors()
                if isinstance(e, ValidationError)
                else f"Invalid JSON body: {e.msg}"
            )
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST.value, detail=detail
            ) from e

    async def _chat_completion_body(self, raw_request: Request) -> Any:
        return await self._parse_openai_request(
            raw_request, self.chat_completion_request
        )

    async def _completion_body(self, raw_request: Request) -> Any:
        return await self._parse_openai_request(raw_request, self.completion_request)

    def _register_profile_routes_if_enabled(self) -> None:
        """Mirror vLLM profile API: POST /start_profile, /stop_profile when profiler is configured."""
        args = self.config.get_args()
        if args is None:
            return
        profiler_config = getattr(args, "profiler_config", None)
        profiler = (
            getattr(profiler_config, "profiler", None)
            if profiler_config is not None
            else None
        )
        if profiler is None:
            return
        logger.warning(
            "Profiler with mode '%s' is enabled on the infer API server. "
            "This should ONLY be used for local development!",
            profiler,
        )

        @self.app.post("/start_profile")
        async def start_profile(raw_request: Request):
            logger.info("Starting profiler...")
            engine_client = getattr(raw_request.app.state, "engine_client", None)
            if engine_client is None:
                raise HTTPException(
                    status_code=HTTPStatus.NOT_IMPLEMENTED.value,
                    detail="Profiling is not supported for this engine.",
                )
            await engine_client.start_profile()
            logger.info("Profiler started.")
            return Response(status_code=200)

        @self.app.post("/stop_profile")
        async def stop_profile(raw_request: Request):
            logger.info("Stopping profiler...")
            engine_client = getattr(raw_request.app.state, "engine_client", None)
            if engine_client is None:
                raise HTTPException(
                    status_code=HTTPStatus.NOT_IMPLEMENTED.value,
                    detail="Profiling is not supported for this engine.",
                )
            await engine_client.stop_profile()
            logger.info("Profiler stopped.")
            return Response(status_code=200)

    def _register_routes(self):
        @self.app.post("/v1/chat/completions")
        @with_cancellation
        async def create_chat_completion(
            request: Annotated[Any, Depends(self._chat_completion_body)],
            raw_request: Request,
        ):
            return await self.app.state.openai_serving_chat.handle_request(
                request, raw_request
            )

        @self.app.post("/v1/completions")
        @with_cancellation
        async def create_completion(
            request: Annotated[Any, Depends(self._completion_body)],
            raw_request: Request,
        ):
            return await self.app.state.openai_serving_completion.handle_request(
                request, raw_request
            )

        @self.app.get("/v1/models")
        async def list_models():
            models = getattr(self.app.state, "openai_serving_models", None)
            if models is None:
                raise HTTPException(
                    status_code=HTTPStatus.NOT_IMPLEMENTED.value,
                    detail="Model listing is not available for this engine.",
                )
            return await models.show_available_models()

        @self.app.get("/health")
        async def health(raw_request: Request):
            is_healthy = await self.app.state.health_checker()
            return is_healthy

        self._register_profile_routes_if_enabled()

    def _run_server(self):
        config_kwargs = {
            "app": self.app,
            "host": self.host,
            "port": self.port,
            "log_level": "warning",
            "workers": 1,
            "loop": "uvloop",
            "http": "httptools",
        }
        config = uvicorn.Config(**config_kwargs)

        config.load()
        if self.infer_tls_config and self.infer_tls_config.enable_tls:
            ssl_context = CertUtil.create_ssl_context(self.infer_tls_config)
            if ssl_context:
                config.ssl = ssl_context
            else:
                raise RuntimeError("Failed to create ssl context")
            logger.info(f"InferEndpoint started: https://{self.host}:{self.port}")
        else:
            logger.info(f"InferEndpoint started: http://{self.host}:{self.port}")

        self._server = uvicorn.Server(config)
        if not self._stop_event.is_set():
            self._server.run()
