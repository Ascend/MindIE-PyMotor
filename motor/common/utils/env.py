#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import os


class Env:
    @property
    def job_name(self):
        return os.getenv("JOB_NAME", None)

    @property
    def config_path(self):
        return os.getenv("CONFIG_PATH", None)

    @property
    def hccl_path(self):
        return os.getenv("HCCL_PATH", None)

    @property
    def ranktable_path(self):
        return os.getenv("RANKTABLE_PATH", None)

    @property
    def motor_engine_path(self):
        return os.getenv("MOTOR_ENGINE_PATH", None)
    
    @property
    def role(self):
        return os.getenv("ROLE", None)

    @property
    def pod_ip(self):
        return os.getenv("POD_IP", None)

    @property
    def coordinator_service(self):
        return os.getenv("COORDINATOR_SERVICE", "mindie-motor-coordinator-service.mindie-motor.svc.cluster.local")

    @property
    def controller_service(self):
        return os.getenv("CONTROLLER_SERVICE", "mindie-motor-controller-service.mindie-motor.svc.cluster.local")


Env = Env()