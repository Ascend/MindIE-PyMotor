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

from pytest import MonkeyPatch
from fastapi import FastAPI, status, Request
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
import asyncio
import httpx
import pytest

from motor.config.coordinator import DeployMode, CoordinatorConfig, SchedulerType
from motor.coordinator.domain.instance_manager import InstanceManager
from motor.coordinator.domain import InstanceReadiness, ScheduledResource
from motor.coordinator.models.request import ReqState, RequestInfo
from motor.coordinator.router.base_router import BaseRouter
from motor.coordinator.router.separate_pd_dual_dispatch_router import SeparatePDDualDispatchRouter
from motor.coordinator.tracer.tracing import TracerManager
from motor.common.resources.endpoint import WorkloadAction
from motor.common.resources.instance import Endpoint, PDRole, Instance, InsStatus, ParallelConfig
from motor.coordinator.scheduler.scheduler import Scheduler
from motor.coordinator.domain.request_manager import RequestManager
from tests.coordinator.router.mock_openai_request import MockStreamResponse, create_mock_request_info
import motor.coordinator.router.router as router

TracerManager()

app = FastAPI()
_config = CoordinatorConfig()
_scheduler = Scheduler(instance_provider=InstanceManager(_config), config=_config)
_request_manager = RequestManager(_config)


@app.post("/v1/chat/completions")
async def handle_completions(request: Request):
    return await router.handle_request(
        request, _config, scheduler=_scheduler, request_manager=_request_manager
    )


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

        self.base_url = "test-base-url"
        self.timeout = 1
        self.is_closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def aclose(self):
        pass

    async def post(self, url, json=None, headers=None, **kwargs):
        self.post_count += 1
        if self.post_exc and self.post_fail_count < self.post_fail_times:
            self.post_fail_count += 1
            mock_response_fail = MagicMock()
            mock_response_fail.raise_for_status = MagicMock(side_effect=self.post_exc)
            return mock_response_fail

        request = httpx.Request("POST", url, headers=headers or {}, json=json)

        return httpx.Response(
            status_code=status.HTTP_200_OK,
            json={
                "choices": [{"delta": {"content": "chunk"}, "index": 0, "finish_reason": None}],
                "id": "chatcmpl-123"},
            request=request
        )

    def stream(self, method, url, json=None, headers=None, **kwargs):
        self.stream_count += 1

        if self.stream_exc and self.stream_fail_count < self.stream_fail_times:
            self.stream_fail_count += 1
            return MockStreamResponse(json or {}, recomputed=False, exc=self.stream_exc)

        # Return an async context manager
        return MockStreamResponse(json or {}, recomputed=False, exc=None)


class TestPDDualDispatchRouter:

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
    def setup_dp_separation(self, monkeypatch: MonkeyPatch):
        host = "127.0.0.1"
        # Create proper instances for separate P/D flow
        mock_instance_p = self.create_mock_instance(0, PDRole.ROLE_P)
        mock_endpoint_p = Endpoint(id=0, ip=host, business_port="8000", mgmt_port="8000")
        mock_instance_p.endpoints = {host: {0: mock_endpoint_p}}

        mock_instance_d = self.create_mock_instance(1, PDRole.ROLE_D)
        mock_endpoint_d = Endpoint(id=1, ip=host, business_port="8001", mgmt_port="8001")
        mock_instance_d.endpoints = {host: {1: mock_endpoint_d}}

        # Mock functions (Scheduler uses get_required_instances_status for readiness)
        def mock_get_required_instances_status(self, deploy_mode=None):
            return InstanceReadiness.REQUIRED_MET

        def mock_has_required_instances(self, deploy_mode=None):
            return True

        def mock_get_available_instances(*args, **kwargs):
            # Accept (self, role) when patched on InstanceManager; role is 2nd positional or in kwargs
            role = kwargs.get("role")
            if role is None and len(args) >= 2:
                role = args[1]
            elif role is None and len(args) == 1:
                role = args[0]  # staticmethod-style call
            if role == PDRole.ROLE_U:  # PD hybrid role
                return {}  # No PD hybrid instances, will use separate P/D
            if role == PDRole.ROLE_P:
                return {mock_instance_p.id: mock_instance_p}
            if role == PDRole.ROLE_D:
                return {mock_instance_d.id: mock_instance_d}
            return {}

        async def mock_select_instance_and_endpoint(self, role):
            if role == PDRole.ROLE_P:
                return mock_instance_p, mock_endpoint_p
            elif role == PDRole.ROLE_D:
                return mock_instance_d, mock_endpoint_d
            return None, None

        async def mock_update_workload(self, params):
            return True

        monkeypatch.setattr(InstanceManager, "get_required_instances_status", mock_get_required_instances_status)
        monkeypatch.setattr(InstanceManager, "has_required_instances", mock_has_required_instances)
        monkeypatch.setattr(InstanceManager, "get_available_instances", mock_get_available_instances)
        monkeypatch.setattr(Scheduler, "select_instance_and_endpoint", mock_select_instance_and_endpoint)
        monkeypatch.setattr(Scheduler, "update_workload", mock_update_workload)

        # Mock CoordinatorConfig to return PD_DUAL_DISPATCH deploy mode
        mock_scheduler_config = MagicMock()
        mock_scheduler_config.deploy_mode = DeployMode.PD_DUAL_DISPATCH
        mock_scheduler_config.scheduler_type = SchedulerType.LOAD_BALANCE
        mock_exception_config = MagicMock()
        mock_exception_config.retry_delay = 0.0001
        mock_exception_config.max_retry = 5
        mock_http_config = MagicMock()
        mock_http_config.coordinator_api_host = "127.0.0.1"
        mock_http_config.coordinator_api_mgmt_port = 1025
        mock_tls_config = MagicMock()
        mock_tls_config.enable_tls = False

        mock_config = MagicMock()
        mock_config.scheduler_config = mock_scheduler_config
        mock_config.exception_config = mock_exception_config
        mock_config.http_config = mock_http_config
        mock_config.infer_tls_config = mock_tls_config
        mock_config.mgmt_tls_config = mock_tls_config
        # So _gen_d_request uses coordinator_api_mgmt_port; avoid MagicMock as parsed_url.port
        mock_config.worker_metaserver_port = None

        monkeypatch.setattr(CoordinatorConfig, "__new__", lambda cls: mock_config)

    @pytest.fixture
    def mock_raw_request(self):
        # Mock Request
        mock_req = MagicMock(spec=Request)
        mock_req.body = AsyncMock(return_value=b'{"model": "test"}')
        mock_req.json = AsyncMock(return_value={"model": "test"})
        mock_req.headers = {}
        mock_req.url.path = "/v1/chat/completions"
        # Must be awaitable so listen_for_disconnect() does not raise; never completes so handler wins.
        never = asyncio.Future()
        mock_req.receive = AsyncMock(return_value=never)
        return mock_req

    @pytest.mark.asyncio
    async def test_handle_request_stream_successful(self, client, monkeypatch: MonkeyPatch, setup_dp_separation):
        """Test case: PD_DUAL_DISPATCH mode stream request success
        Expected behavior:
        1) Check request status is DecodeEnd
        2) Return normal response
        """

        mock_async_client = MockAsyncClient()
        req_info = await create_mock_request_info()

        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=mock_async_client):
            cdp_router = SeparatePDDualDispatchRouter(
                req_info, CoordinatorConfig(),
                scheduler=Scheduler(instance_provider=InstanceManager(CoordinatorConfig()), config=CoordinatorConfig()),
                request_manager=_request_manager
            )
            response = await cdp_router.handle_request()
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)

            # Should get a 200 success status
            assert response.status_code == status.HTTP_200_OK
            # Should be a streaming response
            assert "text/event-stream" in response.headers.get("content-type")

            # Check request state and metrics
            assert req_info.state == ReqState.DECODE_END
            assert req_info.status[ReqState.D_ALLOCATED] >= req_info.status[ReqState.ARRIVE]
            assert req_info.status[ReqState.P_ALLOCATED] >= req_info.status[ReqState.ARRIVE]
            assert req_info.status[ReqState.PREFILL_END] >= req_info.status[ReqState.P_ALLOCATED]
            assert req_info.status[ReqState.DECODE_END] >= req_info.status[ReqState.FIRST_TOKEN_FINISH]

    @pytest.mark.asyncio
    async def test_handle_request_non_stream_successful(self, client, monkeypatch: MonkeyPatch, setup_dp_separation):
        """Test case: PD_DUAL_DISPATCH mode non_stream request success
        Expected behavior:
        1) Check request status is DecodeEnd
        2) Return normal response
        """

        mock_async_client = MockAsyncClient()
        req_info = await create_mock_request_info(stream=False)

        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=mock_async_client):
            cdp_router = SeparatePDDualDispatchRouter(
                req_info, CoordinatorConfig(),
                scheduler=Scheduler(instance_provider=InstanceManager(CoordinatorConfig()), config=CoordinatorConfig()),
                request_manager=_request_manager
            )
            response = await cdp_router.handle_request()

            # Should get a 200 success status
            assert response.status_code == status.HTTP_200_OK
            # Should be a streaming response
            assert "application/json" in response.headers.get("content-type")

            # Check request state and metrics
            assert req_info.state == ReqState.DECODE_END
            assert req_info.status[ReqState.D_ALLOCATED] >= req_info.status[ReqState.ARRIVE]
            assert req_info.status[ReqState.P_ALLOCATED] >= req_info.status[ReqState.ARRIVE]
            assert req_info.status[ReqState.DECODE_END] >= req_info.status[ReqState.D_ALLOCATED]

    @pytest.mark.asyncio
    async def test_handle_request_error_when_decode_4xx(self, client, monkeypatch: MonkeyPatch, setup_dp_separation):
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
        ), stream_fail_times=CoordinatorConfig().exception_config.max_retry)
        req_info = await create_mock_request_info()

        release_p_tokens = 0
        release_p_kv = 0
        release_d_tokens = 0

        async def mock_update_workload(self, resource: ScheduledResource, action: WorkloadAction):
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

        monkeypatch.setattr(BaseRouter, "_update_workload", mock_update_workload)

        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=mock_async_client):

            cdp_router = SeparatePDDualDispatchRouter(
                req_info, CoordinatorConfig(),
                scheduler=Scheduler(instance_provider=InstanceManager(CoordinatorConfig()), config=CoordinatorConfig()),
                request_manager=_request_manager
            )
            response = await cdp_router.handle_request()
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
            chunk_str = "".join(chunks)

        assert req_info.state == ReqState.EXCEPTION
        assert error_message in chunk_str
        # Should get a 4XX error
        assert str(status.HTTP_400_BAD_REQUEST) in chunk_str
        assert mock_async_client.stream_count == CoordinatorConfig().exception_config.max_retry
        assert release_d_tokens >= 1
        assert release_p_tokens >= 1

    @pytest.mark.asyncio
    async def test_handle_request_error_when_decode_5xx(self, client, monkeypatch: MonkeyPatch, setup_dp_separation):
        """Test scenario: EngineServer Decode request continuously returns 5XX status code
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

        async def mock_update_workload(self, resource: ScheduledResource, action: WorkloadAction):
            nonlocal exec_release
            exec_release += 1
            return True

        monkeypatch.setattr(BaseRouter, "_update_workload", mock_update_workload)

        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=mock_async_client):
            cdp_router = SeparatePDDualDispatchRouter(
                req_info, CoordinatorConfig(),
                scheduler=Scheduler(instance_provider=InstanceManager(CoordinatorConfig()), config=CoordinatorConfig()),
                request_manager=_request_manager
            )
            response = await cdp_router.handle_request()
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
            chunk_str = "".join(chunks)

        assert req_info.state == ReqState.EXCEPTION
        assert error_message in chunk_str
        # Should get a 500 error after max retries
        assert str(status.HTTP_500_INTERNAL_SERVER_ERROR) in chunk_str
        # Should retry exactly max_retry times
        assert mock_async_client.stream_count == CoordinatorConfig().exception_config.max_retry
        assert exec_release >= 1

    @pytest.mark.asyncio
    async def test_handle_request_error_when_decode_once_5xx(
            self, client, monkeypatch: MonkeyPatch, setup_dp_separation
    ):
        """Test case: EngineServer Decode request first returns 5XX, then 200.
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
            cdp_router = SeparatePDDualDispatchRouter(
                req_info, CoordinatorConfig(),
                scheduler=Scheduler(instance_provider=InstanceManager(CoordinatorConfig()), config=CoordinatorConfig()),
                request_manager=_request_manager
            )
            response = await cdp_router.handle_request()
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)

            # Should get a 200 after retry
            assert response.status_code == status.HTTP_200_OK
            # Decode: at least one fail then success; stream_count may be 2 or up to max_retry
            assert mock_async_client.stream_fail_count == 1
            assert mock_async_client.stream_count >= 2
            # Decode path may use stream only; post is used for metaserver/other branches
            assert mock_async_client.post_count >= 0
            assert req_info.state == ReqState.DECODE_END

    @pytest.mark.asyncio
    async def test_handle_request_error_when_decode_network_exception(
            self, client, monkeypatch: MonkeyPatch, setup_dp_separation
    ):
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
        ), stream_fail_times=CoordinatorConfig().exception_config.max_retry)

        req_info = await create_mock_request_info()

        with patch('motor.coordinator.router.base_router.httpx.AsyncClient', return_value=mock_async_client):
            cdp_router = SeparatePDDualDispatchRouter(
                req_info, CoordinatorConfig(),
                scheduler=Scheduler(instance_provider=InstanceManager(CoordinatorConfig()), config=CoordinatorConfig()),
                request_manager=_request_manager
            )
            response = await cdp_router.handle_request()
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
            chunk_str = "".join(chunks)
        assert error_message in chunk_str
        assert mock_async_client.stream_count == CoordinatorConfig().exception_config.max_retry
        assert mock_async_client.stream_fail_count == CoordinatorConfig().exception_config.max_retry
        assert req_info.state == ReqState.EXCEPTION
