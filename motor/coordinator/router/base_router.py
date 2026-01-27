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

import contextlib
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Optional, AsyncGenerator

import httpx
from fastapi import status, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

from motor.common.resources.endpoint import WorkloadAction
from motor.common.resources.instance import PDRole
from motor.common.utils.http_client import AsyncSafeHTTPSClient
from motor.common.utils.logger import get_logger
from motor.common.utils.security_utils import filter_sensitive_headers, filter_sensitive_body
from motor.config.coordinator import CoordinatorConfig
from motor.coordinator.models.contants import REQUEST_ID_KEY, DEFAULT_REQUEST_ID
from motor.coordinator.models.request import ErrorResponse
from motor.coordinator.models.request import RequestInfo, ReqState, ScheduledResource
from motor.coordinator.scheduler.scheduler import Scheduler

logger = get_logger(__name__)


class RequestLoggerAdapter(logging.LoggerAdapter):
    """logger adaptor add req_id as prefix"""

    def process(self, msg: str, kwargs: Any) -> tuple[str, Any]:
        req_id = self.extra.get(REQUEST_ID_KEY, DEFAULT_REQUEST_ID) if self.extra else DEFAULT_REQUEST_ID
        return f"[{req_id}] {msg}", kwargs


class BaseRouter(ABC):
    """Base router class for handling requests with different instance configurations"""

    def __init__(self, req_info: RequestInfo, config: CoordinatorConfig):
        """Initialize the base router with request information

        Args:
            req_info: Request information object containing request details
        """
        self.config = config
        self.req_info = req_info
        self.first_chunk_sent = False
        self.logger = RequestLoggerAdapter(
            logger, 
            extra={REQUEST_ID_KEY: req_info.req_id}
        )

    @contextlib.asynccontextmanager
    async def _manage_resource_context(self, role: PDRole, release_func):
        """
        Manage scheduled resource
        """
        resource: Optional[ScheduledResource] = None
        try:
            resource = self.prepare_resource(role)
            yield resource
        finally:
            if resource and not release_func(resource):
                self.logger.warning(
                    "Fail to release %s resource | Instance: %s | Endpoint: %s | State: %s",
                    role.name, resource.instance.id, resource.endpoint.id, self.req_info.state
                )

    @contextlib.asynccontextmanager
    async def _manage_client_context(self, resource: ScheduledResource, timeout: int):
        """
        Manage schedule resource and http client
        :param resource: ScheduledResource
        :param timeout: http client timeout
        """
        endpoint = resource.endpoint
        address = f"{endpoint.ip}:{endpoint.business_port}"
        async with AsyncSafeHTTPSClient(address=address,
                                        tls_config=self.config.infer_tls_config,
                                        timeout=timeout) as client:
            yield client

    @abstractmethod
    async def handle_request(self) -> StreamingResponse | JSONResponse:
        """Handle the request based on specific implementation

        Returns:
            StreamingResponse: The response stream for the request
        """
        pass

    def prepare_resource(self, role: PDRole) -> ScheduledResource:
        """Prepare resource for the given role by scheduling an instance

        Args:
            role: The role (PDRole) to prepare resource for

        Returns:
            tuple: A tuple containing the scheduled instance and endpoint

        Raises:
            Exception: If scheduling fails after maximum retry attempts
        """
        self.req_info.update_state(ReqState.P_SCHEDULING if role == PDRole.ROLE_P else ReqState.D_SCHEDULING)

        for i in range(self.config.exception_config.max_retry):
            result = Scheduler().select_instance_and_endpoint(role)
            self.logger.debug("Scheduling attempt %d for role %s", i + 1, role)
            # Check return value, ensure it's iterable with two elements
            if isinstance(result, (tuple, list)) and len(result) == 2 and all(result):
                ins, endpoint = result
                break
            self.logger.warning("Scheduling failed, role: %s, retrying %d/%d, result: %s", role, i + 1, 
                                self.config.exception_config.max_retry, result)
            if i == self.config.exception_config.max_retry - 1:
                self.req_info.update_state(ReqState.EXCEPTION)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE, 
                    detail=f"Scheduling failed, role:{role}"
                )
            time.sleep(0.1)
        self.logger.info(
            "Dispatch request api=%s len=%d -> endpoint_id=%s endpoint=%s:%s status=%s | "
            "instance_id=%s job=%s model=%s role=%s",
            self.req_info.api, self.req_info.req_len,
            endpoint.id, endpoint.ip, endpoint.business_port, endpoint.status,
            ins.id, ins.job_name, ins.model_name, role
        )

        # If scheduler returns normally, it means allocation was successful
        self.req_info.update_state(ReqState.P_ALLOCATED if role == PDRole.ROLE_P else ReqState.D_ALLOCATED)
        if not Scheduler().update_workload(ins, endpoint, self.req_info.req_id, 
                                           WorkloadAction.ALLOCATION, self.req_info.req_len):
            self.req_info.update_state(ReqState.EXCEPTION)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                detail=f"Allocation failed, role:{role}"
            )
        self.logger.debug("Allocated instance: %s, role: %s", ins.job_name, role)
        return ScheduledResource(instance=ins, endpoint=endpoint)

    async def forward_stream_request(self,
                                     req_data: dict,
                                     client: httpx.AsyncClient,
                                     ) -> AsyncGenerator[str, None]:
        """Forward streaming request to the given endpoint

        Args:
            req_data: The request data to forward
            client: The client connection with scheduled endpoint

        Yields:
            The stream response from the endpoint
        """

        headers = {
            'Content-Type': 'application/json',
            'X-Request-Id': self.req_info.req_id
        }
        self.logger.debug("Forward stream request base_url: %s, api: %s, headers: %s, body: %s, timeout: %s",
                          client.base_url, self.req_info.api, headers, req_data, client.timeout)

        self.first_chunk_sent = False
        async with client.stream(
            "POST",
            f"/{self.req_info.api}",
            json=req_data,
            headers=headers
        ) as response:
            if not response.is_success:
                await response.aread()
                response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if not self.first_chunk_sent and chunk:
                    self.first_chunk_sent = True
                    self.req_info.update_state(ReqState.FIRST_TOKEN_FINISH)
                yield chunk
            return

    async def forward_post_request(self,
                                   req_data: dict,
                                   client: httpx.AsyncClient,
                                   ) -> httpx.Response:
        """Forward non-streaming request to the given resource

        Args:
            req_data: The request data to forward
            client: The client to scheduled endpoint

        Returns:
            The response from the endpoint
        """
        headers = {
            'Content-Type': 'application/json',
            'X-Request-Id': self.req_info.req_id
        }
        filtered_headers = filter_sensitive_headers(headers)
        filtered_body = filter_sensitive_body(req_data)
        self.logger.debug("Forward post request base_url: %s, api: %s, headers: %s, body: %s, timeout: %s",
                          client.base_url, self.req_info.api, filtered_headers, filtered_body, client.timeout)

        response = await client.post(f"/{self.req_info.api}",
                                        json=req_data,
                                        headers=headers)
        response.raise_for_status()
        return response

    def release_all(self, resource: ScheduledResource):
        return self._update_workload(resource, WorkloadAction.RELEASE_TOKENS) and \
            self._update_workload(resource, WorkloadAction.RELEASE_KV)

    def release_tokens(self, resource: ScheduledResource):
        return self._update_workload(resource, WorkloadAction.RELEASE_TOKENS)

    def release_kv(self, resource: ScheduledResource):
        return self._update_workload(resource, WorkloadAction.RELEASE_KV)

    def _update_workload(self, resource: ScheduledResource, action: WorkloadAction):
        """Update the given resource's workload

        Args:
            resource: The scheduled resource to update

        Returns:
            The result of update
        """
        if not (
            resource 
            and isinstance(resource, ScheduledResource)
            and resource.instance
            and resource.endpoint
        ):
            self.logger.warning("Resource is empty")
            return False

        return Scheduler().update_workload(
            resource.instance,
            resource.endpoint, 
            self.req_info.req_id,
            action,
            self.req_info.req_len
        ) 

    def _log_request_details(self):
        current_time = time.time()
        cost_time = current_time - self.req_info.status[ReqState.ARRIVE]
        self.logger.debug("API: %s, Length: %d, State: %s, Cost Time: %s, All status Time: %s",
                          self.req_info.api, self.req_info.req_len, self.req_info.state, 
                          cost_time, self.req_info.status)

    def _generate_streaming_error_chunk(self, e: Exception) -> str:
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