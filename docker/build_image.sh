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

# Build a MindIE-PyMotor image directly from the local source tree.
#
# Usage (run from the repository root):
#   bash docker/build_image.sh
#
# Override defaults via environment variables, e.g.:
#   SYSTEM=openEuler24.03 DEVICE=910c VLLM_ASCEND_VERSION=v0.13.0 \
#       bash docker/build_image.sh

set -euo pipefail

SYSTEM=${SYSTEM:-Ubuntu24.04}                       # Ubuntu24.04 / openEuler24.03
DEVICE=${DEVICE:-910b}                              # 310p / 910b / 910c
ARCH=${ARCH:-$(uname -m)}                           # x86_64 / aarch64
PYMOTOR_VERSION=${PYMOTOR_VERSION:-0.1.0}           # MindIE-PyMotor version
VLLM_ASCEND_VERSION=${VLLM_ASCEND_VERSION:-main}    # vllm-ascend base image tag prefix
IMAGE_VERSION=${IMAGE_VERSION:-${PYMOTOR_VERSION}}  # Tag of the produced image

case "${DEVICE}" in
    310p) PRODUCT=300I-Duo ;;
    910b) PRODUCT=800I-A2 ;;
    910c) PRODUCT=800I-A3 ;;
    *) echo "Unsupported DEVICE: ${DEVICE}" >&2; exit 1 ;;
esac

case "${SYSTEM}_${DEVICE}" in
    Ubuntu24.04_310p)    BASE_IMAGE_TAG=${VLLM_ASCEND_VERSION}-310p ;;
    openEuler24.03_310p) BASE_IMAGE_TAG=${VLLM_ASCEND_VERSION}-310p-openeuler ;;
    Ubuntu24.04_910b)    BASE_IMAGE_TAG=${VLLM_ASCEND_VERSION} ;;
    openEuler24.03_910b) BASE_IMAGE_TAG=${VLLM_ASCEND_VERSION}-openeuler ;;
    Ubuntu24.04_910c)    BASE_IMAGE_TAG=${VLLM_ASCEND_VERSION}-a3 ;;
    openEuler24.03_910c) BASE_IMAGE_TAG=${VLLM_ASCEND_VERSION}-a3-openeuler ;;
    *) echo "Unsupported SYSTEM/DEVICE combo: ${SYSTEM}/${DEVICE}" >&2; exit 1 ;;
esac

IMAGE_TAG="mindie-pymotor:${IMAGE_VERSION}-${PRODUCT}-py3.11-${SYSTEM}-${ARCH}"

PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "${PROJECT_ROOT}"

echo "Building ${IMAGE_TAG} from quay.nju.edu.cn/ascend/vllm-ascend:${BASE_IMAGE_TAG} ..."
docker build \
    --network=host \
    --build-arg "BASE_IMAGE_TAG=${BASE_IMAGE_TAG}" \
    -t "${IMAGE_TAG}" \
    -f docker/Dockerfile \
    .

echo "Done. Image tag: ${IMAGE_TAG}"
