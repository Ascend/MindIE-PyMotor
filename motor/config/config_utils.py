# coding=utf-8
# Copyright (c) 2025, HUAWEI CORPORATION.  All rights reserved.

import json
from enum import Enum
from pathlib import Path
from typing import Any

MINDIE_MOTOR_CONFIG_FILENAME = "config_sample.json"
MOTOR_DEPLOY_CONFIG = "motor_deploy_config"
TLS_CONFIG = "tls_config"
MGMT_TLS_CONFIG = "mgmt_tls_config"
INFER_TLS_CONFIG = "infer_tls_config"
ETCD_TLS_CONFIG = "etcd_tls_config"
GRPC_TLS_CONFIG = "grpc_tls_config"
TLS_ENABLE = "tls_enable"
CA_FILE = "ca_file"
CERT_FILE = "cert_file"
KEY_FILE = "key_file"
ENGINE_CONFIG = "engine_config"
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
    logger: Any,
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
    mgmt_tls_config = user_config_data[MOTOR_DEPLOY_CONFIG][TLS_CONFIG][MGMT_TLS_CONFIG]
    updated_config[MGMT_TLS_CONFIG] = mgmt_tls_config

    infer_tls_config = user_config_data[MOTOR_DEPLOY_CONFIG][TLS_CONFIG][INFER_TLS_CONFIG]
    updated_config[INFER_TLS_CONFIG] = infer_tls_config

    engine_config = updated_config[ENGINE_CONFIG]

    if infer_tls_config and infer_tls_config[TLS_ENABLE]:
        engine_config[SSL_KEYFILE] = infer_tls_config[KEY_FILE]
        engine_config[SSL_CERTFILE] = infer_tls_config[CERT_FILE]
        engine_config[SSL_CA_CERTS] = infer_tls_config[CA_FILE]

    if mgmt_tls_config and mgmt_tls_config[TLS_ENABLE]:
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