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

from http import HTTPStatus

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from vllm.entrypoints.openai.completion.serving import OpenAIServingCompletion as VllmOpenAIServingCompletion
from vllm.engine.protocol import EngineClient
from vllm.entrypoints.openai.models.serving import OpenAIServingModels
from vllm.entrypoints.logger import RequestLogger
from vllm.entrypoints.openai.completion.protocol import CompletionRequest, CompletionResponse
from vllm.entrypoints.openai.engine.protocol import ErrorResponse


class OpenAIServingCompletion(VllmOpenAIServingCompletion):
    def __init__(
        self,
        engine_client: EngineClient,
        models: OpenAIServingModels,
        *,
        request_logger: RequestLogger | None,
    ):
        super().__init__(
            engine_client,
            models,
            request_logger=request_logger,
        )

    async def handle_request(self, request: CompletionRequest, raw_request: Request):
        try:
            generator = await self.create_completion(request, raw_request)
        except OverflowError as e:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST.value, detail=str(e)
            )from e
        except Exception as e:
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR.value, detail=str(e)
            )from e

        if isinstance(generator, ErrorResponse):
            return JSONResponse(
                content=generator.model_dump(), status_code=generator.error.code
            )
        elif isinstance(generator, CompletionResponse):
            return JSONResponse(
                content=generator.model_dump()
            )

        return StreamingResponse(content=generator, media_type="text/event-stream")
