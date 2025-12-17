# MindIE-pyMotor

## 1. 产品简介

**MindIE pyMotor是面向通用大模型PD分离部署场景的推理服务化框架，通过开放、可扩展的推理服务化平台架构提供推理服务化能力，支持对接业界主流推理框架接口，满足大语言模型的高性能推理需求**。

## 2. 关键特性

| 特性       | 说明              |
| ------------ | ----------------- |
| **PD分离部署** | 模型推理的Prefill阶段和Decode阶段分别实例化部署在不同的机器资源上同时进行推理，提升推理性能，其特性介绍详情请参见[PD分离部署](https://www.hiascend.com/document/detail/zh/mindie/10RC3/mindieservice/servicedev/mindie_service0138.html)。 |

## 3. 快速开始

### 3.1 环境准备

本文档以Atlas 800I A2 推理服务器和Qwen3-0.6B模型为例，让开发者快速开始使用MindIE-pyMotor进行大模型PD分离部署和推理流程。

#### 前提条件

物理机部署场景，需要在物理机安装NPU驱动固件以及部署Docker，执行如下步骤判断是否已安装NPU驱动固件、K8s集群和部署Docker。

- 执行以下命令查看NPU驱动固件是否安装。
  
  ```bash
  npu-smi info
  ```
  
  **图1** 回显信息
  ![image](https://www.hiascend.com/doc_center/source/zh/mindie/22RC1/quickstart/figure/zh-cn_image_0000002474350016.png)

  **表1** Atlas A2 推理系列产品
  
  | 产品型号 | 参考文档 |
  | --- | --- |
  | Atlas 800I A2 | 《Atlas A2 中心推理和训练硬件 24.1.0 NPU驱动和固件安装指南》中的“[物理机安装与卸载](https://support.huawei.com/enterprise/zh/doc/EDOC1100438838/b1977c97)”章节 |
  
  
- 执行以下命令查看K8s集群是否就绪。
  
  ```bash
  kubectl get Node -A
  ```
  
  回显以下信息表示K8s集群已就绪。
  
  ```bash
  NAME         STATUS   ROLES                         AGE   VERSION
  ```
- 执行以下命令查看Docker是否已安装并启动。
  
  ```bash
  docker ps
  ```
  
  回显以下信息表示Docker已安装并启动。
  
  ```bash
  CONTAINER ID        IMAGE        COMMAND         CREATED        STATUS         PORTS           NAMES
  ```

#### 获取模型权重

1. 请先下载权重，这里以Qwen3-0.6B为例，请到官方下载权重文件并将权重文件上传至服务器任意目录（如/mnt/weight）。
2. 执行以下命令，修改权重文件权限：
   
   ```bash
   chmod -R 755 /mnt/weight
   ```

#### 获取容器镜像

进入[昇腾官方镜像仓库](https://www.hiascend.com/developer/ascendhub/detail/af85b724a7e5469ebd7ea13c3439d48f)，根据设备型号选择下载对应的MindIE镜像。

该镜像已具备模型运行所需的基础环境

### 3.2 PD分离部署

1. **将本代码仓的deployer目录上传至K8s集群的master服务器上**
2. **配置服务化参数**
   
   - 打开“user_config.json”文件
     
     ```bash
     cd deployer
     vim user_config.json
     ```
   - 按“i”进入编辑模式，根据实际情况修改“user_config.json”中的配置参数。（以下已Qwen3-0.6B为例）
     
     ```
     {
        "version": "v2.0",
        "motor_deploy_config": {
          "p_instances_num": 1,
          "d_instances_num": 1,
          "single_p_instance_pod_num": 1,
          "single_d_instance_pod_num": 1,
          "p_pod_npu_num": 1,
          "d_pod_npu_num": 1,
          "image_name": "vllm-ascend:b150_motor",
          "job_id": "mindie-pymotor",
          "hardware_type": "800I_A2"
        },
        "motor_controller_config": {},
        "motor_nodemanger_config": {},
        "motor_coordinator_config": {},
        "motor_engine_prefill_config": {
          "engine_type": "vllm",
          "model_config": {
            "model_name": "qwen3",
            "model_path": "/mnt/mindie_data/weight/Qwen3-0.6B",
            "npu_mem_utils": 0.9,
            "prefill_parallel_config": {
              "dp_size": 1,
              "tp_size": 1,
              "pp_size": 1,
              "enable_ep": false,
              "dp_rpc_port": 9000
            }
          },
          "engine_config": {
            "kv_transfer_config": {
              "kv_connector": "MooncakeLayerwiseConnector",
              "kv_buffer_device": "npu",
              "kv_role": "kv_producer",
              "kv_connector_module_path": "vllm_ascend.distributed.mooncake_layerwise_connector",
              "kv_connector_extra_config": {
                "use_ascend_direct": true,
                "prefill": {},
                "decode": {}
              }
            }
          }
        },
        "motor_engine_decode_config": {
          "engine_type": "vllm",
          "model_config": {
            "model_name": "qwen3",
            "model_path": "/mnt/mindie_data/weight/Qwen3-0.6B",
            "npu_mem_utils": 0.9,
            "decode_parallel_config": {
              "dp_size": 1,
              "tp_size": 1,
              "pp_size": 1,
              "enable_ep": false,
              "dp_rpc_port": 9000
            }
          },
          "engine_config": {
            "kv_transfer_config": {
              "kv_connector": "MooncakeLayerwiseConnector",
              "kv_buffer_device": "npu",
              "kv_role": "kv_consumer",
              "kv_connector_module_path": "vllm_ascend.distributed.mooncake_layerwise_connector",
              "kv_connector_extra_config": {
                "use_ascend_direct": true,
                "prefill": {},
                "decode": {}
              }
            }
          }
        }
      }
     ```
     
     如上的参数说明如下：
     
     | 配置项 | 取值类型 | 取值范围 | 配置说明 |
     | --- | --- | --- | --- |
     | version | string | v2.0 | 配置文件版本 |
     | p_instances_num | int | ≥1 | P实例个数 |
     | d_instances_num | int | ≥1 | D实例个数 |
     | single_p_instance_pod_num | int | ≥1 | 单个P实例所占pod容器个数 |
     | single_d_instance_pod_num | int | ≥1 | 单个D实例所占pod容器个数 |
     | p_pod_npu_num | int | ≥1 | 单个P节点pod容器所占用的NPU卡数 |
     | d_pod_npu_num | int | ≥1 | 单个D节点pod容器所占用的NPU卡数 |
     | image_name | string | 字符串 | docker加载的镜像名称，例如“vllm-ascend:b150_motor” |
     | job_id | string | 字符串 | PD分离部署任务名称，例如“mindie-pymotor” |
     | hardware_type | string | [800I_A2, 800I_A3] | 服务器硬件类型 |
     | api_key_config.enabled | bool | [true, false] | 推理接口是否校验key |
     | engine_type | string | 字符串 | 对接的推理引擎类型，例如“vllm” |
     | model_name | string | 字符串 | 模型名称，例如“qwen3_8B” |
     | model_path | string | 文件路径 | 模型权重文件所在路径 |
     | npu_mem_utils | float | 0到1之间的小数 | NPU内存使用占比上限，例如“0.95” |
     | prefill_parallel_config.dp_size | int | ≥1 | 数据并行参数 |
     | prefill_parallel_config.tp_size | int | ≥1 | 张量并行参数 |
     | prefill_parallel_config.pp_size | int | ≥1 | 流水线并行参数 |
     | prefill_parallel_config.enable_ep | bool | [true, false] | 专家并行开关 |
     | prefill_parallel_config.dp_rpc_port | int | 有效端口范围 | RPC通信的端口号 |
     | engine_config | json | 推理引擎原生参数 | 推理引擎原生参数，参考对应推理引擎的说明，直接已json对象形式填写 |
     
     
   - 配置k8s的namespace，配置namespace值为“user_config.json”中的“job_id”。
     
     ```
     kubectl create ns mindie-pymotor
     ```
3. **配置环境变量配置**
   
   - 打开“env.json”文件
     
     ```bash
     cd deployer/conf
     vim env.json
     ```
   - 按“i”进入编辑模式，根据实际情况修改“env.json”中的配置参数。
     
     ```bash
     {
        "version": "2.0.0",
        "motor_common_env": {
          "CANN_INSTALL_PATH": "/usr/local/Ascend"
        },
        "motor_controller_env": {},
        "motor_coordinator_env": {},
        "motor_engine_prefill_env": {},
        "motor_engine_decode_env": {}
     }
     ```
4. **启动服务**
  
   执行以下命令：
   
   ```
   cd deployer
   python3 deploy.py
   ```
   
5. **发送请求**
  
   执行以下命令：
   
   ```
   curl -X POST http://127.0.0.1:31015/v1/chat/completions \
   -H "Content-Type: application/json" \
   -d '{
   "model": "qwen3",
   "messages": [
   {
   "role": "system",
   "content": "You are a helpful assistant."
   },
   {
   "role": "user",
   "content": "who are you?"
   }
   ],
   "max_tokens":36,
   "stream":true
   }'
   ```
   
   返回结果如果如下，则说明尚未启动就绪：
   
   ```
   {"detail":"Service is not available"}
   ```
   
   等待一段时间后再次尝试。回显类似如下内容说明推理服务已就绪
   
   ```
   data: {"id":"17658563046856100000c836403d","object":"chat.completion.chunk","created":1765856304,"model":"qwen3","choices":[{"index":0,"delta":{"role":"assistant","content":""},"logprobs":null,"finish_reason":null}],"prompt_token_ids":null}
   
   data: {"id":"17658563046856100000c836403d","object":"chat.completion.chunk","created":1765856304,"model":"qwen3","choices":[{"index":0,"delta":{"content":"<think>"},"logprobs":null,"finish_reason":null,"token_ids":null}]}
   
   data: {"id":"17658563046856100000c836403d","object":"chat.completion.chunk","created":1765856304,"model":"qwen3","choices":[{"index":0,"delta":{"content":"\n"},"logprobs":null,"finish_reason":null,"token_ids":null}]}
   
   data: {"id":"17658563046856100000c836403d","object":"chat.completion.chunk","created":1765856304,"model":"qwen3","choices":[{"index":0,"delta":{"content":"Okay"},"logprobs":null,"finish_reason":null,"token_ids":null}]}
   
   ...
   
   data: [DONE]
   ```
