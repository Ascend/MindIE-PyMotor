# ras_starter MindIE-pyMotor 中的部署指南

## 概述

出于PD实例可靠性增强的目的，**MindIE-pyMotor** 提供一个参考脚本 **ras_starter** 进行大EP服务的健康状态监控和快速重启，**ras_starter** 启动后，当软件故障发生服务不可用时，该脚本可在20分钟内检测并自动重拉。本文档提供快速部署 **ras_starter** 的完整配置部署示例。

适用范围说明：
- 适用机器：Atlas 800I A2/A3，Atlas 900I A3 机器
- 适用场景：大EP出现挂死等服务不可用且不可自恢复的场景


## 1. 准备软件或数据

### 1.1 前提条件

- **硬件**: Atlas 800I A3 推理服务器
- **软件**: 
  - NPU 驱动和固件已安装 (`npu-smi info` 可正常显示)
  - Kubernetes 集群就绪 (`kubectl get Node -A`)
  - Docker 已安装并运行 (`docker ps`)

### 1.2 获取ras_starter脚本及其依赖文件

从 https://gitcode.com/Ascend/MindIE-pyMotor-private/tree/master/examples/fault_tolerance/ras_starter/ras_starter.py 获取最新的ras_starter脚本

## 2. 部署步骤

2.1 登陆master节点，将 **准备软件或数据** 下载的 "ras_starter.py" 脚本上传到 “examples/deployer” 路径下。

2.2 （可选）若用户环境变量配置了Coordinator的证书校验，还需要将对应证书放置在 “examples/deployer/security” 目录，具体调用见下述代码：
def load_cert():
    context = create_default_context(Purpose.SERVER_AUTH)
    cert_file_map = {
        "ca_cert": "./security/ca.pem",
        "tls_cert": "./security/cert.pem",
        "tls_key": "./security/cert.key.pem",
    }

并需要用户在首次启动脚本时手动输入解密tls_key文件的密码；

2.3 执行以下命令拉起ras_starter脚本进行后台监控：
(nohup) python3 ras_starter.py --user_config_path ./user_config.json

若预期单独记录ras_starter日志，则执行
(nohup) python3 ras_starter.py --user_config_path ./user_config.json > ras_starter_result.txt 2>&1 &

由于 ras_starter 的定位为大 EP 的健康伴侣，与大 EP 的启动执行脚本 deploy.py 解耦。若 ras_starter 先启动，用户需在 `examples/deployer` 目录下手动执行 `python3 deploy.py --config_dir ../infer_engines/vllm`（或使用 `--user_config_path` 与 `--env_config_path` 指定配置文件）拉起服务后，ras_starter 才进入监控流程，否则将一直等待服务拉起并 ready。同时，若 ras_starter 监控过程中，用户手动执行 `bash delete.sh` 删除服务，ras_starter 将无法正确获取服务状态，此时需手动重新拉起服务，ras_starter 才将继续监控。

