#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


from pytest import MonkeyPatch
from fastapi import FastAPI, status, Request, HTTPException
from unittest.mock import AsyncMock, Mock, patch, MagicMock
from fastapi.testclient import TestClient
import pytest
import httpx

from motor.config.coordinator import DeployMode, CoordinatorConfig
from motor.coordinator.core.instance_manager import InstanceManager
from motor.coordinator.models.request import ScheduledResource
from motor.coordinator.router.base_router import BaseRouter
from motor.coordinator.router.separate_pd_router import SeparatePDRouter
from motor.resources.endpoint import WorkloadAction
from motor.resources.instance import Endpoint, PDRole, Instance, InsStatus, ParallelConfig
from tests.coordinator.router.mock_openai_request import MockStreamResponse, create_mock_request_info
from motor.coordinator.scheduler.scheduler import Scheduler
import motor.coordinator.router.router as router
from motor.utils.logger import get_logger

logger = get_logger(__name__)

app = FastAPI()
@app.post("/v1/chat/completions")
async def handle_completions(request: Request):
    return await router.handle_request(request)


# Create mock stream client
class MockAsyncClient:
    def __init__(self, recomputed: bool = True, fail_times: int = 0):
        self.recomputed = recomputed
        self.fail_times = fail_times
        self.fail_count = 0
        
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
    
    def stream(self, method, url, json=None, headers=None):
        if self.fail_count < self.fail_times:
            self.fail_count += 1
            return MockStreamResponse(json or {}, self.recomputed, httpx.HTTPStatusError(
                message="Simulated stream error",
                request=None,
                response=httpx.Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
            ))
            
        # Return an async context manager
        return MockStreamResponse(json or {}, self.recomputed)


class TestRouterPDSeparation:
    
    @pytest.fixture
    def client(self):
        return TestClient(app)
    
    @classmethod
    def create_mock_instance(self, instance_id, role):
        """Create a proper mock Instance object"""
        mock_instance = Instance(
            job_name=f"test-job-{instance_id}",
            model_name=f"test-model-{instance_id}",
            id=instance_id,
            role=role,
            status=InsStatus.ACTIVE,
            parallel_config=ParallelConfig(dp_size=1, tp_size=1),
            endpoints={}
        )
        return mock_instance
    
    @pytest.fixture
    def setup_pd_separation(self, monkeypatch: MonkeyPatch):
        host = "127.0.0.1"
        # Create proper instances for separate P/D flow
        mock_instance_p = self.create_mock_instance(0, PDRole.ROLE_P)
        mock_endpoint_p = Endpoint(id=0, ip=host, business_port="8000", mgmt_port="8000")
        mock_instance_p.endpoints = {host: {0: mock_endpoint_p}}
        
        mock_instance_d = self.create_mock_instance(1, PDRole.ROLE_D)
        mock_endpoint_d = Endpoint(id=1, ip=host, business_port="8001", mgmt_port="8000")
        mock_instance_d.endpoints = {host: {1: mock_endpoint_d}}
        
        # Mock functions
        def mock_is_available(self):
            return True
        
        def mock_get_available_instances(role):
            if role == PDRole.ROLE_U:  # PD hybrid role
                return []  # No PD hybrid instances, will use separate P/D
            elif role == PDRole.ROLE_P:
                return [mock_instance_p]
            elif role == PDRole.ROLE_D:
                return [mock_instance_d]
            return []
        
        def mock_select_instance_and_endpoint(self, role):
            if role == PDRole.ROLE_P:
                return mock_instance_p, mock_endpoint_p
            elif role == PDRole.ROLE_D:
                return mock_instance_d, mock_endpoint_d
            return None, None
        
        def mock_update_workload(self, instance: Instance, endpoint: Endpoint, req_id: str,
                        workload_action, request_length: int) -> bool:
            return True
        
        def mock_from_string(value):
            return DeployMode.CPCD_SEPARATE
        
        monkeypatch.setattr(InstanceManager, "is_available", mock_is_available)
        monkeypatch.setattr(InstanceManager, "get_available_instances", mock_get_available_instances)
        monkeypatch.setattr(Scheduler, "select_instance_and_endpoint", mock_select_instance_and_endpoint)
        monkeypatch.setattr(Scheduler, "update_workload", mock_update_workload)
        monkeypatch.setattr(DeployMode, "from_string", mock_from_string)
        monkeypatch.setattr(CoordinatorConfig().exception_config, "retry_delay", 0.0001)
    
    @pytest.mark.asyncio
    async def test_empty_request_body(self, client):
        """Test handling of empty request body"""
        response = client.post("/v1/chat/completions", content="")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json().get("detail") == "Empty request body"
        response = client.post("/v1/chat/completions", json="")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json().get("detail") == "Empty request json"
        
    @pytest.mark.asyncio
    async def test_scheduler_fail(self, monkeypatch: MonkeyPatch):
        
        def mock_is_available(self):
            return True
        monkeypatch.setattr(InstanceManager, "is_available", mock_is_available)
        
        def mock_select_instance_and_endpoint(self, role):
            return None
        monkeypatch.setattr(Scheduler, "select_instance_and_endpoint", mock_select_instance_and_endpoint)

        req_info = await create_mock_request_info()
        pd_router = SeparatePDRouter(req_info)
        
        chunks = []
        stream_resp = await pd_router.handle_request()
        async for chunk in stream_resp.body_iterator:
            chunks.append(chunk)
        chunk_str = b"".join(chunks).decode('utf-8')
            
        assert str(status.HTTP_503_SERVICE_UNAVAILABLE) in chunk_str
        assert f"Scheduling failed, role:{PDRole.ROLE_P}" in chunk_str
    
    @pytest.mark.asyncio
    async def test_gen_p_request_modifications(self, monkeypatch: MonkeyPatch, setup_pd_separation):
        """Test that gen_p_request correctly modifies request parameters"""
        KV_TRANSFER_KEY = "kv_transfer_params"
        max_tokens = 100
        stream = True
        req_info = await create_mock_request_info(max_tokens=max_tokens, stream=stream)
        
        generated_prefill_request = {}
        async def mock_forward_post_request(self, req_data: dict, resource: ScheduledResource):
            nonlocal generated_prefill_request
            generated_prefill_request = req_data
            logger.info(f"Generated P request: {generated_prefill_request}")
            # Return a mock response for P request
            mock_response = Mock()
            mock_response.json.return_value = {
                KV_TRANSFER_KEY: {
                    "do_remote_decode": True,
                    "remote_engine_id": "test-engine",
                    "remote_host": "127.0.0.1",
                    "remote_port": "8001"
                }
            }
            yield mock_response
        monkeypatch.setattr(SeparatePDRouter, "forward_post_request", mock_forward_post_request)
        
        generated_decode_request = {}
        async def mock_forward_stream_request(self, req_data: dict, resource: ScheduledResource):
            nonlocal generated_decode_request
            generated_decode_request = req_data
            logger.info(f"Generated D request: {generated_decode_request}")
            # Yield a simple response for D request
            yield b'{"choices": [{"delta": {"content": "Hello"}}]}'
        monkeypatch.setattr(SeparatePDRouter, "forward_stream_request", mock_forward_stream_request)

        pd_router = SeparatePDRouter(req_info)
        chunks = []
        stream_resp = await pd_router.handle_request()
        async for chunk in stream_resp.body_iterator:
            chunks.append(chunk)

        # Assert prefill request modifications
        assert generated_prefill_request["stream"] is False
        assert generated_prefill_request["max_tokens"] == 1
        assert "stream_options" not in generated_prefill_request
        assert KV_TRANSFER_KEY in generated_prefill_request
        assert generated_prefill_request[KV_TRANSFER_KEY]["do_remote_decode"] is True
        assert generated_prefill_request[KV_TRANSFER_KEY]["do_remote_prefill"] is False
        # Assert decode request modifications
        assert generated_decode_request["stream"] == stream
        assert generated_decode_request["max_tokens"] == max_tokens
        assert KV_TRANSFER_KEY in generated_decode_request

    @pytest.mark.asyncio
    async def test_engine_server_prefill_4xx_status_code(self, client, monkeypatch: MonkeyPatch, setup_pd_separation):
        """Test case: Prefill EngineServer returns 4XX status code
        Expected behavior:
        1) Check request status is Exception
        2) No request retry triggered
        3) Directly return error message
        """
        
        # Mock the HTTP forwarding function to return a 4XX error
        error_message = "Bad Request"
        
        exec_release = 0
        def mock_update_workload(self, resource: ScheduledResource, action: WorkloadAction):
            nonlocal exec_release
            exec_release += 1
            return True
        monkeypatch.setattr(BaseRouter, "_BaseRouter__update_workload", mock_update_workload)
        
        # Create a mock response with 4XX status code
        mock_response_fail = MagicMock()
        mock_response_fail.status_code = status.HTTP_400_BAD_REQUEST
        mock_response_fail.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError(
            message=error_message, request=MagicMock(), 
            response=httpx.Response(status_code=status.HTTP_400_BAD_REQUEST, text=error_message)
        ))
        # mock AsyncClient in router
        mock_async_client = AsyncMock()
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=None)
        mock_async_client.post = AsyncMock(return_value=mock_response_fail)
        
        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=mock_async_client):
            
            response = client.post("/v1/chat/completions", json={
                "model": "test-model", 
                "messages": [{"role": "user", "content": "Hello"}]
            })
            logger.info(f"Response status code: {response.text}")
            
        assert error_message in response.text
        # Should get a 4XX error
        assert str(status.HTTP_400_BAD_REQUEST) in response.text
        # Should only try once (no retry for 4XX)
        assert mock_async_client.post.await_count == 1 
        assert exec_release > 1
        
    @pytest.mark.asyncio
    async def test_engine_server_prefill_continuous_5xx_status_code(self, client, monkeypatch: MonkeyPatch, setup_pd_separation):
        """Test case: EngineServer Prefill request continuously returns 5XX status code
        Expected behavior:
        1) Check request status is Exception
        2) Trigger request retry
        3) Request retry fails: return error message
        """
        # Mock the HTTP forwarding function to always return a 5XX error
        error_message = "Test Internal Error"
        
        exec_release = 0
        def mock_update_workload(self, resource: ScheduledResource, action: WorkloadAction):
            nonlocal exec_release
            exec_release += 1
            return True
        monkeypatch.setattr(BaseRouter, "_BaseRouter__update_workload", mock_update_workload)
        
        # Create a mock response with 5XX status code
        mock_response_fail = MagicMock()
        mock_response_fail.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        mock_response_fail.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError(
            error_message, request=MagicMock(), response=mock_response_fail
        ))
        # mock AsyncClient in router
        mock_async_client = AsyncMock()
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=None)
        mock_async_client.post = AsyncMock(return_value=mock_response_fail)
        
        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=mock_async_client):
            response = client.post("/v1/chat/completions", json={
                "model": "test-model", 
                "messages": [{"role": "user", "content": "Hello"}]
            })
            
        assert error_message in response.text
        # Should get a 500 error after max retries
        assert str(status.HTTP_500_INTERNAL_SERVER_ERROR) in response.text
        # Should retry exactly max_retry times
        assert mock_async_client.post.await_count == CoordinatorConfig().exception_config.max_retry
        assert exec_release > 1

    @pytest.mark.asyncio
    async def test_engine_server_prefill_once_5xx_status_code(self, client, monkeypatch: MonkeyPatch, setup_pd_separation):
        """Test case: EngineServer Prefill request first returns 5XX status code, then returns 200 normally
        Expected behavior:
        1) Check request status is Exception
        2) Trigger request retry
        3) Request retry succeeds
        """
        # Mock the HTTP forwarding function to always return a 5XX error
        error_message = "Internal Error"
        
        # Create a mock response with 5XX status code
        mock_response_fail = MagicMock()
        mock_response_fail.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        mock_response_fail.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError(
            error_message, request=MagicMock(), response=mock_response_fail
        ))
        # Create a mock response with 200 status code
        mock_response_success = MagicMock()
        mock_response_success.status_code = 200
        mock_response_success.raise_for_status = MagicMock()
        # mock AsyncClient in router
        mock_async_client = AsyncMock()
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=None)
        mock_async_client.post = AsyncMock(side_effect=[mock_response_fail, mock_response_success])

        decode_count = 0
        async def mock_forward_stream_request(self, req_data: dict, resource: ScheduledResource):
            # Yield a simple response for D request
            nonlocal decode_count
            decode_count += 1
            yield b'{"choices": [{"delta": {"content": "Hello"}}]}'
        
        monkeypatch.setattr(SeparatePDRouter, "forward_stream_request", mock_forward_stream_request)        
        
        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=mock_async_client):
            response = client.post("/v1/chat/completions", json={
                "model": "test-model", 
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True
            })
            
        # Should get a 200 after retry
        assert response.status_code == status.HTTP_200_OK
        # Should retry exactly max_retry times
        assert mock_async_client.post.await_count == 2
        # Should call decode once
        assert decode_count == 1

    @pytest.mark.asyncio
    async def test_engine_server_prefill_network_exception(self, client, monkeypatch: MonkeyPatch, setup_pd_separation):
        """Test case: EngineServer network exception
        Expected behavior:
        1) Check request status is Exception
        2) No request retry triggered
        3) Directly return error message
        """
        # Mock the HTTP forwarding function to always raise a network exception        
        error_message = "Test Connection error"
        # Create a mock response with 5XX status code
        mock_response_fail = MagicMock()
        mock_response_fail.raise_for_status = MagicMock(side_effect=httpx.ConnectError(
            error_message, request=MagicMock()
        ))
        # mock AsyncClient in router
        mock_async_client = AsyncMock()
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=None)
        mock_async_client.post = AsyncMock(return_value=mock_response_fail)
        
        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=mock_async_client):
            response = client.post("/v1/chat/completions", json={
                "model": "test-model", 
                "messages": [{"role": "user", "content": "Hello"}]
            })
            
        assert error_message in response.text
        assert mock_async_client.post.await_count == 1

    @pytest.mark.asyncio
    async def test_engine_server_decode_continuous_5xx_status_code(self, client, monkeypatch: MonkeyPatch, setup_pd_separation):
        """Test case: EngineServer Decode request continuously returns 5XX status code
        Expected behavior:
        1) Check request status is Exception
        2) Trigger request retry
        3) Request retry fails: return error message
        """
        # Mock the HTTP stream forwarding function to return a 5XX error once
        mock_async_client = MockAsyncClient(recomputed=False, fail_times=CoordinatorConfig().exception_config.max_retry)

        async def mock_forward_post_request(self, req_data: dict, resource: ScheduledResource):
            # Return a mock response for P request
            mock_response = Mock()
            mock_response.json.return_value = {
                "kv_transfer_params": {
                    "do_remote_decode": True,
                    "remote_engine_id": "test-engine",
                    "remote_host": "127.0.0.1",
                    "remote_port": "8001"
                }
            }
            yield mock_response
        
        monkeypatch.setattr(SeparatePDRouter, "forward_post_request", mock_forward_post_request)
        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=mock_async_client):
            response = client.post("/v1/chat/completions", json={
                "model": "test-model", 
                "messages": [{"role": "user", "content": "Hello"}]
            })
            
            # Should get a 500 after reach max retries
            assert str(status.HTTP_500_INTERNAL_SERVER_ERROR) in response.text
            assert "Simulated stream error" in response.text
            # Should call decode once
            assert mock_async_client.fail_count == CoordinatorConfig().exception_config.max_retry

    @pytest.mark.asyncio
    async def test_engine_server_decode_once_5xx_status_code(self, client, monkeypatch: MonkeyPatch, setup_pd_separation):
        """Test case: EngineServer Decode request first returns 5XX status code, then returns 200 normally
        Expected behavior:
        1) Check request status is Exception
        2) Trigger request retry
        3) Request retry succeeds
        """
        # Mock the HTTP stream forwarding function to return a 5XX error once
        mock_async_client = MockAsyncClient(recomputed=False, fail_times=1)

        async def mock_forward_post_request(self, req_data: dict, resource: ScheduledResource):
            # Return a mock response for P request
            mock_response = Mock()
            mock_response.json.return_value = {
                "kv_transfer_params": {
                    "do_remote_decode": True,
                    "remote_engine_id": "test-engine",
                    "remote_host": "127.0.0.1",
                    "remote_port": "8001"
                }
            }
            yield mock_response
        
        monkeypatch.setattr(SeparatePDRouter, "forward_post_request", mock_forward_post_request)
        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=mock_async_client):
            response = client.post("/v1/chat/completions", json={
                "model": "test-model", 
                "messages": [{"role": "user", "content": "Hello"}]
            })
            
            # Should get a 200 after retry
            assert response.status_code == status.HTTP_200_OK
            # Should call decode once
            assert mock_async_client.fail_count == 1

    @pytest.mark.asyncio
    async def test_successful_request_with_separate_pd(self, client, monkeypatch: MonkeyPatch, setup_pd_separation):
        """Test case: PD separation mode request succeeds
        Expected behavior:
        1) Check request status is DecodeEnd
        2) Return normal response
        """
        # Mock the HTTP forwarding functions
        async def mock_forward_post_request(self, req_data: dict, resource: ScheduledResource):
            # Return a mock response for P request
            mock_response = Mock()
            mock_response.json.return_value = {
                "kv_transfer_params": {
                    "do_remote_decode": True,
                    "remote_engine_id": "test-engine",
                    "remote_host": "127.0.0.1",
                    "remote_port": "8001"
                }
            }
            yield mock_response
        
        async def mock_forward_stream_request(self, req_data: dict, resource: ScheduledResource):
            # Yield a simple response for D request
            yield b'{"choices": [{"delta": {"content": "Hello"}}]}'
        
        monkeypatch.setattr(SeparatePDRouter, "forward_post_request", mock_forward_post_request)
        monkeypatch.setattr(SeparatePDRouter, "forward_stream_request", mock_forward_stream_request)
        
        response = client.post("/v1/chat/completions", json={
            "model": "test-model", 
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True
        })
        
        # Should get a 200 success status
        assert response.status_code == status.HTTP_200_OK
        # Should be a streaming response
        assert response.headers.get("content-type") == "application/json"
    
    @pytest.mark.asyncio
    async def test_engine_server_stream_recompute(self, client, monkeypatch: MonkeyPatch, setup_pd_separation):
        # Mock the HTTP forwarding functions
        async def mock_forward_post_request(self, req_data: dict, resource: ScheduledResource):
            logger.info(f"Prefill request: {req_data}")
            # Return a mock response for P request
            mock_response = Mock()
            mock_response.json.return_value = {
                "kv_transfer_params": {
                    "do_remote_decode": True,
                    "remote_engine_id": "test-engine",
                    "remote_host": "127.0.0.1",
                    "remote_port": "8001"
                }
            }
            yield mock_response
        monkeypatch.setattr(SeparatePDRouter, "forward_post_request", mock_forward_post_request)
        
        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=MockAsyncClient()):
            import json
            result = ""
            response = client.post("/v1/chat/completions", json={
                "model": "qwen3", 
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 10,
                "stream": True
            })
            assert response.status_code == status.HTTP_200_OK
            
            # Parse streaming response
            for chunk in response.iter_lines():
                if not chunk: continue
                
                logger.info(f"Received: {chunk}")
                # Process SSE format data
                if chunk.startswith("data: "):
                    chunk = chunk[6:]  # Remove "data: " prefix
                if chunk == "[DONE]":
                    break
                try:
                    chunk_json = json.loads(chunk)  # Validate if it's valid JSON
                    if "choices" in chunk_json and len(chunk_json["choices"]) > 0:
                            delta = chunk_json["choices"][0].get("delta", {})
                            if "content" in delta:
                                result += delta["content"]
                except json.JSONDecodeError:
                    logger.error("JSONDecodeError")
                    continue
        
            assert result == ",1,2,3,4,5,6,7,8,9,10"
            
            
    @pytest.mark.asyncio
    async def test_engine_server_nostream_recompute(self, client, monkeypatch: MonkeyPatch, setup_pd_separation):
        
        # Mock the HTTP forwarding functions
        async def mock_forward_post_request(self, req_data: dict, resource: ScheduledResource):
            logger.info(f"Prefill request: {req_data}")
            # Return a mock response for P request
            mock_response = Mock()
            mock_response.json.return_value = {
                "kv_transfer_params": {
                    "do_remote_decode": True,
                    "remote_engine_id": "test-engine",
                    "remote_host": "127.0.0.1",
                    "remote_port": "8001"
                }
            }
            yield mock_response
        monkeypatch.setattr(SeparatePDRouter, "forward_post_request", mock_forward_post_request)
        
        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=MockAsyncClient()):
            import json
            result = ""
            response = client.post("/v1/chat/completions", json={
                "model": "qwen3", 
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 10,
                "stream": False
            })
            assert response.status_code == status.HTTP_200_OK
            
            # Parse streaming response
            for chunk in response.iter_lines():
                if not chunk: continue
                
                logger.info(f"Received: {chunk}")
                try:
                    chunk_json = json.loads(chunk)  # Validate if it's valid JSON
                    if "choices" in chunk_json and len(chunk_json["choices"]) > 0:
                            message = chunk_json["choices"][0].get("message", {})
                            if "content" in message:
                                result += message["content"]
                except json.JSONDecodeError:
                    logger.error("JSONDecodeError")
                    continue
        
            assert result == ",1,2,3,4,5,6,7,8,9,10"

    @pytest.mark.asyncio
    async def test_resource_release(self, client, monkeypatch: MonkeyPatch, setup_pd_separation):
        pass
