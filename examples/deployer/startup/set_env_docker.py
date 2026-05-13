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
import argparse
import os
from datetime import datetime
from zoneinfo import ZoneInfo


MOTOR_COMMON_ENV = "motor_common_env"
ENGINE_TYPE = "engine_type"
NORTH_PLATFORM = "north_platform"
MODEL_NAME = "model_name"
SERVICE_ID = "service_id"


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def read_json(file_path):
    """Read JSON file"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def shell_escape(value):
    if not isinstance(value, str):
        return str(value)
    
    value = value.replace('\\', '\\\\')
    value = value.replace('"', '\\"')
    value = value.replace('$', '\\$')
    value = value.replace('`', '\\`')
    value = value.replace('\n', '\\n')
    value = value.replace('\r', '\\r')
    value = value.replace('\t', '\\t')
    
    return value


def update_shell_safely(script_path, env_config, component_key="", function_name="set_common_env"):
    all_env_vars = {}
    all_env_vars.update(env_config[MOTOR_COMMON_ENV])
    if component_key and component_key in env_config:
        all_env_vars.update(env_config[component_key])

    with open(script_path, 'r') as f:
        lines = f.readlines()

    start_idx, end_idx = -1, -1
    for i, line in enumerate(lines):
        if line.strip().startswith(f"function {function_name}()"):
            start_idx = i
        elif start_idx != -1 and line.strip() == "}":
            end_idx = i
            break

    new_function_lines = [
        f"function {function_name}() {{\n",
        *[
            f'    export {key}="{shell_escape(value)}"\n' if isinstance(value, str) else f'    export {key}={value}\n'
            for key, value in all_env_vars.items()
        ],
        "}\n"
    ]

    if start_idx != -1 and end_idx != -1:
        new_lines = lines[:start_idx] + new_function_lines + lines[end_idx + 1:]
    else:
        new_lines = new_function_lines + lines

    with open(script_path, 'w') as f:
        f.writelines(new_lines)


def get_json_by_path(data, path, default=None):
    keys = path.split(".")
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
            if current is None:
                return default
        else:
            return default
    return current


def set_env_docker(configmap_path):
    user_config = read_json(os.path.join(configmap_path, "user_config.json"))
    env_config = read_json(os.path.join(configmap_path, "env.json"))
    common_shell_path = os.path.join(configmap_path, "common.sh")
    single_container_shell_path = os.path.join(configmap_path, "all_combine_in_single_container.sh")
    controller_shell_path = os.path.join(configmap_path, "controller.sh")
    coordinator_shell_path = os.path.join(configmap_path, "coordinator.sh")
    engine_shell_path = os.path.join(configmap_path, "engine.sh")
    kv_pool_shell_path = os.path.join(configmap_path, "kv_pool.sh")
    kv_conductor_shell_path = os.path.join(configmap_path, "kv_conductor.sh")

    deploy_mode = get_json_by_path(user_config, "motor_deploy_config.deploy_mode")

    engine_type = get_json_by_path(user_config, "motor_engine_prefill_config.engine_type", "Unknown")
    model_name = get_json_by_path(user_config, "motor_engine_prefill_config.model_config.model_name", "Unknown")
    north_platform = get_json_by_path(user_config, "north_config.name")

    if MOTOR_COMMON_ENV not in env_config:
        env_config[MOTOR_COMMON_ENV] = {}

    env_config[MOTOR_COMMON_ENV][ENGINE_TYPE] = engine_type
    logger.info(f"Set {ENGINE_TYPE} environment variable to: {engine_type}")

    env_config[MOTOR_COMMON_ENV][MODEL_NAME] = model_name
    logger.info(f"Set {MODEL_NAME} environment variable to: {model_name}")

    env_config[MOTOR_COMMON_ENV][NORTH_PLATFORM] = north_platform
    logger.info(f"Set {NORTH_PLATFORM} environment variable to: {north_platform}")

    service_id = (
        f"{get_json_by_path(user_config, 'motor_deploy_config.job_id')}_"
        f"{datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d%H%M%S')}"
    )
    env_config[MOTOR_COMMON_ENV][SERVICE_ID] = service_id
    logger.info(f"Set {SERVICE_ID} environment variable to: {service_id}")

    update_shell_safely(common_shell_path, env_config, MOTOR_COMMON_ENV, "set_common_env")

    if deploy_mode == "single_container":
        update_shell_safely(single_container_shell_path, env_config, "motor_controller_env", "set_controller_env")
        update_shell_safely(single_container_shell_path, env_config, "motor_coordinator_env", "set_coordinator_env")
        update_shell_safely(single_container_shell_path, env_config, "motor_engine_encode_env", "set_encode_env")
        update_shell_safely(single_container_shell_path, env_config, "motor_engine_prefill_env", "set_prefill_env")
        update_shell_safely(single_container_shell_path, env_config, "motor_engine_decode_env", "set_decode_env")
        update_shell_safely(single_container_shell_path, env_config, "motor_kv_cache_pool_env", "set_kv_pool_env")
        update_shell_safely(
            single_container_shell_path, env_config, "motor_kv_conductor_env", "set_kv_conductor_env"
        )
    else:
        update_shell_safely(controller_shell_path, env_config, "motor_controller_env", "set_controller_env")
        update_shell_safely(coordinator_shell_path, env_config, "motor_coordinator_env", "set_coordinator_env")
        update_shell_safely(engine_shell_path, env_config, "motor_engine_encode_env", "set_encode_env")
        update_shell_safely(engine_shell_path, env_config, "motor_engine_prefill_env", "set_prefill_env")
        update_shell_safely(engine_shell_path, env_config, "motor_engine_decode_env", "set_decode_env")
        update_shell_safely(kv_pool_shell_path, env_config, "motor_kv_cache_pool_env", "set_kv_pool_env")
        update_shell_safely(
            kv_conductor_shell_path, env_config, "motor_kv_conductor_env", "set_kv_conductor_env"
        )


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--configmap_path",
        "-c",
        type=str,
        help="Path of configmap"
    )
    return parser.parse_args()


def main():
    args = parse_arguments()

    set_env_docker(args.configmap_path)


if __name__ == "__main__":
    main()