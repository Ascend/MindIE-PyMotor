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

import lib.constant as C
from lib.utils import (
    generate_unique_id, load_yaml, write_yaml, safe_exec_cmd, logger,
    modify_log_mount, obtain_engine_instance_total
)
from lib.generator import k8s_utils
from lib.generator.k8s_utils import set_engine_base_name, modify_sp_block_num


def update_engine_base_name(user_config):
    engine_type = user_config.get(C.MOTOR_ENGINE_PREFILL_CONFIG, {}).get(C.ENGINE_TYPE, C.ENGINE_TYPE_MINDIE_LLM)
    if engine_type in C.SERVER_BASE_NAME_MAP:
        set_engine_base_name(C.SERVER_BASE_NAME_MAP[engine_type])
    else:
        set_engine_base_name(C.ENGINE_TYPE_MINDIE_SERVER)


def build_engine_env_items(role, job_name, include_kv_pool=False):
    env_items = [
        {C.NAME: C.ENV_ROLE, C.VALUE: role},
        {C.NAME: C.ENV_JOB_NAME, C.VALUE: job_name},
        {C.NAME: C.ENV_CONTROLLER_SERVICE, C.VALUE: k8s_utils.g_controller_service},
        {C.NAME: C.ENV_COORDINATOR_SERVICE, C.VALUE: k8s_utils.g_coordinator_service}
    ]
    if include_kv_pool and k8s_utils.g_kv_pool_enabled:
        env_items.append({C.NAME: C.ENV_KVP_MASTER_SERVICE, C.VALUE: k8s_utils.g_kv_pool_service})
    return env_items


def set_engine_metadata(deployment_data, deploy_config, index, node_type, job_name):
    deployment_data[C.METADATA][C.NAMESPACE] = deploy_config[C.CONFIG_JOB_ID]
    unique_name = f"{k8s_utils.g_engine_base_name}-{node_type}{index}"
    deployment_data[C.METADATA][C.NAME] = unique_name
    deployment_data[C.METADATA][C.LABELS][C.APP] = unique_name
    deployment_data[C.SPEC][C.SELECTOR][C.MATCHLABELS][C.APP] = unique_name
    deployment_data[C.SPEC][C.TEMPLATE][C.METADATA][C.LABELS][C.APP] = unique_name
    deployment_data[C.METADATA][C.LABELS][C.JOB_NAME] = job_name


def set_engine_env(container, node_type, job_name):
    role = C.ROLE_PREFILL if node_type == C.NODE_TYPE_P else C.ROLE_DECODE
    if C.ENV not in container:
        container[C.ENV] = []
    container[C.ENV].extend(build_engine_env_items(role, job_name, include_kv_pool=True))


def set_engine_replicas(deployment_data, deploy_config, node_type):
    instance_pod_num_key = C.SINGER_P_INSTANCES_NUM if node_type == C.NODE_TYPE_P else C.SINGER_D_INSTANCES_NUM
    if instance_pod_num_key in deploy_config:
        deployment_data[C.SPEC][C.REPLICAS] = int(deploy_config[instance_pod_num_key])


def set_container_npu(container, npu_num):
    if C.RESOURCES not in container:
        return
    container[C.RESOURCES][C.REQUESTS][C.ASCEND_910_NPU_NUM] = npu_num
    container[C.RESOURCES][C.LIMITS][C.ASCEND_910_NPU_NUM] = npu_num


def set_engine_npu(container, deploy_config, node_type):
    if node_type == C.NODE_TYPE_P and C.P_POD_NPU_NUM in deploy_config:
        npu_num = int(deploy_config[C.P_POD_NPU_NUM])
    elif node_type == C.NODE_TYPE_D and C.D_POD_NPU_NUM in deploy_config:
        npu_num = int(deploy_config[C.D_POD_NPU_NUM])
    else:
        return
    set_container_npu(container, npu_num)


def apply_node_selector_by_hardware(pod_spec, hardware_type):
    if hardware_type == C.HARDWARE_TYPE_800I_A2:
        pod_spec[C.NODE_SELECTOR][C.ACCELERATOR_TYPE] = C.ACCELERATOR_TYPE_910B
    elif hardware_type == C.HARDWARE_TYPE_800I_A3:
        pod_spec[C.NODE_SELECTOR][C.ACCELERATOR_TYPE] = C.ACCELERATOR_TYPE_A3


def set_engine_node_selector(deployment_data, deploy_config, node_type):
    modify_sp_block_num(deployment_data, node_type, deploy_config)
    hardware_type = deploy_config[C.HARDWARE_TYPE]
    pod_spec = deployment_data[C.SPEC][C.TEMPLATE][C.SPEC]
    pod_spec[C.NODE_SELECTOR] = pod_spec.get(C.NODE_SELECTOR, {})
    apply_node_selector_by_hardware(pod_spec, hardware_type)


def set_weight_mount(pod_spec, container, weight_mount_path):
    volume_found = False
    for volume in pod_spec.get(C.VOLUMES, []):
        if volume[C.NAME] == C.WEIGHT_MOUNT:
            volume[C.HOST_PATH][C.PATH] = weight_mount_path
            volume_found = True
            break
    if not volume_found:
        pod_spec.setdefault(C.VOLUMES, []).append({
            C.NAME: C.WEIGHT_MOUNT,
            C.HOST_PATH: {C.PATH: weight_mount_path}
        })
    volume_mount_found = False
    for volume_mount in container.get(C.VOLUME_MOUNTS, []):
        if volume_mount[C.NAME] == C.WEIGHT_MOUNT:
            volume_mount[C.MOUNT_PATH] = weight_mount_path
            volume_mount_found = True
            break
    if not volume_mount_found:
        container.setdefault(C.VOLUME_MOUNTS, []).append({
            C.NAME: C.WEIGHT_MOUNT,
            C.MOUNT_PATH: weight_mount_path
        })


def set_engine_weight_mount(deployment_data, container, deploy_config):
    weight_mount_path = deploy_config.get(C.WEIGHT_MOUNT_PATH, C.DEFAULT_WEIGHT_MOUNT_PATH)
    pod_spec = deployment_data[C.SPEC][C.TEMPLATE][C.SPEC]
    set_weight_mount(pod_spec, container, weight_mount_path)


def modify_engine_yaml(deployment_data, user_config, index, node_type):
    deploy_config = user_config[C.MOTOR_DEPLOY_CONFIG]
    container = deployment_data[C.SPEC][C.TEMPLATE][C.SPEC][C.CONTAINERS][0]
    container[C.IMAGE] = deploy_config[C.IMAGE_NAME]
    job_name = f"{deploy_config[C.CONFIG_JOB_ID]}-{node_type}{index}-{generate_unique_id()}"
    set_engine_metadata(deployment_data, deploy_config, index, node_type, job_name)
    container[C.NAME] = k8s_utils.g_engine_base_name
    if C.ENV not in container:
        container[C.ENV] = []
    set_engine_env(container, node_type, job_name)
    set_engine_replicas(deployment_data, deploy_config, node_type)
    set_engine_npu(container, deploy_config, node_type)
    set_engine_node_selector(deployment_data, deploy_config, node_type)
    set_engine_weight_mount(deployment_data, container, deploy_config)
    modify_log_mount(deployment_data, user_config, deployment_data[C.METADATA][C.NAME])


def validate_instance_nums(user_config):
    deploy_config = user_config[C.MOTOR_DEPLOY_CONFIG]
    p_total, d_total = obtain_engine_instance_total(deploy_config)
    if p_total <= C.INSTANCE_NUM_ZERO:
        raise ValueError(f"{C.P_INSTANCES_NUM} must be greater than {C.INSTANCE_NUM_ZERO}")
    if p_total > C.INSTANCE_NUM_MAX:
        raise ValueError(f"{C.P_INSTANCES_NUM} must not exceed {C.INSTANCE_NUM_MAX}")
    if d_total <= C.INSTANCE_NUM_ZERO:
        raise ValueError(f"{C.D_INSTANCES_NUM} must be greater than {C.INSTANCE_NUM_ZERO}")
    if d_total > C.INSTANCE_NUM_MAX:
        raise ValueError(f"{C.D_INSTANCES_NUM} must not exceed {C.INSTANCE_NUM_MAX}")


def generate_yaml_engine(input_yaml, output_file, user_config):
    logger.info(f"Generating YAML from {input_yaml} to {output_file}")
    deploy_config = user_config[C.MOTOR_DEPLOY_CONFIG]
    p_total, d_total = obtain_engine_instance_total(deploy_config)
    for p_index in range(p_total):
        data = load_yaml(input_yaml, True)
        modify_engine_yaml(data, user_config, p_index, C.NODE_TYPE_P)
        output_file_p = output_file + f"_{C.NODE_TYPE_P}{p_index}.yaml"
        write_yaml(data, output_file_p, True)
        k8s_utils.g_generate_yaml_list.append(output_file_p)
    for d_index in range(d_total):
        data = load_yaml(input_yaml, True)
        modify_engine_yaml(data, user_config, d_index, C.NODE_TYPE_D)
        output_file_d = output_file + f"_{C.NODE_TYPE_D}{d_index}.yaml"
        write_yaml(data, output_file_d, True)
        k8s_utils.g_generate_yaml_list.append(output_file_d)


def elastic_distributed_engine_deploy(deploy_config, baseline_deploy_config, out_deploy_yaml_path):
    scale_engine_by_type(deploy_config, baseline_deploy_config, out_deploy_yaml_path, C.NODE_TYPE_P)
    scale_engine_by_type(deploy_config, baseline_deploy_config, out_deploy_yaml_path, C.NODE_TYPE_D)
    logger.info("Engine scale done.")


def scale_engine_by_type(deploy_config, baseline_deploy_config, out_deploy_yaml_path, node_type):
    job_id = deploy_config[C.CONFIG_JOB_ID]
    totals = obtain_engine_instance_total(deploy_config)
    bases = obtain_engine_instance_total(baseline_deploy_config)
    total = totals[0] if node_type == C.NODE_TYPE_P else totals[1]
    base = bases[0] if node_type == C.NODE_TYPE_P else bases[1]
    if total < base:
        logger.info(f"Scale-in {node_type} instance, {base} -> {total}")
        for index in reversed(range(total, base)):
            yaml_path = os.path.join(out_deploy_yaml_path, f"{k8s_utils.g_engine_base_name}_{node_type}{index}.yaml")
            safe_exec_cmd(f"kubectl delete -f {yaml_path} -n {job_id}")
            if os.path.exists(yaml_path):
                os.remove(yaml_path)
    if total > base:
        logger.info(f"Scale-out {node_type} instance, {base} -> {total}")
        for index in range(base, total):
            yaml_path = os.path.join(out_deploy_yaml_path, f"{k8s_utils.g_engine_base_name}_{node_type}{index}.yaml")
            safe_exec_cmd(f"kubectl apply -f {yaml_path} -n {job_id}")
