#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Callable
import asyncio
import functools

from fastapi import status, HTTPException
import httpx

from motor.config.coordinator import CoordinatorConfig
from motor.coordinator.models.request import ReqState, ScheduledResource
from motor.coordinator.core.instance_healthchecker import InstanceHealthChecker
from motor.utils.logger import get_logger

logger = get_logger(__name__)


def handle_request_errors(stream=True):
    """Decorator to handle request errors with retry logic
    
    Args:
        stream: Whether the function returns a stream or a single response
        
    Returns:
        Decorator function that wraps the target function with error handling
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            if not __check_request_params(args, kwargs):
                async for result in __execute_without_retry(func, stream, *args, **kwargs):
                    yield result
                return
                
            async for result in __execute_with_retry(func, stream, *args, **kwargs):
                yield result
            return
        return wrapper
    return decorator


def __check_request_params(args: tuple, kwargs: dict) -> bool:
    """Check if required request parameters are present and valid"""
    request_params = [
        ['resource', ScheduledResource],
        ['req_data', dict]
    ]
    
    if not args or len(args) == 0:
        return False
        
    for param_name, param_type in request_params:
        if param_name not in kwargs or not isinstance(kwargs[param_name], param_type):
            return False
    return True


async def __execute_without_retry(func: Callable, stream: bool, *args, **kwargs):
    """Execute function without retry logic"""
    if stream:
        async for chunk in func(*args, **kwargs):
            yield chunk
    else:
        result = await func(*args, **kwargs)
        yield result


async def __execute_with_retry(func: Callable, stream: bool, *args, **kwargs):
    """Execute function with retry logic and error handling"""
    last_exc = None
    self_instance = args[0] if args else None
    resource = kwargs['resource']
    
    logger.debug("Forwarding request to instance at %s:%s with data: %s", \
        resource.endpoint.ip, resource.endpoint.port, kwargs['req_data'])
    
    for attempt in range(CoordinatorConfig().exception_config.max_retry):
        try:
            async for result in __try_execute_function(func, stream, *args, **kwargs):
                yield result
            return
        except Exception as e:
            last_exc = e 
            await __handle_execution_exception(e, self_instance, resource)
            
        if last_exc and attempt == CoordinatorConfig().exception_config.max_retry - 1:
            __handle_final_failure(self_instance, last_exc)
            if isinstance(last_exc, HTTPException):
                raise last_exc
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                detail=str(last_exc)
            )
        
        await __handle_retry_delay(attempt)
    

async def __try_execute_function(func: Callable, stream: bool, *args, **kwargs):
    """Try to execute the function once"""
    if stream:
        async for chunk in func(*args, **kwargs):
            yield chunk
    else:
        result = await func(*args, **kwargs)
        yield result


async def __handle_execution_exception(exception: Exception, self_instance, resource: ScheduledResource) -> Exception:
    """Handle exceptions during function execution
    
    Returns:
        Exception to raise (or None to continue retrying)
    """
    if isinstance(exception, httpx.HTTPStatusError):
        return await __handle_http_status_error(exception, self_instance)
    elif isinstance(exception, httpx.RequestError):
        return __handle_request_error(exception, self_instance, resource)
    else:
        return __handle_general_exception(exception, self_instance)


async def __handle_http_status_error(error: httpx.HTTPStatusError, self_instance):
    """Handle HTTP status errors"""
    if hasattr(error, 'response') and hasattr(error.response, 'status_code'):
        status_code = error.response.status_code
        logger.warning(f"HTTP error, status code: {status_code}")
        
        # 4XX: Client request error, return directly
        if __is_client_error(status_code):
            self_instance.req_info.update_state(ReqState.INVALID)
            raise HTTPException(
                status_code=status_code, 
                detail=error.response.text
            )
    else:
        logger.warning(f"HTTP error, but fail to parse status code: {error}")


def __is_client_error(status_code: int) -> bool:
    """Check if status code indicates a client error (4XX)"""
    return (status_code >= status.HTTP_400_BAD_REQUEST and 
            status_code < status.HTTP_500_INTERNAL_SERVER_ERROR)


def __handle_request_error(error: httpx.RequestError, self_instance, resource: ScheduledResource):
    """Handle HTTP request errors"""
    self_instance.req_info.update_state(ReqState.EXCEPTION)
    
    if isinstance(error, httpx.TransportError):
        InstanceHealthChecker().push_exception_instance(resource.instance, resource.endpoint)
    
    if isinstance(error, httpx.NetworkError):
        logger.warning("Network error: %s", str(error))
    elif isinstance(error, httpx.TimeoutException):
        logger.warning("Timeout error: %s", str(error))
        self_instance.req_info.update_state(ReqState.TIMEOUT)
    else:
        logger.warning(f"Unknown request error: {str(error)}")
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
        detail=str(error)
    )


def __handle_general_exception(error: Exception, self_instance):
    """Handle general exceptions"""
    logger.warning(f"Failed for forwarding /{self_instance.req_info.api}, error: {str(error)}")
    
    # If any chunk has been sent, do not retry
    if self_instance.first_chunk_sent:
        logger.error(f"Streaming to client interrupted after response started: {str(error)}")
        self_instance.req_info.update_state(ReqState.EXCEPTION)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Streaming interrupted, {str(error)}"
        )


async def __handle_retry_delay(attempt: int):
    """Handle delay between retry attempts"""
    config = CoordinatorConfig().exception_config
    logger.warning("Attempt failed, retrying %d/%d", attempt + 1, config.max_retry)
    await asyncio.sleep(config.retry_delay * (2 ** (attempt - 1)))


def __handle_final_failure(self_instance, exception: Exception):
    """Handle final failure after all retries exhausted"""
    config = CoordinatorConfig().exception_config
    logger.error("Stream request forwarding failed, reach max retries %d", config.max_retry)
    self_instance.req_info.update_state(ReqState.EXCEPTION)
