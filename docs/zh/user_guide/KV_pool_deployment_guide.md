# KV池化能力部署

## 1. 特性介绍

pyMotor KV池化能力基于vllm-ascend本身池化能力，能力介绍和环境依赖可参考[vllm-ascend池化文档](https://docs.vllm.ai/projects/ascend/zh-cn/main/user_guide/feature_guide/kv_pool.html)。

通过修改user_config.json配置文件后即可通过deploy.py脚本完成服务部署。

## 2. 部署流程

pyMotor开启KV池化能力只需修改user_config.json配置文件后，通过deploy.py脚本即可完成服务部署，具体流程如下。
> 注意：开启池化能力前请参考[pyMotor快速开始](../../../README.md)，确保环境能正常完成基础的服务部署。

### 2.1 应用补丁

由于vllm代码的layerwise KV-cache传输叠加KV池化存在推理bug，需要应用vllm_multi_connector.patch补丁，具体操作步骤可参考[pyMotor应用补丁](../../../patch/README.md)。

### 2.2 配置user_config.json

同[vllm-ascend池化文档](https://docs.vllm.ai/projects/ascend/zh-cn/main/user_guide/feature_guide/kv_pool.html)中kv-transfer-config配置，在user_config.json配置文件中需要调整P/D实例kv_transfer_config内的配置，以[pyMotor快速开始](../../../README.md)中实例uesr_config.json为参考基线，适配打开KV池化后的配置文件示例如下

```json
{
  "version": "v2.0",
  "motor_deploy_config": {
    "p_instances_num": 1,
    "d_instances_num": 1,
    "single_p_instance_pod_num": 1,
    "single_d_instance_pod_num": 1,
    "p_pod_npu_num": 4,
    "d_pod_npu_num": 4,
    "image_name": "mindie-motor-vllm:dev-2.2.RC1.B153-800I-A3-py311-Ubuntu24.04-lts-aarch64",
    "job_id": "mindie-motor",
    "hardware_type": "800I_A2",
    "env_path": "./conf/env.json",
    "weight_mount_path": "/mnt/weight/"
  },
  "motor_controller_config": {
    "standby_config": {
      "enable_master_standby": false
    },
    "fault_tolerance_config": {
      "enable_fault_tolerance": true,
      "enable_scale_p2d": true,
      "enable_lingqu_network_recover": true
    }
  },
  "motor_coordinator_config": {
    "standby_config": {
      "enable_master_standby": false
    },
    "request_limit": {
      "single_node_max_requests": 4096,
      "max_requests": 10000
    }
  },
  "motor_nodemanger_config": {
  },
  "motor_engine_prefill_config": {
    "engine_type": "vllm",
    "model_config": {
      "model_name": "qwen3-8B",
      "model_path": "/mnt/weight/qwen3_8B",
      "npu_mem_utils": 0.9,
      "prefill_parallel_config": {
        "dp_size": 2,
        "tp_size": 2,
        "pp_size": 1,
        "enable_ep": false,
        "dp_rpc_port": 9000
      }
    },
    "engine_config": {
      "enforce-eager": true,
      "max_model_len": 2048,
      "kv_transfer_config": {
        "kv_connector": "MultiConnector",
        "kv_role": "kv_producer",
        "kv_connector_extra_config": {
          "use_layerwise": true,
          "connectors": [
            {
              "kv_connector": "MooncakeLayerwiseConnector",
              "kv_role": "kv_producer",
              "kv_port": "20001",
              "kv_connector_extra_config": {
                  "send_type": "PUT",
                  "prefill": {
                    "dp_size": 2,
                    "tp_size": 2
                  },
                  "decode": {
                    "dp_size": 2,
                    "tp_size": 2
                  }
              }
            },
            {
              "kv_connector": "AscendStoreConnector",
              "kv_role": "kv_producer",
              "lookup_rpc_port": "0",
              "backend": "mooncake"
            }
          ]
        }
      }
    }
  },
  "motor_engine_decode_config": {
    "engine_type": "vllm",
    "model_config": {
      "model_name": "qwen3-8B",
      "model_path": "/mnt/weight/qwen3_8B",
      "npu_mem_utils": 0.9,
      "decode_parallel_config": {
        "dp_size": 2,
        "tp_size": 2,
        "pp_size": 1,
        "enable_ep": false,
        "dp_rpc_port": 9000
      }
    },
    "engine_config": {
      "max_model_len": 2048,
      "kv_transfer_config": {
        "kv_connector": "MultiConnector",
        "kv_role": "kv_consumer",
        "kv_connector_extra_config": {
          "use_layerwise": true,
          "connectors": [
            {
              "kv_connector": "MooncakeLayerwiseConnector",
              "kv_role": "kv_consumer",
              "kv_port": "20002",
              "kv_connector_extra_config": {
                  "send_type": "PUT",
                  "prefill": {
                    "dp_size": 2,
                    "tp_size": 2
                  },
                  "decode": {
                    "dp_size": 2,
                    "tp_size": 2
                  }
              }
            },
            {
              "kv_connector": "AscendStoreConnector",
              "kv_role": "kv_consumer",
              "lookup_rpc_port": "1",
              "backend": "mooncake"
            }
          ]
        }
      }
    }
  },
  "kv_cache_pool_config": {
    "metadata_server": "P2PHANDSHAKE",
    "protocol": "ascend",
    "device_name": "",
    "global_segment_size": "1GB"
  }
}
```

### 2.3 部署服务

通过deploy.py脚本部署服务。

```bash
python deploy.py
```
