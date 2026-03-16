#!/usr/bin/env bash

# Generate CA certificate script
# Usage: ./gen_ca.sh <ca_path> [ca_password]
# Example: ./gen_ca.sh /path/to/ca 1234qwer

set -e

# Check parameters
if [ -z "$1" ]; then
    echo "Error: Please provide CA certificate save path"
    echo "Usage: $0 <ca_path> [ca_password]"
    echo "Example: $0 /path/to/ca 1234qwer"
    exit 1
fi

ca_path=$1
ca_pwd=${2:-1234qwer}

# Create directory (if not exists)
ca_dir=$(dirname "$ca_path")
if [ ! -d "$ca_dir" ]; then
    mkdir -p "$ca_dir"
    chmod 700 "$ca_dir"
fi

ca_config_file="${ca_path}/ca.conf"
ca_key_file="${ca_path}/ca.key.pem"
ca_cert_file="${ca_path}/ca.pem"

# Check if files already exist
if [ -f "$ca_key_file" ] || [ -f "$ca_cert_file" ]; then
    echo "Warning: CA certificate or private key file already exists"
    read -p "Overwrite? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Operation cancelled"
        exit 1
    fi
fi

echo "Generating CA private key..."
# Generate CA private key (RSA 3072 bits, password encrypted)
openssl genrsa -aes256 -out "$ca_key_file" -passout pass:"$ca_pwd" 3072

echo "Generating CA certificate..."

cat > "$ca_config_file" <<EOF
[req]
distinguished_name = req_distinguished_name
prompt = no

[req_distinguished_name]
C = CN
L = Shanghai
O = Huawei
OU = Ascend
CN = MindIE

[v3_ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,digitalSignature,keyCertSign,cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always,issuer:always
EOF

# Generate CA certificate (self-signed, valid for 365 days, SHA256 signature algorithm)
openssl req -new -x509 -days 365 -key "$ca_key_file" -out "$ca_cert_file" \
    -passin pass:"$ca_pwd" \
    -sha256 \
    -extensions v3_ca \
    -config "$ca_config_file"

# Set file permissions
chmod 600 "$ca_key_file"
chmod 644 "$ca_cert_file"

echo "CA certificate generated successfully!"
echo "CA private key: $ca_key_file"
echo "CA certificate: $ca_cert_file"
echo "Password: $ca_pwd"