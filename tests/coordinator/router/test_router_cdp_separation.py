#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from pytest import MonkeyPatch
from fastapi import FastAPI, status, Request
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import httpx
import pytest

from motor.config.coordinator import DeployMode, CoordinatorConfig
from motor.coordinator.core.instance_manager import InstanceManager
from motor.coordinator.models.request import ReqState, ScheduledResource
from motor.coordinator.router.base_router import BaseRouter
from motor.coordinator.router.separate_cdp_router import SeparateCDPRouter
from motor.common.resources.endpoint import WorkloadAction
from motor.common.resources.instance import Endpoint, PDRole, Instance, InsStatus, ParallelConfig
from motor.coordinator.scheduler.scheduler import Scheduler
from tests.coordinator.router.mock_openai_request import MockStreamResponse, create_mock_request_info
import motor.coordinator.router.router as router
from motor.coordinator.scheduler.scheduler import Scheduler
from motor.common.utils.logger import get_logger

logger = get_logger(__name__)

app = FastAPI()
@app.post("/v1/chat/completions")
async def handle_completions(request: Request):
    return await router.handle_request(request)

@app.post("/v1/metaserver")
async def handle_metaserver(request: Request):
    return await router.handle_metaserver_request(request)


class MockAsyncClient:
    
    def __init__(self, post_exc: Exception = None, stream_exc: Exception = None, 
                 post_fail_times: int = 1, stream_fail_times: int = 1):
        self.post_exc = post_exc
        self.post_fail_times = post_fail_times
        self.post_count = 0
        self.post_fail_count = 0
        
        self.stream_exc = stream_exc
        self.stream_fail_times = stream_fail_times
        self.stream_count = 0
        self.stream_fail_count = 0
        
        self.req_data_from_metaserver = {}
        self.req_headers_from_router = {}
        
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
    
    async def post(self, url, json=None, headers=None):
        logger.info(f"----------req_data_from_metaserver:{json}")
        self.post_count += 1
        if self.post_exc and self.post_fail_count < self.post_fail_times:
            self.post_fail_count += 1
            mock_response_fail = MagicMock()
            mock_response_fail.raise_for_status = MagicMock(side_effect=self.post_exc)
            return mock_response_fail
        
        self.req_data_from_metaserver = json
        request = httpx.Request("POST", url, headers=headers or {}, json=json)
        
        return httpx.Response(
            status_code = status.HTTP_200_OK, 
            json={
            "choices": [{"delta": {"content": "decoded chunk"}, "index": 0, "finish_reason": None}],
            "id": "chatcmpl-123"},
            request=request
        )
    
    def stream(self, method, url, json=None, headers=None):
        self.stream_count += 1
        logger.info(f"----------req_data_from_coordinator:{json}")
        if self.stream_exc and self.stream_fail_count < self.stream_fail_times:
            self.stream_fail_count += 1
            return MockStreamResponse(json or {}, recomputed=False, exc=self.stream_exc)
            
        from urllib.parse import urlparse
        client = TestClient(app)
        logger.info(f"req_data_from_router header: {headers}, body:{json}")
        self.req_headers_from_router = headers
        
        url = json["kv_transfer_params"]["metaserver"]
        parsed_url = urlparse(url)
        
        # Forward request to metaserver
        try:
            response = client.post(parsed_url.path, json={
                "request_id": headers.get("X-Request-Id"), 
                "do_remote_decode": False,
                "do_remote_prefill": True,
                "remote_engine_id": "test-engine",
                "remote_host": parsed_url.hostname,
                "remote_port": str(parsed_url.port)
            })
            logger.debug(f"metaserver response : {response.text}")
            response.raise_for_status()
        except Exception as e:
            return MockStreamResponse(json or {}, recomputed=False, exc=httpx.HTTPStatusError(
                message=response.text, request=MagicMock(), 
                response=httpx.Response(status_code=response.status_code, text=response.text)
            ))
        
        # Return an async context manager
        return MockStreamResponse(json or {}, recomputed=False, exc=None)


class TestRouterCDPSeparation:
    
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
    def setup_cdp_separation(self, monkeypatch: MonkeyPatch):
        host = "127.0.0.1"
        # Create proper instances for separate P/D flow
        mock_instance_p = self.create_mock_instance(0, PDRole.ROLE_P)
        mock_endpoint_p = Endpoint(id=0, ip=host, business_port="8000", mgmt_port="8000")
        mock_instance_p.endpoints = {host: {0: mock_endpoint_p}}
        
        mock_instance_d = self.create_mock_instance(1, PDRole.ROLE_D)
        mock_endpoint_d = Endpoint(id=1, ip=host, business_port="8001", mgmt_port="8001")
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
        
        monkeypatch.setattr(InstanceManager, "is_available", mock_is_available)
        monkeypatch.setattr(InstanceManager, "get_available_instances", mock_get_available_instances)
        monkeypatch.setattr(Scheduler, "select_instance_and_endpoint", mock_select_instance_and_endpoint)
        monkeypatch.setattr(Scheduler, "update_workload", mock_update_workload)
        monkeypatch.setattr(CoordinatorConfig().scheduler_config, "deploy_mode", DeployMode.CDP_SEPARATE)
        monkeypatch.setattr(CoordinatorConfig().exception_config, "retry_delay", 0.0001)
    
    @pytest.mark.asyncio
    async def test_successful_request_with_separate_cdp(self, client, monkeypatch: MonkeyPatch, setup_cdp_separation):
        """Test case: CDP separation mode request success
        Expected behavior:
        1) Check request status is DecodeEnd
        2) Return normal response
        """
        
        mock_async_client = MockAsyncClient()
        
        req_info = await create_mock_request_info()
        origin_req_id = req_info.req_id
        origin_req_len = req_info.req_len
        origin_req_data = req_info.req_data
        
        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=mock_async_client):
            
            cdp_router = SeparateCDPRouter(req_info)
            response = await cdp_router.handle_request()
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
        
            # Should get a 200 success status
            assert response.status_code == status.HTTP_200_OK
            # Should be a streaming response
            assert "text/event-stream" in response.headers.get("content-type")
            
            # Check metaserver request data
            req_data = mock_async_client.req_data_from_metaserver
            assert req_data["stream"] is False
            assert req_data["max_tokens"] == 1
            # kv_transfer_params from D instance
            kv_transfer_params = req_data["kv_transfer_params"]
            assert kv_transfer_params["request_id"] == mock_async_client.req_headers_from_router["X-Request-Id"]
            assert kv_transfer_params["do_remote_decode"] is False
            assert kv_transfer_params["do_remote_prefill"] is True
            assert kv_transfer_params["remote_host"] == CoordinatorConfig().server_config.mgmt_host
            assert kv_transfer_params["remote_port"] == str(CoordinatorConfig().server_config.mgmt_port)

            # Request info should not be modified by metaserver
            assert req_info.req_id == origin_req_id
            assert req_info.req_len == origin_req_len
            assert req_info.req_data == origin_req_data
            
            # Check request state and metrics
            assert req_info.state == ReqState.DECODE_END
            assert req_info.d_scheduled_time >= req_info.start_time
            assert req_info.p_scheduled_time >= req_info.d_scheduled_time
            assert req_info.prefill_end_time >= req_info.p_scheduled_time
            assert req_info.first_token_time >= req_info.prefill_end_time
            assert req_info.decode_end_time >= req_info.first_token_time

    @pytest.mark.asyncio
    async def test_engine_server_decode_4xx_status_code(self, client, monkeypatch: MonkeyPatch, setup_cdp_separation):
        """Test case: Decode EngineServer returns 4XX status code
        Expected behavior:
        1) No request retry triggered
        2) Directly return error message
        """
        # Mock the HTTP forwarding function to return a 4XX error
        error_message = "Test Bad Request"
        mock_async_client = MockAsyncClient(stream_exc=httpx.HTTPStatusError(
            message=error_message,
            request=None,
            response=httpx.Response(status_code=status.HTTP_400_BAD_REQUEST, text=error_message)
        ))
        req_info = await create_mock_request_info()
        
        release_p_tokens = 0
        release_p_kv = 0
        release_d_tokens = 0
        def mock_update_workload(self, resource: ScheduledResource, action: WorkloadAction):
            nonlocal release_p_tokens
            nonlocal release_p_kv
            nonlocal release_d_tokens
            if resource.instance.role == PDRole.ROLE_P:
                if action == WorkloadAction.RELEASE_TOKENS:
                    release_p_tokens += 1
                elif action == WorkloadAction.RELEASE_KV:
                    release_p_kv += 1
            elif resource.instance.role == PDRole.ROLE_D:
                if action == WorkloadAction.RELEASE_TOKENS:
                    release_d_tokens += 1
            return True
        monkeypatch.setattr(BaseRouter, "_BaseRouter__update_workload", mock_update_workload)
        
        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=mock_async_client):
        
            cdp_router = SeparateCDPRouter(req_info)
            response = await cdp_router.handle_request()
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
            chunk_str = b"".join(chunks).decode('utf-8')
            
        assert req_info.state == ReqState.INVALID
        assert error_message in chunk_str
        # Should get a 4XX error
        assert str(status.HTTP_400_BAD_REQUEST) in chunk_str
        # Should only try once (no retry for 4XX)
        assert mock_async_client.stream_count == 1 
        assert release_d_tokens >= 1
        assert release_p_tokens == 0
        assert release_p_tokens == 0
        
    @pytest.mark.asyncio
    async def test_engine_server_decode_continuous_5xx_status_code(self, client, monkeypatch: MonkeyPatch, setup_cdp_separation):
        """Test scenario: EngineServer Prefill request continuously returns 5XX status code
        Expected behavior:
        1) Check request status is Exception
        2) Trigger request retry
        3) Request retry fails: return error message
        """
        # Mock the HTTP forwarding function to return a 4XX error
        error_message = "Test Internal Server Error"
        mock_async_client = MockAsyncClient(stream_exc=httpx.HTTPStatusError(
            message=error_message,
            request=None,
            response=httpx.Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, text=error_message)
        ), stream_fail_times=CoordinatorConfig().exception_config.max_retry)
        req_info = await create_mock_request_info()
        
        exec_release = 0
        def mock_update_workload(self, resource: ScheduledResource, action: WorkloadAction):
            nonlocal exec_release
            exec_release += 1
            return True
        monkeypatch.setattr(BaseRouter, "_BaseRouter__update_workload", mock_update_workload)
        
        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=mock_async_client):
            cdp_router = SeparateCDPRouter(req_info)
            response = await cdp_router.handle_request()
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
            chunk_str = b"".join(chunks).decode('utf-8')
            
        assert req_info.state == ReqState.EXCEPTION
        assert error_message in chunk_str
        # Should get a 500 error after max retries
        assert str(status.HTTP_500_INTERNAL_SERVER_ERROR) in chunk_str
        # Should retry exactly max_retry times
        assert mock_async_client.stream_count == CoordinatorConfig().exception_config.max_retry
        assert exec_release >= 1
        
    @pytest.mark.asyncio
    async def test_engine_server_decode_once_5xx_status_code(self, client, monkeypatch: MonkeyPatch, setup_cdp_separation):
        """Test case: EngineServer Decode request first returns 5XX status code, then returns 200 normally
        Expected behavior:
        1) Check request status is Exception
        2) Trigger request retry
        3) Request retry succeeds
        """
        # Mock the HTTP stream forwarding function to return a 5XX error once
        error_message = "Test Internal Server Error"
        mock_async_client = MockAsyncClient(stream_exc=httpx.HTTPStatusError(
            message=error_message,
            request=None,
            response=httpx.Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
        ), stream_fail_times=1)
        req_info = await create_mock_request_info()
        
        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=mock_async_client):
            cdp_router = SeparateCDPRouter(req_info)
            response = await cdp_router.handle_request()
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
            
            # Should get a 200 after retry
            assert response.status_code == status.HTTP_200_OK
            # Should call decode twice
            assert mock_async_client.stream_fail_count == 1
            assert mock_async_client.stream_count == 2
            # Should call prefill once
            assert mock_async_client.post_count == 1
            assert req_info.state == ReqState.DECODE_END
    
    @pytest.mark.asyncio
    async def test_engine_server_decode_network_exception(self, client, monkeypatch: MonkeyPatch, setup_cdp_separation):
        """Test case: EngineServer Decode network exception
        Expected behavior:
        1) Check request status is Exception
        2) No request retry triggered
        3) Directly return error message
        """
        # Mock the HTTP forwarding function to always raise a network exception        
        error_message = "Connection error"
        # mock AsyncClient in router
        mock_async_client = MockAsyncClient(stream_exc=httpx.ConnectError(
            error_message, 
            request=MagicMock()
        ))
        req_info = await create_mock_request_info()
        
        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=mock_async_client):
            cdp_router = SeparateCDPRouter(req_info)
            response = await cdp_router.handle_request()
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
            chunk_str = b"".join(chunks).decode('utf-8')
        logger.info(f"chunk_str:{chunk_str}")
        assert error_message in chunk_str
        assert mock_async_client.stream_count == 1
        assert mock_async_client.stream_fail_count == 1
        assert req_info.state == ReqState.EXCEPTION
    
    @pytest.mark.asyncio
    async def test_engine_server_prefill_network_exception(self, client, monkeypatch: MonkeyPatch, setup_cdp_separation):
        """Test case: EngineServer network exception
        Expected behavior:
        1) Check request status is Exception
        2) No request retry triggered
        3) Directly return error message
        """
        # Mock the HTTP forwarding function to always raise a network exception        
        error_message = "Connection error"
        retry_times = CoordinatorConfig().exception_config.max_retry
        # mock AsyncClient in router
        mock_async_client = MockAsyncClient(post_exc=httpx.ConnectError(message=error_message, request=MagicMock()), 
                                            post_fail_times=retry_times)
        req_info = await create_mock_request_info()
        
        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=mock_async_client):
            cdp_router = SeparateCDPRouter(req_info)
            response = await cdp_router.handle_request()
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
            chunk_str = b"".join(chunks).decode('utf-8')
            
        assert error_message in chunk_str
        assert mock_async_client.post_count == retry_times
        assert mock_async_client.post_fail_count == retry_times
        assert req_info.state == ReqState.EXCEPTION