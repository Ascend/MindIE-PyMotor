# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
import argparse
import os

import lib.constant as C
from lib.utils import logger, read_json, set_env_to_shell, get_deploy_paths
from lib.generator import k8s_utils
from lib.generator.k8s_utils import (
    get_baseline_config_from_configmap, exec_all_kubectl_multi, exec_all_kubectl_singer,
    create_motor_config_configmap, init_service_domain_name, get_deploy_mode_from_config,
    update_kv_pool_enabled_flag, update_kv_conductor_enabled_flag, set_user_config_path
)
from lib.generator.controller import generate_yaml_controller
from lib.generator.coordinator import generate_yaml_coordinator
from lib.generator.engine import generate_yaml_engine, update_engine_base_name, validate_instance_nums
from lib.generator.kv_pool import generate_yaml_kv_pool, normalize_kv_cache_pool_config
from lib.generator.kv_conductor import generate_yaml_kv_conductor, normalize_kv_conductor_config
from lib.generator.single_container import generate_yaml_single_container
from lib.generator.infer_service import (
    generate_yaml_infer_service_set, init_infer_service_domain_name, update_infer_service_replicas_only
)
from lib.config_validator import (
    validate_deploy_mode_consistency, validate_deploy_mode_value,
    validate_only_instance_changed, resolve_config_paths
)


def handle_update_config(deploy_config):
    baseline_config = get_baseline_config_from_configmap(deploy_config[C.CONFIG_JOB_ID])
    if baseline_config is None:
        raise FileNotFoundError("ConfigMap motor-config not found or has no user_config in cluster. "
                                "Please deploy once before updating configmap.")
    baseline_deploy = baseline_config["motor_deploy_config"]
    if (deploy_config.get(C.P_INSTANCES_NUM) != baseline_deploy.get(C.P_INSTANCES_NUM)
            or deploy_config.get(C.D_INSTANCES_NUM) != baseline_deploy.get(C.D_INSTANCES_NUM)):
        raise ValueError(
            "P/D instance count in user_config differs from the deployed baseline. "
            "Use --update_instance_num to scale instances instead of --update_config."
        )

    validate_deploy_mode_consistency(deploy_config, baseline_deploy)

    create_motor_config_configmap(deploy_config[C.CONFIG_JOB_ID])
    logger.info("Configmap refreshed.")


def handle_update_instance_num(user_config):
    deploy_config = user_config[C.MOTOR_DEPLOY_CONFIG]
    baseline_config = get_baseline_config_from_configmap(deploy_config[C.CONFIG_JOB_ID])
    if baseline_config is None:
        raise FileNotFoundError("ConfigMap motor-config not found. "
                                "Please deploy once before scaling.")
    validate_only_instance_changed(user_config, baseline_config)

    baseline_deploy = baseline_config.get(C.MOTOR_DEPLOY_CONFIG, {})
    deploy_mode_arg = baseline_deploy.get(C.DEPLOY_MODE_CONFIG_KEY, C.DEPLOY_MODE_MULTI_DEPLOYMENT_YAML)
    validate_deploy_mode_value(deploy_mode_arg)

    update_kv_pool_enabled_flag(user_config)
    update_engine_base_name(user_config)

    k8s_utils.g_generate_yaml_list = []
    paths = get_deploy_paths()

    if deploy_mode_arg == C.DEPLOY_MODE_INFER_SERVICE_SET:
        infer_input = paths["infer_service_input_yaml"]
        infer_output = paths["infer_service_output_yaml"]
        if os.path.exists(infer_output):
            update_infer_service_replicas_only(infer_output, deploy_config)
        else:
            init_service_domain_name(
                paths["controller_input_yaml"],
                paths["coordinator_input_yaml"],
                paths["kv_pool_input_yaml"],
                paths["kv_conductor_input_yaml"],
                deploy_config
            )
            if not os.path.exists(infer_input):
                raise FileNotFoundError(f"InferServiceSet template yaml not found: {infer_input}.")
            init_infer_service_domain_name(infer_input, deploy_config)
            generate_yaml_infer_service_set(infer_input, infer_output, user_config)
    else:
        generate_yaml_engine(paths["engine_input_yaml"], paths["engine_output_yaml"], user_config)

    exec_all_kubectl_multi(deploy_config, baseline_config, deploy_mode_arg)
    logger.info("instance num update end.")


def deploy_services_multi_yaml(paths, user_config):
    deploy_config = user_config[C.MOTOR_DEPLOY_CONFIG]
    init_service_domain_name(
        paths["controller_input_yaml"], paths["coordinator_input_yaml"],
        paths["kv_pool_input_yaml"], paths["kv_conductor_input_yaml"], deploy_config
    )
    generate_yaml_controller(
        paths["controller_input_yaml"], paths["controller_output_yaml"], user_config
    )
    generate_yaml_coordinator(
        paths["coordinator_input_yaml"], paths["coordinator_output_yaml"], user_config
    )
    generate_yaml_engine(paths["engine_input_yaml"], paths["engine_output_yaml"], user_config)
    if k8s_utils.g_kv_pool_enabled:
        kv_pool_config = normalize_kv_cache_pool_config(user_config)
        generate_yaml_kv_pool(
            paths["kv_pool_input_yaml"], paths["kv_pool_output_yaml"], user_config, kv_pool_config
        )
    if k8s_utils.g_kv_conductor_enabled:
        kv_conductor_config = normalize_kv_conductor_config(user_config)
        generate_yaml_kv_conductor(
            paths["kv_conductor_input_yaml"], paths["kv_conductor_output_yaml"],
            user_config, kv_conductor_config
        )
    exec_all_kubectl_multi(deploy_config, None, C.DEPLOY_MODE_MULTI_DEPLOYMENT_YAML)


def deploy_services_infer_service_set(paths, user_config):
    deploy_config = user_config[C.MOTOR_DEPLOY_CONFIG]
    init_service_domain_name(
        paths["controller_input_yaml"], paths["coordinator_input_yaml"],
        paths["kv_pool_input_yaml"], paths["kv_conductor_input_yaml"], deploy_config
    )
    infer_input = paths["infer_service_input_yaml"]
    if not os.path.exists(infer_input):
        raise FileNotFoundError(
            f"InferServiceSet template yaml not found: {infer_input}. "
            "Please ensure infer_service_template.yaml exists in yaml_template folder."
        )
    init_infer_service_domain_name(infer_input, deploy_config)
    generate_yaml_infer_service_set(
        infer_input, paths["infer_service_output_yaml"], user_config
    )
    exec_all_kubectl_multi(deploy_config, None, C.DEPLOY_MODE_INFER_SERVICE_SET)


def deploy_services_single_container(paths, user_config):
    deploy_config = user_config[C.MOTOR_DEPLOY_CONFIG]
    update_kv_pool_enabled_flag(user_config)
    generate_yaml_single_container(
        paths["single_container_input_yaml"], paths["single_container_output_yaml"], user_config
    )
    exec_all_kubectl_singer(deploy_config, paths["single_container_output_yaml"])


def deploy_services(user_config, env_config_path):
    deploy_config = user_config[C.MOTOR_DEPLOY_CONFIG]
    update_kv_pool_enabled_flag(user_config)
    update_kv_conductor_enabled_flag(user_config)
    update_engine_base_name(user_config)

    deploy_mode_arg = get_deploy_mode_from_config(deploy_config)
    set_env_to_shell(user_config, env_config_path, deploy_mode_arg)

    k8s_utils.g_generate_yaml_list = []
    paths = get_deploy_paths()

    if deploy_mode_arg == C.DEPLOY_MODE_SINGLE_CONTAINER:
        deploy_services_single_container(paths, user_config)
    elif deploy_mode_arg == C.DEPLOY_MODE_INFER_SERVICE_SET:
        deploy_services_infer_service_set(paths, user_config)
    else:
        deploy_services_multi_yaml(paths, user_config)

    logger.info("all deploy end.")


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config_dir",
        "--dir",
        type=str,
        help="Directory containing user_config.json and env.json, "
        "select from examples/infer_engines/ based on your engine and model requirements"
    )
    parser.add_argument(
        "--user_config_path",
        "--config",
        type=str,
        help="Path of user config, takes precedence over config_dir if specified"
    )
    parser.add_argument(
        "--env_config_path",
        "--env",
        type=str,
        help="Path of env config, takes precedence over config_dir if specified"
    )
    parser.add_argument(
        "--update_config",
        action="store_true",
        help="Only refresh configmap without applying deployments"
    )
    parser.add_argument(
        "--update_instance_num",
        action="store_true",
        help="Scale instances by comparing ConfigMap baseline with current user_config"
    )
    return parser.parse_args()


def main():
    args = parse_arguments()

    user_config_path, env_config_path = resolve_config_paths(
        args.config_dir, args.user_config_path, args.env_config_path
    )

    set_user_config_path(user_config_path)
    os.makedirs(C.OUTPUT_ROOT_PATH, exist_ok=True)
    user_config = read_json(user_config_path)
    validate_instance_nums(user_config)

    if args.update_config:
        deploy_config = user_config[C.MOTOR_DEPLOY_CONFIG]
        handle_update_config(deploy_config)
        return
    if args.update_instance_num:
        handle_update_instance_num(user_config)
        return

    deploy_services(user_config, env_config_path)


if __name__ == "__main__":
    main()
