#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import inspect


def func_has_parameter(func, param_name: str) -> bool:
    try:
        sig = inspect.signature(func)
        return param_name in sig.parameters
    except (ValueError, TypeError):
        return False
