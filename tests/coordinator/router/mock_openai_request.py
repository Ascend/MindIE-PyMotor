#!/usr/bin/env python3
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
import json
import time

from fastapi import Request, status
import httpx

from motor.coordinator.models.request import RequestInfo, ReqState
from motor.common.utils.logger import get_logger

logger = get_logger(__name__)


def mock_stream_response(request_data: dict, max_num = 10, recomputed: bool = False):
    """Generate mock streaming response for testing purposes
    
    Args:
        request_data: The request data containing messages and parameters
        max_num: Maximum number to generate in the sequence
        recomputed: Whether to simulate recomputation scenario
    """
    # Parse request data
    messages = request_data.get("messages", [])
    max_tokens = request_data.get("max_tokens", 10)
    recompute_threshold = 2
    
    # Get the last character from user input as starting point
    if not messages or not messages[0].get("content"):
        return
    content: str = messages[0]["content"]
    
    # Get the last character, convert to number, then generate subsequent numbers
    try:
        chunks = content.split(',')
        last_num = chunks[-1]
        start_num = int(last_num)
    except (ValueError, IndexError):
        logger.info(f"chunks:{chunks}")
        logger.info(f"last_num:{last_num}")
        start_num = 0
    
    # Generate response stream
    for i in range(max_tokens):
        current_num = start_num + 1 + i
        
        # Check if this is the last token
        is_last_token = (i == max_tokens - 1)
        is_finish = current_num >= max_num
        
        response_chunk = {
            "choices": [{
                "delta": {"content": ','+str(current_num)},
                "finish_reason": "stop" if is_finish else None,
                "stop_reason": "reach_max_token" if not is_finish and is_last_token else None
            }]
        }
        
        if recomputed and i >= recompute_threshold:
            response_chunk["choices"][0]["stop_reason"] = "recomputed"
            response_chunk["choices"][0]["delta"]["content"] = ""
        
        chunk_bytes = f"data: {json.dumps(response_chunk)}\n\n".encode('utf-8')
        yield chunk_bytes
        
        if is_last_token or is_finish: break
   
        
def mock_nostream_response(request_data: dict, max_num = 10, recomputed: bool = False):
    """Generate mock non-streaming response for testing purposes
    
    Args:
        request_data: The request data containing messages and parameters
        max_num: Maximum number to generate in the sequence
        recomputed: Whether to simulate recomputation scenario
    """
    # Parse request data
    messages = request_data.get("messages", [])
    max_tokens = request_data.get("max_tokens", 10)
    recompute_threshold = 2
    
    # Get the last character from user input as starting point
    if not messages or not messages[0].get("content"):
        return
    content: str = messages[0]["content"]
    
    # Get the last character, convert to number, then generate subsequent numbers
    try:
        chunks = content.split(',')
        last_num = chunks[- 1]
        start_num = int(last_num)
    except (ValueError, IndexError):
        logger.info(f"chunks:{chunks}")
        logger.info(f"last_num:{last_num}")
        start_num = 0
    
    # Generate response
    all_content = ''
    completion_tokens = 0
    for i in range(max_tokens):
        current_num = start_num + 1 + i
        
        # Check if this is the last token
        is_last_token = (i == max_tokens - 1)
        is_finish = current_num >= max_num
        
        if not (recomputed and i >= recompute_threshold):
            all_content += ','+str(current_num)
            completion_tokens = i + 1
            
        if not is_last_token and not is_finish and not (recomputed and i >= recompute_threshold):
            continue
        
        response_chunk = {
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": all_content,
                    },
                    "finish_reason": "stop" if is_finish else None,
                    "stop_reason": "reach_max_token" if not is_finish and is_last_token else None,
                }
            ],
            "usage": {
                "prompt_tokens": start_num,
                "total_tokens": start_num + completion_tokens,
                "completion_tokens": completion_tokens,
                "prompt_tokens_details": None
            },
        }
        
        
        if recomputed and i >= recompute_threshold:
            response_chunk["choices"][0]["stop_reason"] = "recomputed"
        
        chunk_bytes = json.dumps(response_chunk).encode('utf-8')
        yield chunk_bytes
        
        if is_last_token or is_finish: break


async def create_mock_request_info(api = "/v1/chat/completions", max_tokens = 100, stream = True):
    # Create a mock scope for the request
    scope = {
        "type": "http",
        "method": "POST",
        "path": api,
        "headers": [],
    }

    # Create a request body
    request_body = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": stream,
        "stream_options": {"include_usage": True},
        "max_tokens": max_tokens
    }
    
    # Create a mock request object
    request = Request(scope)
    request._body = json.dumps(request_body).encode()
    request_body = await request.body()
    req_len = len(request_body)
    request_json = await request.json()

    # Create a RequestInfo
    req_info = RequestInfo(
        req_id="test-id",
        req_data=request_json.copy(),
        req_len=req_len,
        api=api
    )
    
    return req_info

  
# Create a mock response class that supports async context manager
class MockStreamResponse:
    """Mock OpenAI streaming response"""
    def __init__(self, request_data: dict, recomputed: bool = True, exc: Exception = None):
        self.recomputed = recomputed
        self.exc = exc
        self.request_data = request_data
        self.is_success = exc is None
        self.status_code = status.HTTP_200_OK
        
    async def aread(self):
        if isinstance(self.exc, httpx.HTTPStatusError):
            self.text = self.exc.response.text
        elif isinstance(self.exc, httpx.RequestError):
            self.text = str(self.exc)
        return self.text
        
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    def raise_for_status(self):
        if self.exc:
            raise self.exc

    async def aiter_bytes(self):
        if self.exc:
            return
        stream = self.request_data.get("stream", True)
        mock_response = mock_stream_response if stream else mock_nostream_response
        for chunk in mock_response(request_data = self.request_data, recomputed = self.recomputed):
            await asyncio.sleep(0.00001)
            yield chunk
