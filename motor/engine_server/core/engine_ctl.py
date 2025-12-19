#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

from abc import ABC, abstractmethod


class EngineController(ABC):

    @abstractmethod
    def control(self, cmd):
        pass

    @abstractmethod
    def stop(self):
        pass
