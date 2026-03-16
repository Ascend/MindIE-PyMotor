# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

"""Coordinator data models: request, response, constants."""

__all__ = [
    # Request
    "RequestType",
    "ReqState",
    "RequestInfo",
    # Response
    "RequestResponse",
    "ErrorResponse",
    # Constants
    "CHAT_COMPLETION_PREFIX",
    "COMPLETION_PREFIX",
    "COMPLETION_SUFFIX",
    "DEFAULT_REQUEST_ID",
    "REQUEST_ID_KEY",
    "REQUEST_DATA_KEY",
    "RESOURCE_KEY",
    "OpenAIField",
]

from motor.coordinator.models.constants import (
    CHAT_COMPLETION_PREFIX,
    COMPLETION_PREFIX,
    COMPLETION_SUFFIX,
    DEFAULT_REQUEST_ID,
    OpenAIField,
    REQUEST_DATA_KEY,
    REQUEST_ID_KEY,
    RESOURCE_KEY,
)
from motor.coordinator.models.request import (
    RequestInfo,
    RequestType,
    ReqState,
)
from motor.coordinator.models.response import ErrorResponse, RequestResponse
