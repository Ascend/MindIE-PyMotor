# Copyright Huawei Technologies Co., Ltd. 2025. All rights reserved.
import argparse
import os
import json
import logging
import uuid
import time
import shlex
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
APP = "app"
VALUE = "value"
RESOURCES = "resources"
DEPLOYMENT = "deployment"
HARDWARE_TYPE = 'hardware_type'
ANNOTATIONS = "annotations"
SP_BLOCK = "sp-block"
NAME_FLAG = " -n "
BOOT_SHELL_PATH = "./boot_helper/boot.sh"
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


def modify_controller_or_coordinator_yaml(data, user_config):
    """Modify controller or coordinator YAML configuration"""
    deploy_config = user_config["motor_deploy_config"]

    # Modify deployment data
    deployment_data = data[0] if isinstance(data, list) else data
    deployment_data[METADATA][NAMESPACE] = deploy_config[CONFIG_JOB_ID]

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

    # Modify service data
    service_data = data[1]
    service_data[METADATA][NAMESPACE] = deploy_config[CONFIG_JOB_ID]
    if role == COORDINATOR:
        external_service_data = data[2]
        external_service_data[METADATA][NAMESPACE] = deploy_config[CONFIG_JOB_ID]

    if role == CONTROLLER:
        container[ENV].extend([
            {NAME: "COORDINATOR_SERVICE", VALUE: g_coordinator_service}
        ])
    else:
        container[ENV].extend([
            {NAME: "CONTROLLER_SERVICE", VALUE: g_controller_service}
        ])
    modify_coordinator_or_controller_replicas(data[0], user_config, role)


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


def modify_server_yaml(deployment_data, deploy_config, user_config, index, node_type):
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

    
    # Modify replicas
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


def generate_yaml_server(input_yaml, output_file, deploy_config, user_config):
    logger.info(f"Generating YAML from {input_yaml} to {output_file}")
    global g_generate_yaml_list
    p_total, d_total = obtain_server_instance_total(deploy_config)
    for p_index in range(p_total):
        data = load_yaml(input_yaml, True)
        modify_server_yaml(data, deploy_config, user_config, p_index, "p")
        output_file_p = output_file + "_p" + str(p_index) + ".yaml"
        write_yaml(data, output_file_p, True)
        g_generate_yaml_list.append(output_file_p)
    for d_index in range(d_total):
        data = load_yaml(input_yaml, True)
        modify_server_yaml(data, deploy_config, user_config, d_index, "d")
        output_file_d = output_file + "_d" + str(d_index) + ".yaml"
        write_yaml(data, output_file_d, True)
        g_generate_yaml_list.append(output_file_d)


def generate_yaml_kv_pool(input_yaml, output_file, deploy_config):
    logger.info(f"Generating YAML from {input_yaml} to {output_file}")
    data = load_yaml(input_yaml, False)
    # Modify deployment data
    deployment_data = data[0]
    deployment_data[METADATA][NAMESPACE] = deploy_config[CONFIG_JOB_ID]

    container = deployment_data[SPEC][TEMPLATE][SPEC]["containers"][0]
    container["image"] = deploy_config["image_name"]

    if ENV not in container:
        container[ENV] = []

    container[ENV].extend([
        {NAME: "KVP_MASTER_SERVICE", VALUE: g_kv_pool_service}
    ])
    
    # Modify service data
    service_data = data[1]
    service_data[METADATA][NAMESPACE] = deploy_config[CONFIG_JOB_ID]

    write_yaml(data, output_file, False)
    global g_generate_yaml_list
    g_generate_yaml_list.append(output_file)


def init_service_domain_name(controller_input_yaml, coordinator_input_yaml, kv_pool_input_yaml, deploy_config):

    controller_data = load_yaml(controller_input_yaml, False)
    coordinator_data = load_yaml(coordinator_input_yaml, False)
    kv_pull_data = load_yaml(kv_pool_input_yaml, False)
    controller_service_data = controller_data[1]
    coordinator_service_data = coordinator_data[1]
    kv_pull_service_data = kv_pull_data[1]

    global g_controller_service
    controller_name = controller_service_data[METADATA][NAME]
    g_controller_service = f"{controller_name}.{deploy_config[CONFIG_JOB_ID]}.svc.cluster.local"
    global g_coordinator_service
    coordinator_name = coordinator_service_data[METADATA][NAME]
    g_coordinator_service = f"{coordinator_name}.{deploy_config[CONFIG_JOB_ID]}.svc.cluster.local"
    global g_kv_pool_service
    kv_pool_name = kv_pull_service_data[METADATA][NAME]
    g_kv_pool_service = f"{kv_pool_name}.{deploy_config[CONFIG_JOB_ID]}.svc.cluster.local"


def exec_all_kubectl_multi(deploy_config, out_path, user_config_path):
    job_id = deploy_config[CONFIG_JOB_ID]
    out_deploy_yaml_path = os.path.join(out_path, 'deployment')
    
    # Create base configmaps
    safe_exec_cmd("kubectl create configmap boot-bash-script --from-file=./boot_helper/boot.sh"
                + NAME_FLAG + job_id)
    safe_exec_cmd("kubectl create configmap hccl-tools-script --from-file=./boot_helper/hccl_tools.py"
                + NAME_FLAG + job_id)
    safe_exec_cmd("kubectl create configmap update-config-script "
                  "--from-file=./boot_helper/update_config_from_user_config.py" + NAME_FLAG + job_id)
    safe_exec_cmd("kubectl create configmap probe-shell-script --from-file=./probe/probe.sh" + NAME_FLAG + job_id)
    safe_exec_cmd("kubectl create configmap probe-python-script --from-file=./probe/probe.py" + NAME_FLAG + job_id)
    safe_exec_cmd(f"kubectl create configmap user-config --from-file=user_config.json={user_config_path}"
                + NAME_FLAG + job_id)
    
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


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--user_config_path",
        type=str,
        default="./user_config.json",
        help="Path of user config"
    )
    parser.add_argument("--deploy_yaml_path", type=str, default='./deployment', help="Path of yaml")
    parser.add_argument("--output_path", type=str, default="./output", help="Path of output")
    return parser.parse_args()


def main():
    args = parse_arguments()

    deploy_yaml_root_path = args.deploy_yaml_path
    output_root_path = args.output_path
    user_config_path = args.user_config_path
    
    # Ensure necessary directories exist
    os.makedirs(output_root_path, exist_ok=True)
    os.makedirs(os.path.join(output_root_path, DEPLOYMENT), exist_ok=True)
    
    logger.info(f"Starting service deployment using config file path: {user_config_path}.")

    user_config = read_json(user_config_path)
    deploy_config = user_config["motor_deploy_config"]

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
    generate_yaml_server(server_input_yaml, server_output_yaml, deploy_config, user_config)
    if g_kv_pool_enabled:
        generate_yaml_kv_pool(kv_pool_input_yaml, kv_pool_output_yaml, deploy_config)
    exec_all_kubectl_multi(deploy_config, output_root_path, user_config_path)

    logger.info("all deploy end.")


if __name__ == '__main__':
    main()