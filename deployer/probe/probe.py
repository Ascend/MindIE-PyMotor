#!/usr/bin/env python3
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

import json
import logging
import os
import sys
from enum import Enum

import httpx

MOTOR_DEPLOY_CONFIG = "motor_deploy_config"
TLS_CONFIG = "tls_config"
MGMT_TLS_CONFIG = "mgmt_tls_config"
ENABLE_TLS = "enable_tls"
CA_FILE = "ca_file"
CERT_FILE = "cert_file"
KEY_FILE = "key_file"


class ConfigKey(Enum):
    MOTOR_CONTROLLER = "motor_controller_config"
    MOTOR_COORDINATOR = "motor_coordinator_config"
    MOTOR_ENGINE_PREFILL = "motor_engine_prefill_config"
    MOTOR_ENGINE_DECODE = "motor_engine_decode_config"
    MOTOR_NODEMANAGER = "motor_nodemanger_config"
    MOTOR_KV_POOL = "kv_cache_pool_config"

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Hard-coded URLs for all probe types
PROBE_URLS = {
    'startup': '/startup',
    'readiness': '/readiness',
    'liveness': '/liveness'
}

# Hard-coded default ports
DEFAULT_PORTS = {
    'controller': 1026,
    'coordinator': 1026,
}

# HTTP request timeout
TIMEOUT = 600


def get_val_by_key_path(config, key_path):
    keys = key_path.split('.')
    config_element = config
    for key in keys:
        if not isinstance(config_element, dict) or key not in config_element:
            logger.info(f"Key '{key}' not found in config: {key_path}")
            return None
        config_element = config_element[key]
    return config_element


def get_builtin_default_port(role):
    """
    Get built-in default port when JSON config is not available.

    Args:
        role: 'controller' or 'coordinator'

    Returns:
        Built-in default port number, or -1 if not found
    """
    port = DEFAULT_PORTS.get(role)
    if port is not None:
        logger.info(f"Using hard-coded default port: {port}")
        return port
    else:
        logger.error(f"Unknown role: {role}")
        return -1


def _get_mgmt_tls_config(user_config):
    if not isinstance(user_config, dict):
        return None
    deploy_config = user_config.get(MOTOR_DEPLOY_CONFIG)
    if not isinstance(deploy_config, dict):
        return None
    tls_config = deploy_config.get(TLS_CONFIG)
    if not isinstance(tls_config, dict):
        return None
    mgmt_tls_config = tls_config.get(MGMT_TLS_CONFIG)
    if not isinstance(mgmt_tls_config, dict):
        return None
    return mgmt_tls_config


def get_config(role):
    config_path = os.environ.get('CONFIG_PATH')
    if not config_path:
        logger.error("CONFIG_PATH environment variable not set")
        return -1

    user_config_path = config_path
    if os.path.isdir(user_config_path):
        user_config_path = os.path.join(user_config_path, 'user_config.json')

    if not os.path.exists(user_config_path):
        logger.error(f"User config file does not exist: {user_config_path}")
        return -1

    try:
        with open(user_config_path, 'r', encoding='utf-8') as file:
            user_config = json.load(file)
    except Exception as e:
        logger.error(f"Failed to load JSON config {user_config_path}: {e}")
        return -1

    if not isinstance(user_config, dict):
        logger.error(f"Invalid config format in {user_config_path}, expected JSON object")
        return -1

    role_key = {
        "controller": ConfigKey.MOTOR_CONTROLLER.value,
        "coordinator": ConfigKey.MOTOR_COORDINATOR.value,
    }.get(role)

    role_config = user_config.get(role_key)
    if isinstance(role_config, dict):
        config = dict(role_config)
    else:
        # Fallback: treat USER_CONFIG_PATH as a raw role config
        config = dict(user_config)
        logger.warning(
            f"Role config '{role_key}' not found, using raw config from {user_config_path}"
        )

    mgmt_tls_config = _get_mgmt_tls_config(user_config)
    if isinstance(mgmt_tls_config, dict) and MGMT_TLS_CONFIG not in config:
        config[MGMT_TLS_CONFIG] = mgmt_tls_config

    return config


def send_http_request(ip, port, url_path, config):
    """
    Send HTTP request to the probe endpoint.

    Args:
        ip: IP address
        port: Port number
        url_path: URL path (e.g., '/startup')

    Returns:
        True if successful, False otherwise
    """
    url = f"http://{ip}:{port}{url_path}"
    headers = {
        'User-Agent': 'sh-probe',
        'Content-Type': 'application/json'
    }

    enable_tls = get_val_by_key_path(config, f'{MGMT_TLS_CONFIG}.{ENABLE_TLS}')
    
    try:
        if enable_tls:
            url = f"https://{ip}:{port}{url_path}"

            cert_file = get_val_by_key_path(config, f'{MGMT_TLS_CONFIG}.{CERT_FILE}')
            key_file = get_val_by_key_path(config, f'{MGMT_TLS_CONFIG}.{KEY_FILE}')
            ca_file = get_val_by_key_path(config, f'{MGMT_TLS_CONFIG}.{CA_FILE}')
            password = get_val_by_key_path(config, f'{MGMT_TLS_CONFIG}.passwd_file')

            client = httpx.Client(
                headers=headers,
                timeout=TIMEOUT,
                cert=(cert_file, key_file, password if password else None),
                verify=ca_file
            )
        else:
            client = httpx.Client(headers=headers, timeout=TIMEOUT)
        response = client.get(url)
        if response.status_code == 200:
            return True
        else:
            logger.error(f"HTTP request failed with status code: {response.status_code}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")

    return False


def main():
    """
    Main probe function.
    Usage: python probe.py <role> <probe_type>
    Where:
        role: 'controller' or 'coordinator'
        probe_type: 'startup', 'readiness', or 'liveness'
    """
    if len(sys.argv) != 3:
        logger.error("Usage: python probe.py <role> <probe_type>")
        logger.error("  role: 'controller' or 'coordinator'")
        logger.error("  probe_type: 'startup', 'readiness', or 'liveness'")
        sys.exit(1)

    role = sys.argv[1]
    probe_type = sys.argv[2]

    # Validate role
    if role not in ['controller', 'coordinator']:
        logger.error(f"Invalid role: {role}. Must be 'controller' or 'coordinator'")
        sys.exit(1)

    # Validate probe_type
    if probe_type not in PROBE_URLS:
        logger.error(f"Invalid probe type: {probe_type}. Must be one of {list(PROBE_URLS.keys())}")
        sys.exit(1)

    # Get pod IP from environment
    pod_ip = os.environ.get('POD_IP')
    if not pod_ip:
        logger.error("POD_IP environment variable not set")
        sys.exit(1)

    config = get_config(role)
    logger.info(f"config: {config}")
    if config == -1:
        logger.error("Failed to get config")
        sys.exit(1)

    port = get_val_by_key_path(config, f'api_config.{role}_api_port')
    if not isinstance(port, int) or port < 1024 or port > 65535:
        logger.warning(f"Invalid port in config: {port}, using built-in default port")
        port = get_builtin_default_port(role)
        if port == -1:
            logger.error("Failed to get port")
            sys.exit(1)

    # Get URL path
    url_path = PROBE_URLS[probe_type]

    logger.info(f"Executing {probe_type} probe for {role} at {pod_ip}:{port}{url_path}")

    # Send HTTP request
    success = send_http_request(pod_ip, port, url_path, config)

    if success:
        logger.info(f"Service is {probe_type}")
        sys.exit(0)  # success
    else:
        logger.error(f"Service is not {probe_type}")
        sys.exit(1)  # failure


if __name__ == "__main__":
    main()
