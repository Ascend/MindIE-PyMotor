# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from collections.abc import Mapping, Sequence

from lib.utils import logger

UPDATE_CONFIG_WHITELIST = {
    "motor_deploy_config": {
        "tls_config": {
            "north_tls_config": [
                "enable_tls", 
                "ca_file", 
                "cert_file",
                "key_file",
                "passwd_file",
                "crl_file",
            ],
        },
    },
    "north_config": [
        "name",
        "ip",
        "port"
    ],
    "motor_controller_config": {
        "logging_config": [
            "log_level",
        ],
        "observability_config": [
            "observability_enable",
            "metrics_ttl",
        ],
    },
    "motor_coordinator_config": {
        "logging_config": [
            "log_level",
        ],
        "exception_config": [
            "max_retry",
            "retry_delay",
            "first_token_timeout",
            "infer_timeout",
        ],
        "timeout_config": [
            "request_timeout",
            "connection_timeout",
            "read_timeout",
            "write_timeout",
            "keep_alive_timeout",
        ],
    },
    "motor_nodemanger_config": {
        "logging_config": [
            "log_level",
        ],
    },
}


def _normalize_path_token(path_token):
    return path_token.split("[", 1)[0]


def _is_non_string_sequence(value):
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _path_is_whitelisted(path):
    whitelist_node = UPDATE_CONFIG_WHITELIST
    path_tokens = path.split(".")
    for index, path_token in enumerate(path_tokens):
        normalized_token = _normalize_path_token(path_token)
        if isinstance(whitelist_node, Mapping):
            if normalized_token not in whitelist_node:
                return False
            whitelist_node = whitelist_node[normalized_token]
            continue

        return index == len(path_tokens) - 1 and normalized_token in whitelist_node

    return False


def _collect_changed_paths(current_value, baseline_value, path=""):
    if isinstance(current_value, Mapping) and isinstance(baseline_value, Mapping):
        changed_paths = []
        all_keys = sorted(set(current_value) | set(baseline_value))
        for key in all_keys:
            next_path = f"{path}.{key}" if path else str(key)
            changed_paths.extend(
                _collect_changed_paths(
                    current_value.get(key),
                    baseline_value.get(key),
                    next_path,
                )
            )
        return changed_paths

    if baseline_value is None and isinstance(current_value, Mapping):
        changed_paths = []
        for key in sorted(current_value):
            next_path = f"{path}.{key}" if path else str(key)
            changed_paths.extend(_collect_changed_paths(current_value[key], None, next_path))
        return changed_paths

    if current_value is None and isinstance(baseline_value, Mapping):
        changed_paths = []
        for key in sorted(baseline_value):
            next_path = f"{path}.{key}" if path else str(key)
            changed_paths.extend(_collect_changed_paths(None, baseline_value[key], next_path))
        return changed_paths

    if baseline_value is None and _is_non_string_sequence(current_value):
        changed_paths = []
        for index, item in enumerate(current_value):
            next_path = f"{path}[{index}]"
            changed_paths.extend(_collect_changed_paths(item, None, next_path))
        return changed_paths

    if current_value is None and _is_non_string_sequence(baseline_value):
        changed_paths = []
        for index, item in enumerate(baseline_value):
            next_path = f"{path}[{index}]"
            changed_paths.extend(_collect_changed_paths(None, item, next_path))
        return changed_paths

    if _is_non_string_sequence(current_value) and _is_non_string_sequence(baseline_value):
        if len(current_value) != len(baseline_value):
            return [path]
        changed_paths = []
        for index, (current_item, baseline_item) in enumerate(zip(current_value, baseline_value)):
            next_path = f"{path}[{index}]"
            changed_paths.extend(_collect_changed_paths(current_item, baseline_item, next_path))
        return changed_paths

    if current_value != baseline_value:
        return [path]
    return []


def validate_update_config_whitelist(user_config, baseline_config):
    changed_paths = _collect_changed_paths(user_config, baseline_config)
    illegal_paths = sorted(path for path in changed_paths if not _path_is_whitelisted(path))
    if illegal_paths:
        illegal_path_str = ", ".join(illegal_paths)
        logger.error(
            "The following config items are not allowed to be updated by --update_config: %s",
            illegal_path_str,
        )
        raise ValueError("Found non-whitelisted config updates in user_config.")
