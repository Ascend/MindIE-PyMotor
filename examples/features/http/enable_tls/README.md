# motor支持https加密通信

## 1. 生成CA证书

通过`gen_ca_openssl.sh`生成ca证书，拷贝脚本到`/mnt/cert_scripts`目录下 ,并将ca证书生成到`/mnt/cert_scripts/ca`目录下，执行

```sh
bash /mnt/cert_scriptsgen_ca_openssl.sh /mnt/cert_scripts/ca
```

，在`boot.sh`中执行

## 2. 生成服务端证书

通过`gen_cert_openssl.sh`生成服务端证书，将其拷贝到`/mnt/cert_scripts`目录下，执行

### 2.1 修改`boot.sh`，新增`apply_openssl_gen_cert`函数

```sh
apply_openssl_gen_cert() {
    local ca_path=$1
    local base_cert_path=$2
    local cert_names=$3
    local ca_password=${4:-1234qwer}
    local cert_password=${5:-5678asdf}

    local gen_cert_script="${GEN_CERT_SCRIPT}"

    if [ ! -f "$gen_cert_script" ]; then
        echo "Error: Certificate generation script not found: $gen_cert_script"
        return 1
    fi

    if [ -z "$cert_names" ]; then
        echo "Error: cert_names parameter is required"
        echo "Usage: apply_openssl_gen_cert <ca_path> <base_cert_path> <cert_names> [ca_password] [cert_password]"
        echo "Example: apply_openssl_gen_cert /path/to/ca /path/to/security \"infer mgmt etcd clusterd\""
        return 1
    fi

    # 遍历每个cert_name，生成对应的证书
    for cert_name in $cert_names; do
        local cert_path="${base_cert_path}/${cert_name}"
        echo "Generating certificate for: $cert_name"
        echo "Certificate path: $cert_path"

        mkdir -p "$cert_dir"

        # 将cert_password写入到passwd.txt文件中
        cat>"${cert_path}/key_pwd.txt"<<EOF
        ${cert_password}
EOF

        cp "$ca_path/ca.pem" "$ca_path/ca.key.pem" "$cert_path"

        # 调用证书生成脚本
        bash "$gen_cert_script" "$ca_path" "$cert_path" "$ca_password" "$cert_password"

        if [ $? -ne 0 ]; then
            echo "Error: Failed to generate certificate for $cert_name"
            return 1
        fi

        echo "Certificate generated successfully for: $cert_name"
        echo "---"
    done

    echo "All certificates generated successfully!"
}

# 使用示例：
# apply_openssl_gen_cert \
#     "/usr/local/Ascend/pyMotor/conf/security/ca" \
#     "/usr/local/Ascend/pyMotor/conf/security" \
#     "infer mgmt etcd clusterd" \
#     "1234qwer" \
#     "5678asdf"
```

**函数说明：**

- `ca_path`: CA证书所在路径
- `base_cert_path`: 证书保存的基础路径，每个cert_name会在该路径下创建子目录
- `cert_names`: 证书名称列表，多个名称用空格分隔（如："infer mgmt etcd clusterd"）
- `ca_password`: CA证书密码（可选，默认：1234qwer）
- `cert_password`: 服务器证书密码（可选，默认：5678asdf）

**生成的文件：**
对于每个cert_name，会在`${base_cert_path}/${cert_name}/`目录下生成：

- `cert.pem` - 服务器证书
- `cert.key.pem` - 加密的私钥
- `decrypt.cert.key.pem` - 未加密的私钥（用于配置中的key_file）
- `cert.conf` - OpenSSL配置文件

### 2.2 增加`apply_openssl_gen_cert`函数的调用

在`boot.sh`中，需要在各个角色（prefill、decode、controller、coordinator）启动前调用`apply_openssl_gen_cert`函数生成证书。

**调用位置说明：**

1. **prefill和decode角色**：在`if [ "$ROLE" = "prefill" ] || [ "$ROLE" = "decode" ]; then`之后，在启动命令之前调用
2. **controller角色**：在`if [ "$ROLE" = "controller" ]; then`之后，在启动命令之前调用
3. **coordinator角色**：在`if [ "$ROLE" == "coordinator" ]; then`之后，在启动命令之前调用

**示例代码：**

```sh
# 在boot.sh中添加证书生成逻辑

# 定义证书路径和名称（根据实际部署情况调整）
CA_PATH="/mnt/cert_scripts/ca"

# 容器内的路径
BASE_CERT_PATH="/usr/local/Ascend/pyMotor/conf/security"
CERT_NAMES="infer mgmt etcd grpc"
GEN_CERT_SCRIPT="/mnt/cert_scripts/openssl_gen_cert.sh"

# prefill和decode角色启动前生成证书
if [ "$ROLE" = "prefill" ] || [ "$ROLE" = "decode" ]; then
    # 生成证书
    apply_openssl_gen_cert "$CA_PATH" "$BASE_CERT_PATH" "$CERT_NAMES"

    # Use hccl_tools.py to generate ranktable.json
    # ... 其他初始化代码 ...

    # Nodemanager start command
    python3 -m motor.node_manager.main &
    # ... 其他代码 ...
fi

# controller角色启动前生成证书
if [ "$ROLE" = "controller" ]; then
    # 生成证书
    apply_openssl_gen_cert "$CA_PATH" "$BASE_CERT_PATH" "$CERT_NAMES"

    # ... 其他初始化代码 ...

    # Controller start command
    python3 -m motor.controller.main --config $USER_CONFIG_PATH
fi

# coordinator角色启动前生成证书
if [ "$ROLE" == "coordinator" ]; then
    # 生成证书
    apply_openssl_gen_cert "$CA_PATH" "$BASE_CERT_PATH" "$CERT_NAMES"

    # ... 其他初始化代码 ...

    # Coordinator start command
    python3 -m motor.coordinator.main
fi
```

**注意事项：**

- 证书生成应在服务启动之前完成，确保服务启动时证书文件已存在
- 如果证书已存在，脚本会提示是否覆盖，可根据实际需求选择
- 证书路径和名称需要与`user_config.json`中的配置保持一致

## 3. 配置证书

在`user_config.json`中增加如下配置项，`enable`设置成`true`，同时证书路径要配置正确

> 限制：当前只支持解密的`key_file`, `passwd_file`和`tls_crl`为预留字段

```user_config.json
{
  "motor_deploy_config": {
    "tls_config": {
      "infer_tls_config": {
        "enable_tls": true,
        "ca_file": "/usr/local/Ascend/pyMotor/conf/security/infer/ca.pem",
        "cert_file": "/usr/local/Ascend/pyMotor/conf/security/infer/cert.pem",
        "key_file": "/usr/local/Ascend/pyMotor/conf/security/infer/decrypt.cert.key.pem",
        "passwd_file": "",
        "tls_crl": ""
      },
      "mgmt_tls_config": {
        "enable_tls": true,
        "ca_file": "/usr/local/Ascend/pyMotor/conf/security/mgmt/ca.pem",
        "cert_file": "/usr/local/Ascend/pyMotor/conf/security/mgmt/cert.pem",
        "key_file": "/usr/local/Ascend/pyMotor/conf/security/mgmt/decrypt.cert.key.pem",
        "passwd_file": "",
        "tls_crl": ""
      },
      "etcd_tls_config": {
        "enable_tls": true,
        "ca_file": "/usr/local/Ascend/pyMotor/conf/security/etcd/ca.pem",
        "cert_file": "/usr/local/Ascend/pyMotor/conf/security/etcd/cert.pem",
        "key_file": "/usr/local/Ascend/pyMotor/conf/security/etcd/decrypt.cert.key.pem",
        "passwd_file": "",
        "tls_crl": ""
      },
      "grpc_tls_config": {
        "enable_tls": true,
        "ca_file": "/usr/local/Ascend/pyMotor/conf/security/grpc/ca.pem",
        "cert_file": "/usr/local/Ascend/pyMotor/conf/security/grpc/cert.pem",
        "key_file": "/usr/local/Ascend/pyMotor/conf/security/grpc/decrypt.cert.key.pem",
        "passwd_file": "",
        "tls_crl": ""
      }
    }
  }
}
```
