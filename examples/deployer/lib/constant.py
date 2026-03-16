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

GREEN = '\033[32m'
RESET = '\033[0m'

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
NODE_SELECTOR = "nodeSelector"
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
DATA = "data"
STARTUP_ROOT_PATH = "./startup"
COMMON_SHELL_PATH = os.path.join(STARTUP_ROOT_PATH, "common.sh")
CONTROLLER_SHELL_PATH = os.path.join(STARTUP_ROOT_PATH, "roles/controller.sh")
COORDINATOR_SHELL_PATH = os.path.join(STARTUP_ROOT_PATH, "roles/coordinator.sh")
ENGINE_SHELL_PATH = os.path.join(STARTUP_ROOT_PATH, "roles/engine.sh")
KV_POOL_SHELL_PATH = os.path.join(STARTUP_ROOT_PATH, "roles/kv_pool.sh")
SINGLE_CONTAINER_SHELL_PATH = os.path.join(STARTUP_ROOT_PATH, "roles/all_combine_in_single_container.sh")
MOTOR_COMMON_ENV = "motor_common_env"
WEIGHT_MOUNT = "weight-mount"
KV_CACHE_POOL_CONFIG = "kv_cache_pool_config"
KV_POOL_PORT = "port"
KV_POOL_EVICTION_HIGH_WATERMARK_RATIO = "eviction_high_watermark_ratio"
KV_POOL_EVICTION_RATIO = "eviction_ratio"
DEFAULT_KV_POOL_PORT = 50088
KV_CONDUCTOR_CONFIG = "kv_conductor_config"
KV_CONDUCTOR_PORT = "http_server_port"
KV_CONDUCTOR_SHELL_PATH = os.path.join(STARTUP_ROOT_PATH, "roles/kv_conductor.sh")
STANDBY_CONFIG = "standby_config"
MOTOR_CONTROLLER_CONFIG = "motor_controller_config"
MOTOR_COORDINATOR_CONFIG = "motor_coordinator_config"
MOTOR_NODEMANAGER_CONFIG = "motor_nodemanger_config"
ENABLE_MASTER_STANDBY = "enable_master_standby"
INSTANCE_NUM_ZERO = 0
INSTANCE_NUM_MAX = 16
MOTOR_CONFIG_CONFIGMAP_NAME = "motor-config"
ENGINE_TYPE_VLLM = "vllm"
ENGINE_TYPE_MINDIE_LLM = "mindie-llm"
ENGINE_TYPE_MINDIE_SERVER = "mindie-server"
ENGINE_TYPE_SGLANG = "sglang"
SERVER_BASE_NAME_MAP = {
    ENGINE_TYPE_VLLM: ENGINE_TYPE_VLLM,
    ENGINE_TYPE_MINDIE_LLM: ENGINE_TYPE_MINDIE_SERVER,
    ENGINE_TYPE_SGLANG: ENGINE_TYPE_SGLANG
}
LOG_PATH = "plog-path"
DEPLOY_YAML_ROOT_PATH = "./yaml_template"
OUTPUT_ROOT_PATH = "./output_yamls"
SELECTOR = "selector"
DEPLOY_MODE_INFER_SERVICE_SET = "infer_service_set"
DEPLOY_MODE_MULTI_DEPLOYMENT_YAML = "multi_deployment"
DEPLOY_MODE_SINGLE_CONTAINER = "single_container"
DEPLOY_MODE_CONFIG_KEY = "deploy_mode"
VALID_DEPLOY_MODES = (DEPLOY_MODE_INFER_SERVICE_SET, DEPLOY_MODE_MULTI_DEPLOYMENT_YAML, DEPLOY_MODE_SINGLE_CONTAINER)
MATCHLABELS = "matchLabels"
LOGGING_CONFIG = "logging_config"
SERVICE_ID = "service_id"
HOST_PATH = "hostPath"
ENGINE_TYPE = "engine_type"
NORTH_PLATFORM = "north_platform"
MODEL_NAME = "model_name"

HARDWARE_TYPE_800I_A2 = "800I_A2"
HARDWARE_TYPE_800I_A3 = "800I_A3"
ACCELERATOR_TYPE = "accelerator-type"
ACCELERATOR_TYPE_910B = "module-910b-8"
ACCELERATOR_TYPE_A3 = "module-a3-16"

CONTAINERS = "containers"
IMAGE = "image"
IMAGE_NAME = "image_name"
ROLE_PREFILL = "prefill"
ROLE_DECODE = "decode"
NODE_TYPE_P = "p"
NODE_TYPE_D = "d"
ROLE_SINGLE_CONTAINER = "SINGLE_CONTAINER"
REQUESTS = "requests"
LIMITS = "limits"

ENV_ROLE = "ROLE"
ENV_JOB_NAME = "JOB_NAME"
ENV_CONTROLLER_SERVICE = "CONTROLLER_SERVICE"
ENV_COORDINATOR_SERVICE = "COORDINATOR_SERVICE"
ENV_KVP_MASTER_SERVICE = "KVP_MASTER_SERVICE"
ENV_KV_POOL_PORT = "KV_POOL_PORT"
ENV_KV_POOL_EVICTION_HIGH_WATERMARK_RATIO = "KV_POOL_EVICTION_HIGH_WATERMARK_RATIO"
ENV_KV_POOL_EVICTION_RATIO = "KV_POOL_EVICTION_RATIO"

VOLUMES = "volumes"
VOLUME_MOUNTS = "volumeMounts"
PATH = "path"
WEIGHT_MOUNT_PATH = "weight_mount_path"

MOTOR_DEPLOY_CONFIG = "motor_deploy_config"
MOTOR_ENGINE_PREFILL_CONFIG = "motor_engine_prefill_config"
ENGINE_CONFIG = "engine_config"
KV_TRANSFER_CONFIG = "kv_transfer_config"
KV_CONNECTOR = "kv_connector"
MULTI_CONNECTOR = "MultiConnector"

PORTS = "ports"
PORT = "port"
TARGET_PORT = "targetPort"
MOUNT_PATH = "mountPath"
DEFAULT_WEIGHT_MOUNT_PATH = "/mnt/weight"
JOB_NAME = "job-name"
ROLES = "roles"
SERVICES = "services"
KIND_KEY = "kind"
