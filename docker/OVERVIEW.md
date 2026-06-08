# MindIE-Motor 

> English | [中文](./OVERVIEW.zh.md)

## Quick Reference

- MindIE-Motor is maintained by the [MindIE community](https://www.hiascend.com/cn/developer/software/mindie)

- Where to get help

    - [MindIE Image Registry](https://www.hiascend.com/developer/ascendhub/detail/af85b724a7e5469ebd7ea13c3439d48f)
    - [MindIE-Motor Documentation](https://gitcode.com/Ascend/MindIE-PyMotor/blob/master/docs/zh/index.md)
    - [Atlas Developer Community](https://www.hiascend.com/developer)
    - [Report an Issue](https://gitcode.com/Ascend/MindIE-PyMotor/issues)

---

## MindIE-Motor

Provides one‑click PD-separated deployment, flexibly adapts to multiple inference engines (vLLM, SGLang) through a cloud‑native plug‑in architecture, and combines high‑performance scheduling with load balancing capabilities to build highly available, scalable large‑scale inference services.

---

## Supported Tags and Dockerfile Links

### Tag Specification

Official pre-built image tags follow this format:

```text
<MotorVersion>-vllm-ascend-<vllm-ascend-version>-<ProductSeries>-<PythonVersion>-<OperatingSystem>-lts
```

| Field | Example Value | Description |
|---|---|---|
| `MotorVersion` | `3.0.0` | Motor version number |
| `vllm-ascend version` | `v0.18.0` | Base inference engine (vllm-ascend) version |
| `ProductSeries` | `800I-A2`, `800I-A3`, `300I-Duo` | Target Atlas product series |
| `PythonVersion` | `py3.11` | Python version |
| `OperatingSystem` | `Ubuntu24.04-lts` | Base OS and distribution identifier |

### Image Registry Address

Official MindIE-Motor images are hosted on Quay:

```text
quay.io/ascend/mindie-motor
```

**Pull examples:**

```bash
# Atlas 800I A2
docker pull quay.io/ascend/mindie-motor:3.0.0-vllm-ascend-v0.18.0-800I-A2-py3.11-Ubuntu24.04-lts

# Atlas 800I A3
docker pull quay.io/ascend/mindie-motor:3.0.0-vllm-ascend-v0.18.0-800I-A3-py3.11-Ubuntu24.04-lts
```

> For faster downloads, replace `quay.io` with `quay.nju.edu.cn`.

**Locally built image tag example (`build_image.sh` defaults):**

```text
mindie-pymotor:0.1.0-800I-A2-py3.11-Ubuntu24.04-x86_64
```

> Note: Images built locally via `build_image.sh` use a different naming scheme. The
> OS field comes from the `SYSTEM` environment variable (default `Ubuntu24.04`),
> with an architecture suffix appended.

### Build Parameters

The build script reads the following environment variables (all optional, with
sensible defaults). Override them on the command line as needed.

| Variable | Description | Required | Default | Example Value |
|---|---|---|---|---|
| SYSTEM | Server OS and version | No | `Ubuntu24.04` | Ubuntu24.04 / openEuler24.03 |
| DEVICE | Atlas device model | No | `910` | 310 / 910 / A3 |
| ARCH | System architecture | No | `$(uname -m)` | x86_64 / aarch64 |
| PYMOTOR_VERSION | MindIE-PyMotor version number | No | `0.1.0` | 0.1.0 |
| VLLM_ASCEND_VERSION | vllm-ascend base image version/branch | No | `main` | v0.18.0 / v0.13.0 / main |
| IMAGE_VERSION | Version tag for the final built image | No | `${PYMOTOR_VERSION}` | v1.0.0 |

---

## Quick Start

### Prerequisites (Optional)

#### Install Drivers

- Firmware and drivers have been installed on the host. Refer to [Install Drivers and Firmware](https://www.hiascend.com/document/detail/zh/mindie/100/envdeployment/instg/mindie_instg_0006.html) for details.
- Docker is installed on the host.

---

### Pull MindIE-Motor Image

Pull a pre-built image from the official registry :

```bash
# Atlas 800I A2
docker pull quay.io/ascend/mindie-motor:3.0.0-vllm-ascend-v0.18.0-800I-A2-py3.11-Ubuntu24.04-lts

# Atlas 800I A3
docker pull quay.io/ascend/mindie-motor:3.0.0-vllm-ascend-v0.18.0-800I-A3-py3.11-Ubuntu24.04-lts
```

### Build MindIE-Motor Image

The image is built directly from the local repository: `docker/Dockerfile`
takes care of installing Python dependencies, compiling the wheel via
`build.sh`, and installing it into the base image.

A ready-to-use helper script is provided at `docker/build_image.sh`. From the
**repository root** simply run:

```bash
# Default: Ubuntu24.04 / 910b / vllm-ascend tag "main"
bash docker/build_image.sh

# Override defaults via environment variables
SYSTEM=openEuler24.03 DEVICE=910c VLLM_ASCEND_VERSION=v0.13.0 \
    bash docker/build_image.sh
```

The script's contents are reproduced below for reference:

```sh
#!/bin/bash
# Build a MindIE-PyMotor image from the local source tree.
# Run from the repository root:
#   bash docker/build_image.sh
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
```

What the build does, in order:

1. Pull the base image `quay.nju.edu.cn/ascend/vllm-ascend:${BASE_IMAGE_TAG}`.
2. Copy the current source tree into `/opt/MindIE-PyMotor` inside the image.
3. Inside the image, run:

    ```bash
    pip install -r requirements.txt
    bash build.sh
    cd dist && pip install motor*.whl --force-reinstall
    ```

4. Then build and install the `ccae_reporter` helper from
   `examples/features/observability/`, which lets the image integrate with
   the CCAE cluster autonomous engine:

    ```bash
    cd examples/features/observability
    pip install -r requirements.txt
    bash build.sh
    pip install --force-reinstall dist/ccae_reporter-*.whl
    ```

### Run MindIE-Motor Container

Make sure Atlas drivers are installed on the host and `/dev/davinci*` device nodes
are available before running.

#### Minimal verification command

Use this to confirm the image starts and can access NPUs (replace `IMAGE_NAME` with
your actual tag):

```bash
IMAGE_NAME="quay.io/ascend/mindie-motor:3.0.0-vllm-ascend-v0.18.0-800I-A2-py3.11-Ubuntu24.04-lts"

docker run --rm -it \
  --device=/dev/davinci_manager \
  --device=/dev/devmm_svm \
  --device=/dev/hisi_hdc \
  --device=/dev/davinci0 \
  -v /usr/local/Ascend/driver:/usr/local/Ascend/driver:ro \
  -v /usr/local/Ascend/add-ons/:/usr/local/Ascend/add-ons/:ro \
  -v /usr/local/sbin/npu-smi:/usr/local/sbin/npu-smi:ro \
  -v /var/log/npu/:/usr/slog \
  "${IMAGE_NAME}" \
  bash -c "npu-smi info && python -c 'import motor; print(\"motor ok\")'"
```

#### Start an inference service

A full deployment requires `boot.sh`, `user_config.json`, and related files mounted
into the container. See the [docker-only single-container deployment
guide](../docs/zh/developer_guide/docker_only/single_container_docker_only.md) for the
end-to-end workflow.

```bash
CONFIGMAP_PATH="/path/to/configmap"   # absolute path; must contain boot.sh, user_config.json, etc.
IMAGE_NAME="quay.io/ascend/mindie-motor:3.0.0-vllm-ascend-v0.18.0-800I-A2-py3.11-Ubuntu24.04-lts"

docker run -u root --rm --name mindie-motor \
  -e ASCEND_RUNTIME_OPTIONS=NODRV \
  -e CONFIGMAP_PATH="${CONFIGMAP_PATH}" \
  -e CONFIG_PATH=/usr/local/Ascend/pyMotor/conf \
  -e ROLE=SINGLE_CONTAINER \
  --device=/dev/davinci_manager \
  --device=/dev/devmm_svm \
  --device=/dev/hisi_hdc \
  --device=/dev/davinci0 \
  --device=/dev/davinci1 \
  -p 1025:1025 \
  -p 1026:1026 \
  -v /usr/local/Ascend/driver:/usr/local/Ascend/driver \
  -v /usr/local/Ascend/add-ons/:/usr/local/Ascend/add-ons/ \
  -v /usr/local/sbin/npu-smi:/usr/local/sbin/npu-smi \
  -v /usr/local/sbin:/usr/local/sbin \
  -v /var/log/npu/:/usr/slog \
  -v /mnt:/mnt \
  -v "${CONFIGMAP_PATH}:${CONFIGMAP_PATH}" \
  "${IMAGE_NAME}" \
  bash -c 'export POD_IP=$(grep $(hostname) /etc/hosts | cut -f1) && source ${CONFIGMAP_PATH}/boot.sh'
```

Common parameters:

| Parameter / env var | Description |
|---|---|
| `--device=/dev/davinci{N}` | Map NPU devices; add `davinci0`, `davinci1`, etc. as needed |
| `--device=/dev/davinci_manager`, etc. | Ascend management devices; usually required for inference |
| `-v /usr/local/Ascend/driver:...` | Mount the host Ascend driver directory |
| `-v ${CONFIGMAP_PATH}:...` | Mount the startup scripts and config directory |
| `-p <host>:<container>` | Expose API ports; must match `user_config.json` |
| `ASCEND_RUNTIME_OPTIONS=NODRV` | Reuse the host driver; no in-container driver install |
| `CONFIGMAP_PATH` | In-container path to startup scripts; must match the mount |
| `CONFIG_PATH` | Motor config directory; default `/usr/local/Ascend/pyMotor/conf` |
| `ROLE` | Deployment role; use `SINGLE_CONTAINER` for single-container PD separation |

### How to Extend

```bash
# Use MindIE-PyMotor image as base, add user software
FROM quay.io/ascend/mindie-motor:3.0.0-vllm-ascend-v0.18.0-800I-A2-py3.11-Ubuntu24.04-lts

RUN apt update -y && \
    apt install gcc ...

...
```

---

## Supported Hardware

| Chip Series | Product Example | Architecture |
|---|---|---|
| Atlas 910 | Atlas 800T A2, Atlas 900 A2 PoD | ARM64 / x86_64 |
| Atlas A3 | Atlas 800T A3 | ARM64 / x86_64 |
| Atlas 310 | Atlas 300I Pro, Atlas 300V Pro | ARM64 / x86_64 |

---

## Image Version Notes

| Image Version | Description | Notes |
| - | - | - |
| 3.0.0 | MindIE 3.0.0 Release | 2026/5/6: Initial release |
| 3.0.0b2 | MindIE 3.0.0 Beta2 | 2026/4/21: Initial release; MindIE Atlas 300I DUO hardware supports dynamic LoRA load/unload, per-tensor loading, and Qwen3-VL 8B/30B-A3B models |
| 2.3.1 | MindIE 2.3.1 patch release | 2026/4/16: Initial release; fixes DSv3.1 w8a8c8 BFCL-multiturn accuracy degradation, request nesting layer limit of 10 layers, and related issues |
| 2.3.0 | MindIE 2.3.0 commercial release | 2026/1/18: Initial release |
| 2.2.RC1 | MindIE 2.2 release candidate | 2025/12/31: ras-restart script optimization; 2025/11/21: Initial release |
| 2.2.T32 | DeepSeek-V3.2 performance optimization | 2025/12/05: Performance optimization; for DSv3.2 trial only, not recommended for other models. Guide: https://www.hiascend.com/forum/thread-0278200283718182227-1-1.html |
| 2.1.RC2 | MindIE 2.1 patch release | 2025/9/21: Initial release |
| 2.1.RC1 | MindIE 2.1 release candidate | 2025/8/15: Default transformers version upgraded to 4.51.0 |
| 2.0.RC2 | MindIE 2.0 release candidate |  |
| 1.0.0 | MindIE 1.0 official release |  |

## License

See the [license information of Motor](https://gitcode.com/Ascend/MindIE-PyMotor/blob/master/LICENSE.md) included in these images.

As with all container images, pre‑installed software packages (Python, system libraries, etc.) may be subject to their own licenses.
