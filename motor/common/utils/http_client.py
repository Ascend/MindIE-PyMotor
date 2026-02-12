#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

from enum import Enum
from ssl import Purpose
from typing import Any, Optional

import httpx
import requests
from requests import Response
from requests.adapters import HTTPAdapter

from motor.common.utils.cert_util import CertUtil
from motor.config.tls_config import TLSConfig


class ConnectionMode(Enum):
    SHORT = "short"
    LONG = "long"


class SafeHTTPSClient:
    def __init__(self,
                 address: str,
                 tls_config: Optional[TLSConfig] = None,
                 mode: ConnectionMode = ConnectionMode.SHORT,
                 timeout: float = 5):

        self.protocol = 'http://'
        self.timeout = timeout
        self.session = requests.Session()

        if tls_config and tls_config.enable_tls:
            self.protocol = 'https://'
            ssl_context = CertUtil.create_ssl_context(tls_config=tls_config, purpose=Purpose.CLIENT_AUTH)

            adapter = HTTPAdapter()
            adapter.init_poolmanager(
                connections=10,
                ssl_context=ssl_context,
                maxsize=10,
            )
            self.session.mount(self.protocol, adapter)
        self.base_url = self.protocol + address.rstrip('/')

        # set https headers
        self.session.headers.update({
            'User-Agent': 'Secure-HTTPS-Client/1.0',
            'Accept': 'application/json',
            # Default: close = short connection; else keep alive = long connection
            'Connection': 'close' if mode == ConnectionMode.SHORT else 'Keep-Alive',
            'Content-Type': 'application/json'
        })

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def request(self, method: str, endpoint: str, data: dict | None = None,
                params: dict | None = None) -> dict[str, Any]:
        resp = self._request(method, endpoint, data, params)
        return resp.json() if resp else None

    def get(self, endpoint: str, params: dict | None = None) -> dict[str, Any]:
        return self.request('GET', endpoint, params=params)

    def do_get(self, endpoint: str, params: dict | None = None) -> Response:
        return self._request('GET', endpoint, params=params)

    def post(self, endpoint: str, data: dict | None = None) -> dict[str, Any]:
        return self.request('POST', endpoint, data=data)

    def do_post(self, endpoint: str, data: dict | None = None) -> Response:
        return self._request('POST', endpoint, data=data)

    def close(self) -> None:
        self.session.close()

    def _request(self, method: str, endpoint: str, data: dict | None = None,
                 params: dict | None = None) -> Response:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            response = self.session.request(
                method=method.upper(),
                url=url,
                json=data,
                params=params,
                timeout=self.timeout
            )

            response.raise_for_status()
            return response
        except requests.exceptions.SSLError as e:
            raise Exception(f"SSL verify failed: {e}") from e
        except requests.exceptions.HTTPError as e:
            raise Exception(f"http response error {e.response.status_code}, {e.response.text}") from e
        except Exception as e:
            raise Exception(f"send request {url} error: {e}") from e


class AsyncSafeHTTPSClient():
    
    @staticmethod
    def create_client(
            address: str,
            tls_config: Optional[TLSConfig] = None,
            **client_kwargs
    ):

        verify = True

        if tls_config and tls_config.enable_tls:
            verify = CertUtil.create_ssl_context(tls_config=tls_config, purpose=Purpose.CLIENT_AUTH)
            base_url = f"https://{address}"
        else:
            base_url = f"http://{address}"
        return httpx.AsyncClient(base_url=base_url,
                                    verify=verify,
                                    **client_kwargs)
