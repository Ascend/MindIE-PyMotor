#!/bin/bash
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Current node role: ROLE=$ROLE"

set_common_env

setup_motor_log_path() {
    if [ -n "$MOTOR_LOG_ROOT_PATH" ] && [ -n "$MODEL_NAME" ] && [ -n "$SERVICE_ID" ]; then
        chmod 750 "$MOTOR_LOG_ROOT_PATH"
        if [ ! -d "$MOTOR_LOG_ROOT_PATH/$MODEL_NAME/$SERVICE_ID/motor" ]; then
            mkdir -p -m 750 "$MOTOR_LOG_ROOT_PATH/$MODEL_NAME/$SERVICE_ID/motor"
        fi
        export MOTOR_LOG_PATH="$MOTOR_LOG_ROOT_PATH/$MODEL_NAME/$SERVICE_ID/motor"
    fi
}

setup_ascend_work_path() {
    if [ -n "$MOTOR_LOG_ROOT_PATH" ] && [ -n "$MODEL_NAME" ] && [ -n "$SERVICE_ID" ]; then
        chmod 750 "$MOTOR_LOG_ROOT_PATH"
        if [ ! -d "$MOTOR_LOG_ROOT_PATH/$MODEL_NAME/$SERVICE_ID/ascend_work_path" ];then
            mkdir -p -m 750 "$MOTOR_LOG_ROOT_PATH/$MODEL_NAME/$SERVICE_ID/ascend_work_path"
        fi
        export ASCEND_WORK_PATH="$MOTOR_LOG_ROOT_PATH/$MODEL_NAME/$SERVICE_ID/ascend_work_path"
    fi
}

setup_ascend_cache_path() {
    if [ -n "$MOTOR_LOG_ROOT_PATH" ] && [ -n "$MODEL_NAME" ] && [ -n "$SERVICE_ID" ]; then
        chmod 750 "$MOTOR_LOG_ROOT_PATH"
        if [ ! -d "$MOTOR_LOG_ROOT_PATH/$MODEL_NAME/$SERVICE_ID/ascend_cache_path" ];then
            mkdir -p -m 750 "$MOTOR_LOG_ROOT_PATH/$MODEL_NAME/$SERVICE_ID/ascend_cache_path"
        fi
        export ASCEND_CACHE_PATH="$MOTOR_LOG_ROOT_PATH/$MODEL_NAME/$SERVICE_ID/ascend_cache_path"
    fi
}

setup_jemalloc() {
    jemalloc_path=$(find /usr -type f -name "libjemalloc.so.2" 2>/dev/null | head -n 1)
    if [[ -n "$jemalloc_path" ]]; then
        export LD_PRELOAD="${jemalloc_path}:${LD_PRELOAD}"
        echo "jemalloc found at: $jemalloc_path"
        echo "LD_PRELOAD is set successfully."
    else
        echo "Warning: libjemalloc.so.2 not found under /usr"
        echo "Please make sure jemalloc is installed."
    fi
}

USER_CONFIG_FILE="$CONFIGMAP_PATH/user_config.json"
export USER_CONFIG_PATH="$USER_CONFIG_FILE"

mkdir "$CONFIG_PATH" -p
chmod 750 "$CONFIG_PATH"

USER_CONFIG_DST="$CONFIG_PATH/user_config.json"
CONFIG_SYNC_INTERVAL="${CONFIG_SYNC_INTERVAL:-10}"
CONFIG_SYNC_PID_FILE="$CONFIG_PATH/user_config_sync.pid"

sync_user_config() {
    if [ -f "$USER_CONFIG_FILE" ]; then
        if [ ! -f "$USER_CONFIG_DST" ] || ! cmp -s "$USER_CONFIG_FILE" "$USER_CONFIG_DST"; then
            cp -f "$USER_CONFIG_FILE" "$USER_CONFIG_DST"
            chmod 640 "$USER_CONFIG_DST"
        fi
        export USER_CONFIG_PATH="$USER_CONFIG_DST"
    else
        export USER_CONFIG_PATH="$USER_CONFIG_FILE"
    fi
}

sync_user_config
if [ -f "$USER_CONFIG_FILE" ]; then
    if [ -f "$CONFIG_SYNC_PID_FILE" ] && kill -0 "$(cat "$CONFIG_SYNC_PID_FILE")" 2>/dev/null; then
        echo "Config sync loop already running (pid=$(cat "$CONFIG_SYNC_PID_FILE"))"
    else
        (
            while true; do
                sleep "$CONFIG_SYNC_INTERVAL"
                sync_user_config
            done
        ) &
        echo "$!" > "$CONFIG_SYNC_PID_FILE"
    fi
fi

if [ "$SAVE_CORE_DUMP_FILE_ENABLE" = "1" ]; then
    ulimit -c 31457280
    mkdir -p /var/coredump
    chmod 700 /var/coredump
    sysctl -w kernel.core_pattern=/var/coredump/core.%e.%p.%t
else
    ulimit -c 0
fi

set_cann_env() {
    export LD_LIBRARY_PATH="$LD_LIBRARY_PATH:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/driver/lib64/common"
    source "$CANN_INSTALL_PATH/ascend-toolkit/set_env.sh"
    source "$CANN_INSTALL_PATH/nnal/atb/set_env.sh"
}

gen_ranktable_config() {
    if [ -f "$CONFIGMAP_PATH/hccl_tools.py" ]; then
        echo "Using hccl_tools.py to generate ranktable.json..."
        export HCCL_PATH="$CONFIG_PATH/hccl.json"
        export PATH="/usr/local/Ascend/driver/tools:$PATH"
        PYTHONUNBUFFERED=1 python3 "$CONFIGMAP_PATH/hccl_tools.py" --hccl_path "$HCCL_PATH"
        export RANKTABLE_PATH="$CONFIG_PATH/ranktable.json"
    else
        echo "hccl_tools.py does not exist, skip ranktable generation"
    fi
}

gen_kv_pool_config() {
    if [ -n "$KVP_MASTER_SERVICE" ]; then
        echo "Updating kv cache pool configuration file..."
        export MOONCAKE_CONFIG_PATH=$CONFIG_PATH/kv_cache_pool_config.json
        export LD_LIBRARY_PATH=/usr/local/Ascend/ascend-toolkit/latest/python/site-packages/mooncake:$LD_LIBRARY_PATH
        if [ "$ROLE" = "SINGLE_CONTAINER" ]; then
            KVP_MASTER_SERVICE=$POD_IP
        fi
        python3 "$CONFIGMAP_PATH/mooncake_config.py" pool "$MOONCAKE_CONFIG_PATH" "$USER_CONFIG_PATH"
    fi
}
