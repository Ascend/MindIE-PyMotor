#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import os


class Env:
    @property
    def job_name(self):
        return os.getenv("JOB_NAME", "test_job")

    @property
    def install_path(self):
        return os.getenv("INSTALL_PATH", "motor/")

    @property
    def home_hccl_path(self):
        return os.getenv("HOME_HCCL_PATH", "motor/config")

    @property
    def ranktable_path(self):
        return os.getenv("RANKTABLE_PATH", None)

    @property
    def pod_ip(self):
        return os.getenv("POD_IP", None)

Env = Env()