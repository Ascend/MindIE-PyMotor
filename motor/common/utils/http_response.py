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

from typing import Any, Optional, Dict
from fastapi import HTTPException, status


def format_success_response(data: Optional[Any] = None, message: str = "Success") -> Dict[str, Any]:
    """Format a success response
    
    Args:
        data: Data to return
        message: Success message
        
    Returns:
        Standardized response dictionary
    """
    return {
        "code": 200,
        "message": message,
        "data": data or {}
    }


def raise_http_exception(
    message: str, 
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
    code: Optional[int] = None,
    data: Optional[Any] = None
) -> None:
    """Raise an HTTP exception
    
    Args:
        message: Error message
        status_code: HTTP status code
        code: Business error code (defaults to HTTP status code)
        data: Additional error data
    """
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": code or status_code,
            "message": message,
            "data": data or {}
        }
    )


def raise_bad_request(message: str, code: int = 400, data: Optional[Any] = None) -> None:
    """Raise 400 Bad Request error"""
    raise_http_exception(message, status.HTTP_400_BAD_REQUEST, code, data)


def raise_unauthorized(message: str, code: int = 401, data: Optional[Any] = None) -> None:
    """Raise 401 Unauthorized error"""
    raise_http_exception(message, status.HTTP_401_UNAUTHORIZED, code, data)


def raise_forbidden(message: str, code: int = 403, data: Optional[Any] = None) -> None:
    """Raise 403 Forbidden error"""
    raise_http_exception(message, status.HTTP_403_FORBIDDEN, code, data)


def raise_not_found(message: str, code: int = 404, data: Optional[Any] = None) -> None:
    """Raise 404 Forbidden error"""
    raise_http_exception(message, status.HTTP_404_NOT_FOUND, code, data)


def raise_internal_error(message: str, code: int = 500, data: Optional[Any] = None) -> None:
    """Raise 500 Forbidden error"""
    raise_http_exception(message, status.HTTP_500_INTERNAL_SERVER_ERROR, code, data)