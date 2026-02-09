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

echo -e "NOW EXECUTING [kubectl delete] COMMANDS. THE RESULT IS: \n\n"

DEFAULT_NAME_SPACE="mindie-motor"
USER_CONFIG_FILE="./user_config.json"

if [ -f "$USER_CONFIG_FILE" ] && [ -r "$USER_CONFIG_FILE" ]; then
    JOB_ID=$(grep -o '"job_id"[[:space:]]*:[[:space:]]*"[^"]*"' "$USER_CONFIG_FILE" | sed -E 's/"job_id"[[:space:]]*:[[:space:]]*"([^"]*)"/\1/')
    if [ -n "$JOB_ID" ]; then
        DEFAULT_NAME_SPACE="$JOB_ID"
    fi
fi

NAME_SPACE="$DEFAULT_NAME_SPACE"
if [ -n "$1" ]; then
    NAME_SPACE="$1"
fi

kubectl delete cm motor-config -n "$NAME_SPACE";

YAML_DIR=./output/deployment

for yaml_file in "$YAML_DIR"/*.yaml; do
	if [ -f "$yaml_file" ]; then
		kubectl delete -f "$yaml_file"
	fi
done

for file in ./*user_config*; do
    if [ -f "$file" ]; then
        sed -i -E 's/("model_id"\s*:\s*)"[^"]*"/\1""/g' "$file"
        echo "change $file model_id to empty"
    fi
done

sed -i '/^function set_controller_env()/,/^}/d' ./boot_helper/boot.sh
sed -i '/^function set_coordinator_env()/,/^}/d' ./boot_helper/boot.sh
sed -i '/^function set_prefill_env()/,/^}/d' ./boot_helper/boot.sh
sed -i '/^function set_decode_env()/,/^}/d' ./boot_helper/boot.sh
sed -i '/^function set_common_env()/,/^}/d' ./boot_helper/boot.sh
sed -i '/^function set_kv_pool_env()/,/^}/d' ./boot_helper/boot.sh
sed -i '/./,$!d' ./boot_helper/boot.sh

rm -rf $YAML_DIR
