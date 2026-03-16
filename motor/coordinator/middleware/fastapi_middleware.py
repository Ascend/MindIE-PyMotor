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
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from motor.common.utils.logger import get_logger
from motor.common.utils.security_utils import validate_file_security

from .rate_limiter import SimpleRateLimiter

logger = get_logger(__name__)

# Environment variable name constants
ENV_RATE_LIMIT_ENABLED = "RATE_LIMIT_ENABLED"
ENV_RATE_LIMIT_MAX_REQUESTS = "RATE_LIMIT_MAX_REQUESTS"
ENV_RATE_LIMIT_WINDOW_SIZE = "RATE_LIMIT_WINDOW_SIZE"
ENV_RATE_LIMIT_SCOPE = "RATE_LIMIT_SCOPE"
ENV_RATE_LIMIT_SKIP_PATHS = "RATE_LIMIT_SKIP_PATHS"


@dataclass
class SimpleRateLimitConfig:
    enabled: bool = True
    max_requests: int = 100
    window_size: int = 60
    scope: str = "per_ip"  # "global", "per_ip", "per_user"
    skip_paths: list = None
    error_message: str = "Request too frequent, please try again later"
    error_status_code: int = 429
    
    def __post_init__(self):
        if self.skip_paths is None:
            self.skip_paths = [
                "/liveness", "/ready", "/metrics", 
                "/docs", "/redoc", "/openapi.json",
                "/favicon.ico", "/startup"
            ]


def load_rate_limit_config(config_file: str | None = None) -> SimpleRateLimitConfig:
    """
    load rate limiting config
    
    Args:
        config_file: Configuration file path, if None use default configuration
        
    Returns:
        SimpleRateLimitConfig: Rate limiting configuration
    """
    config = SimpleRateLimitConfig()
    
    # load from config file first
    if config_file and os.path.exists(config_file):
        try:
            validate_file_security(config_file)

            with open(config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            for key, value in data.items():
                if hasattr(config, key):
                    setattr(config, key, value)
            
            logger.info(f"Loaded rate limiting configuration from file: {config_file}")
            
        except Exception as e:
            logger.error(f"Failed to load configuration file: {e}")
            logger.info("Using default configuration")
    else:
        logger.info("Using default rate limiting configuration")
    
    # get from env first and override (if any)
    # RATE_LIMIT_ENABLED（enable）
    if os.getenv(ENV_RATE_LIMIT_ENABLED) is not None:
        config.enabled = os.getenv(ENV_RATE_LIMIT_ENABLED, "true").lower() in ("true", "1", "yes")
    
    # RATE_LIMIT_MAX_REQUESTS（maximum number of requests）
    if os.getenv(ENV_RATE_LIMIT_MAX_REQUESTS) is not None:
        try:
            config.max_requests = int(os.getenv(ENV_RATE_LIMIT_MAX_REQUESTS))
        except (ValueError, TypeError):
            env_value = os.getenv(ENV_RATE_LIMIT_MAX_REQUESTS)
            logger.warning(f"Invalid {ENV_RATE_LIMIT_MAX_REQUESTS} value: {env_value}, using default")
    
    # RATE_LIMIT_WINDOW_SIZE（time window size）
    if os.getenv(ENV_RATE_LIMIT_WINDOW_SIZE) is not None:
        try:
            config.window_size = int(os.getenv(ENV_RATE_LIMIT_WINDOW_SIZE))
        except (ValueError, TypeError):
            env_value = os.getenv(ENV_RATE_LIMIT_WINDOW_SIZE)
            logger.warning(f"Invalid {ENV_RATE_LIMIT_WINDOW_SIZE} value: {env_value}, using default")
    
    # RATE_LIMIT_SCOPE（scope）
    if os.getenv(ENV_RATE_LIMIT_SCOPE) is not None:
        config.scope = os.getenv(ENV_RATE_LIMIT_SCOPE)
    
    # RATE_LIMIT_SKIP_PATHS (set of skip paths)
    if os.getenv(ENV_RATE_LIMIT_SKIP_PATHS) is not None:
        skip_paths_str = os.getenv(ENV_RATE_LIMIT_SKIP_PATHS, "")
        if skip_paths_str:
            config.skip_paths = [path.strip() for path in skip_paths_str.split(",") if path.strip()]
    
    logger.info(
        f"Rate limit config: enabled={config.enabled}, "
        f"max_requests={config.max_requests}, window_size={config.window_size}s"
    )
    
    return config


class SimpleRateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI rate limiting middleware.
    """

    def __init__(self,
                 app: ASGIApp,
                 rate_limiter: SimpleRateLimiter | None = None,
                 skip_paths: list | None = None,
                 error_message: str = "Request too frequent, please try again later",
                 error_status_code: int = 429):
        """
        initialize rate limiting middleware
        
        Args:
            app: FastAPI application instance
            rate_limiter: Rate limiter instance
            skip_paths: List of paths to skip
            error_message: Rate limiting error message
            error_status_code: Rate limiting error status code
        """
        super().__init__(app)
        
        self.rate_limiter = rate_limiter or SimpleRateLimiter()
        self.skip_paths = skip_paths or [
            "/liveness", "/ready", "/metrics", "/docs", "/redoc", "/openapi.json"
        ]
        self.error_message = error_message
        self.error_status_code = error_status_code
        self.enabled = True  # Hot-reload can disable rate limit via update_config(enabled=False)

        self.stats = {
            "total_requests": 0,
            "allowed_requests": 0,
            "blocked_requests": 0,
            "start_time": time.time()
        }

    @staticmethod
    def _extract_request_data(request: Request) -> Dict[str, Any]:
        return {
            "endpoint": request.url.path,
            "method": request.method,
            "timestamp": time.time()
        }

    @staticmethod
    def _create_rate_limit_headers(limit_info: Dict[str, Any]) -> Dict[str, str]:
        headers = {}
        
        if "available" in limit_info:
            headers["X-RateLimit-Remaining"] = str(limit_info["available"])
        if "limit" in limit_info:
            headers["X-RateLimit-Limit"] = str(limit_info["limit"])
        if "window_size" in limit_info:
            headers["X-RateLimit-Window"] = str(limit_info["window_size"])
        
        return headers
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        self.stats["total_requests"] += 1
        if not self.enabled:
            return await call_next(request)
        if self._should_skip_path(request.url.path):
            return await call_next(request)

        try:
            request_data = self._extract_request_data(request)
            
            # Check rate limiting
            allowed, limit_info = self.rate_limiter.is_allowed(request_data)
            
            if allowed:
                # Request allowed, continue processing
                self.stats["allowed_requests"] += 1
                
                # Create response
                response = await call_next(request)
                
                # Add rate limiting information to response headers
                rate_limit_headers = self._create_rate_limit_headers(limit_info)
                for key, value in rate_limit_headers.items():
                    response.headers[key] = value
                
                return response
            else:
                # Request rate limited
                self.stats["blocked_requests"] += 1
                
                # Create rate limiting response headers
                rate_limit_headers = self._create_rate_limit_headers(limit_info)
                
                # Build error response
                error_response = {
                    "error": "rate_limit_exceeded",
                    "message": self.error_message,
                    "details": {
                        "available": limit_info.get("available", 0),
                        "limit": limit_info.get("limit", 0),
                        "window_size": limit_info.get("window_size", 0)
                    }
                }
                
                logger.warning(f"Request rate limited: {request_data['endpoint']}")
                
                response = JSONResponse(
                    status_code=self.error_status_code,
                    content=error_response,
                    headers=rate_limit_headers
                )
                
                return response
                
        except Exception as e:
            logger.error(f"Error in rate limiting middleware processing request: {e}")
            # Allow request by default when error occurs
            self.stats["allowed_requests"] += 1
            return await call_next(request)

    def update_config(
        self,
        skip_paths: list | None = None,
        error_message: str | None = None,
        error_status_code: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        """Update middleware config at runtime (for config hot-reload)."""
        if skip_paths is not None:
            self.skip_paths = skip_paths
        if error_message is not None:
            self.error_message = error_message
        if error_status_code is not None:
            self.error_status_code = error_status_code
        if enabled is not None:
            self.enabled = enabled

    def _should_skip_path(self, path: str) -> bool:
        return any(path.startswith(skip_path) for skip_path in self.skip_paths)


def create_simple_rate_limit_middleware(app: ASGIApp, 
                                      max_requests: int = 100,
                                      window_size: int = 60) -> SimpleRateLimitMiddleware:
    # Create rate limiter
    rate_limiter = SimpleRateLimiter(
        max_requests=max_requests,
        window_size=window_size
    )
    
    # Create middleware
    middleware = SimpleRateLimitMiddleware(
        app=app,
        rate_limiter=rate_limiter,
        skip_paths=["/liveness", "/ready", "/metrics", "/docs", "/redoc", "/openapi.json"]
    )
    
    return middleware