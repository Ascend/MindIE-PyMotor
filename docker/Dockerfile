# syntax=docker/dockerfile:1.4
#
# Build MindIE-PyMotor on top of a vllm-ascend base image, using the local
# repository as the build context.
#
# Example:
#   cd <repo-root>
#   docker build --network=host \
#       --build-arg BASE_IMAGE_TAG=main \
#       -t mindie-pymotor:dev \
#       -f docker/Dockerfile .
#
# The full base image is `quay.nju.edu.cn/ascend/vllm-ascend:${BASE_IMAGE_TAG}`.
# Pick BASE_IMAGE_TAG from the values listed in `docker/OVERVIEW.md`
# (e.g. `main`, `v0.13.0-a3`, `v0.13.0-310p-openeuler`, ...).

ARG BASE_IMAGE_TAG=main
FROM quay.nju.edu.cn/ascend/vllm-ascend:${BASE_IMAGE_TAG}

ARG PIP_INDEX_URL=https://repo.huaweicloud.com/repository/pypi/simple
ARG PIP_TRUSTED_HOST=repo.huaweicloud.com

ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /opt/MindIE-PyMotor

COPY . /opt/MindIE-PyMotor/

RUN set -eux; \
    pip install --no-cache-dir -r requirements.txt; \
    bash build.sh; \
    pip install --no-cache-dir --force-reinstall dist/motor*.whl; \
    rm -rf build/ motor.egg-info/ dist/

RUN set -eux; \
    cd examples/features/observability; \
    pip install --no-cache-dir -r requirements.txt; \
    bash build.sh; \
    pip install --no-cache-dir --force-reinstall dist/ccae_reporter-*.whl; \
    rm -rf build/ dist/ ccae_reporter.egg-info/

RUN set -eux; \
    mkdir -p /tmp/motor; \
    mv /opt/MindIE-PyMotor/examples /tmp/motor/; \
    ls -l /tmp/motor/examples; \
    rm -rf /tmp/motor/examples/features/fault_tolerance/ras_monitor; \
    ls -l /tmp/motor/examples/features/fault_tolerance; \
    find /tmp -mindepth 1 -maxdepth 1 ! -name "motor" -exec rm -rf {} +

WORKDIR /workspace
