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

import os
import re
import json
import subprocess
from motor.common.utils.env import Env


def _get_hardware_type():
    """
    Get hardware_type from config file
    """
    config_path = Env.user_config_path
    if not config_path:
        # Config path not set, return empty string
        return ""
    
    try:
        # Check if config_path is already pointing to user_config.json
        if os.path.basename(config_path) == "user_config.json":
            config_file_path = config_path
        else:
            config_file_path = os.path.join(config_path, "user_config.json")
        
        with open(config_file_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return config.get("motor_deploy_config", {}).get("hardware_type", "")
    except Exception as e:
        # Failed to read config, return empty string
        logger.debug(f"Failed to read hardware_type from config: {e}")
        return ""


def get_device_info_from_rank_table():
    """
    Get device_info from RANK_TABLE_PATH file
    """
    rank_table_path = Env.ranktable_path
    if not rank_table_path:
        raise ValueError("Environment variable RANKTABLE_PATH is not set")
    
    try:
        with open(rank_table_path, 'r', encoding='utf-8') as f:
            rank_table = json.load(f)
    except Exception as e:
        raise RuntimeError(f"Error reading RANK_TABLE_PATH file: {str(e)}") from e
    
    def find_device_id(data):
        if isinstance(data, dict):
            if "device_id" in data:
                return data["device_id"]
            for value in data.values():
                result = find_device_id(value)
                if result is not None:
                    return result
        elif isinstance(data, list):
            for item in data:
                result = find_device_id(item)
                if result is not None:
                    return result
        return None
    
    device_id_str = find_device_id(rank_table)
    if device_id_str is None:
        raise ValueError("device_id field not found in RANK_TABLE_PATH file")
    
    try:
        i = int(device_id_str)
    except ValueError as e:
        raise ValueError(f"device_id field value is not a valid integer") from e
    
    # Get hardware type from config
    hardware_type = _get_hardware_type()
    
    # Calculate actual device_id and chip_id based on hardware type
    if hardware_type == "800I_A2":
        device_id = i
        chip_id = 0
    else:
        device_id = i // 2
        chip_id = i % 2
    
    return device_id, chip_id


def get_aicore_usage():
    """
    Get AICore usage rate
    """
    device_id, chip_id = get_device_info_from_rank_table()
    cmd = [
        "npu-smi",
        "info",
        "-t", "usages",
        "-i", str(device_id),
        "-c", str(chip_id)
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            start_new_session=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"npu-smi execution failed from subprocess: {str(e)}") from e

    output = result.stdout

    match = re.search(r"Aicore Usage Rate\(%\)\s*:\s*(\d+)", output)
    if not match:
        raise ValueError("Aicore Usage Rate not found")

    return int(match.group(1))