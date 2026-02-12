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
import logging
import os
import sys
from typing import Any


ENCODING_UTF8 = "utf-8"
CONFIG_KEY = "kv_cache_pool_config"
MASTER_SERVER_ADDRESS = "master_server_address"
MASTER_SERVER_PORT_KEY = "port"
DEFAULT_MASTER_SERVER_PORT = "50088"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def read_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding=ENCODING_UTF8) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object at root, got: {type(data)}")
    return data


def write_json(path: str, data: dict[str, Any]) -> None:
    ensure_parent_dir(path)
    with open(path, "w", encoding=ENCODING_UTF8) as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def generate_kv_cache_pool_config(output_path: str, user_config_path: str) -> bool:
    if not os.path.exists(user_config_path):
        logging.error("user_config.json does not exist: %s", user_config_path)
        return False

    user_cfg = read_json(user_config_path)
    kv_cfg = user_cfg.get(CONFIG_KEY)

    # Copy to avoid mutating in-memory user config
    out_cfg: dict[str, Any] = dict(kv_cfg)

    kvp_master_service = os.getenv("KVP_MASTER_SERVICE", "")
    if not kvp_master_service:
        logging.error("Env KVP_MASTER_SERVICE is required but not set, cannot generate kv_cache_pool_config")
        return False
    master_server_port = kv_cfg.get(MASTER_SERVER_PORT_KEY, DEFAULT_MASTER_SERVER_PORT)
    out_cfg[MASTER_SERVER_ADDRESS] = f"{kvp_master_service}:{master_server_port}"

    write_json(output_path, out_cfg)
    logging.info("kv_cache_pool_config generated: %s", output_path)
    return True


def main() -> None:
    # Inputs: argv[0] = script name; argv[1] = output config path (e.g. kv_cache_pool_config.json);
    #         argv[2] = path to user_config.json. Hence expect len(sys.argv) == 3.
    if len(sys.argv) != 3:
        logging.info("Usage: python3 update_kv_cache_pool_config.py <output_config_path> <user_config_path>")
        sys.exit(1)

    output_path = sys.argv[1]
    user_config_path = sys.argv[2]
    success = generate_kv_cache_pool_config(output_path, user_config_path)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

