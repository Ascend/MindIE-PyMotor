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
import argparse
import os
import json
import logging
import uuid
import time
import yaml as ym

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Define constants
P_INSTANCES_NUM = "p_instances_num"
D_INSTANCES_NUM = "d_instances_num"
CONFIG_JOB_ID = "job_id"
SINGER_P_INSTANCES_NUM = "single_p_instance_pod_num"
SINGER_D_INSTANCES_NUM = "single_d_instance_pod_num"
P_POD_NPU_NUM = "p_pod_npu_num"
D_POD_NPU_NUM = "d_pod_npu_num"
ASCEND_910_NPU_NUM = "huawei.com/Ascend910"
METADATA = "metadata"
CONTROLLER = "controller"
COORDINATOR = "coordinator"
NAMESPACE = "namespace"
NAME = "name"
ENV = "env"
SPEC = "spec"
TEMPLATE = "template"
REPLICAS = "replicas"
LABELS = "labels"
KIND = "kind"
APP = "app"
VALUE = "value"
RESOURCES = "resources"
SUBJECTS = "subjects"
DEPLOYMENT = "deployment"
DEPLOYMENT_KIND = "Deployment"
SERVICE_ACCOUNT = "ServiceAccount"
SERVICE = "Service"
CLUSTER_ROLE_BINDING = "ClusterRoleBinding"
HARDWARE_TYPE = 'hardware_type'
ANNOTATIONS = "annotations"
SP_BLOCK = "sp-block"
NAME_FLAG = " -n "
BOOT_SHELL_PATH = "./boot_helper/boot.sh"
KV_CACHE_POOL_CONFIG = "kv_cache_pool_config"
KV_POOL_PORT = "port"
KV_POOL_EVICTION_HIGH_WATERMARK_RATIO = "eviction_high_watermark_ratio"
KV_POOL_EVICTION_RATIO = "eviction_ratio"
DEFAULT_KV_POOL_PORT = 50088
STANDBY_CONFIG = "standby_config"
MOTOR_CONTROLLER_CONFIG = "motor_controller_config"
MOTOR_COORDINATOR_CONFIG = "motor_coordinator_config"
ENABLE_MASTER_STANDBY = "enable_master_standby"
SERVER_BASE_NAME_MAP = {
    "vllm": "vllm",
    "mindie-llm": "mindie-server",
    "sglang": "sglang"
}

# Global variables
g_controller_service = "mindie-motor-controller-service"
g_coordinator_service = "mindie-motor-coordinator-service"
g_kv_pool_service = "kvp-master"
g_kv_pool_enabled = False
g_engine_base_name = "mindie-server"
g_generate_yaml_list = []


def read_json(file_path):
    """Read JSON file"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def write_yaml(data, output_file, single_doc=True):
    """Write to YAML file"""
    logger.info(f"Writing YAML to {output_file}")
    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        if single_doc:
            ym.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False, width=float("inf"))
        else:
            ym.dump_all(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False, width=float("inf"))


def load_yaml(input_yaml, single_doc):
    """Load YAML file"""
    with open(input_yaml, 'r', encoding="utf-8") as f:
        if single_doc:
            data = ym.safe_load(f)
        else:
            data = list(ym.safe_load_all(f))
    return data


def exec_cmd(command):
    """Execute command"""
    logger.info(f"Executing command: {command}")
    os.popen(command).read()


def safe_exec_cmd(command):
    """Safely execute command"""
    try:
        exec_cmd(command)
    except Exception as e:
        logger.warning(f"Command execution failed: {e}")
        raise


def apply_configmap(create_cmd: str):
    """Create or update a configmap by applying the generated manifest."""
    safe_exec_cmd(f"{create_cmd} --dry-run=client -o yaml | kubectl apply -f -")


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


def update_shell_script_safely(script_path, env_config, component_key="", function_name="set_common_env"):
    all_env_vars = {}
    all_env_vars.update(env_config["motor_common_env"])
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
        new_lines = new_function_lines + ["\n"] + lines

    with open(script_path, 'w') as f:
        f.writelines(new_lines)


def generate_unique_id():
    timestamp = str(int(time.time() * 1000))
    random_part = str(uuid.uuid4()).split('-')[0]
    return f"{timestamp}{random_part}"


def update_kv_pool_enabled_flag(user_config):
    global g_kv_pool_enabled
    g_kv_pool_enabled = False

    kv_connector = user_config.get("motor_engine_prefill_config", {}).get("engine_config", {})\
                    .get("kv_transfer_config", {}).get("kv_connector", "")
    if kv_connector == "MultiConnector":
        g_kv_pool_enabled = True


def _extract_resources(data):
    """Extract deployment, services, and RBAC resources from YAML data"""
    deployment_data = None
    service_list = []
    rbac_resources = []

    if isinstance(data, list):
        for item in data:
            if item.get(KIND) == DEPLOYMENT_KIND:
                deployment_data = item
            elif item.get(KIND) == SERVICE:
                service_list.append(item)
            else:
                # RBAC resources: ServiceAccount, ClusterRole, ClusterRoleBinding
                rbac_resources.append(item)
    else:
        deployment_data = data

    return deployment_data, service_list, rbac_resources


def _set_rbac_namespace(rbac_resources, namespace):
    """Set namespace for RBAC resources"""
    for rbac_resource in rbac_resources:
        if rbac_resource.get(KIND) == SERVICE_ACCOUNT:
            rbac_resource[METADATA][NAMESPACE] = namespace
        elif rbac_resource.get(KIND) == CLUSTER_ROLE_BINDING:
            rbac_resource[METADATA][NAMESPACE] = namespace
            # Also update namespace in subjects for ServiceAccount references
            if SUBJECTS in rbac_resource:
                for subject in rbac_resource[SUBJECTS]:
                    if subject.get(KIND) == SERVICE_ACCOUNT:
                        subject[NAMESPACE] = namespace


def _modify_deployment(deployment_data, deploy_config, user_config):
    """Modify deployment namespace and environment variables"""
    if not deployment_data:
        return
    
    namespace = deploy_config[CONFIG_JOB_ID]
    deployment_data[METADATA][NAMESPACE] = namespace

    container = deployment_data[SPEC][TEMPLATE][SPEC]["containers"][0]
    container["image"] = deploy_config["image_name"]

    role = CONTROLLER if CONTROLLER in deployment_data[METADATA][NAME] else COORDINATOR
    if ENV not in container:
        container[ENV] = []

    container[ENV].append({
        NAME: "ROLE",
        VALUE: role
    })

    # Generate unique job name
    uuid_spec = generate_unique_id()
    job_name = f"{deploy_config[CONFIG_JOB_ID]}-{role}-{uuid_spec}"
    deployment_data[METADATA][LABELS]["job-name"] = job_name
    container[ENV].append({
        NAME: "JOB_NAME",
        VALUE: job_name
    })

    container[ENV].extend([
        {NAME: "CONTROLLER_SERVICE", VALUE: g_controller_service},
        {NAME: "COORDINATOR_SERVICE", VALUE: g_coordinator_service}
    ])

    modify_coordinator_or_controller_replicas(deployment_data, user_config, role)


def _set_services_namespace(service_list, namespace):
    """Set namespace for all services"""
    for service_data in service_list:
        service_data[METADATA][NAMESPACE] = namespace


def modify_controller_or_coordinator_yaml(data, user_config):
    """Modify controller or coordinator YAML configuration"""
    deploy_config = user_config["motor_deploy_config"]
    namespace = deploy_config[CONFIG_JOB_ID]

    # Extract resources
    deployment_data, service_list, rbac_resources = _extract_resources(data)

    # Set namespace for RBAC resources
    _set_rbac_namespace(rbac_resources, namespace)

    # Modify deployment data
    _modify_deployment(deployment_data, deploy_config, user_config)

    # Set namespace for all services
    _set_services_namespace(service_list, namespace)


def modify_coordinator_or_controller_replicas(data, user_config, role):
    #  Modify replicas based on standby_config from motor_controller_config and motor_coordinator_config
    if role == CONTROLLER:
        if MOTOR_CONTROLLER_CONFIG in user_config and \
           STANDBY_CONFIG in user_config[MOTOR_CONTROLLER_CONFIG] and \
           user_config[MOTOR_CONTROLLER_CONFIG][STANDBY_CONFIG][ENABLE_MASTER_STANDBY]:
            data[SPEC][REPLICAS] = 2
    elif role == COORDINATOR:
        if MOTOR_COORDINATOR_CONFIG in user_config and \
           STANDBY_CONFIG in user_config[MOTOR_COORDINATOR_CONFIG] and \
           user_config[MOTOR_COORDINATOR_CONFIG][STANDBY_CONFIG][ENABLE_MASTER_STANDBY]:
            data[SPEC][REPLICAS] = 2


def modify_sp_block_num(data, pd_flag, config):
    if HARDWARE_TYPE not in config or config[HARDWARE_TYPE] == "800I_A2":
        if ANNOTATIONS in data[METADATA]:
            del data[METADATA][ANNOTATIONS]
        return
    if pd_flag == "d":
        single_d_instance_pod_num = int(config[SINGER_D_INSTANCES_NUM])
        d_pod_npu_num = int(config[D_POD_NPU_NUM])
        sp_block_num = single_d_instance_pod_num * d_pod_npu_num
        data[METADATA][ANNOTATIONS][SP_BLOCK] = f"{sp_block_num}"
    elif pd_flag == "p":
        single_p_instance_pod_num = int(config[SINGER_P_INSTANCES_NUM])
        p_pod_npu_num = int(config[P_POD_NPU_NUM])
        sp_block_num = single_p_instance_pod_num * p_pod_npu_num
        data[METADATA][ANNOTATIONS][SP_BLOCK] = f"{sp_block_num}"


def update_engine_base_name(user_config):
    global g_engine_base_name
    g_engine_base_name = "mindie-server"
    engine_type = user_config.get("motor_engine_prefill_config", {}).get("engine_type", "mindie-llm")
    if engine_type in SERVER_BASE_NAME_MAP:
        g_engine_base_name = SERVER_BASE_NAME_MAP[engine_type]


def normalize_kv_cache_pool_config(user_config):
    kv_config = user_config.get(KV_CACHE_POOL_CONFIG)
    if not isinstance(kv_config, dict):
        raise ValueError(f"Missing or invalid '{KV_CACHE_POOL_CONFIG}' in user config")

    if KV_POOL_PORT not in kv_config:
        kv_config[KV_POOL_PORT] = DEFAULT_KV_POOL_PORT

    return kv_config


def modify_server_yaml(deployment_data, deploy_config, index, node_type):
    container = deployment_data[SPEC][TEMPLATE][SPEC]["containers"][0]

    deployment_data[SPEC][TEMPLATE][SPEC]["containers"][0]["image"] = deploy_config["image_name"]
    
    # Update metadata
    deployment_data[METADATA][NAMESPACE] = deploy_config[CONFIG_JOB_ID]
    
    # Modify deployment name to make it unique for each instance
    unique_name = f"{g_engine_base_name}-{node_type}{index}"
    deployment_data[METADATA][NAME] = unique_name
    deployment_data[METADATA][LABELS][APP] = unique_name
    deployment_data['spec']['selector']['matchLabels']['app'] = unique_name
    deployment_data[SPEC][TEMPLATE][METADATA][LABELS][APP] = unique_name
    container[NAME] = g_engine_base_name

    uuid_spec = generate_unique_id()
    job_name = f"{deploy_config[CONFIG_JOB_ID]}-{node_type}{index}-{uuid_spec}"
    deployment_data[METADATA][LABELS]["job-name"] = job_name
    
    # Add ROLE environment variable
    role = "prefill" if node_type == "p" else "decode"
    if ENV not in container:
        container[ENV] = []

    container[ENV].extend([
        {NAME: "ROLE", VALUE: role},
        {NAME: "JOB_NAME", VALUE: job_name},
        {NAME: "CONTROLLER_SERVICE", VALUE: g_controller_service},
        {NAME: "COORDINATOR_SERVICE", VALUE: g_coordinator_service}
    ])

    if g_kv_pool_enabled:
        container[ENV].append(
            {NAME: "KVP_MASTER_SERVICE", VALUE: g_kv_pool_service}
        )

    instance_pod_num_key = SINGER_P_INSTANCES_NUM if node_type == "p" else SINGER_D_INSTANCES_NUM
    if instance_pod_num_key in deploy_config:
        deployment_data[SPEC]["replicas"] = int(deploy_config[instance_pod_num_key])
    
    # Modify NPU num for server yaml
    if node_type == "p" and P_POD_NPU_NUM in deploy_config:
        npu_num = int(deploy_config[P_POD_NPU_NUM])
        container[RESOURCES]["requests"][ASCEND_910_NPU_NUM] = npu_num
        container[RESOURCES]["limits"][ASCEND_910_NPU_NUM] = npu_num
    elif node_type == "d" and D_POD_NPU_NUM in deploy_config:
        npu_num = int(deploy_config[D_POD_NPU_NUM])
        container[RESOURCES]["requests"][ASCEND_910_NPU_NUM] = npu_num
        container[RESOURCES]["limits"][ASCEND_910_NPU_NUM] = npu_num

    hardware_type = deploy_config[HARDWARE_TYPE]
    modify_sp_block_num(deployment_data, node_type, deploy_config)
    if hardware_type == "800I_A2":
        deployment_data[SPEC][TEMPLATE][SPEC]["nodeSelector"]["accelerator-type"] = "module-910b-8"
    elif hardware_type == "800I_A3":
        deployment_data[SPEC][TEMPLATE][SPEC]["nodeSelector"]["accelerator-type"] = "module-a3-16"

    weight_mount_path = deploy_config.get("weight_mount_path", "/mnt/weight")
    for volume in deployment_data[SPEC][TEMPLATE][SPEC]["volumes"]:
        if volume["name"] == "weight-mount":
            volume["hostPath"]["path"] = weight_mount_path
    for volume_mount in container["volumeMounts"]:
        if volume_mount["name"] == "weight-mount":
            volume_mount["mountPath"] = weight_mount_path


def obtain_server_instance_total(deploy_config):
    p_instances = int(deploy_config.get(P_INSTANCES_NUM, 1))
    d_instances = int(deploy_config.get(D_INSTANCES_NUM, 1))
    return p_instances, d_instances


def generate_yaml_controller_or_coordinator(input_yaml, output_file, deploy_config):
    logger.info(f"Generating YAML from {input_yaml} to {output_file}")
    data = load_yaml(input_yaml, False)
    modify_controller_or_coordinator_yaml(data, deploy_config)
    write_yaml(data, output_file, False)
    global g_generate_yaml_list
    g_generate_yaml_list.append(output_file)


def generate_yaml_server(input_yaml, output_file, deploy_config):
    logger.info(f"Generating YAML from {input_yaml} to {output_file}")
    global g_generate_yaml_list
    p_total, d_total = obtain_server_instance_total(deploy_config)
    for p_index in range(p_total):
        data = load_yaml(input_yaml, True)
        modify_server_yaml(data, deploy_config, p_index, "p")
        output_file_p = output_file + "_p" + str(p_index) + ".yaml"
        write_yaml(data, output_file_p, True)
        g_generate_yaml_list.append(output_file_p)
    for d_index in range(d_total):
        data = load_yaml(input_yaml, True)
        modify_server_yaml(data, deploy_config, d_index, "d")
        output_file_d = output_file + "_d" + str(d_index) + ".yaml"
        write_yaml(data, output_file_d, True)
        g_generate_yaml_list.append(output_file_d)


def generate_yaml_kv_pool(input_yaml, output_file, deploy_config, kv_pool_config):
    logger.info(f"Generating YAML from {input_yaml} to {output_file}")
    data = load_yaml(input_yaml, False)
    # Modify deployment data
    deployment_data = data[0]
    deployment_data[METADATA][NAMESPACE] = deploy_config[CONFIG_JOB_ID]

    container = deployment_data[SPEC][TEMPLATE][SPEC]["containers"][0]
    container["image"] = deploy_config["image_name"]

    if ENV not in container:
        container[ENV] = []

    service_port = kv_pool_config.get(KV_POOL_PORT)
    missing_keys = []
    if KV_POOL_EVICTION_HIGH_WATERMARK_RATIO not in kv_pool_config:
        missing_keys.append(KV_POOL_EVICTION_HIGH_WATERMARK_RATIO)
    if KV_POOL_EVICTION_RATIO not in kv_pool_config:
        missing_keys.append(KV_POOL_EVICTION_RATIO)
    if missing_keys:
        raise ValueError(
            f"Missing required kv cache pool config: {missing_keys}. "
            f"Please configure them in '{KV_CACHE_POOL_CONFIG}'."
        )

    kv_pool_env = [
        {NAME: "KVP_MASTER_SERVICE", VALUE: g_kv_pool_service},
        {NAME: "KV_POOL_PORT", VALUE: str(service_port)},
        {NAME: "KV_POOL_EVICTION_HIGH_WATERMARK_RATIO",
            VALUE: str(kv_pool_config[KV_POOL_EVICTION_HIGH_WATERMARK_RATIO])},
        {NAME: "KV_POOL_EVICTION_RATIO", VALUE: str(kv_pool_config[KV_POOL_EVICTION_RATIO])},
    ]

    container[ENV].extend(kv_pool_env)
    
    # Modify service data
    service_data = data[1]
    service_data[METADATA][NAMESPACE] = deploy_config[CONFIG_JOB_ID]
    ports = service_data.get(SPEC, {}).get("ports", [])
    if not ports:
        raise ValueError(
            "Missing required service ports in 'kv_pool_init.yaml'. "
            "Please configure spec.ports for KV pool service."
        )
    ports[0]["port"] = service_port
    ports[0]["targetPort"] = service_port

    write_yaml(data, output_file, False)
    global g_generate_yaml_list
    g_generate_yaml_list.append(output_file)


def init_service_domain_name(controller_input_yaml, coordinator_input_yaml, kv_pool_input_yaml, deploy_config):

    controller_data = load_yaml(controller_input_yaml, False)
    coordinator_data = load_yaml(coordinator_input_yaml, False)
    kv_pull_data = load_yaml(kv_pool_input_yaml, False)

    # Find Service resource from controller data
    controller_service_data = None
    for doc in controller_data:
        if doc.get(KIND) == SERVICE:
            controller_service_data = doc
            break

    # Find first Service resource from coordinator data
    coordinator_service_data = None
    for doc in coordinator_data:
        if doc.get(KIND) == SERVICE:
            coordinator_service_data = doc
            break

    # Find Service resource from kv_pool data
    kv_pull_service_data = None
    for doc in kv_pull_data:
        if doc.get(KIND) == SERVICE:
            kv_pull_service_data = doc
            break

    global g_controller_service
    controller_name = controller_service_data[METADATA][NAME]
    g_controller_service = f"{controller_name}.{deploy_config[CONFIG_JOB_ID]}.svc.cluster.local"
    global g_coordinator_service
    coordinator_name = coordinator_service_data[METADATA][NAME]
    g_coordinator_service = f"{coordinator_name}.{deploy_config[CONFIG_JOB_ID]}.svc.cluster.local"
    global g_kv_pool_service
    kv_pool_name = kv_pull_service_data[METADATA][NAME]
    g_kv_pool_service = f"{kv_pool_name}.{deploy_config[CONFIG_JOB_ID]}.svc.cluster.local"


def exec_all_kubectl_multi(deploy_config, user_config_path):
    job_id = deploy_config[CONFIG_JOB_ID]
    
    create_base_configmap(job_id, user_config_path)
    
    # Apply YAML files
    for yaml_file in g_generate_yaml_list:
        safe_exec_cmd(f"kubectl apply -f {yaml_file} -n {job_id}")


def set_env_to_shell(deploy_config):
    env_config_path = deploy_config.get("env_path", "./conf/env.json")
    if os.path.exists(env_config_path):
        env_config = read_json(env_config_path)
        update_shell_script_safely(BOOT_SHELL_PATH, env_config, "motor_common_env", "set_common_env")
        update_shell_script_safely(BOOT_SHELL_PATH, env_config, "motor_controller_env", "set_controller_env")
        update_shell_script_safely(BOOT_SHELL_PATH, env_config, "motor_coordinator_env", "set_coordinator_env")
        update_shell_script_safely(BOOT_SHELL_PATH, env_config, "motor_engine_prefill_env", "set_prefill_env")
        update_shell_script_safely(BOOT_SHELL_PATH, env_config, "motor_engine_decode_env", "set_decode_env")
        update_shell_script_safely(BOOT_SHELL_PATH, env_config, "motor_kv_cache_pool_env", "set_kv_pool_env")


def create_base_configmap(job_id, user_config_path):
    # Create base configmap with all mounted files
    apply_configmap(
        "kubectl create configmap motor-config "
        "--from-file=./boot_helper/boot.sh "
        "--from-file=./boot_helper/hccl_tools.py "
        "--from-file=./boot_helper/update_kv_cache_pool_config.py "
        "--from-file=./probe/probe.sh "
        "--from-file=./probe/probe.py "
        f"--from-file=user_config.json={user_config_path}"
        + NAME_FLAG + job_id
    )


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--user_config_path",
        type=str,
        default="./user_config.json",
        help="Path of user config"
    )
    parser.add_argument(
        "--update_config",
        action="store_true",
        help="Only refresh configmap without applying deployments"
    )
    return parser.parse_args()


def main():
    args = parse_arguments()

    deploy_yaml_root_path = "./deployment"
    output_root_path = "./output"
    user_config_path = args.user_config_path
    
    # Ensure necessary directories exist
    os.makedirs(output_root_path, exist_ok=True)
    os.makedirs(os.path.join(output_root_path, DEPLOYMENT), exist_ok=True)
    
    logger.info(f"Starting service deployment using config file path: {user_config_path}.")

    user_config = read_json(user_config_path)
    deploy_config = user_config["motor_deploy_config"]

    if args.update_config:
        create_base_configmap(deploy_config[CONFIG_JOB_ID], user_config_path)
        logger.info("configmap refresh end.")
        return

    update_kv_pool_enabled_flag(user_config)
    update_engine_base_name(user_config)
    
    set_env_to_shell(deploy_config)

    # Use new YAML template files
    controller_input_yaml = os.path.join(deploy_yaml_root_path, 'controller_init.yaml')
    controller_output_yaml = os.path.join(output_root_path, DEPLOYMENT, 'mindie_motor_controller.yaml')
    coordinator_input_yaml = os.path.join(deploy_yaml_root_path, 'coordinator_init.yaml')
    coordinator_output_yaml = os.path.join(output_root_path, DEPLOYMENT, 'mindie_motor_coordinator.yaml')
    server_input_yaml = os.path.join(deploy_yaml_root_path, 'engine_init.yaml')
    server_output_yaml = os.path.join(output_root_path, DEPLOYMENT, g_engine_base_name)
    kv_pool_input_yaml = os.path.join(deploy_yaml_root_path, 'kv_pool_init.yaml')
    kv_pool_output_yaml = os.path.join(output_root_path, DEPLOYMENT, 'mindie_ms_kv_pool.yaml')

    # Generate YAML files - pass user_config instead of user_config_path
    init_service_domain_name(controller_input_yaml, coordinator_input_yaml, kv_pool_input_yaml, deploy_config)
    generate_yaml_controller_or_coordinator(controller_input_yaml, controller_output_yaml, user_config)
    generate_yaml_controller_or_coordinator(coordinator_input_yaml, coordinator_output_yaml, user_config)
    generate_yaml_server(server_input_yaml, server_output_yaml, deploy_config)
    if g_kv_pool_enabled:
        kv_pool_config = normalize_kv_cache_pool_config(user_config)
        generate_yaml_kv_pool(kv_pool_input_yaml, kv_pool_output_yaml, deploy_config, kv_pool_config)
    exec_all_kubectl_multi(deploy_config, user_config_path)

    logger.info("all deploy end.")


if __name__ == '__main__':
    main()