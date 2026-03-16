# 部署方式配置说明（deployment_backend）

## 概述

`motor_deploy_config.deployment_backend` 用于选择 MindIE PyMotor 的部署方式，决定 deploy.py 如何生成并应用 Kubernetes 资源。

**默认行为**：默认的 `user_config.json` 中不包含 `deployment_backend` 字段，此时使用 CRD 方式（`infer_service_set`）部署。如需使用传统多 YAML 方式，在 `motor_deploy_config` 中显式配置 `"deployment_backend": "multi_deployment"` 即可。

## 配置项

| 取值 | 说明 |
|------|------|
| `infer_service_set` | 默认方式。生成单个 `infer_service.yaml`（含 RBAC + InferServiceSet），由 CRD controller 统一拉起 controller、coordinator、prefill、decode 等 pod。需集群预先安装 InferServiceSet CRD。 |
| `multi_deployment` | 传统方式。生成 controller、coordinator、engine_*、kv_pool 等多个独立 YAML，分别 apply。无 CRD 依赖。 |

不配置时默认为 `infer_service_set`。

## 配置示例

在 `user_config.json` 的 `motor_deploy_config` 中。使用 CRD 方式时可不配置 `deployment_backend`，也可显式配置为 `"deployment_backend": "infer_service_set"`。

使用 multi_deployment 时需显式添加：

```json
{
  "motor_deploy_config": {
    "deployment_backend": "multi_deployment",
    ...
  }
}
```

## 重要约束

- **首次部署**：从 `user_config.json` 读取 `deployment_backend`，按所选方式部署。
- **扩缩容（`--update_instance_num`）**：以集群 ConfigMap 中已保存的 baseline 为准，**不允许**在 user_config 中修改 `deployment_backend`，否则报错。
- **刷新 ConfigMap（`--update_config`）**：同样以 baseline 为准，**不允许**修改 `deployment_backend`，否则报错。

如需切换部署方式，需先删除当前部署，再修改 `deployment_backend` 后重新执行全量部署。
