#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
from fastapi.responses import StreamingResponse
from fastapi import HTTPException, status

from motor.coordinator.models.request import ReqState, ScheduledResource
from motor.coordinator.router.base_router import BaseRouter
from motor.config.coordinator import CoordinatorConfig
from motor.resources.instance import PDRole
from motor.utils.logger import get_logger

logger = get_logger(__name__)


class SeparatePDRouter(BaseRouter):
    """Handle request with separate P and D instances (original behavior)"""
    
    def __init__(self, req_info):
        super().__init__(req_info)
        self.retry = True  # Need to re-request when recomputing
        self.retry_count = 0    # Recomputation count
        self.total_generated_token = "" # Record all generated tokens during recomputation
        
    async def handle_request(self) -> StreamingResponse:
        """Handle request with separate P and D instances"""
        
        async def generate_stream():
            while self.retry:
                self.first_chunk_sent = False
                self.retry = False
                
                prefill_resource: ScheduledResource = None
                try:
                    # Schedule P instance
                    prefill_resource = self.prepare_resource(PDRole.ROLE_P)
                    # Forward P request
                    p_resp_json = await self.__forward_p_request(prefill_resource)
                    logger.debug("Prefill response received: %s", p_resp_json)
                except Exception as e:
                    logger.error("Error occurred while forwarding P request: %s", e)
                    if isinstance(e, HTTPException):
                        error_response = {
                            "status_code": e.status_code,
                            "error_type": type(e).__name__,
                            "error_message": e.detail,
                        }
                    else:
                        error_response = {
                            "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                        }
                    yield f"data: {json.dumps(error_response)}".encode('utf-8')
                    return
                finally:
                    if prefill_resource and self.req_info.state != ReqState.PREFILL_END:
                        # When forwarding fails, releases p tokens and kvcache
                        if not self.release_all(prefill_resource):
                            logger.warning(f"Fail to release prefill resource, \
                                instance id: {prefill_resource.instance.id}, \
                                endpoint id: {prefill_resource.endpoint.id}, \
                                req state: {self.req_info.state}")

                decode_resource: ScheduledResource = None
                try:
                    # Schedule D instance
                    decode_resource = self.prepare_resource(PDRole.ROLE_D)
                    # Forward D request
                    async for chunk in self.__forward_d_request(p_resp_json, prefill_resource, decode_resource):
                        yield chunk
                except Exception as e:
                    logger.error("Error occurred while forwarding Decode request: %s", e)
                    raise e
                finally:
                    # After streaming done or error occurred, release tokens
                    if decode_resource and not self.release_tokens(decode_resource):
                        logger.warning(f"Fail to release decode resource, instance id: {decode_resource.instance.id}, \
                            endpoint id: {decode_resource.endpoint.id}, \
                            req state: {self.req_info.state}")
        return StreamingResponse(generate_stream(),
                                 media_type="application/json")
    
    def __gen_p_request(self) -> dict:
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

    async def __forward_p_request(self, resource: ScheduledResource):
        """Forward P request to the given resource"""
        req_data = self.__gen_p_request()
        # P non-streaming request
        async for response in self.forward_post_request(req_data=req_data, resource=resource):
            resp_json = response.json()
            self.req_info.update_state(ReqState.PREFILL_END)
            self.release_tokens(resource)
        return resp_json

    def __gen_d_request(self, resp_json: dict) -> dict:
        """Generate D request parameters"""
        req_data = self.req_info.req_data.copy()
        kv_transfer_params = resp_json.get('kv_transfer_params', {})
        if kv_transfer_params:
            req_data["kv_transfer_params"] = kv_transfer_params
        return req_data
        
    async def __forward_d_request(self, resp_json: dict,
                                prefill_resource: ScheduledResource,
                                decode_resource: ScheduledResource):
        """Forward D request to the given resource"""
        try:
            req_data = self.__gen_d_request(resp_json)
            request_info = self.__extract_request_info(req_data)
            
            async for chunk in self.__process_stream_chunks(req_data, request_info, 
                                                        prefill_resource, decode_resource):
                yield chunk
                
            self.req_info.update_state(ReqState.DECODE_END)
            self.release_tokens(decode_resource)
            logger.info(f"Completed streaming for request {self.req_info}")
        except Exception as e:
            self.__handle_stream_error(prefill_resource, e)
            if isinstance(e, HTTPException):
                error_response = {
                    "status_code": e.status_code,
                    "error_type": type(e).__name__,
                    "error_message": e.detail,
                }
            else:
                error_response = {
                    "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                }
            yield f"data: {json.dumps(error_response)}".encode('utf-8')

    def __extract_request_info(self, req_data: dict) -> dict:
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

    async def __process_stream_chunks(self, req_data: dict, request_info: dict,
                                prefill_resource: ScheduledResource,
                                decode_resource: ScheduledResource):
        """Process stream chunks from decode resource"""
        release_kv = False
        
        async for chunk in self.forward_stream_request(req_data=req_data, resource=decode_resource):
            if not release_kv and chunk:
                release_kv = True
                self.release_kv(prefill_resource)
                
            processed_chunk = self.__process_single_chunk(chunk, request_info, req_data)
            if processed_chunk is None:  # Recomputation triggered
                self.__handle_recomputation(req_data, request_info, prefill_resource, decode_resource)
                return
                
            if processed_chunk:
                yield processed_chunk

    def __process_single_chunk(self, chunk: bytes, request_info: dict, req_data: dict):
        """Process a single chunk and return processed chunk or None for recomputation"""
        chunk_json = self.__parse_chunk(chunk)
        if chunk_json is None:
            return chunk
        
        choices = chunk_json.get("choices", [])
        if not choices:
            return chunk
        
        content = self.__extract_content_from_choice(choices[0])
        request_info["generated_token"] += content
        
        # Update completion tokens
        self.__update_completion_tokens(request_info, chunk_json)
        
        # Check for recomputation
        if choices[0].get("stop_reason") == "recomputed":
            return None  # Signal recomputation needed
        
        # Handle retry case for non-streaming
        if self.retry_count > 0 and not request_info["stream_flag"]:
            self.__update_retry_response(choices[0], request_info)
            return json.dumps(chunk_json).encode("utf-8")
        
        return chunk

    def __parse_chunk(self, chunk: bytes) -> dict:
        """Parse chunk bytes to JSON, return None if parsing fails"""
        try:
            chunk_str = chunk.decode("utf-8").strip()
        except UnicodeDecodeError:
            logger.debug("Skipping chunk: %s", chunk)
            return None
            
        if not chunk_str:
            return None
            
        if chunk_str.startswith("data: "):
            chunk_str = chunk_str[len("data: "):]
            
        try:
            return json.loads(chunk_str)
        except json.JSONDecodeError:
            logger.debug("Skipping chunk str: %s", chunk_str)
            return None

    def __extract_content_from_choice(self, choice: dict) -> str:
        """Extract content from choice delta/message/text"""
        delta = choice.get("delta") or {}
        message = choice.get("message") or {}
        
        return (
            delta.get("content") or 
            message.get("content") or 
            choice.get("text") or 
            ""
        )

    def __update_completion_tokens(self, request_info: dict, chunk_json: dict):
        """Update completion tokens count"""
        completion_tokens_key = "completion_tokens"
        usage = chunk_json.get("usage", {})
        
        if request_info["stream_flag"]:
            request_info[completion_tokens_key] += 1
        else:
            request_info[completion_tokens_key] += usage.get("completion_tokens", 0)

    def __update_retry_response(self, choice: dict, request_info: dict):
        """Update response for retry case in non-streaming mode"""
        self.total_generated_token += request_info["generated_token"]
        
        if request_info["chat_flag"]:
            choice["message"]["content"] = self.total_generated_token
        else:
            choice["text"] = self.total_generated_token

    def __handle_recomputation(self, req_data: dict, request_info: dict,
                            prefill_resource: ScheduledResource,
                            decode_resource: ScheduledResource):
        """Handle recomputation logic"""
        if self.retry_count >= CoordinatorConfig().exception_config.max_retry:
            raise HTTPException(status.HTTP_507_INSUFFICIENT_STORAGE, "Insufficient compute resource")
        
        self.retry = True
        self.req_info.update_state(ReqState.RECOMPUTE)
        self.total_generated_token += request_info["generated_token"]
        self.release_all(prefill_resource)
        self.release_tokens(decode_resource)
        
        self.__prepare_retry_request(req_data, request_info)

    def __prepare_retry_request(self, req_data: dict, request_info: dict):
        """Prepare request data for retry"""
        self.retry_count += 1
        new_prompt = request_info["origin_prompt"] + request_info["generated_token"]
        
        if request_info["chat_flag"]:
            req_data["messages"][0]["content"] = new_prompt
        else:
            req_data["prompt"] = new_prompt
        
        # Adjust max_tokens for retry
        token_adjustment = 1 if request_info["stream_flag"] else 0
        req_data["max_tokens"] = (request_info["origin_max_tokens"] - 
                                request_info["completion_tokens"] + token_adjustment)
        
        self.req_info.req_len = len(json.dumps(req_data).encode("utf-8"))
        self.req_info.req_data = req_data
        
        logger.info("Recomputing request %s, retry count: %d, new req_info: %s",
                    self.req_info.req_id, self.retry_count, self.req_info)

    def __handle_stream_error(self, prefill_resource: ScheduledResource, error: Exception):
        """Handle streaming errors"""
        if not self.first_chunk_sent:
            self.release_kv(prefill_resource)
        
        logger.error("Error during streaming from decoder %s, aborted request %s, error: %s",
                    self.req_info.api, self.req_info.req_id, str(error))
