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

import json

from pytest import MonkeyPatch
from fastapi import FastAPI, status, Request
from fastapi.testclient import TestClient
from unittest.mock import MagicMock
import pytest

from motor.config.coordinator import DeployMode, CoordinatorConfig, SchedulerType
from motor.coordinator.domain.instance_manager import InstanceManager
from motor.coordinator.router.pd_hybrid_router import PDHybridRouter
from motor.common.resources.instance import Endpoint, PDRole, Instance, InsStatus, ParallelConfig
from motor.common.resources.endpoint import Workload
from motor.coordinator.domain import InstanceReadiness
from motor.coordinator.scheduler.scheduler import Scheduler
from motor.coordinator.tracer.tracing import TracerManager
from motor.coordinator.domain.request_manager import RequestManager
from motor.coordinator.models.request import RequestInfo, ReqState
import motor.coordinator.router.router as router
from motor.common.utils.logger import get_logger

TracerManager()

logger = get_logger(__name__)

app = FastAPI()
_config = CoordinatorConfig()
_scheduler = Scheduler(instance_provider=InstanceManager(_config), config=_config)
_request_manager = RequestManager(_config)


@app.post("/v1/chat/completions")
async def handle_completions(request: Request):
    return await router.handle_request(
        request, _config, scheduler=_scheduler, request_manager=_request_manager
    )

@pytest.fixture
def mock_forward_stream_request(monkeypatch):
    """Mock forward_stream_request 并自动设置和清理"""
    async def mock_impl(self, req_data: dict, client, timeout):
        responses = [
            b'{"choices": [{"text": "chunk 1"}]}',
            b'{"choices": [{"text": "chunk 2"}]}',
            b'{"choices": [{"text": "chunk 3"}]}'
        ]
        for chunk in responses:
            yield chunk
        trace_obj = getattr(self.req_info, "trace_obj", None)
        if trace_obj is not None:
            trace_obj.set_count_token(1)
    
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
        mock_instance = self.create_mock_instance(0, PDRole.ROLE_P)
        mock_endpoint = Endpoint(id=0, ip="127.0.0.1", business_port="8000", mgmt_port="8000")
        mock_instance.endpoints = {"127.0.0.1": {0: mock_endpoint}}
        
        # Mock functions (Scheduler uses get_required_instances_status for readiness)
        def mock_get_required_instances_status(self, deploy_mode=None):
            return InstanceReadiness.REQUIRED_MET

        def mock_has_required_instances(self, deploy_mode=None):
            return True

        def mock_get_available_instances(self, role=None):
            if role == PDRole.ROLE_P:  # PD hybrid uses ROLE_P
                return {mock_instance.id: mock_instance}
            return {}
        
        async def mock_select_and_allocate(self, role, req_info):
            if role == PDRole.ROLE_P:
                return mock_instance, mock_endpoint, Workload()
            return None

        async def mock_update_workload(self, params):
            return True

        monkeypatch.setattr(InstanceManager, "get_required_instances_status", mock_get_required_instances_status)
        monkeypatch.setattr(InstanceManager, "has_required_instances", mock_has_required_instances)
        monkeypatch.setattr(InstanceManager, "get_available_instances", mock_get_available_instances)
        monkeypatch.setattr(Scheduler, "select_and_allocate", mock_select_and_allocate)
        monkeypatch.setattr(Scheduler, "update_workload", mock_update_workload)

        # Mock CoordinatorConfig to return SINGLE_NODE deploy mode
        mock_scheduler_config = MagicMock()
        mock_scheduler_config.deploy_mode = DeployMode.SINGLE_NODE
        mock_scheduler_config.scheduler_type = SchedulerType.LOAD_BALANCE
        mock_exception_config = MagicMock()
        mock_exception_config.retry_delay = 0.0001
        mock_exception_config.max_retry = 5
        mock_http_config = MagicMock()
        mock_http_config.coordinator_api_host = "127.0.0.1"
        mock_http_config.coordinator_api_mgmt_port = 1025

        mock_config = MagicMock()
        mock_config.scheduler_config = mock_scheduler_config
        mock_config.exception_config = mock_exception_config
        mock_config.http_config = mock_http_config

        monkeypatch.setattr(CoordinatorConfig, "__new__", lambda cls: mock_config)
        
    
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
            api="v1/chat/completions"
        )
        
        # Test the PD hybrid forwarding function
        hybrid_router = PDHybridRouter(
            req_info, CoordinatorConfig(),
            scheduler=Scheduler(instance_provider=InstanceManager(CoordinatorConfig()), config=CoordinatorConfig()),
            request_manager=RequestManager(_config)
        )
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
            api="v1/chat/completions"
        )
        
        error_message = "PD hybrid request failed"
        # Mock the stream request function to fail in PDHybridRouter
        async def mock_forward_stream_request(self, req_data, client, timeout):
            # This function should be an async generator that raises an exception
            raise Exception(error_message)
            yield
        monkeypatch.setattr(PDHybridRouter, "forward_stream_request", mock_forward_stream_request)
        
        # Test the PD hybrid forwarding function with failure
        hybrid_router = PDHybridRouter(
            req_info, CoordinatorConfig(),
            scheduler=Scheduler(instance_provider=InstanceManager(CoordinatorConfig()), config=CoordinatorConfig()),
            request_manager=RequestManager(_config)
        )
        # Create an async generator and consume it
        stream_resp = await hybrid_router.handle_request()
        chunks = []
        async for chunk in stream_resp.body_iterator:
            chunks.append(chunk)
        chunk_str = "".join(chunks)
        
        assert error_message in chunk_str

    @pytest.mark.asyncio
    async def test_successful_request_with_pd_hybrid(self, client, monkeypatch: MonkeyPatch,
                                                     setup_pd_hybrid, mock_forward_stream_request):
        """
        Expected behavior:
        1) Check request status is DecodeEnd
        2) Return normal response
        """
        
        response = client.post("/v1/chat/completions", json={
            "model": "test-model", 
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True
        })
        
        # Should get a 200 success status
        assert response.status_code == status.HTTP_200_OK
        # Should be a streaming response
        assert "text/event-stream" in response.headers.get("content-type")
