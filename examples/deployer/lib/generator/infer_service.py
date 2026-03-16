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
    generate_unique_id, load_yaml, logger, write_yaml, obtain_engine_instance_total
)
from lib.generator import k8s_utils
from lib.generator.k8s_utils import (
    set_controller_service, set_coordinator_service, set_rbac_namespace,
    extract_rbac_resources, apply_sp_block_annotation
)
from lib.generator.engine import (
    build_engine_env_items, set_container_npu, apply_node_selector_by_hardware, set_weight_mount
)


def get_infer_role(infer_service_set, role_name):
    """Get role by name from InferServiceSet spec.template.roles."""
    roles = infer_service_set.get(C.SPEC, {}).get(C.TEMPLATE, {}).get(C.ROLES, [])
    for role in roles:
        if role.get(C.NAME) == role_name:
            return role
    return None


def set_container_env(container, env_list):
    """Append or update env vars in container."""
    if C.ENV not in container:
        container[C.ENV] = []
    existing_names = {e[C.NAME] for e in container[C.ENV] if isinstance(e, dict) and C.NAME in e}
    for env_item in env_list:
        name = env_item.get(C.NAME)
        if name not in existing_names:
            container[C.ENV].append(env_item)
            existing_names.add(name)


def _find_infer_service_set_doc(all_docs):
    for doc in all_docs:
        if doc and doc.get(C.KIND) == "InferServiceSet":
            return doc
    raise ValueError("InferServiceSet document not found in infer_service_template.yaml")


def _configure_control_role(infer_doc, user_config, role_name, config_key):
    deploy_config = user_config[C.MOTOR_DEPLOY_CONFIG]
    role = get_infer_role(infer_doc, role_name)
    if not role:
        return
    role[C.REPLICAS] = 1
    cfg = user_config.get(config_key, {})
    standby_cfg = cfg.get(C.STANDBY_CONFIG, {})
    replicas = 2 if standby_cfg.get(C.ENABLE_MASTER_STANDBY) else 1
    workload_spec = role.setdefault(C.SPEC, {})
    workload_spec[C.REPLICAS] = replicas
    template = workload_spec.setdefault(C.TEMPLATE, {})
    pod_spec = template.setdefault(C.SPEC, {})
    containers = pod_spec.get(C.CONTAINERS, [])
    if not containers:
        return
    container = containers[0]
    container[C.IMAGE] = deploy_config[C.IMAGE_NAME]
    job_id = deploy_config[C.CONFIG_JOB_ID]
    uuid_spec = generate_unique_id()
    job_name = f"{job_id}-{role_name}-{uuid_spec}"
    set_container_env(container, build_engine_env_items(role_name, job_name))


def _configure_controller_role(infer_doc, user_config):
    _configure_control_role(infer_doc, user_config, C.CONTROLLER, C.MOTOR_CONTROLLER_CONFIG)


def _configure_coordinator_role(infer_doc, user_config):
    _configure_control_role(infer_doc, user_config, C.COORDINATOR, C.MOTOR_COORDINATOR_CONFIG)


def _apply_infer_node_selector_and_sp_block(deploy_config, pod_spec, template, instance_key, npu_key):
    hardware_type = deploy_config.get(C.HARDWARE_TYPE, C.HARDWARE_TYPE_800I_A2)
    pod_spec[C.NODE_SELECTOR] = pod_spec.get(C.NODE_SELECTOR, {})
    apply_node_selector_by_hardware(pod_spec, hardware_type)

    if hardware_type == C.HARDWARE_TYPE_800I_A3:
        sp_block_num = int(deploy_config.get(instance_key, 1)) * int(deploy_config.get(npu_key, 1))
        apply_sp_block_annotation(template.setdefault(C.METADATA, {}), sp_block_num, hardware_type)


def _configure_engine_role(infer_doc, user_config, infer_name, role_name):
    deploy_config = user_config[C.MOTOR_DEPLOY_CONFIG]
    role = get_infer_role(infer_doc, role_name)
    if not role:
        return
    prefix_map = {C.ROLE_PREFILL: "p", C.ROLE_DECODE: "d"}
    prefix = prefix_map.get(role_name)
    if not prefix:
        return
    instances_key = f"{prefix}_instances_num"
    pods_key = f"single_{prefix}_instance_pod_num"
    npu_key = f"{prefix}_pod_npu_num"
    
    total_instances = int(deploy_config.get(instances_key, 1))
    single_instance = int(deploy_config.get(pods_key, 1))
    role[C.REPLICAS] = total_instances
    workload_spec = role.setdefault(C.SPEC, {})
    workload_spec[C.REPLICAS] = single_instance
    selector = workload_spec.setdefault(C.SELECTOR, {}).setdefault(C.MATCHLABELS, {})
    selector[C.APP] = infer_name
    template = workload_spec.setdefault(C.TEMPLATE, {})
    template.setdefault(C.METADATA, {}).setdefault(C.LABELS, {})[C.APP] = infer_name
    pod_spec = template.setdefault(C.SPEC, {})
    containers = pod_spec.get(C.CONTAINERS, [])
    if not containers:
        return
    container = containers[0]
    container[C.IMAGE] = deploy_config[C.IMAGE_NAME]
    container[C.NAME] = infer_name
    job_id = deploy_config[C.CONFIG_JOB_ID]
    job_name_base = f"{job_id}-{infer_name}"
    set_container_env(container, build_engine_env_items(role_name, job_name_base, include_kv_pool=True))
    npu_num = int(deploy_config.get(npu_key, 1))
    set_container_npu(container, npu_num)
    weight_path = deploy_config.get(C.WEIGHT_MOUNT_PATH, C.DEFAULT_WEIGHT_MOUNT_PATH)
    set_weight_mount(pod_spec, container, weight_path)
    _apply_infer_node_selector_and_sp_block(
        deploy_config, pod_spec, template, pods_key, npu_key
    )


def generate_yaml_infer_service_set(input_yaml, output_file, user_config):
    """Generate InferServiceSet yaml from template and user_config."""
    logger.info(f"Generating InferServiceSet YAML from {input_yaml} to {output_file}")
    deploy_config = user_config[C.MOTOR_DEPLOY_CONFIG]
    all_docs = load_yaml(input_yaml, False)
    if not isinstance(all_docs, list):
        all_docs = [all_docs]
    namespace = deploy_config[C.CONFIG_JOB_ID]
    infer_doc = _find_infer_service_set_doc(all_docs)
    infer_name = infer_doc.get(C.METADATA, {}).get(C.NAME, "mindie-server")
    set_rbac_namespace(extract_rbac_resources(all_docs), namespace)
    infer_doc[C.METADATA][C.NAMESPACE] = namespace
    _configure_controller_role(infer_doc, user_config)
    _configure_coordinator_role(infer_doc, user_config)
    _configure_engine_role(infer_doc, user_config, infer_name, C.ROLE_PREFILL)
    _configure_engine_role(infer_doc, user_config, infer_name, C.ROLE_DECODE)
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    write_yaml(all_docs, output_file, False)
    k8s_utils.g_generate_yaml_list.append(output_file)


def init_infer_service_domain_name(infer_service_template_yaml, deploy_config):
    """
    Set g_controller_service and g_coordinator_service for CRD InferServiceSet mode.
    CRD creates services with naming: {service_name}-{infer_service_set_name}-0-{role_name}
    """
    all_docs = load_yaml(infer_service_template_yaml, False)
    if not isinstance(all_docs, list):
        all_docs = [all_docs]
    infer_doc = _find_infer_service_set_doc(all_docs)
    infer_name = infer_doc.get(C.METADATA, {}).get(C.NAME, "mindie-server")
    namespace = deploy_config[C.CONFIG_JOB_ID]

    def get_service_fqdn_for_role(role_name):
        role = get_infer_role(infer_doc, role_name)
        if not role:
            return None
        services = role.get(C.SERVICES, [])
        if not services:
            return None
        service_name = services[0].get(C.NAME, "")
        role_name_val = role.get(C.NAME, role_name)
        full_service_name = f"{service_name}-{infer_name}-0-{role_name_val}"
        return f"{full_service_name}.{namespace}.svc.cluster.local"

    controller_service = get_service_fqdn_for_role(C.CONTROLLER)
    coordinator_service = get_service_fqdn_for_role(C.COORDINATOR)
    if not controller_service or not coordinator_service:
        raise ValueError("Controller or coordinator role not found in infer_service_template.yaml")
    set_controller_service(controller_service)
    set_coordinator_service(coordinator_service)


def update_infer_service_replicas_only(infer_service_yaml_path, deploy_config):
    """Update only prefill/decode instance count (role.replicas) in infer_service.yaml for scaling."""
    logger.info(f"Updating InferServiceSet instance replicas in {infer_service_yaml_path}")
    all_docs = load_yaml(infer_service_yaml_path, False)
    if not isinstance(all_docs, list):
        all_docs = [all_docs]
    infer_doc = _find_infer_service_set_doc(all_docs)
    p_total, d_total = obtain_engine_instance_total(deploy_config)

    prefill_role = get_infer_role(infer_doc, C.ROLE_PREFILL)
    if prefill_role:
        prefill_role[C.REPLICAS] = p_total

    decode_role = get_infer_role(infer_doc, C.ROLE_DECODE)
    if decode_role:
        decode_role[C.REPLICAS] = d_total

    os.makedirs(os.path.dirname(infer_service_yaml_path), exist_ok=True)
    write_yaml(all_docs, infer_service_yaml_path, False)
    k8s_utils.g_generate_yaml_list.append(infer_service_yaml_path)
