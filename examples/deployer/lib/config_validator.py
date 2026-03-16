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

import lib.constant as C
from lib.utils import logger


def resolve_config_paths(config_dir, user_config_path, env_config_path):
    if not config_dir and not user_config_path and not env_config_path:
        logger.error("No configuration provided. Please use one of the following options:")
        logger.error("  --config_dir <dir>     : Directory containing user_config.json and env.json")
        logger.error("  --config <file>        : Path to user_config.json (requires --env)")
        logger.error("  --env <file>           : Path to env.json (requires --config)")
        logger.error("Example:")
        logger.error("  python deploy.py --config_dir ../infer_engines/vllm")
        logger.error(
            "  python deploy.py --config ../infer_engines/vllm/user_config.json --env ../infer_engines/vllm/env.json"
        )
        raise ValueError("Missing required configuration. Use --config_dir or both --config and --env.")

    if config_dir:
        dir_user_config = os.path.join(config_dir, "user_config.json")
        dir_env_config = os.path.join(config_dir, "env.json")

        if not user_config_path:
            if os.path.exists(dir_user_config):
                user_config_path = dir_user_config
                logger.info(f"Using user_config.json from config_dir: {user_config_path}")
            else:
                logger.error(f"user_config.json not found in {config_dir}")
                raise FileNotFoundError(f"user_config.json not found in {config_dir}")

        if not env_config_path:
            if os.path.exists(dir_env_config):
                env_config_path = dir_env_config
                logger.info(f"Using env.json from config_dir: {env_config_path}")
            else:
                logger.error(f"env.json not found in {config_dir}")
                raise FileNotFoundError(f"env.json not found in {config_dir}")

    if user_config_path and not env_config_path:
        logger.error("--config is specified but --env is missing")
        raise ValueError("Both --config and --env must be specified together, or use --config_dir")

    if env_config_path and not user_config_path:
        logger.error("--env is specified but --config is missing")
        raise ValueError("Both --config and --env must be specified together, or use --config_dir")

    logger.info(f"{C.GREEN}User config path: {user_config_path}{C.RESET}")
    logger.info(f"{C.GREEN}Env config path: {env_config_path}{C.RESET}")

    return user_config_path, env_config_path


def strip_instance_nums(config_dict):
    cleaned = json.loads(json.dumps(config_dict))
    cleaned["motor_deploy_config"].pop(C.P_INSTANCES_NUM, None)
    cleaned["motor_deploy_config"].pop(C.D_INSTANCES_NUM, None)
    return cleaned


def validate_only_instance_changed(current_config, baseline_config):
    if strip_instance_nums(current_config) != strip_instance_nums(baseline_config):
        raise ValueError("user_config changes detected beyond instance numbers. "
                         "Only p_instances_num/d_instances_num can be modified for scaling.")


def validate_deploy_mode_consistency(deploy_config, baseline_config):
    """Validate that deploy_mode hasn't changed when updating config."""
    baseline_mode = baseline_config.get(C.DEPLOY_MODE_CONFIG_KEY, C.DEPLOY_MODE_MULTI_DEPLOYMENT_YAML)
    current_mode = deploy_config.get(C.DEPLOY_MODE_CONFIG_KEY, C.DEPLOY_MODE_MULTI_DEPLOYMENT_YAML)
    if baseline_mode != current_mode:
        raise ValueError(
            f"motor_deploy_config.{C.DEPLOY_MODE_CONFIG_KEY} cannot be changed when updating config. "
            f"Current deployment uses '{baseline_mode}', user_config has '{current_mode}'."
        )


def validate_deploy_mode_value(deploy_mode_arg):
    """Validate deploy_mode value is valid."""
    if deploy_mode_arg not in C.VALID_DEPLOY_MODES:
        raise ValueError(
            f"Baseline config has invalid {C.DEPLOY_MODE_CONFIG_KEY}: {deploy_mode_arg}. "
            f"Must be one of {list(C.VALID_DEPLOY_MODES)}."
        )
