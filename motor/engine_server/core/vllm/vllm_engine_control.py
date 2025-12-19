#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import zmq

from motor.engine_server.core.engine_ctl import EngineController
from motor.common.utils.logger import get_logger
from motor.engine_server.core.vllm.utils import get_control_socket

logger = get_logger("engine_server")


class VllmEngineController(EngineController):
    def __init__(self, dp_rank: int, recv_timeout: int = 2, send_timeout: int = 2):
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)
        self.socket.setsockopt(zmq.SNDTIMEO, send_timeout)
        self.socket.setsockopt(zmq.RCVTIMEO, recv_timeout)

        self.address = get_control_socket(dp_rank)
        self.socket.connect(self.address)

        logger.info(f"engine controller successfully connect to : {self.address}")

    def control(self, cmd: str) -> str:
        try:
            self.socket.send_string(cmd)
            res = self.socket.recv_string()
            logger.info(f"engine controller received: {res}")
            return res
        except Exception as e:
            logger.error(f"engine controller cmd [{cmd}] occur exception: {e}")
            raise e

    def stop(self):
        self.socket.close()
        self.context.term()
