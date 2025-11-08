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
import os
from typing import Any
import requests


class SafeHTTPSClient:
    def __init__(self,
                 base_url: str,
                 cert_file: str | None = None,
                 key_file: str | None = None,
                 ca_file: str | None = None,
                 timeout: float = 5):

        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.session = requests.Session()

        # set https cert and CA cert
        self.cert = None
        self.verify = True

        if cert_file and key_file:
            if not os.path.exists(cert_file) or not os.path.exists(key_file):
                raise FileNotFoundError("can not find cert file or key file.")
            self.cert = (cert_file, key_file)

        if ca_file:
            if not os.path.exists(ca_file):
                raise FileNotFoundError("can not find CA cert file.")
            self.verify = ca_file

        # set https headers
        self.session.headers.update({
            'User-Agent': 'Secure-HTTPS-Client/1.0',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        })

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def request(self, method: str, endpoint: str, data: dict | None = None,
                params: dict | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        try:
            response = self.session.request(
                method=method.upper(),
                url=url,
                json=data,
                params=params,
                cert=self.cert,
                verify=self.verify,
                timeout=self.timeout
            )

            response.raise_for_status()
            return response.json()
        except requests.exceptions.SSLError as e:
            raise Exception(f"SSL verify failed: {e}") from e
        except requests.exceptions.HTTPError as e:
            raise Exception(f"http response error {e.response.status_code}, {e.response.text}") from e
        except Exception as e:
            raise Exception("send request error.") from e

    def get(self, endpoint: str, params: dict | None = None) -> dict[str, Any]:
        return self.request('GET', endpoint, params=params)

    def post(self, endpoint: str, data: dict | None = None) -> dict[str, Any]:
        return self.request('POST', endpoint, data=data)

    def close(self) -> None:
        self.session.close()
