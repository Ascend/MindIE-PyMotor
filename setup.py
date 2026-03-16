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

from setuptools import setup, find_packages


setup(
    name="motor",
    version="0.1.0",
    description="A Python package named motor.",
    packages=find_packages(),
    python_requires=">=3.11",
    install_requires=[

    ],
    package_data={
        "motor": ["version.info"],
    },
    include_package_data=True,
    zip_safe=False,
    entry_points={
        "console_scripts": [
            "engine_server = motor.engine_server.cli.main:main",
        ]
    }
)
