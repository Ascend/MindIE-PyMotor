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

import asyncio
import random
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import anyio
from fastapi.responses import StreamingResponse, JSONResponse

from motor.common.resources.instance import PDRole
from motor.common.utils.env import Env
from motor.coordinator.domain import ScheduledResource
from motor.coordinator.models.request import ReqState
from motor.coordinator.router.base_router import BaseRouter


class SeparatePDDualDispatchRouter(BaseRouter):

    @staticmethod
    def _generate_bootstrap_room() -> int:
        """Generate a unique bootstrap room ID for disaggregated serving.

        Returns:
            Random 63-bit integer.
        """
        return random.randint(0, 2**63 - 1)

    @asynccontextmanager
    async def _dual_env_context(self):
        async with self._manage_request_context(), \
                self._manage_resource_context(PDRole.ROLE_P, self.release_all) as p_res, \
                self._manage_resource_context(PDRole.ROLE_D, self.release_tokens) as d_res, \
                self._manage_client_context(p_res) as p_client, \
                self._manage_client_context(d_res) as d_client:
            yield p_res, d_res, p_client, d_client

    async def handle_request(self) -> StreamingResponse | JSONResponse:
        """Entry point for handling dual dispatch requests."""
        is_stream = self.req_info.req_data.get("stream", False)
        if is_stream:
            return StreamingResponse(
                self._generate_stream_response(),
                media_type="text/event-stream"
            )
        return await self._generate_response()

    async def _run_prefill(self, req_data, p_client, scope):
        with scope:
            try:
                response = await self.forward_request(
                        req_data, p_client, self.config.exception_config.first_token_timeout
                    )
                self.req_info.update_state(ReqState.PREFILL_END)
                return response
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.logger.error("Prefill error: %s", str(e))
                self.req_info.cancel_scope()

    async def _generate_stream_response(self) -> AsyncGenerator[str, None]:
        """
        Handles streaming requests for Dual Dispatch with retry logic and scope management.
        """
        self.logger.debug("Handling streaming Dual Dispatch request")
        max_retry = self.config.exception_config.max_retry

        for attempt in range(max_retry):
            try:
                # 1. Allocate resources & Initialize contexts
                async with self._dual_env_context() as (p_res, d_res, p_client, d_client):
                    p_scope = anyio.CancelScope()
                    d_scope = anyio.CancelScope()
                    self.req_info.set_cancel_scope(p_scope, PDRole.ROLE_P)
                    self.req_info.set_cancel_scope(d_scope, PDRole.ROLE_D)

                    req_data = await self._gen_dual_request(p_res)
                    p_req_data = await self._gen_p_request(req_data)

                    # 2. Fire Prefill request in background
                    p_task = asyncio.create_task(self._run_prefill(p_req_data, p_client, p_scope))

                    # 3. Fire Decode stream
                    try:
                        with d_scope:
                            async for chunk in self.forward_stream_request(
                                    req_data, d_client, self.config.exception_config.first_token_timeout
                            ):
                                yield chunk

                        self.req_info.update_state(ReqState.DECODE_END)
                        return
                    finally:
                        # Clean up Prefill task if Decode finishes early or encounters an error
                        p_task.cancel()
                        if self.req_info.is_cancelled:
                            raise Exception("Exception occurred in dual dispatch")

            except asyncio.CancelledError:
                self.logger.info("The streaming request was terminated because of timeout or client disconnect.")
                self.req_info.cancel_scope()
                raise
            except Exception as e:
                self.logger.error(
                    "Error in dual dispatch streaming (attempt %d/%d): %s",
                    attempt + 1, max_retry, str(e), exc_info=True
                )
                self.req_info.cancel_scope()

                if self.first_chunk_sent or attempt == max_retry - 1:
                    self.req_info.update_state(ReqState.EXCEPTION)
                    yield self._generate_streaming_error_chunk(e)
                    return

                wait_time = self.config.exception_config.retry_delay * (2 ** attempt)
                self.logger.info("Retrying streaming request in %.2f seconds...", wait_time)
                await asyncio.sleep(wait_time)

    async def _generate_response(self) -> JSONResponse:
        """
        Handles non-streaming requests for Dual Dispatch with retry logic.
        """
        self.logger.debug("Handling non-streaming Dual Dispatch request")
        max_retries = self.config.exception_config.max_retry

        for attempt in range(max_retries):
            try:
                # 1. Allocate resources & Initialize contexts
                async with self._dual_env_context() as (p_res, d_res, p_client, d_client):

                    p_scope = anyio.CancelScope()
                    d_scope = anyio.CancelScope()
                    self.req_info.set_cancel_scope(p_scope, PDRole.ROLE_P)
                    self.req_info.set_cancel_scope(d_scope, PDRole.ROLE_D)

                    req_data = await self._gen_dual_request(p_res)
                    p_req_data = await self._gen_p_request(req_data)

                    # 2. Fire Prefill request in background
                    p_task = asyncio.create_task(self._run_prefill(p_req_data, p_client, p_scope))

                    # 3. Fire Decode request
                    try:
                        with d_scope:
                            response = await self.forward_request(
                                    req_data, d_client, self.config.exception_config.infer_timeout
                                )
                        self.req_info.update_state(ReqState.DECODE_END)
                        return JSONResponse(content=response.json())
                    finally:
                        p_task.cancel()
                        if self.req_info.is_cancelled:
                            raise Exception("Exception occurred in dual dispatch")

            except asyncio.CancelledError:
                self.logger.info("The non-streaming request was terminated because of timeout or client disconnect.")
                self.req_info.cancel_scope()
                raise
            except Exception as e:
                self.logger.error(
                    "Error in dual dispatch decode (attempt %d/%d): %s",
                    attempt + 1, max_retries, str(e)
                )
                self.req_info.cancel_scope()

                if attempt < max_retries - 1:
                    wait_time = self.config.exception_config.retry_delay * (2 ** attempt)
                    self.logger.info("Retrying non-streaming request in %.2f seconds...", wait_time)
                    await asyncio.sleep(wait_time)
                    continue

                self.logger.error("All retries failed for non-streaming dual dispatch request.")
                self.req_info.update_state(ReqState.EXCEPTION)
                raise e

    async def _gen_dual_request(self, prefill: ScheduledResource) -> dict:
        """Inject bootstrap info"""
        req_data = self.req_info.req_data.copy()
        req_data.update({
            "bootstrap_host": prefill.endpoint.ip,
            "bootstrap_port": Env.disaggregation_bootstrap_port,
            "bootstrap_room": self._generate_bootstrap_room(),
        })
        return req_data

    async def _gen_p_request(self, req_data) -> dict:
        p_req_data = req_data.copy()
        p_req_data["stream"] = False
        p_req_data["max_tokens"] = 1

        if "stream_options" in p_req_data:
            del p_req_data["stream_options"]

        return p_req_data
