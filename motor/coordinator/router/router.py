#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from fastapi import HTTPException, Request, status
from fastapi.responses import StreamingResponse
import httpx

from motor.coordinator.models.request import RequestInfo, ReqState
from motor.coordinator.core.request_manager import RequestManager
from motor.config.coordinator import DeployMode, CoordinatorConfig
from motor.coordinator.router.base_router import BaseRouter
from motor.coordinator.router.pd_hybrid_router import PDHybridRouter
from motor.coordinator.router.separate_pd_router import SeparatePDRouter
from motor.coordinator.router.separate_cdp_router import SeparateCDPRouter
from motor.common.utils.logger import get_logger

logger = get_logger(__name__)

_ROUTER_MAP: dict[DeployMode, type['BaseRouter']] = {
    DeployMode.CDP_SEPARATE: SeparateCDPRouter,
    DeployMode.PD_SEPARATE: SeparateCDPRouter,
    DeployMode.CPCD_SEPARATE: SeparatePDRouter,
    DeployMode.SINGLE_NODE: PDHybridRouter,
}


async def handle_request(raw_request: Request) -> StreamingResponse:
    """Handle incoming requests and route them to appropriate router implementation
    
    Args:
        raw_request: The incoming FastAPI request object
        
    Returns:
        StreamingResponse: The response stream from the selected router implementation
        
    Raises:
        HTTPException: If request body is empty or request fail
    """

    req_info = await __create_request_info(raw_request)

    deploy_mode = CoordinatorConfig().scheduler_config.deploy_mode
    router_impl_class = _ROUTER_MAP.get(deploy_mode)
    if not router_impl_class:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Unknown deploy mode: {deploy_mode}"
        )
        
    router_impl = router_impl_class(req_info)
    
    try:
        return await router_impl.handle_request()
    except Exception as e:
        logger.debug(f"Error occurred in proxy server endpoint: {req_info.api}, error: {str(e)}", exc_info=True)
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=str(e)
        ) from e


async def handle_metaserver_request(raw_request: Request) -> httpx.Response:
    """Only for CDP mode
    Handle incoming requests from D Instance and route them to P instance
    
    Args:
        raw_request: The incoming FastAPI request object from D Instance
        
    Returns:
        httpx.Response: The non stream response from the selected P instance
        
    Raises:
        HTTPException: If request body is empty or request fail
    """
    req_info = await __create_request_info(raw_request)
    
    deploy_mode = CoordinatorConfig().scheduler_config.deploy_mode
    if not deploy_mode or (deploy_mode != DeployMode.CDP_SEPARATE and deploy_mode != DeployMode.PD_SEPARATE):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Unsupport deploy mode: {deploy_mode}"
        )
    
    try:
        return await SeparateCDPRouter(req_info=req_info).handle_metaserver_request()
    except Exception as e:
        logger.debug(f"Error occurred in meta server endpoint: {req_info.api}, error: {str(e)}", exc_info=True)
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=str(e)
        ) from e


async def __create_request_info(raw_request: Request) -> RequestInfo:
    request_body = await raw_request.body()
    if not request_body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty request body"
        )
    
    try:
        request_json = await raw_request.json()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON format: {str(e)}"
        ) from e
    
    if not request_json:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty request json"
        )
        
    req_id = RequestManager().generate_request_id()
    req_len = len(request_body)
    api = raw_request.url.path.lstrip('/')
    
    return RequestInfo(
        req_id=req_id,
        req_data=request_json.copy(),
        api=api,
        req_len=req_len,
        state=ReqState.ARRIVE
    )