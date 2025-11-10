#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


from abc import ABC, abstractmethod
from fastapi import status, HTTPException
from fastapi.responses import StreamingResponse
import httpx

from motor.config.coordinator import CoordinatorConfig
from motor.coordinator.models.request import RequestInfo, ReqState, ScheduledResource
from motor.coordinator.scheduler.scheduler import Scheduler
from motor.coordinator.router.request_error_handler import handle_request_errors
from motor.resources.endpoint import WorkloadAction
from motor.resources.instance import PDRole
from motor.utils.logger import get_logger

logger = get_logger(__name__)


class BaseRouter(ABC):
    """Base router class for handling requests with different instance configurations"""
    
    def __init__(self, req_info: RequestInfo):
        """Initialize the base router with request information
        
        Args:
            req_info: Request information object containing request details
        """
        self.req_info = req_info
        self.first_chunk_sent = False
    
    @abstractmethod
    async def handle_request(self) -> StreamingResponse:
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

        for i in range(CoordinatorConfig().exception_config.max_retry):
            result = Scheduler().select_instance_and_endpoint(role)
            logger.debug("Scheduling attempt %d for role %s", i + 1, role)
            # Check return value, ensure it's iterable with two elements
            if isinstance(result, (tuple, list)) and len(result) == 2 and all(result):
                ins, endpoint = result
                break
            logger.warning("Scheduling failed, role: %s, retrying %d/%d, result: %s", role, i + 1, 
                           CoordinatorConfig().exception_config.max_retry, result)
            if i == CoordinatorConfig().exception_config.max_retry - 1:
                self.req_info.update_state(ReqState.EXCEPTION)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE, 
                    detail=f"Scheduling failed, role:{role}"
                )
        logger.debug(f"Scheduled instance: {ins.job_name}, role: {role}")

        # If scheduler returns normally, it means allocation was successful
        self.req_info.update_state(ReqState.P_ALLOCATED if role == PDRole.ROLE_P else ReqState.D_ALLOCATED)
        if not Scheduler().update_workload(ins, endpoint, self.req_info.req_id, 
                                           WorkloadAction.ALLOCATION, self.req_info.req_len):
            self.req_info.update_state(ReqState.EXCEPTION)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                detail=f"Allocation failed, role:{role}"
            )
        logger.debug(f"Allocated instance: {ins.job_name}, role: {role}")
        return ScheduledResource(instance=ins, endpoint=endpoint)
    
    @handle_request_errors(stream=True)
    async def forward_stream_request(self, req_data: dict, resource: ScheduledResource):
        """Forward streaming request to the given endpoint
        
        Args:
            req_data: The request data to forward
            resource: The scheduled resource containing the endpoint
            
        Yields:
            Bytes of the response stream
        """
        logger.debug("Forward stream request: %s", req_data)
        endpoint = resource.endpoint
        headers = {
            'Content-Type': 'text/event-stream',
            'X-Request-Id': self.req_info.req_id
        }
        
        async with httpx.AsyncClient(timeout=CoordinatorConfig().exception_config.first_token_timeout,
                                    base_url=f"http://{endpoint.ip}:{endpoint.port}",
                                    verify=False) as client:
            self.first_chunk_sent = False
            async with client.stream("POST",
                                        f"/{self.req_info.api}",
                                        json=req_data,
                                        headers=headers) as response:
                if not response.is_success:
                    await response.aread()
                    response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    if not self.first_chunk_sent and chunk:
                        self.first_chunk_sent = True
                        self.req_info.update_state(ReqState.FIRST_TOKEN_FINISH)
                    yield chunk
                return

    @handle_request_errors(stream=False)
    async def forward_post_request(self, req_data: dict, resource: ScheduledResource) -> httpx.Response:
        """Forward non-streaming request to the given resource
        
        Args:
            req_data: The request data to forward
            resource: The scheduled resource containing the endpoint
            
        Returns:
            The response from the endpoint
        """
        endpoint = resource.endpoint
        headers = {
            'Content-Type': 'application/json',
            'X-Request-Id': self.req_info.req_id
        }
        async with httpx.AsyncClient(timeout=CoordinatorConfig().exception_config.infer_timeout,
                                    base_url=f"http://{endpoint.ip}:{endpoint.port}",
                                    verify=False) as client:

            response = await client.post(f"/{self.req_info.api}",
                                            json=req_data,
                                            headers=headers)
            response.raise_for_status()
            return response

    def release_all(self, resource: ScheduledResource):
        return self.__update_workload(resource, WorkloadAction.RELEASE_TOKENS) and \
            self.__update_workload(resource, WorkloadAction.RELEASE_KV)
    
    def release_tokens(self, resource: ScheduledResource):
        return self.__update_workload(resource, WorkloadAction.RELEASE_TOKENS)
    
    def release_kv(self, resource: ScheduledResource):
        return self.__update_workload(resource, WorkloadAction.RELEASE_KV)
        
    def __update_workload(self, resource: ScheduledResource, action: WorkloadAction):
        """Update the given resource's workload
        
        Args:
            resource: The scheduled resource to update
            
        Returns:
            The result of update
        """
        if not(resource and isinstance(resource, ScheduledResource) and resource.instance and resource.endpoint):
            logger.warning("Resource is empty")
            return False
        
        return Scheduler().update_workload(resource.instance, resource.endpoint, 
                                           self.req_info.req_id, action, self.req_info.req_len) 
