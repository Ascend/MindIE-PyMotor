#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Callable, TYPE_CHECKING
import asyncio
import functools

from fastapi import status, HTTPException
import httpx

from motor.coordinator.models.request import ReqState, ScheduledResource
from motor.coordinator.models.contants import REQUEST_DATA_KEY, RESOURCE_KEY
from motor.common.utils.security_utils import filter_sensitive_body, sanitize_error_message

# Import only for type checking to avoid runtime circular dependencies
if TYPE_CHECKING:
    from motor.coordinator.router.base_router import BaseRouter


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
    from motor.coordinator.router.base_router import BaseRouter
    request_params = [
        [RESOURCE_KEY, ScheduledResource],
        [REQUEST_DATA_KEY, dict]
    ]
    
    if not args or len(args) == 0 or not isinstance(args[0], BaseRouter):
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
    self_instance: 'BaseRouter' = args[0]
    resource: ScheduledResource = kwargs[RESOURCE_KEY]
    filtered_data = filter_sensitive_body(kwargs[REQUEST_DATA_KEY])
    self_instance.logger.debug("Forwarding request to instance at %s:%s with data: %s", \
        resource.endpoint.ip, resource.endpoint.business_port, filtered_data)
    
    for attempt in range(self_instance.config.exception_config.max_retry):
        try:
            async for result in __try_execute_function(func, stream, *args, **kwargs):
                yield result
            return
        except Exception as e:
            last_exc = e 
            await __handle_execution_exception(e, self_instance, resource)
        except asyncio.CancelledError as ce:
            self_instance.logger.debug("Request was cancelled")
            return
        except BaseException as be:
            self_instance.logger.warning(f"Request occurred unknown critical error: {str(be)}", exc_info=True)
            raise be
            
        if last_exc and attempt == self_instance.config.exception_config.max_retry - 1:
            __handle_final_failure(last_exc, self_instance)
            if isinstance(last_exc, HTTPException):
                raise last_exc
            safe_error_msg = sanitize_error_message(str(last_exc))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                detail=safe_error_msg
            )
        
        await __handle_retry_delay(attempt, self_instance)
    

async def __try_execute_function(func: Callable, stream: bool, *args, **kwargs):
    """Try to execute the function once"""
    if stream:
        async for chunk in func(*args, **kwargs):
            yield chunk
    else:
        result = await func(*args, **kwargs)
        yield result


async def __handle_execution_exception(exception: Exception, 
                                       self_instance: 'BaseRouter', 
                                       resource: ScheduledResource) -> Exception:
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


async def __handle_http_status_error(error: httpx.HTTPStatusError, self_instance: 'BaseRouter'):
    """Handle HTTP status errors"""
    if hasattr(error, 'response') and hasattr(error.response, 'status_code'):
        status_code = error.response.status_code
        self_instance.logger.warning(f"HTTP error, status code: {status_code}")
        
        # 4XX: Client request error, return directly
        if __is_client_error(status_code):
            self_instance.req_info.update_state(ReqState.INVALID)
            raise HTTPException(
                status_code=status_code, 
                detail=error.response.text
            )
    else:
        self_instance.logger.warning(f"HTTP error, but fail to parse status code: {error}")


def __is_client_error(status_code: int) -> bool:
    """Check if status code indicates a client error (4XX)"""
    return (status_code >= status.HTTP_400_BAD_REQUEST and 
            status_code < status.HTTP_500_INTERNAL_SERVER_ERROR)


def __handle_request_error(error: httpx.RequestError, self_instance: 'BaseRouter', resource: ScheduledResource):
    """Handle HTTP request errors"""
    self_instance.req_info.update_state(ReqState.EXCEPTION)
    
    if isinstance(error, httpx.NetworkError):
        self_instance.logger.warning("Network error: %s", str(error))
    elif isinstance(error, httpx.TimeoutException):
        self_instance.logger.warning("Timeout error: %s", str(error))
        self_instance.req_info.update_state(ReqState.TIMEOUT)
    else:
        self_instance.logger.warning(f"Unknown request error: {str(error)}")
    safe_error_msg = sanitize_error_message(str(error))
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
        detail=safe_error_msg
    )


def __handle_general_exception(error: Exception, self_instance: 'BaseRouter'):
    """Handle general exceptions"""
    self_instance.logger.warning(f"Failed for forwarding /{self_instance.req_info.api}, error: {str(error)}")
    
    # If any chunk has been sent, do not retry
    if self_instance.first_chunk_sent:
        self_instance.logger.error(f"Streaming to client interrupted after response started: {str(error)}")
        self_instance.req_info.update_state(ReqState.EXCEPTION)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Streaming interrupted, {str(error)}"
        )


async def __handle_retry_delay(attempt: int, self_instance: 'BaseRouter'):
    """Handle delay between retry attempts"""
    config = self_instance.config.exception_config
    self_instance.logger.warning("Attempt failed, retrying %d/%d", attempt + 1, config.max_retry)
    await asyncio.sleep(config.retry_delay * (2 ** (attempt - 1)))


def __handle_final_failure(exception: Exception, self_instance: 'BaseRouter'):
    """Handle final failure after all retries exhausted"""
    config = self_instance.config.exception_config
    self_instance.logger.error("Stream request forwarding failed, reach max retries %d", config.max_retry)
    self_instance.req_info.update_state(ReqState.EXCEPTION)
