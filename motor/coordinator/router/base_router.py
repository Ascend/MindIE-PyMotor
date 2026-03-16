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
import contextlib
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator

import httpx
from anyio import CancelScope
from fastapi import status, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

from motor.common.resources.endpoint import WorkloadAction
from motor.common.resources.instance import PDRole
from motor.common.utils.http_client import HTTPClientPool
from motor.common.utils.logger import get_logger
from motor.common.utils.security_utils import filter_sensitive_headers, filter_sensitive_body
from motor.config.coordinator import CoordinatorConfig
from motor.coordinator.models.constants import REQUEST_ID_KEY, DEFAULT_REQUEST_ID
from motor.coordinator.models.response import ErrorResponse
from motor.coordinator.domain import ScheduledResource
from motor.coordinator.models.request import RequestInfo, ReqState
from motor.coordinator.domain import SchedulingFacade, UpdateWorkloadParams
from motor.common.resources.instance import Instance
from motor.common.resources.endpoint import Endpoint, Workload
from motor.coordinator.domain.request_manager import RequestManager
from motor.coordinator.router.workload_action_handler import WorkloadActionHandler

logger = get_logger(__name__)

_SCHEDULING_LOG_SAMPLE_RATE = 100  # ~1% sampling at high QPS


def _should_log_scheduling_sample(req_id: str) -> bool:
    return hash(req_id) % _SCHEDULING_LOG_SAMPLE_RATE == 0


class RequestLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: Any) -> tuple[str, Any]:
        req_id = self.extra.get(REQUEST_ID_KEY, DEFAULT_REQUEST_ID) if self.extra else DEFAULT_REQUEST_ID
        return f"[{req_id}] {msg}", kwargs


class BaseRouter(ABC):
    """
    Base router; depends on SchedulingFacade injection.
    """

    def __init__(
        self,
        req_info: RequestInfo,
        config: CoordinatorConfig,
        scheduler: SchedulingFacade,
        request_manager: RequestManager,
        workload_action_handler: WorkloadActionHandler | None = None,
    ):
        self.config = config
        self.req_info = req_info
        self.first_chunk_sent = False
        self.logger = RequestLoggerAdapter(
            logger,
            extra={REQUEST_ID_KEY: req_info.req_id}
        )
        self.is_meta = False
        self._scheduler: SchedulingFacade = scheduler
        self._request_manager = request_manager
        self._workload_action_handler = (
            workload_action_handler
            if workload_action_handler is not None
            else WorkloadActionHandler(self._request_manager)
        )

    @staticmethod
    def _select_endpoint_from_instance(instance: Instance) -> Endpoint | None:
        if instance and instance.endpoints:
            for endpoints_dict in instance.endpoints.values():
                for endpoint in endpoints_dict.values():
                    status_val = endpoint.status.value if hasattr(endpoint.status, "value") else str(endpoint.status)
                    if status_val == "normal":
                        return endpoint
        return None

    @staticmethod
    def _generate_streaming_error_chunk(e: Exception) -> str:
        if isinstance(e, HTTPException):
            error_response = ErrorResponse(
                code=e.status_code,
                type=type(e).__name__,
                message=e.detail,
            )
        elif isinstance(e, httpx.HTTPStatusError):
            error_response = ErrorResponse(
                code=e.response.status_code,
                type=type(e).__name__,
                message=str(e),
            )
        else:
            error_response = ErrorResponse(
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                type=type(e).__name__,
                message=str(e),
            )
        return f"data: {error_response.model_dump_json()}\n\n"

    @abstractmethod
    async def handle_request(self) -> StreamingResponse | JSONResponse:
        pass

    @contextlib.asynccontextmanager
    async def _manage_client_context(self, resource: ScheduledResource):
        endpoint = resource.endpoint
        t0_client = time.perf_counter()
        client_pool = HTTPClientPool()
        client = await client_pool.get_client(
            ip=endpoint.ip,
            port=endpoint.business_port,
            tls_config=self.config.infer_tls_config
        )
        elapsed_client_ms = (time.perf_counter() - t0_client) * 1000
        self.logger.debug(
            "Scheduling latency stage=get_http_client elapsed_ms=%.2f endpoint=%s:%s",
            elapsed_client_ms, endpoint.ip, endpoint.business_port
        )
        yield client

    @contextlib.asynccontextmanager
    async def _manage_resource_context(self, role: PDRole, release_func):
        resource: ScheduledResource | None = None
        trace_obj = self.req_info.trace_obj
        try:
            trace_obj.add_trace_event("Begin Scheduled Resource", is_meta=self.is_meta)
            resource = await self.prepare_resource(role)
            attributes = {
                "instance": f"{resource.instance.id}-{resource.instance.role}",
                "endpoint": f"{resource.endpoint.id}-{resource.endpoint.ip}:{resource.endpoint.business_port}",
            }
            trace_obj.add_trace_event("Scheduled Resource ok", attributes=attributes, is_meta=self.is_meta)
            yield resource
        finally:
            if resource:
                if asyncio.iscoroutinefunction(release_func):
                    with CancelScope(shield=True):
                        result = await release_func(resource)
                else:
                    result = release_func(resource)
                if not result:
                    self.logger.debug(
                        "release_func(%s) returned False instance_id=%s endpoint_id=%s state=%s",
                        role.name, resource.instance.id, resource.endpoint.id, self.req_info.state
                    )

    async def prepare_resource(self, role: PDRole) -> ScheduledResource:
        """Select instance + allocate workload (one RPC), record in RequestManager, retry on failure."""
        self.req_info.update_state(ReqState.P_SCHEDULING if role == PDRole.ROLE_P else ReqState.D_SCHEDULING)

        last_exception = None
        t0_prepare = time.perf_counter()
        for attempt in range(self.config.exception_config.max_retry):
            try:
                t0_select = time.perf_counter()
                result = await self._scheduler.select_and_allocate(
                    role, self.req_info
                )
                elapsed_select_ms = (time.perf_counter() - t0_select) * 1000
                if _should_log_scheduling_sample(self.req_info.req_id):
                    self.logger.info(
                        "Scheduling latency role=%s stage=select_and_allocate elapsed_ms=%.2f attempt=%d/%d",
                        role, elapsed_select_ms, attempt + 1, self.config.exception_config.max_retry
                    )

                if result is None:
                    raise ValueError(f"No instance available for role {role} or allocate failed")

                ins, endpoint, allocate_workload = result
                if not ins or not endpoint:
                    raise ValueError(f"Invalid scheduler result: {result}")

                if not await self._request_manager.add_req_workload(
                    self.req_info.req_id, role, allocate_workload
                ):
                    raise RuntimeError(f"Request {self.req_info.req_id} already allocated for role {role}")

                self.req_info.update_state(
                    ReqState.P_ALLOCATED if role == PDRole.ROLE_P else ReqState.D_ALLOCATED
                )

                elapsed_prepare_ms = (time.perf_counter() - t0_prepare) * 1000
                if _should_log_scheduling_sample(self.req_info.req_id):
                    self.logger.info(
                        "Scheduling role=%s allocated instance_id=%s endpoint_id=%s "
                        "job=%s endpoint=%s:%s total_ms=%.2f",
                        role, ins.id, endpoint.id, ins.job_name,
                        endpoint.ip, endpoint.business_port, elapsed_prepare_ms
                    )
                self.logger.debug(
                    "Dispatch api=%s len=%d endpoint_status=%s model=%s",
                    self.req_info.api, self.req_info.req_len, endpoint.status, ins.model_name
                )
                return ScheduledResource(instance=ins, endpoint=endpoint)
                
            except Exception as e:
                last_exception = e
                self.logger.warning(
                    "Scheduling attempt %d/%d failed for role %s: %s",
                    attempt + 1, self.config.exception_config.max_retry, role, e
                )
                
                if attempt < self.config.exception_config.max_retry - 1:
                    await asyncio.sleep(0.1)
                    continue
        
        self.req_info.update_state(ReqState.EXCEPTION)
        error_detail = (
            f"Scheduling failed after {self.config.exception_config.max_retry} attempts, "
            f"role: {role}"
        )
        if last_exception:
            error_detail += f", last error: {str(last_exception)}"
        
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error_detail
        )

    async def forward_stream_request(self,
                                     req_data: dict,
                                     client: httpx.AsyncClient,
                                     timeout: int
                                     ) -> AsyncGenerator[str, None]:
        trace_obj = self.req_info.trace_obj
        headers = {
            'Content-Type': 'application/json',
            'X-Request-Id': self.req_info.req_id
        }
        trace_obj.set_trace_attribute("server.path", self.req_info.api, self.is_meta)
        headers.update(trace_obj.get_trace_headers_dict(self.is_meta))

        self.logger.debug("Forward stream request base_url: %s, api: %s, headers: %s, body: %s, timeout: %s",
                          client.base_url, self.req_info.api, headers, req_data, timeout)

        self.first_chunk_sent = False
        trace_obj.add_trace_event(
            f"Begin to stream: {client.base_url}/{self.req_info.api}, {client.timeout}",
            is_meta=self.is_meta
        )
        t0_forward = time.perf_counter()
        async with client.stream(
            "POST",
            f"/{self.req_info.api}",
            json=req_data,
            headers=headers,
            timeout=timeout
        ) as response:
            trace_obj.add_trace_event(f"Stream ok: {response.status_code}", is_meta=self.is_meta)
            elapsed_to_connect_ms = (time.perf_counter() - t0_forward) * 1000
            if _should_log_scheduling_sample(self.req_info.req_id):
                self.logger.info(
                    "Scheduling latency stage=forward_to_engine_connect elapsed_ms=%.2f api=%s",
                    elapsed_to_connect_ms, self.req_info.api
                )
            if not response.is_success:
                await response.aread()
            response.raise_for_status()
            count_token = 0
            async for chunk in response.aiter_bytes():
                if not self.first_chunk_sent and chunk:
                    self.first_chunk_sent = True
                    trace_obj.set_time_first_token()
                    elapsed_first_chunk_ms = (time.perf_counter() - t0_forward) * 1000
                    if _should_log_scheduling_sample(self.req_info.req_id):
                        self.logger.info(
                            "Scheduling latency stage=forward_to_engine_first_chunk elapsed_ms=%.2f api=%s",
                            elapsed_first_chunk_ms, self.req_info.api
                        )
                    self.req_info.update_state(ReqState.FIRST_TOKEN_FINISH)
                else:
                    count_token += 1
                yield chunk
            trace_obj.set_count_token(count_token)

    async def forward_request(self,
                             req_data: dict,
                             client: httpx.AsyncClient,
                             timeout: int
                             ) -> httpx.Response:
        """Forward non-streaming request to the given resource

        Args:
            req_data: The request data to forward
            client: The client to scheduled endpoint

        Returns:
            The response from the endpoint
        """
        trace_obj = self.req_info.trace_obj
        headers = {
            'Content-Type': 'application/json',
            'X-Request-Id': self.req_info.req_id
        }
        trace_obj.set_trace_attribute("server.path", self.req_info.api, self.is_meta)
        headers.update(trace_obj.get_trace_headers_dict(self.is_meta))

        filtered_headers = filter_sensitive_headers(headers)
        filtered_body = filter_sensitive_body(req_data)
        self.logger.debug("Forward request base_url: %s, api: %s, headers: %s, body: %s, timeout: %s",
                          client.base_url, self.req_info.api, filtered_headers, filtered_body, timeout)

        trace_obj.add_trace_event(
            f"Begin to post: {client.base_url}/{self.req_info.api}, {client.timeout}",
            is_meta=self.is_meta
        )
        t0_forward = time.perf_counter()
        response = await client.post(f"/{self.req_info.api}",
                                        json=req_data,
                                        headers=headers,
                                        timeout=timeout)
        trace_obj.add_trace_event(f"Post ok: {response.status_code}", is_meta=self.is_meta)
        elapsed_forward_ms = (time.perf_counter() - t0_forward) * 1000
        if _should_log_scheduling_sample(self.req_info.req_id):
            self.logger.info(
                "Scheduling latency stage=forward_to_engine elapsed_ms=%.2f api=%s",
                elapsed_forward_ms, self.req_info.api
            )
        response.raise_for_status()
        await response.aclose()
        return response

    async def release_all(self, resource: ScheduledResource):
        """Release tokens and KV cache; returns True only if both succeed."""
        tokens_result = await self._update_workload(resource, WorkloadAction.RELEASE_TOKENS)
        kv_result = await self._update_workload(resource, WorkloadAction.RELEASE_KV)
        return tokens_result and kv_result

    async def release_tokens(self, resource: ScheduledResource):
        return await self._update_workload(resource, WorkloadAction.RELEASE_TOKENS)

    async def release_kv(self, resource: ScheduledResource):
        return await self._update_workload(resource, WorkloadAction.RELEASE_KV)



    def _parse_scheduler_result(self, result, role: PDRole) -> tuple[Instance, Endpoint]:
        """Return (Instance, Endpoint); accepts tuple or Instance (legacy)."""
        if isinstance(result, (tuple, list)) and len(result) == 2:
            ins, endpoint = result
            if not isinstance(ins, Instance) or not isinstance(endpoint, Endpoint):
                raise ValueError(
                    f"Invalid result types: expected (Instance, Endpoint), "
                    f"got ({type(ins).__name__}, {type(endpoint).__name__})"
                )
            return ins, endpoint

        elif isinstance(result, Instance):
            endpoint = self._select_endpoint_from_instance(result)
            if not endpoint:
                raise ValueError(f"No endpoint found for instance {result.id} (role: {role})")
            return result, endpoint

        else:
            raise ValueError(
                f"Unexpected scheduler result type: {type(result).__name__}, "
                f"expected tuple[Instance, Endpoint] or Instance"
            )

    async def _update_workload(self, resource: ScheduledResource, action: WorkloadAction):
        """Update the given resource's workload.
        Delegates to WorkloadActionHandler to compute workload_change, update RequestManager, then call Scheduler.
        """
        workload_change, role = await self._workload_action_handler.compute_and_update(
            resource,
            self.req_info.req_id,
            action,
            self.req_info.req_len,
        )
        if workload_change is None or role is None:
            return False
        params = UpdateWorkloadParams(
            instance_id=resource.instance.id,
            endpoint_id=resource.endpoint.id,
            role=resource.instance.role,
            req_id=self.req_info.req_id,
            workload_action=action,
            workload_change=workload_change,
        )
        return await self._scheduler.update_workload(params)
    

    def _log_request_details(self):
        current_time = time.time()
        cost_time = current_time - self.req_info.status[ReqState.ARRIVE]
        self.logger.debug("API: %s, Length: %d, State: %s, Cost Time: %s, All status Time: %s",
                          self.req_info.api, self.req_info.req_len, self.req_info.state, 
                          cost_time, self.req_info.status)
