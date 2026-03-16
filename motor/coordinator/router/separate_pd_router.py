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

import json
from fastapi.responses import StreamingResponse
from fastapi import HTTPException, status

from motor.coordinator.domain import ScheduledResource
from motor.coordinator.models.request import ReqState
from motor.coordinator.models.response import ErrorResponse
from motor.coordinator.router.base_router import BaseRouter
from motor.config.coordinator import CoordinatorConfig
from motor.common.resources.instance import PDRole


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
        self.retry = True  # Need to re-request when recomputing
        self.retry_count = 0  # Recomputation count
        self.total_generated_token = ""  # Record all generated tokens during recomputation
        self.is_finished = False

    @staticmethod
    def _extract_content_from_choice(choice: dict) -> str:
        """Extract content from choice delta/message/text"""
        delta = choice.get("delta") or {}
        message = choice.get("message") or {}

        return (
            delta.get("content")
            or message.get("content")
            or choice.get("text")
            or ""
        )

    @staticmethod
    def _update_completion_tokens(request_info: dict, chunk_json: dict):
        """Update completion tokens count"""
        completion_tokens_key = "completion_tokens"
        usage = chunk_json.get("usage", {})

        if request_info["stream_flag"]:
            request_info[completion_tokens_key] += 1
        else:
            request_info[completion_tokens_key] += usage.get("completion_tokens", 0)

    async def generate_stream(self):
        for attempt in range(self.config.exception_config.max_retry):
            # set True to start forward request
            self.retry = True
            while self.retry:
                self.first_chunk_sent = False
                # set False to avoid recompute
                self.retry = False

                async for chunk in self.process_single_attempt(attempt):
                    yield chunk
                if self.is_finished:
                    return

    async def handle_request(self) -> StreamingResponse:
        """Handle request with separate P and D instances"""
        return StreamingResponse(self.generate_stream(),
                                 media_type="application/json")

    async def process_single_attempt(self, attempt):
        prefill_resource: ScheduledResource = None
        try:
            # Schedule P instance
            prefill_resource = await self.prepare_resource(PDRole.ROLE_P)
            # Forward P request
            p_resp_json = await self._forward_p_request(prefill_resource)
            self.logger.debug("Prefill response received: %s", p_resp_json)
        except Exception as e:
            self.logger.error("Error occurred while forwarding P request: %s", e)
            if attempt != self.config.exception_config.max_retry - 1:
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
                yield chunk
            if not self.retry:
                self.is_finished = True
                return
        except Exception as e:
            self.logger.error("Error occurred while forwarding Decode request: %s", e)
            if self.first_chunk_sent or attempt == self.config.exception_config.max_retry - 1:
                yield self._generate_streaming_error_chunk(e)
        finally:
            if decode_resource:
                released = await self.release_tokens(decode_resource)
                if not released:
                    self.logger.debug(
                        "release_tokens(decode) returned False instance_id=%s endpoint_id=%s state=%s",
                        decode_resource.instance.id, decode_resource.endpoint.id, self.req_info.state)

    def _gen_p_request(self) -> dict:
        """Generate P request parameters"""
        req_data = self.req_info.req_data.copy()
        req_data['kv_transfer_params'] = {
            "do_remote_decode": True,
            "do_remote_prefill": False,
            "remote_engine_id": None,
            "remote_block_ids": None,
            "remote_host": None,
            "remote_port": None,
            "aborted_request": [],
        }
        req_data["stream"] = False
        req_data["max_tokens"] = 1
        req_data["min_tokens"] = 1
        if "stream_options" in req_data:
            del req_data["stream_options"]
        return req_data

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
            self.req_info.update_state(ReqState.PREFILL_END)
            await self.release_tokens(resource)
            return resp_json

    def _gen_d_request(self, resp_json: dict) -> dict:
        """Generate D request parameters"""
        req_data = self.req_info.req_data.copy()
        kv_transfer_params = resp_json.get('kv_transfer_params', {})
        if kv_transfer_params:
            req_data["kv_transfer_params"] = kv_transfer_params
        return req_data

    async def _forward_d_request(
        self,
        resp_json: dict,
        prefill_resource: ScheduledResource,
        decode_resource: ScheduledResource
    ):
        """Forward D request to the given resource"""
        try:
            req_data = self._gen_d_request(resp_json)
            request_info = self._extract_request_info(req_data)

            async for chunk in self._process_stream_chunks(
                req_data,
                request_info, 
                prefill_resource,
                decode_resource
            ):
                yield chunk

            self.req_info.update_state(ReqState.DECODE_END)
            self.logger.debug("Completed streaming for request %s", self.req_info)
        except Exception as e:
            await self._handle_stream_error(prefill_resource, e)
            raise e

    def _extract_request_info(self, req_data: dict) -> dict:
        """Extract request information from req_data"""
        stream_flag = bool(req_data.get("stream", False))
        chat_flag = "messages" in req_data

        if "prompt" in req_data:
            origin_prompt = req_data["prompt"]
        elif chat_flag:
            messages = req_data["messages"]
            origin_prompt = messages[0].get("content", "")
        else:
            origin_prompt = ""

        origin_max_tokens = req_data.get("max_tokens", 16)

        return {
            "stream_flag": stream_flag,
            "chat_flag": chat_flag,
            "origin_prompt": origin_prompt,
            "origin_max_tokens": origin_max_tokens,
            "generated_token": "",
            "completion_tokens": 0
        }

    async def _process_stream_chunks(
        self,
        req_data: dict,
        request_info: dict,
        prefill_resource: ScheduledResource,
        decode_resource: ScheduledResource
    ):
        """Process stream chunks from decode resource"""
        release_kv = False

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

                processed_chunk = self._process_single_chunk(chunk, request_info, req_data)
                if processed_chunk is None:  # Recomputation triggered
                    await self._handle_recomputation(req_data, request_info, prefill_resource, decode_resource)
                    return

                if processed_chunk:
                    yield processed_chunk

    def _process_single_chunk(self, chunk: bytes, request_info: dict, req_data: dict):
        """Process a single chunk and return processed chunk or None for recomputation"""
        chunk_json = self._parse_chunk(chunk)
        if chunk_json is None:
            return chunk

        choices = chunk_json.get("choices", [])
        if not choices:
            return chunk

        content = self._extract_content_from_choice(choices[0])
        request_info["generated_token"] += content

        # Update completion tokens
        self._update_completion_tokens(request_info, chunk_json)

        # Check for recomputation
        if choices[0].get("stop_reason") == "recomputed":
            return None  # Signal recomputation needed

        # Handle retry case for non-streaming
        if self.retry_count > 0 and not request_info["stream_flag"]:
            self._update_retry_response(choices[0], request_info)
            return json.dumps(chunk_json).encode("utf-8")

        return chunk

    def _parse_chunk(self, chunk: bytes) -> dict:
        """Parse chunk bytes to JSON, return None if parsing fails"""
        try:
            chunk_str = chunk.decode("utf-8").strip()
        except UnicodeDecodeError:
            self.logger.debug("Skipping chunk: %s", chunk)
            return None

        if not chunk_str:
            return None

        if chunk_str.startswith("data: "):
            chunk_str = chunk_str[len("data: "):]

        try:
            return json.loads(chunk_str)
        except json.JSONDecodeError:
            self.logger.debug("Skipping chunk str: %s", chunk_str)
            return None

    def _update_retry_response(self, choice: dict, request_info: dict):
        """Update response for retry case in non-streaming mode"""
        self.total_generated_token += request_info["generated_token"]

        if request_info["chat_flag"]:
            choice["message"]["content"] = self.total_generated_token
        else:
            choice["text"] = self.total_generated_token

    async def _handle_recomputation(
        self,
        req_data: dict,
        request_info: dict,
        prefill_resource: ScheduledResource,
        decode_resource: ScheduledResource
    ):
        """Handle recomputation logic"""
        if self.retry_count >= self.config.exception_config.max_retry:
            raise HTTPException(status.HTTP_507_INSUFFICIENT_STORAGE, "Insufficient compute resource")

        self.retry = True
        self.req_info.update_state(ReqState.RECOMPUTE)
        self.total_generated_token += request_info["generated_token"]
        await self.release_all(prefill_resource)
        await self.release_tokens(decode_resource)

        self._prepare_retry_request(req_data, request_info)

    def _prepare_retry_request(self, req_data: dict, request_info: dict):
        """Prepare request data for retry"""
        self.retry_count += 1
        new_prompt = request_info["origin_prompt"] + request_info["generated_token"]

        if request_info["chat_flag"]:
            req_data["messages"][0]["content"] = new_prompt
        else:
            req_data["prompt"] = new_prompt

        # Adjust max_tokens for retry
        token_adjustment = 1 if request_info["stream_flag"] else 0
        req_data["max_tokens"] = (
            request_info["origin_max_tokens"] - request_info["completion_tokens"] + token_adjustment
        )

        self.req_info.req_len = len(json.dumps(req_data).encode("utf-8"))
        self.req_info.req_data = req_data

        self.logger.info("Recomputing request %s, retry count: %d, new req_info: %s",
                         self.req_info.req_id, self.retry_count, self.req_info)

    async def _handle_stream_error(self, prefill_resource: ScheduledResource, error: Exception):
        """Handle streaming errors"""
        if not self.first_chunk_sent:
            await self.release_kv(prefill_resource)

        self.logger.error("Error during streaming from decoder %s, aborted request %s, error: %s",
                          self.req_info.api, self.req_info.req_id, str(error))
