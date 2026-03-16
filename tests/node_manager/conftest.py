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

from unittest.mock import patch


TEST_ENV_VARS = {
    'JOB_NAME': 'test_job',
    'CONFIG_PATH': 'tests/jsons',
    'HCCL_PATH': 'tests/jsons'
}

def setup_test_environment():
    return patch.dict('os.environ', TEST_ENV_VARS)

_env_patcher = setup_test_environment()
_env_patcher.start()

def teardown_test_environment():
    _env_patcher.stop()
