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


# Setup log and work paths for different roles
setup_motor_log_path() {
    if [ -n "$MOTOR_LOG_ROOT_PATH" ] && [ -n "$MODEL_NAME" ] && [ -n "$SERVICE_ID" ]; then
        chmod 750 "$MOTOR_LOG_ROOT_PATH"
        if [ ! -d "$MOTOR_LOG_ROOT_PATH/$MODEL_NAME/$SERVICE_ID/motor" ]; then
            mkdir -p -m 750 "$MOTOR_LOG_ROOT_PATH/$MODEL_NAME/$SERVICE_ID/motor"
        fi
        export MOTOR_LOG_PATH="$MOTOR_LOG_ROOT_PATH/$MODEL_NAME/$SERVICE_ID/motor"
    fi
}

# Setup ascend work path for prefill/decode roles
setup_ascend_work_path() {
    if [ -n "$MOTOR_LOG_ROOT_PATH" ] && [ -n "$MODEL_NAME" ] && [ -n "$SERVICE_ID" ]; then
        chmod 750 "$MOTOR_LOG_ROOT_PATH"
        if [ ! -d "$MOTOR_LOG_ROOT_PATH/$MODEL_NAME/$SERVICE_ID/ascend_work_path" ];then
            mkdir -p -m 750 "$MOTOR_LOG_ROOT_PATH/$MODEL_NAME/$SERVICE_ID/ascend_work_path"
        fi
        export ASCEND_WORK_PATH="$MOTOR_LOG_ROOT_PATH/$MODEL_NAME/$SERVICE_ID/ascend_work_path"
    fi
}

# Setup ascend cache path for prefill/decode roles
setup_ascend_cache_path() {
    if [ -n "$MOTOR_LOG_ROOT_PATH" ] && [ -n "$MODEL_NAME" ] && [ -n "$SERVICE_ID" ]; then
        chmod 750 "$MOTOR_LOG_ROOT_PATH"
        if [ ! -d "$MOTOR_LOG_ROOT_PATH/$MODEL_NAME/$SERVICE_ID/ascend_cache_path" ];then
            mkdir -p -m 750 "$MOTOR_LOG_ROOT_PATH/$MODEL_NAME/$SERVICE_ID/ascend_cache_path"
        fi
        export ASCEND_CACHE_PATH="$MOTOR_LOG_ROOT_PATH/$MODEL_NAME/$SERVICE_ID/ascend_cache_path"
    fi
}

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

mkdir "$CONFIG_PATH" -p
chmod 750 "$CONFIG_PATH"

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
        echo "$!" > "$CONFIG_SYNC_PID_FILE"
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

set_cann_env() {
    export LD_LIBRARY_PATH="$LD_LIBRARY_PATH:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/driver/lib64/common"
    source "$CANN_INSTALL_PATH/ascend-toolkit/set_env.sh"
    source "$CANN_INSTALL_PATH/nnal/atb/set_env.sh"
}

gen_ranktable_config() {
    # Use hccl_tools.py to generate ranktable.json
    if [ -f "$CONFIGMAP_PATH/hccl_tools.py" ]; then
        echo "Using hccl_tools.py to generate ranktable.json..."
        export HCCL_PATH="$CONFIG_PATH/hccl.json"
        export PATH="/usr/local/Ascend/driver/tools:$PATH"
        PYTHONUNBUFFERED=1 python3 "$CONFIGMAP_PATH/hccl_tools.py" --hccl_path "$HCCL_PATH"
        # ranktable output path, use by NodeManager
        export RANKTABLE_PATH="$CONFIG_PATH/ranktable.json"
    else
        echo "hccl_tools.py does not exist, skip ranktable generation"
    fi
}

gen_kv_pool_config() {
    # KV cache pool scenario only: KVP_MASTER_SERVICE (kv cache pool master-service) is set
    # only when KV pool is enabled; then generate kv_cache_pool_config.json.
    if [ -n "$KVP_MASTER_SERVICE" ]; then
        echo "Updating kv cache pool configuration file..."
        export MOONCAKE_CONFIG_PATH=$CONFIG_PATH/kv_cache_pool_config.json
        export LD_LIBRARY_PATH=/usr/local/Ascend/ascend-toolkit/latest/python/site-packages/mooncake:$LD_LIBRARY_PATH
        if [ "$ROLE" = "SINGLE_CONTAINER" ]; then
            KVP_MASTER_SERVICE=$POD_IP
        fi
        python3 "$CONFIGMAP_PATH/update_kv_cache_pool_config.py" "$MOONCAKE_CONFIG_PATH" "$USER_CONFIG_PATH"
    fi
}

if [ "$ROLE" = "SINGLE_CONTAINER" ]; then
    export MOTOR_COORDINATOR_CONFIG_PATH="$USER_CONFIG_PATH"
    export MOTOR_CONTROLLER_CONFIG_PATH="$USER_CONFIG_PATH"
    export MOTOR_NODE_MANAGER_CONFIG_PATH="$USER_CONFIG_PATH"
    export MOTOR_ENGINE_PATH="$USER_CONFIG_PATH"
    export CONTROLLER_SERVICE="$POD_IP"
    export COORDINATOR_SERVICE="$POD_IP"

    # Use hccl_tools.py to generate ranktable.json
    gen_ranktable_config

    # Set environment variables for CANN
    set_cann_env

    # Init pid list
    pids=()

    # Start coordinator
    set_coordinator_env
    ROLE=coordinator python3 -m motor.coordinator.main &
    pids+=($!)

    # Start controller
    set_controller_env
    ROLE=controller python3 -m motor.controller.main --config $MOTOR_CONTROLLER_CONFIG_PATH &
    pids+=($!)

    # Set kv cache pool config
    gen_kv_pool_config

    # Start kv_pool
    if [ -n "$KVP_MASTER_SERVICE" ]; then
        set_kv_pool_env
        ROLE=kv_pool mooncake_master --port "$KV_POOL_PORT" \
        --eviction_high_watermark_ratio "$KV_POOL_EVICTION_HIGH_WATERMARK_RATIO" \
        --eviction_ratio "$KV_POOL_EVICTION_RATIO" &
	pids+=($!)
    fi

    # Start nodemanager
    p_instances_num=$(grep '"p_instances_num"' $USER_CONFIG_PATH | sed 's/.*:[[:space:]]*\([0-9.]*\).*/\1/')
    d_instances_num=$(grep '"d_instances_num"' $USER_CONFIG_PATH | sed 's/.*:[[:space:]]*\([0-9.]*\).*/\1/')
    for i in $(seq 0 $((p_instances_num - 1))); do
        ROLE=prefill INDEX=$i JOB_NAME=p$i RANKTABLE_PATH=$CONFIG_PATH/ranktable_p${i}.json python3 -m motor.node_manager.main &
	pids+=($!)
        echo "pull up instance: ROLE=prefill INDEX=$i JOB_NAME=p$i RANKTABLE_PATH=$CONFIG_PATH/ranktable_p${i}.json python3 -m motor.node_manager.main &"
    done

    for i in $(seq 0 $((d_instances_num - 1))); do
        ROLE=decode INDEX=$i JOB_NAME=d$i RANKTABLE_PATH=$CONFIG_PATH/ranktable_d${i}.json python3 -m motor.node_manager.main &
	pids+=($!)
        echo "pull up instance: ROLE=decode INDEX=$i JOB_NAME=d$i RANKTABLE_PATH=$CONFIG_PATH/ranktable_d${i}.json python3 -m motor.node_manager.main &"
    done

    for pid in "${pids[@]}"; do
        wait $pid
    done
    echo "All processes finished successfully."
    exit 0
fi

if [ "$ROLE" = "prefill" ] || [ "$ROLE" = "decode" ]; then
    export MOTOR_NODE_MANAGER_CONFIG_PATH="$USER_CONFIG_PATH"
    export MOTOR_ENGINE_PATH="$USER_CONFIG_PATH"

    # Use hccl_tools.py to generate ranktable.json
    gen_ranktable_config

    # Set environment variables for CANN
    set_cann_env

    # Set kv cache pool config
    gen_kv_pool_config

    # Set log and work paths
    setup_motor_log_path
    setup_ascend_work_path
    setup_ascend_cache_path

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
    set_controller_env
    export MOTOR_CONTROLLER_CONFIG_PATH="$USER_CONFIG_PATH"
    setup_motor_log_path

    # Controller start command
    python3 -m motor.controller.main --config $MOTOR_CONTROLLER_CONFIG_PATH
fi

if [ "$ROLE" == "coordinator" ]; then
    set_coordinator_env
    export MOTOR_COORDINATOR_CONFIG_PATH="$USER_CONFIG_PATH"
    setup_motor_log_path

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
