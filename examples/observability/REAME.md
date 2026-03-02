## 功能介绍

CCAE(Cluster Computing Autonomous Engine)是华为开发的一套集群自智引擎系统

Motor 推理服务可纳管至 CCAE。本组件（Observability CCAE）负责采集 Motor 的运行信息（告警、日志、inventory、metrics 等）并上报至 CCAE，便于在 CCAE 侧进行统一监控与运维管理。

## 安装步骤

### 1. 构建 Observability CCAE whl包

```bash
# 将代码下载到共享路径下
# share-path="/mnt/share/"
cd ${share-path}
git clone https://gitcode.com/Ascend/MindIE-pyMotor-private.git

# 进入 observability 目录
cd Motor/examples/observability

# 安装依赖
pip install -r requirement.txt

# 构建 wheel 包
./build.sh
```

### 2. 安装 Observability CCAE 包

在 `boot.sh` 中、`set_common_env` 之后添加以下命令：

```bash
pip install ${share-path}/Motor/examples/observability/dist/ccae-*.whl
```

### 3. 在 boot.sh 中添加启动命令

- **Controller**：在 `python3 -m motor.controller.main` 之前添加
  ```bash
  python3 -m ccae.run Controller &
  ```
- **Coordinator**：在 `python3 -m motor.coordinator.main` 之前添加
  ```bash
  python3 -m rcae.run Coordinator &
  ```

## 配置说明

### user_config.json 配置

在 `user_config.json` 中添加 CCAE 配置：

```json
{
  "motor_deploy_config": {
    "tls_config": {
      "north_tls_config": {
        "enable_tls": true,
        "ca_file": "",
        "cert_file": "",
        "key_file": "",
        "passwd_file": ""
      }
    }
  },
  "north_config": {
    "name": "ccae",
    "ip": "ccae-server-ip",
    "port": 31948
  }
}
```
