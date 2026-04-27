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
import time
from typing import AsyncGenerator, Any

import anyio
from fastapi import HTTPException, status
from fastapi.responses import StreamingResponse, JSONResponse

from motor.common.resources.instance import PDRole
from motor.coordinator.models.constants import CHAT_COMPLETION_PREFIX, COMPLETION_PREFIX, COMPLETION_SUFFIX
from motor.coordinator.models.constants import REQUEST_ID_KEY
from motor.coordinator.models.request import ReqState
from motor.coordinator.router.strategies.base import BaseRouter, RecomputeState
import motor.coordinator.router.recompute as recompute_common
from motor.coordinator.router.adapters.completion_to_chat import adapt_completion_nonstream_to_chat
from motor.coordinator.tracer.tracing import TracerManager


class SeparateCDPRouter(BaseRouter):
    """CDP decode router (metaserver prefill and recompute)."""

    def __init__(
        self,
        req_info,
        config,
        scheduler,
        request_manager,
        workload_action_handler=None,
    ):
        super().__init__(
            req_info,
            config,
            scheduler,
            request_manager,
            workload_action_handler,
        )
        self._recompute = RecomputeState()
        self._stream_chunk_sent_to_client = False

    async def handle_request(self) -> StreamingResponse | JSONResponse:

        req_data = self._gen_d_request()

        if self.req_info.req_data.get("stream", False):
            return StreamingResponse(
                self._generate_stream_response(req_data),
                media_type="text/event-stream"
            )
        return await self._generate_response(req_data)

    async def handle_metaserver_request(self) -> dict[str, Any]:
        """
        Handles the Prefill requests by metaserver
        """
        self.is_meta = True
        req_data = await self._gen_p_request()
        trace_obj = self.req_info.trace_obj
        headers = trace_obj.get_trace_headers_dict()
        if headers:
            trace_context = TracerManager().extract_trace_context(headers)
        else:
            trace_context = trace_obj.parent_context
        span_ctx = TracerManager().tracer.start_as_current_span("CDP_Prefill", context=trace_context)
        t0_metaserver = time.perf_counter()
        try:
            with span_ctx as span:
                trace_obj.meta_span = span
                trace_obj.meta_trace_headers = TracerManager().inject_trace_context()
                trace_obj.set_trace_attribute("requestId", self.req_info.req_id, is_meta=True)
                trace_obj.set_trace_attribute("stream", False, is_meta=True)
                # Schedule Prefill instance and forward the request
                async with self._manage_resource_context(PDRole.ROLE_P, self.release_all) as resource, \
                           self._manage_client_context(resource) as client:

                    cancel_scope = anyio.CancelScope()
                    self.req_info.set_cancel_scope(cancel_scope, PDRole.ROLE_P)
                    with cancel_scope:
                        response = await self.forward_request(
                                req_data, client, self.config.exception_config.first_token_timeout
                            )
                        resp_json = response.json()

                        self.logger.debug("Prefill response received: %s", resp_json)
                        usage = resp_json.get("usage", {})
                        if usage:
                            if 'prompt_tokens_details' in usage:
                                prompt_tokens_details = usage['prompt_tokens_details']
                                if prompt_tokens_details is None:
                                    prompt_tokens_details = {"cached_tokens": 0}
                                self.req_info.update_prompt_tokens_details(prompt_tokens_details)
                        self.req_info.update_state(ReqState.PREFILL_END)
                        elapsed_ms = (time.perf_counter() - t0_metaserver) * 1000
                        self.logger.info(
                            "Scheduling latency stage=metaserver_request_total elapsed_ms=%.2f role=ROLE_P",
                            elapsed_ms
                        )
                        return resp_json
                    if self.req_info.is_cancelled:
                        raise Exception("exception occurred in Decode request")
        except asyncio.CancelledError:
            self.logger.info("Metaserver request was cancelled")
            self.req_info.cancel_scope()
            raise
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0_metaserver) * 1000
            self.logger.info(
                "Scheduling latency stage=metaserver_request_total elapsed_ms=%.2f error=%s",
                elapsed_ms, e
            )
            self.req_info.cancel_scope()
            self.req_info.update_state(ReqState.EXCEPTION)
            raise e

    async def _generate_stream_response(self, req_data: dict[str, Any]) -> AsyncGenerator[str, None]:
        """
        Handles streaming Decode requests
        """
        trace_obj = self.req_info.trace_obj
        with self._trace_span("CDP_Decode_stream", True):
            self.logger.debug("Handling streaming Decode request")
            max_retry = self.config.exception_config.transport_retry_limit
            rmax = self.config.exception_config.recompute_retry_limit
            last_error_str: str | None = None
            for attempt in range(max_retry):
                self._stream_chunk_sent_to_client = False
                try:
                    while True:
                        # Recompute path pops kv_transfer_params; each decode leg needs metaserver KV fields.
                        self._attach_cdp_decode_kv_params(req_data)
                        request_info = recompute_common.extract_request_info(req_data)
                        stream_adapter_state: dict = {}
                        recompute_broke_stream = False
                        async with self._manage_request_context(), \
                                self._manage_resource_context(PDRole.ROLE_D, self.release_tokens) as resource, \
                                self._manage_client_context(resource) as client:

                            cancel_scope = anyio.CancelScope()
                            self.req_info.set_cancel_scope(cancel_scope, PDRole.ROLE_D)
                            with cancel_scope:
                                async for chunk in self.forward_stream_request(
                                        req_data, client, self.config.exception_config.infer_timeout
                                ):
                                    # ``process_stream_chunk`` only invokes ``nonstream_retry_patch`` when
                                    # ``retry_count > 0`` and ``request_info["stream_flag"]`` is False (client
                                    # ``stream: false``).  CDP streaming decode always uses ``stream: true``, so
                                    # the patch is never used here; pass ``None`` explicitly.
                                    out = recompute_common.process_stream_chunk(
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
                                    if out is None:
                                        if request_info.get(
                                            recompute_common.RECOMPUTE_BLOCKED_BY_POLICY_KEY
                                        ):
                                            raise recompute_common.recompute_disabled_http_exception()
                                        # Recompute after partial output: rewrite request from token cache.
                                        if self._stream_chunk_sent_to_client:
                                            self.logger.debug(
                                                "Stream recompute after partial output; continuing "
                                                "request_id=%s",
                                                self.req_info.req_id,
                                            )
                                        self._check_recompute_limit(self._recompute.retry_count, rmax)
                                        self.req_info.update_state(ReqState.RECOMPUTE)
                                        # If the client already received stream chunks, the retry run will re-emit the
                                        # same prefix from the engine; do not retain old text for any merge buffer.
                                        if self._stream_chunk_sent_to_client:
                                            self._recompute.total_generated_token = ""
                                        else:
                                            self._recompute.total_generated_token = request_info[
                                                "generated_token"
                                            ]
                                        self._recompute.retry_count = self._prepare_recompute_retry(
                                            req_data, request_info, self._recompute.retry_count
                                        )
                                        recompute_broke_stream = True
                                        break
                                    if out:
                                        self._stream_chunk_sent_to_client = True
                                        yield out
                                else:
                                    self.req_info.update_state(ReqState.DECODE_END)
                                    self.logger.info(trace_obj.set_end_and_ttft_tpot())
                                    return
                            if not recompute_broke_stream and self.req_info.is_cancelled:
                                raise Exception("exception occurred in Prefill request")
                        if recompute_broke_stream:
                            self._bump_req_id_after_recompute_workloads_released(
                                self._recompute.retry_count
                            )
                            continue
                except asyncio.CancelledError:
                    self.logger.info("The streaming request was terminated because of "
                                    "infer timeout or client disconnect.")
                    self.req_info.cancel_scope()
                    raise
                except HTTPException:
                    self.req_info.cancel_scope()
                    raise
                except Exception as e:
                    last_error_str = self._log_cdp_decode_retry_error(
                        "streaming Decode", attempt, max_retry, e, last_error_str
                    )
                    self.req_info.cancel_scope()

                    # If chunk was already sent, cannot retry the HTTP stream.
                    # Send error chunk and terminate.
                    if self._stream_chunk_sent_to_client or attempt == max_retry - 1:
                        trace_obj.set_trace_status(e)
                        self.req_info.update_state(ReqState.EXCEPTION)
                        yield self._generate_streaming_error_chunk(e)
                        return

                    wait_time = self.config.exception_config.retry_delay * (2 ** attempt)
                    self.logger.info("Retrying streaming request in %.2f seconds...", wait_time)
                    await asyncio.sleep(wait_time)

    async def _generate_response(self, req_data: dict[str, Any]) -> JSONResponse:
        """
        Handles non-streaming Decode requests
        """
        trace_obj = self.req_info.trace_obj
        with self._trace_span("CDP_Decode", False):
            self.logger.debug("Handling non-streaming Decode request")
            max_retries = self.config.exception_config.transport_retry_limit
            rmax = self.config.exception_config.recompute_retry_limit
            last_error_str: str | None = None
            for attempt in range(max_retries):
                try:
                    while True:
                        self._attach_cdp_decode_kv_params(req_data)
                        async with self._manage_request_context(), \
                                self._manage_resource_context(PDRole.ROLE_D, self.release_tokens) as resource, \
                                self._manage_client_context(resource) as client:

                            cancel_scope = anyio.CancelScope()
                            self.req_info.set_cancel_scope(cancel_scope, PDRole.ROLE_D)
                            recompute_broke = False
                            with cancel_scope:
                                response = await self.forward_request(
                                        req_data, client, self.config.exception_config.infer_timeout
                                    )
                                body = response.json()
                                if recompute_common.is_recomputed_nonstream_response(body):
                                    if not self.config.exception_config.recompute_enabled:
                                        raise recompute_common.recompute_disabled_http_exception()
                                    self._check_recompute_limit(self._recompute.retry_count, rmax)
                                    self.req_info.update_state(ReqState.RECOMPUTE)
                                    recompute_common.validate_nonstream_recompute_body(
                                        self.req_info.req_id,
                                        body,
                                        logger=self.logger,
                                    )
                                    ri = recompute_common.build_request_info_for_nonstream_recompute(
                                        req_data, body
                                    )
                                    # Each non-stream recomputed body carries the full partial for that round; += would
                                    # duplicate prefixes across recompute rounds.
                                    self._recompute.total_generated_token = ri["generated_token"]
                                    self._recompute.retry_count = self._prepare_recompute_retry(
                                        req_data, ri, self._recompute.retry_count
                                    )
                                    recompute_broke = True
                                    break
                                self.req_info.update_state(ReqState.DECODE_END)
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
                                if self.req_info.prompt_tokens_details:
                                    if body.get("usage", {}):
                                        body['usage']['prompt_tokens_details'] = (
                                            self.req_info.prompt_tokens_details
                                        )
                                return JSONResponse(content=body)
                            if not recompute_broke and self.req_info.is_cancelled:
                                raise Exception("exception occurred in Prefill request")
                        if recompute_broke:
                            self._bump_req_id_after_recompute_workloads_released(
                                self._recompute.retry_count
                            )
                            continue
                except asyncio.CancelledError:
                    self.logger.info("The non streaming request was terminated because of "
                                    "infer timeout or client disconnect.")
                    self.req_info.cancel_scope()
                    raise
                except HTTPException:
                    self.req_info.cancel_scope()
                    raise
                except Exception as e:
                    last_error_str = self._log_cdp_decode_retry_error(
                        "post Decode", attempt, max_retries, e, last_error_str
                    )
                    self.req_info.cancel_scope()
                    trace_obj.set_trace_exception(e)

                    if attempt < max_retries - 1:
                        wait_time = self.config.exception_config.retry_delay * (2 ** attempt)
                        self.logger.info("Retrying non-streaming request in %.2f seconds...", wait_time)
                        await asyncio.sleep(wait_time)
                        continue

                    self.req_info.update_state(ReqState.EXCEPTION)
                    raise e

    def _log_cdp_decode_retry_error(
        self, label: str, attempt: int, max_attempts: int, err: Exception, last_error_str: str | None) -> str:
        """Log one decode retry failure: full traceback on first attempt or when the message changes."""
        err_str = str(err)
        if attempt == 0 or err_str != last_error_str:
            self.logger.error(
                "Error in %s (attempt %d/%d): %s",
                label, attempt + 1, max_attempts, err_str, exc_info=True
            )
        else:
            self.logger.warning(
                "Error in %s (attempt %d/%d): same error as previous attempt: %s",
                label, attempt + 1, max_attempts, err_str
            )
        return err_str

    def _worker_metaserver_url(self) -> str:
        host = self.config.api_config.coordinator_api_host
        worker_port = getattr(self.config, "worker_metaserver_port", None)
        if worker_port is None:
            raise RuntimeError(
                "CDP separate mode requires worker_metaserver_base_port > 0 in "
                "inference_workers_config so that each Worker has a metaserver port; "
                "worker_metaserver_port is not set."
            )
        return f"http://{host}:{worker_port}/v1/metaserver"

    def _attach_cdp_decode_kv_params(self, req_data: dict[str, Any]) -> dict[str, Any]:
        """Set kv_transfer_params and return_token_ids on decode req_data (idempotent per leg)."""
        url = self._worker_metaserver_url()
        req_data["kv_transfer_params"] = {
            "do_remote_decode": False,
            "do_remote_prefill": True,
            "metaserver": url,
        }
        req_data["return_token_ids"] = (
            self.config.exception_config.recompute_enabled
            or req_data.get("_client_return_token_ids", False)
        )
        return req_data

    def _gen_d_request(self) -> dict:
        """Generate D request parameters.

        CDP separate mode requires worker metaserver: config must have
        worker_metaserver_base_port > 0 so that worker_metaserver_port is set at runtime.
        D instances call this Worker's metaserver URL so get_req_info succeeds.
        """
        req_data = self.req_info.req_data.copy()
        return self._attach_cdp_decode_kv_params(req_data)

    async def _gen_p_request(self) -> dict:
        """Generate P request parameters"""
        kv_transfer_params = self.req_info.req_data.copy()

        # get origin req_info reference for update request state
        self.req_info = await self._get_origin_request_info(kv_transfer_params)

        return self._apply_prefill_params(
            self.req_info.req_data, kv_transfer_params=kv_transfer_params
        )

    async def _get_origin_request_info(self, kv_transfer_params: dict):
        def trim_request_id_prefix(vllm_request_id: str) -> str:
            original_id = vllm_request_id
            if vllm_request_id.startswith(CHAT_COMPLETION_PREFIX):
                original_id = vllm_request_id.removeprefix(CHAT_COMPLETION_PREFIX)
            elif vllm_request_id.startswith(COMPLETION_PREFIX) and vllm_request_id.endswith(COMPLETION_SUFFIX):
                original_id = vllm_request_id.removeprefix(COMPLETION_PREFIX).removesuffix(COMPLETION_SUFFIX)
            return original_id
        request_id = trim_request_id_prefix(kv_transfer_params["request_id"])

        req_info = await self._request_manager.get_req_info(request_id)
        if not req_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, 
                detail=f"Request ID {request_id} not found in RequestManager"
            )
        # update real req_id as prefix for logger adaptor
        if isinstance(self.logger.extra, dict):
            self.logger.extra[REQUEST_ID_KEY] = req_info.req_id

        return req_info
