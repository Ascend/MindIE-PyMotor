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

if [ -z "$1" ]; then
    echo "Usage: $0 <namespace>"
    echo "Example: $0 mindie-motor"
    exit 1
fi

NAMESPACE="$1"

echo -e "NOW EXECUTING [kubectl delete] COMMANDS. THE RESULT IS: \n\n"
echo "Namespace: $NAMESPACE"

kubectl delete cm motor-config -n "$NAMESPACE"

YAML_DIR=./output_yamls

for yaml_file in "$YAML_DIR"/*.yaml; do
    if [ -f "$yaml_file" ]; then
        kubectl delete -f "$yaml_file" -n "$NAMESPACE"
    fi
done

# keep the same with yaml_template/engine_template.yaml terminationGracePeriodSeconds: 10
for ((i=10; i>=1; i--)); do
    echo "Waiting for pods to terminate gracefully... ${i}s remaining"
    sleep 1
done

# Terminating is not a status.phase value; stuck terminating pods have metadata.deletionTimestamp set.
kubectl get pods -n "$NAMESPACE" -o jsonpath='{range .items[?(@.metadata.deletionTimestamp)]}{.metadata.name}{"\n"}{end}' | while read -r pod; do
    [ -z "$pod" ] && continue
    kubectl delete pod "$pod" -n "$NAMESPACE" --force --grace-period=0
done

sed -i '/^# patch_begin/,/^# patch_end/d' ./startup/boot.sh
sed -i '/^function set_controller_env()/,/^}/d' ./startup/roles/controller.sh
sed -i '/^function set_coordinator_env()/,/^}/d' ./startup/roles/coordinator.sh
sed -i '/^function set_prefill_env()/,/^}/d' ./startup/roles/engine.sh
sed -i '/^function set_decode_env()/,/^}/d' ./startup/roles/engine.sh
sed -i '/^function set_common_env()/,/^}/d' ./startup/common.sh
sed -i '/^function set_kv_pool_env()/,/^}/d' ./startup/roles/kv_pool.sh
sed -i '/^function set_kv_conductor_env()/,/^}/d' ./startup/roles/kv_conductor.sh
sed -i '/^function set_controller_env()/,/^}/d' ./startup/roles/all_combine_in_single_container.sh
sed -i '/^function set_coordinator_env()/,/^}/d' ./startup/roles/all_combine_in_single_container.sh
sed -i '/^function set_prefill_env()/,/^}/d' ./startup/roles/all_combine_in_single_container.sh
sed -i '/^function set_decode_env()/,/^}/d' ./startup/roles/all_combine_in_single_container.sh
sed -i '/^function set_kv_pool_env()/,/^}/d' ./startup/roles/all_combine_in_single_container.sh
sed -i '/^function set_kv_conductor_env()/,/^}/d' ./startup/roles/all_combine_in_single_container.sh
sed -i '/./,$!d' ./startup/common.sh

echo "Delete completed."
