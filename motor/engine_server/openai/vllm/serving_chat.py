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
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

from http import HTTPStatus

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from vllm.entrypoints.openai.chat_completion.serving import OpenAIServingChat as VllmOpenAIServingChat
from vllm.engine.protocol import EngineClient
from vllm.entrypoints.openai.models.serving import OpenAIServingModels
from vllm.entrypoints.logger import RequestLogger
from vllm.entrypoints.chat_utils import ChatTemplateContentFormatOption
from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest, ChatCompletionResponse
from vllm.entrypoints.openai.engine.protocol import ErrorResponse


class OpenAIServingChat(VllmOpenAIServingChat):
    def __init__(
        self,
        engine_client: EngineClient,
        models: OpenAIServingModels,
        response_role: str,
        *,
        request_logger: RequestLogger | None,
        chat_template: str | None,
        chat_template_content_format: ChatTemplateContentFormatOption,
    ) -> None:
        super().__init__(
            engine_client,
            models,
            response_role,
            request_logger=request_logger,
            chat_template=chat_template,
            chat_template_content_format=chat_template_content_format,
        )

    async def handle_request(self, request: ChatCompletionRequest, raw_request: Request):
        try:
            generator = await self.create_chat_completion(request, raw_request)
        except Exception as e:
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR.value, detail=str(e)
            )from e

        if isinstance(generator, ErrorResponse):
            return JSONResponse(
                content=generator.model_dump(), status_code=generator.error.code
            )

        elif isinstance(generator, ChatCompletionResponse):
            return JSONResponse(
                content=generator.model_dump(),
            )

        return StreamingResponse(content=generator, media_type="text/event-stream")
