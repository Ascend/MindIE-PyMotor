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

set_common_env

echo "Current node role: ROLE=$ROLE"

# Search for libjemalloc.so.2 in /usr directory
jemalloc_path=$(find /usr -type f -name "libjemalloc.so.2" 2>/dev/null | head -n 1)
if [[ -n "$jemalloc_path" ]]; then
    export LD_PRELOAD="${jemalloc_path}:${LD_PRELOAD}"
    echo "jemalloc found at: $jemalloc_path"
    echo "LD_PRELOAD is set successfully."
else
    echo "Warning: libjemalloc.so.2 not found under /usr"
    echo "Please make sure jemalloc is installed."
fi

# Define configuration file paths
USER_CONFIG_FILE="$CONFIGMAP_PATH/user_config.json"
export USER_CONFIG_PATH="$USER_CONFIG_FILE"

mkdir $CONFIG_PATH -p
chmod 750 $CONFIG_PATH

# Avoid using a symlinked ConfigMap file directly (security check rejects it).
# Copy user_config.json from CONFIGMAP_PATH to CONFIG_PATH and use the real file path.
# Periodically sync the file to enable dynamic configuration updates from host side to container side
# without requiring container restart. This allows runtime configuration changes to take effect automatically.
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
    # Periodically refresh local copy to reflect ConfigMap updates.
    if [ -f "$CONFIG_SYNC_PID_FILE" ] && kill -0 "$(cat "$CONFIG_SYNC_PID_FILE")" 2>/dev/null; then
        echo "Config sync loop already running (pid=$(cat "$CONFIG_SYNC_PID_FILE"))"
    else
        (
            while true; do
                sleep "$CONFIG_SYNC_INTERVAL"
                sync_user_config
            done
        ) &
        echo $! > "$CONFIG_SYNC_PID_FILE"
    fi
fi

# Core dump settings
if [ "$SAVE_CORE_DUMP_FILE_ENABLE" = "1" ]; then
    ulimit -c 31457280        # 31457280KB = 30G
    mkdir -p /var/coredump
    chmod 700 /var/coredump
    sysctl -w kernel.core_pattern=/var/coredump/core.%e.%p.%t
else
    ulimit -c 0
fi

if [ "$ROLE" = "prefill" ] || [ "$ROLE" = "decode" ]; then
    export MOTOR_NODE_MANAGER_CONFIG_PATH="$USER_CONFIG_PATH"
    export MOTOR_ENGINE_PATH="$USER_CONFIG_PATH"

    # KV cache pool scenario only: KVP_MASTER_SERVICE (kv cache pool master-service) is set
    # only when KV pool is enabled; then generate kv_cache_pool_config.json.
    if [ -n "$KVP_MASTER_SERVICE" ]; then
        echo "Updating kv cache pool configuration file..."
        export MOONCAKE_CONFIG_PATH=$CONFIG_PATH/kv_cache_pool_config.json
        export LD_LIBRARY_PATH=/usr/local/Ascend/ascend-toolkit/latest/python/site-packages/mooncake:$LD_LIBRARY_PATH
        python3 "$CONFIGMAP_PATH/update_kv_cache_pool_config.py" "$MOONCAKE_CONFIG_PATH" "$USER_CONFIG_PATH"
    fi

    # Use hccl_tools.py to generate ranktable.json
    if [ -f "$CONFIGMAP_PATH/hccl_tools.py" ]; then
        echo "Using hccl_tools.py to generate ranktable.json..."
        export HCCL_PATH="$CONFIG_PATH/hccl.json"
        export PATH="/usr/local/Ascend/driver/tools:$PATH"
        PYTHONUNBUFFERED=1 python3 "$CONFIGMAP_PATH/hccl_tools.py" --hccl_path "$HCCL_PATH"
        # ranktable output path, use by NodeManager
        export RANKTABLE_PATH="$CONFIG_PATH/ranktable.json"
        echo "Ranktable generated successfully: $HCCL_PATH"
    else
        echo "hccl_tools.py does not exist, skip ranktable generation"
    fi

    export RANK_TABLE_PATH="$CONFIG_PATH/ranktable.json"

    # Set environment variables for CANN
    export LD_LIBRARY_PATH="$LD_LIBRARY_PATH:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/driver/lib64/common"
    source "$CANN_INSTALL_PATH/ascend-toolkit/set_env.sh"
    source "$CANN_INSTALL_PATH/nnal/atb/set_env.sh"

    # Set log and work paths
    if [ -n "$MINDIE_LOG_CONFIG_PATH" ] && [ -n "$MODEL_NAME" ] && [ -n "$MODEL_ID" ]; then
        chmod 750 "$MINDIE_LOG_CONFIG_PATH"
        if [ ! -d "$MINDIE_LOG_CONFIG_PATH/$MODEL_NAME/$MODEL_ID" ];then
            mkdir -p -m 750 "$MINDIE_LOG_CONFIG_PATH/$MODEL_NAME/$MODEL_ID"
        fi
        if [ ! -d "$MINDIE_LOG_CONFIG_PATH/$MODEL_NAME/$MODEL_ID/mindie" ];then
            mkdir -p -m 750 "$MINDIE_LOG_CONFIG_PATH/$MODEL_NAME/$MODEL_ID/mindie"
        fi
        if [ ! -d "$MINDIE_LOG_CONFIG_PATH/$MODEL_NAME/$MODEL_ID/ascend_work_path" ];then
            mkdir -p -m 750 "$MINDIE_LOG_CONFIG_PATH/$MODEL_NAME/$MODEL_ID/ascend_work_path"
        fi
        if [ ! -d "$MINDIE_LOG_CONFIG_PATH/$MODEL_NAME/$MODEL_ID/ascend_cache_path" ];then
            mkdir -p -m 750 "$MINDIE_LOG_CONFIG_PATH/$MODEL_NAME/$MODEL_ID/ascend_cache_path"
        fi
        export MINDIE_LOG_PATH="$MINDIE_LOG_CONFIG_PATH/$MODEL_NAME/$MODEL_ID/mindie"
        export ASCEND_WORK_PATH="$MINDIE_LOG_CONFIG_PATH/$MODEL_NAME/$MODEL_ID/ascend_work_path"
        export ASCEND_CACHE_PATH="$MINDIE_LOG_CONFIG_PATH/$MODEL_NAME/$MODEL_ID/ascend_cache_path"
    fi

    # Set role-specific environment variables
    if [ "$ROLE" = "decode" ]; then
        set_decode_env
    elif [ "$ROLE" = "prefill" ]; then
        set_prefill_env
    fi

    # Nodemanager start command
    python3 -m motor.node_manager.main &
    pid=$!
    echo "pull up $ROLE instance"
    wait $pid
    exit_code=$?
    if [ $exit_code -ne 0 ]; then
        echo "Error: mindie daemon exited with code $exit_code"
        exit 1
    fi
    echo "All processes finished successfully."
    exit 0
fi

if [ "$ROLE" = "controller" ]; then
    export MOTOR_CONTROLLER_CONFIG_PATH="$USER_CONFIG_PATH"
    
    if [ -n "$CONTROLLER_LOG_CONFIG_PATH" ] && [ -n "$MODEL_NAME" ] && [ -n "$MODEL_ID" ]; then
        chmod 750 "$CONTROLLER_LOG_CONFIG_PATH"
        if [ ! -d "$CONTROLLER_LOG_CONFIG_PATH/$MODEL_NAME/$MODEL_ID" ];then
            mkdir -p -m 750 "$CONTROLLER_LOG_CONFIG_PATH/$MODEL_NAME/$MODEL_ID"
        fi
        export MINDIE_LOG_PATH="$CONTROLLER_LOG_CONFIG_PATH/$MODEL_NAME/$MODEL_ID/mindie"
    fi
    set_controller_env

    # Controller start command
    python3 -m motor.controller.main --config $MOTOR_CONTROLLER_CONFIG_PATH
fi

if [ "$ROLE" == "coordinator" ]; then
    export MOTOR_COORDINATOR_CONFIG_PATH="$USER_CONFIG_PATH"
    
    if [ -n "$COORDINATOR_LOG_CONFIG_PATH" ] && [ -n "$MODEL_NAME" ] && [ -n "$MODEL_ID" ]; then
        chmod 750 "$COORDINATOR_LOG_CONFIG_PATH"
        if [ ! -d "$COORDINATOR_LOG_CONFIG_PATH/$MODEL_NAME/$MODEL_ID" ];then
            mkdir -p -m 750 "$COORDINATOR_LOG_CONFIG_PATH/$MODEL_NAME/$MODEL_ID"
        fi
        export MINDIE_LOG_PATH="$COORDINATOR_LOG_CONFIG_PATH/$MODEL_NAME/$MODEL_ID/mindie"
    fi
    set_coordinator_env

    # Coordinator start command
    python3 -m motor.coordinator.main
fi

if [ "$ROLE" == "kv_pool" ]; then
    export LD_LIBRARY_PATH=/usr/local/Ascend/ascend-toolkit/latest/python/site-packages/mooncake:$LD_LIBRARY_PATH

    set_kv_pool_env

    # KV Pool start
    mooncake_master --port "$KV_POOL_PORT" \
        --eviction_high_watermark_ratio "$KV_POOL_EVICTION_HIGH_WATERMARK_RATIO" \
        --eviction_ratio "$KV_POOL_EVICTION_RATIO"
fi