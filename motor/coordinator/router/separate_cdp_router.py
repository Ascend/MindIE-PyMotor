#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json

from fastapi.responses import StreamingResponse
from fastapi import HTTPException, status
import httpx

from motor.coordinator.models.request import ReqState, ScheduledResource
from motor.coordinator.router.base_router import BaseRouter
from motor.config.coordinator import CoordinatorConfig
from motor.common.resources.instance import PDRole
from motor.coordinator.core.request_manager import RequestManager
from motor.common.utils.logger import get_logger

logger = get_logger(__name__)


class SeparateCDPRouter(BaseRouter):
    
    async def handle_request(self) -> StreamingResponse:
        
        req_data = self.__gen_d_request()
        try:
            # Schedule D instance
            decode_resource = self.prepare_resource(PDRole.ROLE_D)
        except Exception as e:
            logger.error("Error occurred while scheduling Decode resource: %s", e)
            raise e
        
        async def generate_stream():
            logger.debug("Handling streaming Decode request")
            RequestManager().add_req_info(self.req_info)
            try:
                # Forward D request
                async for chunk in self.forward_stream_request(req_data=req_data, resource=decode_resource):
                    yield chunk
                self.req_info.update_state(ReqState.DECODE_END)
            except Exception as e:
                logger.error("Error occurred while streaming Decode request: %s", e)
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
            finally:
                RequestManager().del_req_info(self.req_info.req_id)
                # After streaming done or error occurred, release tokens
                if decode_resource and not self.release_tokens(decode_resource):
                    logger.warning(f"Fail to release decode resource, instance id: {decode_resource.instance.id}, \
                        endpoint id: {decode_resource.endpoint.id}, \
                        req state: {self.req_info.state}")
                
        async def generate_post():
            logger.debug("Handling non-streaming Decode request")
            RequestManager().add_req_info(self.req_info)
            try:
                # Forward D request
                async for response in self.forward_post_request(req_data=req_data, resource=decode_resource):
                    resp_json = response.json()
                    self.req_info.update_state(ReqState.DECODE_END)
                    return resp_json
            except Exception as e:
                logger.error("Error occurred while posting Decode request: %s", e)
                raise e
            finally:
                RequestManager().del_req_info(self.req_info.req_id)
                # After streaming done or error occurred, release tokens
                if decode_resource and not self.release_tokens(decode_resource):
                    logger.warning(f"Fail to release decode resource, instance id: {decode_resource.instance.id}, \
                        endpoint id: {decode_resource.endpoint.id}, \
                        req state: {self.req_info.state}")
        
        if self.req_info.req_data.get("stream", False):
            return StreamingResponse(generate_stream(), media_type="text/event-stream")
        else:
            return await generate_post()
    
    async def handle_metaserver_request(self) -> httpx.Response:
        prefill_resource: ScheduledResource = None
        req_data = self.__gen_p_request()
        try:
            # Schedule P instance
            prefill_resource = self.prepare_resource(PDRole.ROLE_P)
            # Forward P request
            # P non-streaming request
            async for response in self.forward_post_request(req_data=req_data, resource=prefill_resource):
                resp_json = response.json()
                logger.debug("Prefill response status code: %d, json content: %s", response.status_code, resp_json)
                self.req_info.update_state(ReqState.PREFILL_END)
        except Exception as e:
            logger.error("Error occurred while forwarding P request: %s", e)
            raise e
        finally:
            # After streaming done or error occurred, release tokens
            if prefill_resource and not self.release_all(prefill_resource):
                logger.warning(f"Fail to release decode resource, instance id: {prefill_resource.instance.id}, \
                    endpoint id: {prefill_resource.endpoint.id}, \
                    req state: {self.req_info.state}")
                
        return resp_json
    
    def __gen_d_request(self) -> dict:
        """Generate D request parameters"""
        # read management http config
        host = CoordinatorConfig().server_config.mgmt_host
        port = CoordinatorConfig().server_config.mgmt_port
        
        req_data = self.req_info.req_data.copy()
        req_data['kv_transfer_params'] = {
            "do_remote_decode": False,
            "do_remote_prefill": True,
            "metaserver": f"http://{host}:{port}/v1/metaserver"
        }
        return req_data
    
    def __gen_p_request(self) -> dict:
        """Generate P request parameters"""
        kv_transfer_params = self.req_info.req_data.copy()
        request_id = kv_transfer_params["request_id"]

        req_info = RequestManager().get_req_info(request_id)
        if not req_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, 
                detail=f"Request ID {request_id} not found in RequestManager"
            )
        
        # Req_info reference for update request state
        self.req_info = req_info
        # Copy req_data before modify
        req_data = req_info.req_data.copy()
        req_data["stream"] = False
        req_data["max_tokens"] = 1
        req_data["kv_transfer_params"] = kv_transfer_params
        
        if "stream_options" in req_data:
            del req_data["stream_options"]
        
        return req_data