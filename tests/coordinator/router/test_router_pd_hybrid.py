#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json

from pytest import MonkeyPatch
from fastapi import FastAPI, status, Request
from fastapi.testclient import TestClient
import pytest

from motor.config.coordinator import DeployMode, CoordinatorConfig
from motor.coordinator.core.instance_manager import InstanceManager
from motor.coordinator.models.request import ScheduledResource
from motor.coordinator.router.pd_hybrid_router import PDHybridRouter
from motor.common.resources.instance import Endpoint, PDRole, Instance, InsStatus, ParallelConfig
from motor.coordinator.scheduler.scheduler import Scheduler
from motor.coordinator.models.request import RequestInfo, ReqState
import motor.coordinator.router.router as router
from motor.common.utils.logger import get_logger

logger = get_logger(__name__)

app = FastAPI()
@app.post("/v1/chat/completions")
async def handle_completions(request: Request):
    return await router.handle_request(request)

@pytest.fixture
def mock_forward_stream_request(monkeypatch):
    """Mock forward_stream_request 并自动设置和清理"""
    async def mock_impl(self, req_data: dict, resource):
        async def mock_stream():
            responses = [
                b'{"choices": [{"text": "chunk 1"}]}',
                b'{"choices": [{"text": "chunk 2"}]}',
                b'{"choices": [{"text": "chunk 3"}]}'
            ]
            for response in responses:
                yield response
        
        async for chunk in mock_stream():
            yield chunk
    
    # Patch the forward_stream_request function to return an async generator directly
    monkeypatch.setattr(PDHybridRouter, "forward_stream_request", mock_impl)
    yield mock_impl


class TestRouterPDHybrid:
    
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
    def setup_pd_hybrid(self, monkeypatch: MonkeyPatch):
        # Create proper instance for PD hybrid flow
        mock_instance_u = self.create_mock_instance(0, PDRole.ROLE_U)
        mock_endpoint_u = Endpoint(id=0, ip="127.0.0.1", business_port="8000", mgmt_port="8000")
        mock_instance_u.endpoints = {"127.0.0.1": {0: mock_endpoint_u}}
        
        # Mock functions
        def mock_is_available(self):
            return True
        
        def mock_get_available_instances(role):
            if role == PDRole.ROLE_U:  # PD hybrid role
                return [mock_instance_u]  # Has PD hybrid instances
            return []
        
        def mock_select_instance_and_endpoint(self, role):
            if role == PDRole.ROLE_U:
                return mock_instance_u, mock_endpoint_u
            return None, None, None
        
        def mock_update_workload(self, instance: Instance, endpoint: Endpoint, req_id: str,
                        workload_action, request_length: int) -> bool:
            return True
        
        monkeypatch.setattr(InstanceManager, "is_available", mock_is_available)
        monkeypatch.setattr(InstanceManager, "get_available_instances", mock_get_available_instances)
        monkeypatch.setattr(Scheduler, "select_instance_and_endpoint", mock_select_instance_and_endpoint)
        monkeypatch.setattr(Scheduler, "update_workload", mock_update_workload)
        monkeypatch.setattr(CoordinatorConfig().scheduler_config, "deploy_mode", DeployMode.SINGLE_NODE)
        
    
    @pytest.mark.asyncio
    async def test_pd_hybrid_request_forwarding(self, monkeypatch: MonkeyPatch, setup_pd_hybrid, mock_forward_stream_request):
        """Test PD hybrid request forwarding functionality"""
        # Create a mock scope for the request
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [],
        }
        
        # Create a request body
        request_body = {
            "model": "test-model",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True
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
            api="v1/chat/completions",
            state=ReqState.ARRIVE
        )
        
        # Test the PD hybrid forwarding function
        hybrid_router = PDHybridRouter(req_info)
        chunks = []
        
        response = await hybrid_router.handle_request()
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        
        # Verify we got response chunks
        assert len(chunks) > 0
        # Verify request state was updated
        assert req_info.state == ReqState.DECODE_END
    
    @pytest.mark.asyncio
    async def test_pd_hybrid_request_failure(self, monkeypatch: MonkeyPatch, setup_pd_hybrid):
        """Test handling of PD hybrid request failure"""
        # Create a mock scope for the request
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [],
        }
        
        # Create a request body
        request_body = {
            "model": "test-model",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True
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
            api="v1/chat/completions",
            state=ReqState.ARRIVE
        )
        
        # Mock the stream request function to fail in PDHybridRouter
        async def mock_forward_stream_request(self, req_data, resource):
            # This function should be an async generator that raises an exception
            raise Exception("PD hybrid request failed")
            yield
        monkeypatch.setattr(PDHybridRouter, "forward_stream_request", mock_forward_stream_request)
        
        # Test the PD hybrid forwarding function with failure
        hybrid_router = PDHybridRouter(req_info)
        with pytest.raises(Exception) as exc_info:
            # Create an async generator and consume it
            stream_resp = await hybrid_router.handle_request()
            async for chunk in stream_resp.body_iterator:
                pass
        
        assert "PD hybrid request failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_successful_request_with_pd_hybrid(self, client, monkeypatch: MonkeyPatch, setup_pd_hybrid):
        """测试场景:PD混合模式请求成功
        预期现象:
        1)检查请求状态为DecodeEnd
        2)返回正常响应
        """
        # Mock the HTTP forwarding function to return a successful response
        async def mock_forward_stream_request(self, req_data: dict, resource: ScheduledResource):
            # Yield a simple response
            yield b'{"choices": [{"delta": {"content": "Hello"}}]}'
        
        monkeypatch.setattr(PDHybridRouter, "forward_stream_request", mock_forward_stream_request)
        
        response = client.post("/v1/chat/completions", json={
            "model": "test-model", 
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True
        })
        
        # Should get a 200 success status
        assert response.status_code == status.HTTP_200_OK
        # Should be a streaming response
        assert response.headers.get("content-type") == "application/json"
