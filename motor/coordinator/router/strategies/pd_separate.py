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

from fastapi.responses import StreamingResponse, JSONResponse
from fastapi import HTTPException, status

from motor.coordinator.domain import ScheduledResource
from motor.coordinator.models.request import ReqState
from motor.coordinator.router.strategies.base import BaseRouter, RecomputeState
import motor.coordinator.router.recompute as recompute_common
from motor.coordinator.router.adapters.completion_to_chat import adapt_completion_nonstream_to_chat
from motor.config.coordinator import CoordinatorConfig
from motor.common.resources.instance import PDRole


class _DecodeTransportRetry(Exception):
    """Decode failed before any chunk reached the client; outer stream may retry transport."""

    pass


class SeparatePDRouter(BaseRouter):
    """
    Handle request with separate P and D instances (original behavior).
    """

    def __init__(
        self,
        req_info,
        config: CoordinatorConfig,
        scheduler: "SchedulingFacade",
        request_manager=None,
    ):
        super().__init__(
            req_info, config, scheduler=scheduler,
            request_manager=request_manager
        )
        self._recompute = RecomputeState(wants_retry=True)
        self.is_finished = False
        self._stream_chunk_sent_to_client = False

    async def generate_stream(self):
        tmax = self.config.exception_config.transport_retry_limit
        for attempt in range(tmax):
            self._recompute.wants_retry = True
            while self._recompute.wants_retry:
                self.first_chunk_sent = False
                self._stream_chunk_sent_to_client = False
                self._recompute.wants_retry = False
                decode_wants_transport_retry = False
                try:
                    async for chunk in self.process_single_attempt(attempt):
                        yield chunk
                except _DecodeTransportRetry:
                    decode_wants_transport_retry = True
                if self._recompute.wants_retry:
                    self._bump_req_id_after_recompute_workloads_released(
                        self._recompute.retry_count
                    )
                if self.is_finished:
                    return
                if decode_wants_transport_retry:
                    break

    async def handle_request(self) -> StreamingResponse | JSONResponse:
        """Handle request with separate P and D instances"""
        if self.req_info.req_data.get("stream", False):
            return StreamingResponse(
                self.generate_stream(),
                media_type="text/event-stream",
            )
        return await self._generate_nonstream_json()

    async def process_single_attempt(self, attempt):
        tmax = self.config.exception_config.transport_retry_limit
        prefill_resource: ScheduledResource = None
        try:
            # Schedule P instance
            prefill_resource = await self.prepare_resource(PDRole.ROLE_P)
            # Forward P request
            p_resp_json = await self._forward_p_request(prefill_resource)
            self.logger.debug("Prefill response received: %s", p_resp_json)
        except Exception as e:
            self.logger.error("Error occurred while forwarding P request: %s", e)
            if attempt != tmax - 1:
                self.is_finished = False
                return
            yield self._generate_streaming_error_chunk(e)
            self.is_finished = True
            return
        finally:
            if prefill_resource and self.req_info.state != ReqState.PREFILL_END:
                if not await self.release_all(prefill_resource):
                    self.logger.debug(
                        "release_all(prefill) returned False instance_id=%s endpoint_id=%s state=%s",
                        prefill_resource.instance.id, prefill_resource.endpoint.id, self.req_info.state)

        decode_resource: ScheduledResource = None
        try:
            # Schedule D instance
            decode_resource = await self.prepare_resource(PDRole.ROLE_D)
            # Forward D request
            async for chunk in self._forward_d_request(p_resp_json, prefill_resource, decode_resource):
                if chunk:
                    self._stream_chunk_sent_to_client = True
                yield chunk
            if not self._recompute.wants_retry:
                self.is_finished = True
                return
        except HTTPException as e:
            self.logger.error("Error occurred while forwarding Decode request: %s", e)
            await self._handle_stream_error(prefill_resource, e)
            yield self._generate_streaming_error_chunk(e)
            self.is_finished = True
            return
        except Exception as e:
            self.logger.error("Error occurred while forwarding Decode request: %s", e)
            await self._handle_stream_error(prefill_resource, e)
            if self._stream_chunk_sent_to_client or attempt == tmax - 1:
                yield self._generate_streaming_error_chunk(e)
                self.is_finished = True
            else:
                raise _DecodeTransportRetry() from e
        finally:
            if decode_resource:
                released = await self.release_tokens(decode_resource)
                if not released:
                    self.logger.debug(
                        "release_tokens(decode) returned False instance_id=%s endpoint_id=%s state=%s",
                        decode_resource.instance.id, decode_resource.endpoint.id, self.req_info.state)

    async def _generate_nonstream_json(self) -> JSONResponse:
        tmax = self.config.exception_config.transport_retry_limit
        for attempt in range(tmax):
            self._recompute.wants_retry = True
            while self._recompute.wants_retry:
                self.first_chunk_sent = False
                self._recompute.wants_retry = False
                resp = await self._nonstream_single_attempt(attempt)
                if resp is not None:
                    return resp
                if self._recompute.wants_retry:
                    self._bump_req_id_after_recompute_workloads_released(
                        self._recompute.retry_count
                    )
                if self.is_finished:
                    return JSONResponse(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        content=self.build_error_response(
                            RuntimeError("Non-stream PD request ended without response")
                        ).model_dump(),
                    )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=self.build_error_response(
                RuntimeError("All retries exhausted for non-stream PD request")
            ).model_dump(),
        )

    def _nonstream_error_json_response(self, exc: Exception) -> JSONResponse:
        er = self.build_error_response(exc)
        return JSONResponse(status_code=er.code, content=er.model_dump())

    async def _nonstream_single_attempt(self, attempt: int) -> JSONResponse | None:
        tmax = self.config.exception_config.transport_retry_limit
        prefill_resource: ScheduledResource = None
        try:
            prefill_resource = await self.prepare_resource(PDRole.ROLE_P)
            p_resp_json = await self._forward_p_request(prefill_resource)
            self.logger.debug("Prefill response received: %s", p_resp_json)
        except Exception as e:
            self.logger.error("Error occurred while forwarding P request: %s", e)
            if attempt != tmax - 1:
                self.is_finished = False
                return None
            self.is_finished = True
            return self._nonstream_error_json_response(e)
        finally:
            if prefill_resource and self.req_info.state != ReqState.PREFILL_END:
                if not await self.release_all(prefill_resource):
                    self.logger.debug(
                        "release_all(prefill) returned False instance_id=%s endpoint_id=%s state=%s",
                        prefill_resource.instance.id, prefill_resource.endpoint.id, self.req_info.state,
                    )

        decode_resource: ScheduledResource = None
        try:
            decode_resource = await self.prepare_resource(PDRole.ROLE_D)
            try:
                req_data = self._gen_d_request(p_resp_json)
            except BaseException:
                await self.release_tokens(decode_resource)
                raise
            body = await self._fetch_nonstream_decode_body(
                req_data, prefill_resource, decode_resource
            )
            if body is not None:
                self.req_info.update_state(ReqState.DECODE_END)
                self.logger.debug("Completed non-stream decode for request %s", self.req_info)
                self.is_finished = True
                return JSONResponse(content=body)
            return None
        except HTTPException as e:
            self.logger.error("Error occurred while forwarding Decode request: %s", e)
            await self._handle_stream_error(prefill_resource, e)
            self.is_finished = True
            return self._nonstream_error_json_response(e)
        except Exception as e:
            self.logger.error("Error occurred while forwarding Decode request: %s", e)
            await self._handle_stream_error(prefill_resource, e)
            if self.first_chunk_sent or attempt == tmax - 1:
                self.is_finished = True
                return self._nonstream_error_json_response(e)
            return None

    def _gen_p_request(self) -> dict:
        """Generate P request parameters"""
        kv_dict = {
            "do_remote_decode": True,
            "do_remote_prefill": False,
            "remote_engine_id": None,
            "remote_block_ids": None,
            "remote_host": None,
            "remote_port": None,
            "aborted_request": [],
        }
        return self._apply_prefill_params(
            self.req_info.req_data, kv_transfer_params=kv_dict
        )

    async def _forward_p_request(self, resource: ScheduledResource):
        """Forward P request to the given resource"""
        req_data = self._gen_p_request()

        async with self._manage_client_context(
                resource
            ) as prefill_client:
            # P non-streaming request
            response = await self.forward_request(
                req_data=req_data,
                client=prefill_client,
                timeout=self.config.exception_config.infer_timeout)
            resp_json = response.json()
            usage = resp_json.get("usage", {})
            if usage:
                if 'prompt_tokens_details' in usage:
                    prompt_tokens_details = usage['prompt_tokens_details']
                    if prompt_tokens_details is None:
                        prompt_tokens_details = {"cached_tokens": 0}
                    self.req_info.update_prompt_tokens_details(prompt_tokens_details)
            self.req_info.update_state(ReqState.PREFILL_END)
            await self.release_tokens(resource)
            return resp_json

    def _gen_d_request(self, resp_json: dict) -> dict:
        """Generate D request parameters"""
        req_data = self.req_info.req_data.copy()
        kv_transfer_params = resp_json.get('kv_transfer_params', {})
        if kv_transfer_params:
            req_data["kv_transfer_params"] = kv_transfer_params
        req_data["return_token_ids"] = (
            self.config.exception_config.recompute_enabled
            or req_data.get("_client_return_token_ids", False)
        )
        return req_data

    async def _forward_d_request(
        self,
        resp_json: dict,
        prefill_resource: ScheduledResource,
        decode_resource: ScheduledResource
    ):
        """Forward D request to the given resource (user ``stream: true`` only)."""
        try:
            req_data = self._gen_d_request(resp_json)
            request_info = recompute_common.extract_request_info(req_data)
            async for chunk in self._process_stream_chunks(
                req_data,
                request_info,
                prefill_resource,
                decode_resource
            ):
                yield chunk

            if not self._recompute.wants_retry:
                self.req_info.update_state(ReqState.DECODE_END)
                self.logger.debug("Completed streaming for request %s", self.req_info)
        except Exception as e:
            await self._handle_stream_error(prefill_resource, e)
            raise e

    async def _fetch_nonstream_decode_body(
        self,
        req_data: dict,
        prefill_resource: ScheduledResource,
        decode_resource: ScheduledResource,
    ) -> dict | None:
        """Single JSON body from D (``forward_request``). Returns ``None`` when recompute is scheduled.

        Decode workload tokens are always released in ``finally`` so an exception after
        ``release_kv(prefill)`` or inside ``_prepare_recompute_retry`` cannot skip cleanup.
        Prefill tokens are released in ``_forward_p_request``; only KV may remain until
        ``release_kv`` here or on the success path.
        """
        rmax = self.config.exception_config.recompute_retry_limit
        try:
            async with self._manage_client_context(decode_resource) as decode_client:
                response = await self.forward_request(
                    req_data=req_data,
                    client=decode_client,
                    timeout=self.config.exception_config.infer_timeout,
                )
                body = response.json()
                if recompute_common.is_recomputed_nonstream_response(body):
                    if not self.config.exception_config.recompute_enabled:
                        raise recompute_common.recompute_disabled_http_exception()
                    self._check_recompute_limit(self._recompute.retry_count, rmax)
                    self._recompute.wants_retry = True
                    self.req_info.update_state(ReqState.RECOMPUTE)
                    recompute_common.validate_nonstream_recompute_body(
                        self.req_info.req_id,
                        body,
                        logger=self.logger,
                    )
                    ri = recompute_common.build_request_info_for_nonstream_recompute(req_data, body)
                    await self.release_kv(prefill_resource)
                    self._recompute.retry_count = self._prepare_recompute_retry(
                        req_data, ri, self._recompute.retry_count
                    )
                    return None
                await self.release_kv(prefill_resource)
                self.first_chunk_sent = True
                if (
                    "chat" in self.req_info.effective_entry_api()
                    and body.get("object") == "text_completion"
                ):
                    adapt_completion_nonstream_to_chat(body, req_id=self.req_info.req_id)
                recompute_common.strip_nonstream_response_body_for_client(
                    body,
                    client_return_token_ids=self.req_info.req_data.get(
                        "_client_return_token_ids", False
                    ),
                )
                if self.req_info.prompt_tokens_details:
                    if body.get("usage", {}):
                        body['usage']['prompt_tokens_details'] = (
                            self.req_info.prompt_tokens_details
                        )
                return body
        finally:
            released = await self.release_tokens(decode_resource)
            if not released:
                self.logger.debug(
                    "release_tokens(decode) returned False instance_id=%s endpoint_id=%s state=%s",
                    decode_resource.instance.id,
                    decode_resource.endpoint.id,
                    self.req_info.state,
                )

    async def _process_stream_chunks(
        self,
        req_data: dict,
        request_info: dict,
        prefill_resource: ScheduledResource,
        decode_resource: ScheduledResource
    ):
        """Process stream chunks from decode resource"""
        release_kv = False
        stream_adapter_state: dict = {}

        async with self._manage_client_context(
                decode_resource
            ) as decode_client:
            async for chunk in self.forward_stream_request(
                req_data=req_data,
                client=decode_client,
                timeout=self.config.exception_config.infer_timeout):
                if not release_kv and chunk:
                    release_kv = True
                    await self.release_kv(prefill_resource)

                processed_chunk = recompute_common.process_stream_chunk(
                    chunk,
                    request_info,
                    req_data,
                    retry_count=self._recompute.retry_count,
                    logger=self.logger,
                    nonstream_retry_patch=None,
                    entry_api=self.req_info.effective_entry_api(),
                    stream_adapter_state=stream_adapter_state,
                    req_id=self.req_info.req_id,
                    recompute_enabled=self.config.exception_config.recompute_enabled,
                    prompt_tokens_details=self.req_info.prompt_tokens_details,
                )
                if processed_chunk is None:  # Recomputation or policy block
                    if request_info.get(recompute_common.RECOMPUTE_BLOCKED_BY_POLICY_KEY):
                        raise recompute_common.recompute_disabled_http_exception()
                    await self._handle_recomputation(
                        req_data,
                        request_info,
                        prefill_resource,
                        prefill_kv_released=release_kv,
                    )
                    return

                if processed_chunk:
                    yield processed_chunk

    async def _handle_recomputation(
        self,
        req_data: dict,
        request_info: dict,
        prefill_resource: ScheduledResource,
        *,
        prefill_kv_released: bool = False,
    ):
        """Handle recomputation logic.

        Prefill **tokens** are already released in ``_forward_p_request``; only **KV**
        may still be held. If the stream loop has not yet called ``release_kv`` on the
        first chunk, release KV here. If KV was already released, prefill has nothing
        left to release.
        """
        rmax = self.config.exception_config.recompute_retry_limit
        self._check_recompute_limit(self._recompute.retry_count, rmax)
        self._recompute.wants_retry = True
        self.req_info.update_state(ReqState.RECOMPUTE)
        if not prefill_kv_released:
            await self.release_kv(prefill_resource)
        self._recompute.retry_count = self._prepare_recompute_retry(
            req_data, request_info, self._recompute.retry_count
        )

    async def _handle_stream_error(self, prefill_resource: ScheduledResource, error: Exception):
        """Handle streaming errors"""
        if not self.first_chunk_sent:
            await self.release_kv(prefill_resource)

        self.logger.error(
            "Error during decode forward %s, aborted request %s, error: %s",
            self.req_info.api,
            self.req_info.req_id,
            str(error),
        )
