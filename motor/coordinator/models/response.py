# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

"""Response models for API layer."""

from typing import Any

from pydantic import BaseModel


class RequestResponse(BaseModel):
    """API response model for management endpoints."""

    request_id: str
    status: str
    message: str | None = None
    data: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """API error response model."""

    message: str
    type: str
    code: int
