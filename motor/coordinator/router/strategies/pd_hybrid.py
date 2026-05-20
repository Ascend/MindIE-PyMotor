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
from typing import Dict, AsyncGenerator, Any
import asyncio
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi import HTTPException

from motor.coordinator.models.request import ReqState
from motor.coordinator.router.strategies.base import BaseRouter
import motor.coordinator.router.recompute as recompute_common
from motor.coordinator.router.adapters.completion_to_chat import adapt_completion_nonstream_to_chat
from motor.common.resources.instance import PDRole


class PDHybridRouter(BaseRouter):
    """Handle request with a single PD hybrid instance"""

    @staticmethod
    def _candidate_roles() -> tuple[PDRole, ...]:
        """
        Prefer ROLE_U for true hybrid deployments, but keep ROLE_P fallback
        for PD-separate degradation-to-single-node scenarios.
        """
        return (PDRole.ROLE_U, PDRole.ROLE_P)

    async def _prepare_resource_with_fallback(self, attempt: int, max_retry: int):
        """Fallback to ROLE_P only when scheduling has no ROLE_U resource."""
        last_error = None
        for role in self._candidate_roles():
            try:
                resource = await self.prepare_resource(role)
                return resource
            except HTTPException as role_error:
                last_error = role_error
                self.logger.warning(
                    "Hybrid scheduling with role %s failed on attempt %d/%d: %s",
                    role,
                    attempt + 1,
                    max_retry,
                    role_error,
                )
                continue

        if last_error is not None:
            raise last_error
        raise HTTPException(status_code=503, detail="No available instance for hybrid scheduling")
    
    async def handle_request(self) -> StreamingResponse | JSONResponse:

        req_data = self.req_info.req_data.copy()

        if self.req_info.req_data.get("stream", False):
            return StreamingResponse(
                self._generate_stream(req_data),
                media_type="text/event-stream"
            )
        return await self._generate_post(req_data)

    async def _generate_stream(self, req_data: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """
        Handling hybrid streaming requests
        """
        trace_obj = self.req_info.trace_obj
        with self._trace_span("PDHybrid_Stream", True):
            await self.do_encode()
            self.is_meta = False
            self.logger.debug("Handling hybrid streaming request")
            max_retry = self.config.exception_config.transport_retry_limit

            for attempt in range(max_retry):
                resource = None
                try:
                    resource = await self._prepare_resource_with_fallback(attempt, max_retry)
                    async with self._manage_client_context(resource) as client:
                        async for chunk in self.forward_stream_request(
                                req_data, client, self.config.exception_config.first_token_timeout
                            ):
                            yield recompute_common.strip_stream_chunk_bytes_for_client(
                                chunk,
                                client_return_token_ids=self.req_info.req_data.get(
                                    "_client_return_token_ids", False
                                ),
                            )

                        self.req_info.update_state(ReqState.DECODE_END)
                        self.logger.info(trace_obj.set_end_and_ttft_tpot())
                        return
                except asyncio.CancelledError:
                    self.logger.debug("Stream request was cancelled")
                    raise
                except Exception as e:
                    self.logger.error(
                        "Error in streaming (attempt %d/%d): %s",
                        attempt + 1, max_retry, str(e), exc_info=True
                    )

                    # If chunk was already sent, cannot retry the HTTP stream.
                    # Send error chunk and terminate.
                    if self.first_chunk_sent or attempt == max_retry - 1:
                        trace_obj.set_trace_status(e)
                        self.req_info.update_state(ReqState.EXCEPTION)
                        yield self._generate_streaming_error_chunk(e)
                        return

                    wait_time = self.config.exception_config.retry_delay * (2 ** attempt)
                    self.logger.info("Retrying streaming request in %.2f seconds...", wait_time)
                    await asyncio.sleep(wait_time)
                finally:
                    if resource is not None:
                        await self.release_all(resource)

    async def _generate_post(self, req_data: Dict[str, Any]) -> JSONResponse:
        """
        Handling hybrid non-streaming requests
        """
        trace_obj = self.req_info.trace_obj
        with self._trace_span("PDHybrid", False):
            await self.do_encode()
            self.is_meta = False
            self.logger.debug("Handling hybrid non-streaming request")
            max_retries = self.config.exception_config.transport_retry_limit

            for attempt in range(max_retries):
                resource = None
                try:
                    resource = await self._prepare_resource_with_fallback(attempt, max_retries)
                    async with self._manage_client_context(resource) as client:
                        response = await self.forward_request(
                                req_data, client, self.config.exception_config.infer_timeout
                            )

                        self.req_info.update_state(ReqState.DECODE_END)
                        body = response.json()
                        if (
                            "chat" in self.req_info.effective_entry_api()
                            and body.get("object") == "text_completion"
                        ):
                            adapt_completion_nonstream_to_chat(
                                body, req_id=self.req_info.req_id
                            )
                        recompute_common.strip_nonstream_response_body_for_client(
                            body,
                            client_return_token_ids=self.req_info.req_data.get(
                                "_client_return_token_ids", False
                            ),
                        )
                        return JSONResponse(content=body)

                except asyncio.CancelledError:
                    self.logger.debug("Post request was cancelled")
                    raise
                except Exception as e:
                    self.logger.error("Error in post (attempt %d/%d): %s", attempt + 1, max_retries, str(e))

                    trace_obj.set_trace_exception(e)
                    if attempt < max_retries - 1:
                        wait_time = self.config.exception_config.retry_delay * (2 ** attempt)
                        self.logger.info("Retrying non-streaming request in %.2f seconds...", wait_time)
                        await asyncio.sleep(wait_time)
                        continue

                    self.logger.error("All retries failed for non-streaming decode request.")
                    self.req_info.update_state(ReqState.EXCEPTION)
                    raise e
                finally:
                    if resource is not None:
                        await self.release_all(resource)
