#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.


import os
from pathlib import Path

from motor.engine_server.constants import constants
from motor.engine_server.utils.validators import DirectoryValidator


def get_control_socket(dp_rank: int):
    return f"ipc:///tmp/pymotor/zmq/vllm_engine_ctl/dp_{dp_rank}_zmq_ipc.sock"


def clean_socket_file(ipc_path: str):
    sock_file = ipc_path.replace("ipc://", "")
    if os.path.exists(sock_file):
        os.unlink(sock_file)


def build_socket_file(ipc_path: str):
    sock_file = ipc_path.replace("ipc://", "")
    sock_path = os.path.dirname(sock_file)
    os.makedirs(sock_path, mode=constants.MOTOR_CUSTOM_ZMQ_DIR_PRIVILEGE,
                exist_ok=True)
    if Path(sock_path).is_symlink():
        raise Exception(f"symlink is not supported: {sock_path}")
    if not (DirectoryValidator(sock_path).
            check_directory_permissions(constants.MOTOR_CUSTOM_ZMQ_DIR_PRIVILEGE).check().is_valid()):
        raise Exception(f"path permission is not {oct(constants.MOTOR_CUSTOM_ZMQ_DIR_PRIVILEGE)}: {sock_file}")
    if not os.path.exists(sock_file):
        os.mknod(sock_file, mode=constants.MOTOR_CUSTOM_ZMQ_PRIVILEGE)
