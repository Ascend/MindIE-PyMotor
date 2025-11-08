# coding=utf-8
# Copyright (c) 2025, HUAWEI CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import grpc


class GrpcSecureClientBase:
    def __init__(self, host: str, port: str, is_ssl_secure: bool = False,
                 root_cert: str = None, cert_file: str = None, key_file: str = None):
        self._host = host
        self._port = port
        self._is_ssl_secure = is_ssl_secure
        self._root_cert = root_cert
        self._cert_file = cert_file
        self._key_file = key_file

    def create_secure_channel(self, options: list = None):
        """create secure channel"""
        try:
            channel = grpc.secure_channel(
                f'{self._host}:{self._port}',
                self._load_ssl_credentials(),
                options=options
            )
            return channel
        except Exception as e:
            raise Exception("Failed to create secure channel.") from e

    def connect(self):
        """connect to grpc server"""
        raise NotImplementedError

    def _load_ssl_credentials(self):
        """load ssl certificate and key file"""
        try:
            if self._is_ssl_secure:
                with open(self._root_cert, 'rb') as f:
                    private_key = f.read()
                with open(self._cert_file, 'rb') as f:
                    certificate_chain = f.read()
                with open(self._key_file, 'rb') as f:
                    root_certificates = f.read()

                # create ssl credentials channel
                client_credentials = grpc.ssl_channel_credentials(
                    root_certificates=root_certificates, 
                    private_key=private_key, 
                    certificate_chain=certificate_chain)
            else:
                client_credentials = grpc.ssl_channel_credentials()

            return client_credentials
        except Exception as e:
            raise Exception("Failed to load SSL credentials.") from e