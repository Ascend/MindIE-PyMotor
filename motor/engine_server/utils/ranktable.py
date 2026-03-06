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

import json
import os
from typing import Optional

from motor.common.utils.env import Env
from motor.engine_server.utils.validators import FileValidator


def get_data_parallel_address() -> Optional[str]:
    file_path = Env.ranktable_path
    if not file_path:
        raise ValueError("Environment variable RANKTABLE_PATH is not set")
    if not FileValidator(file_path) \
            .check_not_soft_link().check_file_size().check().is_valid():
        raise ValueError(f"{file_path} is not a valid file path.")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            json_data = json.load(f)
    except FileNotFoundError as e:
        raise FileNotFoundError(f"File {file_path} not found") from e
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"File {file_path} is not valid JSON", doc="", pos=0) from e
    except Exception as e:
        raise RuntimeError(f"Error reading file: {str(e)}") from e

    server_list = json_data.get("server_list", [])
    for server in server_list:
        for device in server.get("device", []):
            if device.get("rank_id") == "0":
                return server.get("container_ip")

    # process for single container
    if len(server_list) == 1:
        return server_list[0].get("container_ip")

    raise ValueError("No device with rank_id=0 found")
