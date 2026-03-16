# coding=utf-8
# Copyright (c) 2025, HUAWEI CORPORATION.  All rights reserved.

import json
from enum import Enum
from pathlib import Path
from typing import Any

from motor.common.utils.logger import get_logger

logger = get_logger(__name__)

MINDIE_MOTOR_CONFIG_FILENAME = "config_sample.json"
MOTOR_DEPLOY_CONFIG = "motor_deploy_config"
MOTOR_ENGINE_PREFILL_CONFIG = "motor_engine_prefill_config"
TLS_CONFIG = "tls_config"
MGMT_TLS_CONFIG = "mgmt_tls_config"
INFER_TLS_CONFIG = "infer_tls_config"
ETCD_TLS_CONFIG = "etcd_tls_config"
GRPC_TLS_CONFIG = "grpc_tls_config"
OBSERVABILITY_TLS_CONFIG = "observability_tls_config"
ENABLE_TLS = "enable_tls"
CA_FILE = "ca_file"
CERT_FILE = "cert_file"
KEY_FILE = "key_file"
PASSWD_FILE = "passwd_file"
CRL_FILE = "crl_file"
ENGINE_CONFIG = "engine_config"
ENGINE_TYPE = "engine_type"
BLOCK_SIZE = "block_size"
KV_EVENTS_CONFIG = "kv-events-config"
PREFILL_KV_EVENT_CONFIG = "prefill_kv_event_config"
ENDPOINT = "endpoint"
REPLAY_ENDPOINT = "replay_endpoint"
KV_CONDUCTOR_CONFIG = "kv_conductor_config"
HTTP_SERVER_PORT = "http_server_port"
MODEL_CONFIG = "model_config"
MODEL_PATH = "model_path"
SSL_ENABLE = "ssl_enable"
SSL_CA_CERTS = "ssl_ca_certs"
SSL_CERTFILE = "ssl_certfile"
SSL_KEYFILE = "ssl_keyfile"
ADDITIONAL_CONFIG = "additional_config"
KV_TRANSFER_CONFIG = "kv_transfer_config"
KV_CONNECTOR_EXTRA_CONFIG = "kv_connector_extra_config"
DEPLOY_CONFIG = "deploy_config"
P_INSTANCES_NUM = "p_instances_num"
D_INSTANCES_NUM = "d_instances_num"


class ConfigKey(Enum):
    MOTOR_CONTROLLER = "motor_controller_config"
    MOTOR_COORDINATOR = "motor_coordinator_config"
    MOTOR_ENGINE_PREFILL = "motor_engine_prefill_config"
    MOTOR_ENGINE_DECODE = "motor_engine_decode_config"
    MOTOR_NODEMANAGER = "motor_nodemanger_config"
    MOTOR_KV_POOL = "kv_cache_pool_config"

    @staticmethod
    def is_valid(config_key: str) -> bool:
        return config_key in [key.value for key in ConfigKey]

    @staticmethod
    def get_supported_keys() -> str:
        return ", ".join([key.value for key in ConfigKey])


def save_config_to_json(
    save_path: str,
    config_key: ConfigKey,
    config_dict: dict[str, Any],
    *,
    file_encoding: str,
    component_name: str,
) -> None:
    """Save config dict to JSON, merging into unified config when needed."""
    save_path_obj = Path(save_path)
    if save_path_obj.name == MINDIE_MOTOR_CONFIG_FILENAME:
        unified_config: dict[str, Any] = {}
        if save_path_obj.exists():
            try:
                with open(save_path_obj, "r", encoding=file_encoding) as f:
                    existing = json.load(f)
                    if isinstance(existing, dict):
                        unified_config = existing
            except Exception as e:
                logger.warning(
                    "Failed to read existing unified config: %s, overwrite with %s config",
                    e,
                    component_name,
                )
        unified_config[config_key.value] = config_dict
        with open(save_path_obj, "w", encoding=file_encoding) as f:
            json.dump(unified_config, f, indent=2, ensure_ascii=False)
    else:
        with open(save_path, "w", encoding=file_encoding) as f:
            json.dump(config_dict, f, indent=2, ensure_ascii=False)


def _get_tls_config(user_config_data: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(user_config_data, dict):
        return None
    deploy_config = user_config_data.get(MOTOR_DEPLOY_CONFIG)
    if not isinstance(deploy_config, dict):
        return None
    tls_config = deploy_config.get(TLS_CONFIG)
    if not isinstance(tls_config, dict):
        return None
    return tls_config


def _update_tls_config(
    tls_configs: list[str],
    updated_config: dict[str, Any],
    user_config_data: dict[str, Any],
) -> None:
    tls_config = _get_tls_config(user_config_data)
    if not tls_config:
        return
    for tls_key in tls_configs:
        if tls_key in tls_config:
            updated_config[tls_key] = tls_config[tls_key]


def _update_engine_server_tls_config(
    updated_config: dict[str, Any],
    user_config_data: dict[str, Any],
) -> None:
    if TLS_CONFIG not in user_config_data[MOTOR_DEPLOY_CONFIG]:
        return

    mgmt_tls_config = user_config_data[MOTOR_DEPLOY_CONFIG][TLS_CONFIG].get(MGMT_TLS_CONFIG)
    updated_config[MGMT_TLS_CONFIG] = mgmt_tls_config

    infer_tls_config = user_config_data[MOTOR_DEPLOY_CONFIG][TLS_CONFIG].get(INFER_TLS_CONFIG)
    updated_config[INFER_TLS_CONFIG] = infer_tls_config

    engine_config = updated_config[ENGINE_CONFIG]

    if infer_tls_config and infer_tls_config[ENABLE_TLS]:
        engine_config[SSL_KEYFILE] = infer_tls_config[KEY_FILE]
        engine_config[SSL_CERTFILE] = infer_tls_config[CERT_FILE]
        engine_config[SSL_CA_CERTS] = infer_tls_config[CA_FILE]

    if mgmt_tls_config and mgmt_tls_config[ENABLE_TLS]:
        if KV_TRANSFER_CONFIG not in engine_config:
            engine_config[KV_TRANSFER_CONFIG] = {}
        kv_transfer_config = engine_config[KV_TRANSFER_CONFIG]
        if KV_CONNECTOR_EXTRA_CONFIG not in kv_transfer_config:
            kv_transfer_config[KV_CONNECTOR_EXTRA_CONFIG] = {}
        kv_connector_config = kv_transfer_config[KV_CONNECTOR_EXTRA_CONFIG]
        if TLS_CONFIG not in kv_connector_config:
            kv_connector_config[TLS_CONFIG] = {}
        tls_config = kv_connector_config[TLS_CONFIG]
        tls_config[SSL_ENABLE] = True
        tls_config[SSL_KEYFILE] = infer_tls_config[KEY_FILE]
        tls_config[SSL_CERTFILE] = infer_tls_config[CERT_FILE]
        tls_config[SSL_CA_CERTS] = infer_tls_config[CA_FILE]


def _update_instances_num(
    updated_config: dict[str, Any],
    user_config_data: dict[str, Any],
) -> None:
    if not isinstance(user_config_data, dict):
        return
    deploy_config = user_config_data.get(MOTOR_DEPLOY_CONFIG)
    if not isinstance(deploy_config, dict):
        return

    updated_config[DEPLOY_CONFIG] = {
        P_INSTANCES_NUM: deploy_config.get(P_INSTANCES_NUM, 1),
        D_INSTANCES_NUM: deploy_config.get(D_INSTANCES_NUM, 1)
    }


def _update_prefill_kv_event_config(
    updated_config: dict[str, Any],
    user_config_data: dict[str, Any]
) -> None:
    try:
        prefill_engine_config = user_config_data[MOTOR_ENGINE_PREFILL_CONFIG][ENGINE_CONFIG]
        if not isinstance(prefill_engine_config, dict):
            logger.warning("prefill_engine_config is not dict")
            return

        kv_events_config = prefill_engine_config.get(KV_EVENTS_CONFIG, None)
        if not isinstance(kv_events_config, dict):
            logger.warning("kv_events_config is None")
            return

        updated_config[PREFILL_KV_EVENT_CONFIG] = {
            ENDPOINT: kv_events_config.get(ENDPOINT, ""),
            REPLAY_ENDPOINT: kv_events_config.get(REPLAY_ENDPOINT, ""),
            BLOCK_SIZE: prefill_engine_config.get("block-size", 128),
            HTTP_SERVER_PORT: user_config_data[KV_CONDUCTOR_CONFIG][HTTP_SERVER_PORT],
            MODEL_PATH: user_config_data[MOTOR_ENGINE_PREFILL_CONFIG][MODEL_CONFIG][MODEL_PATH]
        }
    except Exception as e:
        logger.warning("Failed to get prefill config: %s", e)


def _extract_tls_configs(config_dict: dict) -> dict:
    """Extract TLS configs from config dict and remove them from original dict"""
    tls_configs = {}
    
    # Extract TLS configs
    tls_keys = ["mgmt_tls_config", "infer_tls_config", "etcd_tls_config", "grpc_tls_config", "observability_tls_config"]
    for key in tls_keys:
        if key in config_dict:
            tls_configs[key] = config_dict[key]
            # Remove from original config
            del config_dict[key]
    
    return tls_configs


def generate_user_config_sample(output_path: str):
    """Generate user_config_sample.json from default configurations"""

    from motor.config.controller import ControllerConfig
    from motor.config.coordinator import CoordinatorConfig
    from motor.config.node_manager import NodeManagerConfig
    
    # Create default config instances
    # Note: NodeManagerConfig requires proper initialization, but we can create it with defaults
    # The __post_init__ will set default paths if not provided
    controller_config = ControllerConfig()
    coordinator_config = CoordinatorConfig()
    # NodeManagerConfig can be created without arguments, __post_init__ will handle defaults
    node_manager_config = NodeManagerConfig()
    
    # Convert to dict
    controller_dict = controller_config.to_dict()
    coordinator_dict = coordinator_config.to_dict()
    node_manager_dict = node_manager_config.to_dict()
    
    # Extract TLS configs from all configs
    all_tls_configs = {}
    
    # From controller: mgmt_tls_config, etcd_tls_config, grpc_tls_config, observability_tls_config
    controller_tls = _extract_tls_configs(controller_dict)
    all_tls_configs.update(controller_tls)
    
    # From coordinator: mgmt_tls_config, infer_tls_config, etcd_tls_config
    coordinator_tls = _extract_tls_configs(coordinator_dict)
    # Merge coordinator TLS configs (may override controller's mgmt_tls_config and etcd_tls_config)
    for key, value in coordinator_tls.items():
        all_tls_configs[key] = value
    
    # From node_manager: mgmt_tls_config
    node_manager_tls = _extract_tls_configs(node_manager_dict)
    # Merge node_manager TLS configs (may override others' mgmt_tls_config)
    for key, value in node_manager_tls.items():
        all_tls_configs[key] = value
    
    # Create motor_deploy_config with TLS configs
    # Use default values from user_config.json as reference
    motor_deploy_config = {
        "p_instances_num": coordinator_dict.get(DEPLOY_CONFIG, {}).get("p_instances_num", 1),
        "d_instances_num": coordinator_dict.get(DEPLOY_CONFIG, {}).get("d_instances_num", 1),
        "single_p_instance_pod_num": 1,
        "single_d_instance_pod_num": 1,
        "p_pod_npu_num": 16,
        "d_pod_npu_num": 16,
        "image_name": "",
        "job_id": "mindie-motor",
        "hardware_type": "800I_A3",
        "weight_mount_path": "/mnt/weight/",
        "tls_config": {
            "infer_tls_config": all_tls_configs.get("infer_tls_config", {
                ENABLE_TLS: False,
                CA_FILE: "",
                CERT_FILE: "",
                KEY_FILE: "",
                PASSWD_FILE: "",
                CRL_FILE: ""
            }),
            "mgmt_tls_config": all_tls_configs.get("mgmt_tls_config", {
                ENABLE_TLS: False,
                CA_FILE: "",
                CERT_FILE: "",
                KEY_FILE: "",
                PASSWD_FILE: "",
                CRL_FILE: ""
            }),
            "etcd_tls_config": all_tls_configs.get("etcd_tls_config", {
                ENABLE_TLS: False,
                CA_FILE: "",
                CERT_FILE: "",
                KEY_FILE: "",
                PASSWD_FILE: "",
                CRL_FILE: ""
            }),
            "grpc_tls_config": all_tls_configs.get("grpc_tls_config", {
                ENABLE_TLS: False,
                CA_FILE: "",
                CERT_FILE: "",
                KEY_FILE: "",
                PASSWD_FILE: "",
                CRL_FILE: ""
            }),
            "observability_tls_config": all_tls_configs.get("observability_tls_config", {
                ENABLE_TLS: False,
                CA_FILE: "",
                CERT_FILE: "",
                KEY_FILE: "",
                PASSWD_FILE: "",
                CRL_FILE: ""
            }),
        }
    }
    
    # Remove deploy_config from coordinator_dict if it exists (it's now in motor_deploy_config)
    if DEPLOY_CONFIG in coordinator_dict:
        del coordinator_dict[DEPLOY_CONFIG]
    
    # Build final user config
    user_config = {
        "version": "v2.0",
        "motor_deploy_config": motor_deploy_config,
        "motor_controller_config": controller_dict,
        "motor_coordinator_config": coordinator_dict,
        "motor_nodemanger_config": node_manager_dict,
    }
    
    # Write to file
    output_path_obj = Path(output_path)
    output_path_obj.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path_obj, "w", encoding="utf-8") as f:
        json.dump(user_config, f, indent=2, ensure_ascii=False)
    
    logger.info("Successfully generated user_config_sample.json at: %s", output_path)


if __name__ == "__main__":
    # Default output path
    project_root = Path(__file__).parent.parent.parent
    output_path = project_root / "examples" / "user_config_sample.json"

    generate_user_config_sample(output_path)