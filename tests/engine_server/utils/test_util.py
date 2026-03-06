#!/usr/bin/env python3
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

import pytest

from motor.engine_server.utils.util import func_has_parameter


class TestUtil:
    """Tests for utility functions"""

    def test_func_has_parameter_with_existing_parameter(self):
        """Test func_has_parameter with function that has the parameter"""
        # Test with simple function
        def test_func(a, b, c):
            pass
        
        assert func_has_parameter(test_func, "a") is True
        assert func_has_parameter(test_func, "b") is True
        assert func_has_parameter(test_func, "c") is True

    def test_func_has_parameter_without_parameter(self):
        """Test func_has_parameter with function that doesn't have the parameter"""
        def test_func(a, b, c):
            pass
        
        assert func_has_parameter(test_func, "d") is False
        assert func_has_parameter(test_func, "xyz") is False

    def test_func_has_parameter_with_invalid_inputs(self):
        """Test func_has_parameter with invalid inputs"""
        # Test with non-callable
        assert func_has_parameter("not_a_function", "param") is False
        
