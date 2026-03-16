# DeepSeek 模型在 MindIE-pyMotor 中的部署指南

## 概述

本文档以2.3.RC1.B132 版本为例，提供 **DeepSeek-V3.1-Terminus** 模型在 **Atlas 800I A3** 推理服务器上使用 **MindIE-pyMotor** 进行 PD 分离部署的完整配置示例，旨在帮助用户快速部署DeepSeek系列模型。

适用范围说明：
- 适用模型：DeepSeek-V3.1-Terminus 模型
- 适用机器：Atlas 800I A3 机器
- 适用场景：PD 分离场景
- 适用版本：2.3.RC1.B132 版本
- 适用上下文长度：73K

## 1. 环境准备

### 1.1 前提条件

- **硬件**: Atlas 800I A3 推理服务器
- **软件**: 
  - NPU 驱动和固件已安装 (`npu-smi info` 可正常显示)
  - Kubernetes 集群就绪 (`kubectl get Node -A`)
  - Docker 已安装并运行 (`docker ps`)
- **模型权重**: DeepSeek-V3.1-Terminus-w8a8-QuaRot-lfs 权重文件

### 1.2 获取模型权重

1. 下载 DeepSeek-V3.1 权重文件
2. 将权重文件上传至服务器目录，如 `/mnt/weight/`
3. 设置权限：
   ```bash
   chmod -R 755 /mnt/weight/
   ```

### 1.3 获取容器镜像

从 [昇腾官方镜像仓库](https://www.hiascend.com/developer/ascendhub/detail/af85b724a7e5469ebd7ea13c3439d48f) 下载适用于 A3 服务器的 MindIE 镜像：
```
mindie-motor-vllm:dev-2.3.RC1.B132-800I-A3-py311-Ubuntu24.04-lts-aarch64
```

## 2. DeepSeek-V3.1 专用配置

### 2.1 环境变量配置 (`env.json`)

以下是 DeepSeek-V3.1 模型在 MindIE-pyMotor 中运行所需的环境变量配置：

```json
{
  "version": "2.0.0",
  "motor_common_env": {
    "CANN_INSTALL_PATH": "/usr/local/Ascend"
  },
  "motor_engine_prefill_env": {
    "VLLM_RPC_TIMEOUT": 3600000,
    "VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS": 30000,
    "HCCL_EXEC_TIMEOUT": 204,
    "HCCL_CONNECT_TIMEOUT": 600,
    "HCCL_ENTRY_LOG_ENABLE_": 1,
    "OMP_PROC_BIND": false,
    "OMP_NUM_THREADS": 10,
    "PYTORCH_NPU_ALLOC_CONF": "expandable_segments:True",
    "VLLM_ASCEND_ENABLE_MLAPO": 1,
    "HCCL_BUFFSIZE": 256,
    "TASK_QUEUE_ENABLE": 1,
    "HCCL_OP_EXPANSION_MODE": "AIV",
    "VLLM_USE_V1": 1,
    "ASCEND_BUFFER_POOL": "4:8"
  },
  "motor_engine_decode_env": {
    "HCCL_INTRA_PCIE_ENABLE_": 1,
    "HCCL_INTRA_ROCE_ENABLE_": 0,
    "VLLM_RPC_TIMEOUT": 3600000,
    "VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS": 30000,
    "HCCL_EXEC_TIMEOUT": 204,
    "HCCL_CONNECT_TIMEOUT": 600,
    "HCCL_ENTRY_LOG_ENABLE_": 1,
    "PYTORCH_NPU_ALLOC_CONF": "expandable_segments:True",
    "VLLM_ASCEND_ENABLE_MLAPO": 1,
    "HCCL_BUFFSIZE": 1200,
    "TASK_QUEUE_ENABLE": 1,
    "HCCL_OP_EXPANSION_MODE": "AIV",
    "OMP_PROC_BIND": "false",
    "OMP_NUM_THREADS": 10,
    "ASCEND_BUFFER_POOL": "4:8",
    "VLLM_USE_V1": 1
  }
}
```

### 2.2 服务化参数配置 (`user_config.json`)

以下是 DeepSeek-V3.1 模型在 MindIE-pyMotor 中运行所需的服务化参数配置，可重点关注 engine_config：
```json
{
  "version": "v2.0",
  "motor_deploy_config": {
    "p_instances_num": 2,
    "d_instances_num": 1,
    "single_p_instance_pod_num": 1,
    "single_d_instance_pod_num": 2,
    "p_pod_npu_num": 16,
    "d_pod_npu_num": 16,
    "image_name": "mindie-motor-vllm:dev-2.3.RC1.B132-800I-A3-py311-Ubuntu24.04-lts-aarch64",
    "job_id": "mindie-motor",
    "hardware_type": "800I_A3",
    "weight_mount_path": "/mnt/weight/",
    "tls_config": {
    }
  },
  "motor_controller_config": {
    "standby_config": {
      "enable_master_standby": false
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
      "model_name": "dsv3",
      "model_path": "/mnt/share/weights/DeepSeek-V3.1-Terminus-w8a8-QuaRot-lfs",
      "npu_mem_utils": 0.9,
      "prefill_parallel_config": {
        "dp_size": 2,
        "tp_size": 8,
        "pp_size": 1,
        "enable_ep": true,
        "dp_rpc_port": 9000
      }
    },
    "engine_config": {
      "api-server-count": 1,
      "enforce-eager": true,
      "trust-remote-code": true,
      "max_model_len": 73000,
      "max-num-batched-tokens": 16384,
      "max-num-seqs": 16,
      "quantization": "ascend",
      "seed": 1024,
      "no-enable-prefix-caching": false,
      "distributed-executor-backend": "mp",
      "speculative-config": {
        "num_speculative_tokens": 1,
        "method": "deepseek_mtp"
      },
      "additional-config": {
        "recompute_scheduler_enable": true
      },
      "kv_transfer_config": {
        "kv_connector": "MooncakeLayerwiseConnector",
        "kv_buffer_device": "npu",
        "kv_role": "kv_producer",
        "kv_parallel_size": 1,
        "kv_port": "20001",
        "engine_id": "0",
        "kv_rank": 0,
        "kv_connector_module_path": "vllm_ascend.distributed.mooncake_layerwise_connector",
        "kv_connector_extra_config": {
          "use_ascend_direct": true,
          "prefill": {
            "dp_size": 2,
            "tp_size": 8
          },
          "decode": {
            "dp_size": 32,
            "tp_size": 1
          }
        }
      }
    }
  },
  "motor_engine_decode_config": {
    "engine_type": "vllm",
    "model_config": {
      "model_name": "dsv3",
      "model_path": "/mnt/share/weights/DeepSeek-V3.1-Terminus-w8a8-QuaRot-lfs",
      "npu_mem_utils": 0.9,
      "decode_parallel_config": {
        "dp_size": 32,
        "tp_size": 1,
        "pp_size": 1,
        "enable_ep": true,
        "dp_rpc_port": 9000
      }
    },
    "engine_config": {
      "api-server-count": 1,
      "trust-remote-code": true,
      "max_model_len": 73000,
      "max-num-batched-tokens": 256,
      "max-num-seqs": 24,
      "quantization": "ascend",
      "seed": 1024,
        "no-enable-prefix-caching": false,
        "compilation_config": {
        "cudagraph_capture_sizes": [4, 8, 16, 32, 48, 64, 80, 96],
        "cudagraph_mode": "FULL_DECODE_ONLY"
      },
      "distributed-executor-backend": "mp",
      "speculative-config": {
        "num_speculative_tokens": 3,
        "method": "deepseek_mtp"
      },
      "additional-config": {
        "recompute_scheduler_enable": true,
        "lm_head_tensor_parallel_size":16
      },
      "kv_transfer_config": {
        "kv_connector": "MooncakeLayerwiseConnector",
        "kv_buffer_device": "npu",
        "kv_role": "kv_consumer",
        "kv_parallel_size": 1,
        "kv_port": "20001",
        "engine_id": "0",
        "kv_rank": 0,
        "kv_connector_module_path": "vllm_ascend.distributed.mooncake_layerwise_connector",
        "kv_connector_extra_config": {
          "use_ascend_direct": true,
          "prefill": {
            "dp_size": 2,
            "tp_size": 8
          },
          "decode": {
            "dp_size": 32,
            "tp_size": 1
          }
        }
      }
    }
  },
  "kv_cache_pool_config": {
    "metadata_server": "P2PHANDSHAKE",
    "protocol": "ascend",
    "device_name": "",
    "alloc_in_same_node": true,
    "global_segment_size": "1GB"
  }
}
```

#### 部署资源配置
```json
{
  "motor_deploy_config": {
    "p_instances_num": 2,      // Prefill 实例数
    "d_instances_num": 1,      // Decode 实例数
    "p_pod_npu_num": 16,       // 每个 Prefill Pod 16个NPU
    "d_pod_npu_num": 16,       // 每个 Decode Pod 16个NPU
    "hardware_type": "800I_A3" // A3服务器
  }
}
```

#### Prefill 阶段并行配置
```json
{
  "motor_engine_prefill_config": {
    "model_config": {
      "prefill_parallel_config": {
        "dp_size": 2,   // 数据并行
        "tp_size": 8,   // 张量并行
        "pp_size": 1,   // 流水线并行
        "enable_ep": true  // 启用专家并行
      }
    },
    "engine_config": {
      "max_model_len": 73000,          // 73K上下文
      "max-num-batched-tokens": 16384, // 批次token数
      "quantization": "ascend",
      "speculative-config": {          // DeepSeek MTP配置
        "num_speculative_tokens": 1,
        "method": "deepseek_mtp"
      }
    }
  }
}
```

#### Decode 阶段并行配置
```json
{
  "motor_engine_decode_config": {
    "model_config": {
      "decode_parallel_config": {
        "dp_size": 32,  // 数据并行
        "tp_size": 1,   // 张量并行
        "enable_ep": true
      }
    },
    "engine_config": {
      "max-num-batched-tokens": 256,
      "max-num-seqs": 24,
      "compilation_config": {
        "cudagraph_capture_sizes": [4, 8, 16, 32, 48, 64, 80, 96],
        "cudagraph_mode": "FULL_DECODE_ONLY"
      },
      "additional-config": {
        "lm_head_tensor_parallel_size": 16  // LM头并行
      }
    }
  }
}
```

## 3. 部署步骤

部署请参考 [部署文档](https://gitcode.com/Ascend/MindIE-pyMotor-private/blob/master/README.md)