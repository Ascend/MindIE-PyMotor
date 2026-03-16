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

import asyncio
import hashlib
import threading
from enum import Enum
from ssl import Purpose
from typing import Any, Optional

import httpx
import requests
from requests import Response
from requests.adapters import HTTPAdapter

from motor.common.utils.cert_util import CertUtil
from motor.common.utils.logger import get_logger
from motor.common.utils.singleton import ThreadSafeSingleton
from motor.config.tls_config import TLSConfig

logger = get_logger(__name__)


class ConnectionMode(Enum):
    SHORT = "short"
    LONG = "long"


class SafeHTTPSClient:
    def __init__(self,
                 address: str,
                 protocol: str = 'http://',
                 tls_config: Optional[TLSConfig] = None,
                 mode: ConnectionMode = ConnectionMode.SHORT,
                 timeout: float = 5):

        self.protocol = protocol
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


class AsyncSafeHTTPSClient:
    """Async HTTP client factory for HTTPClientPool to create httpx.AsyncClient."""

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

        if 'limits' not in client_kwargs:
            client_kwargs['limits'] = httpx.Limits(
                max_connections=None,
                max_keepalive_connections=None,
            )

        return httpx.AsyncClient(base_url=base_url,
                                    verify=verify,
                                    **client_kwargs)


class HTTPClientPool(ThreadSafeSingleton):
    """
    HTTP client pool (singleton). Caches httpx.AsyncClient by endpoint and TLS
    config to avoid creating a new client per request.
    """

    def __init__(self):
        if hasattr(self, '_initialized'):
            return

        self._lock = threading.Lock()
        self._client_pool: dict[str, httpx.AsyncClient] = {}
        self._tls_hash_cache: dict[int, str] = {}
        self._initialized = True

    async def get_client(
        self,
        ip: str,
        port: str,
        tls_config: Optional[TLSConfig] = None,
        **client_kwargs
    ) -> httpx.AsyncClient:
        """Get or create HTTP client (thread-safe, double-checked locking)."""
        pool_key = self._get_pool_key(ip, port, tls_config)

        client = self._client_pool.get(pool_key)
        if client and not client.is_closed:
            return client

        old_client_to_close: httpx.AsyncClient | None = None
        with self._lock:
            client = self._client_pool.get(pool_key)
            if client and not client.is_closed:
                return client

            address = f"{ip}:{port}"
            client = AsyncSafeHTTPSClient.create_client(
                address=address,
                tls_config=tls_config,
                **client_kwargs
            )

            if pool_key in self._client_pool:
                old_client_to_close = self._client_pool[pool_key]
                if old_client_to_close and old_client_to_close.is_closed:
                    old_client_to_close = None

            self._client_pool[pool_key] = client

        await self._safe_aclose(old_client_to_close)
        return client
    
    async def close_client(self, ip: str, port: str, tls_config: Optional[TLSConfig] = None) -> None:
        """Close and remove the client for the given endpoint (thread-safe)."""
        pool_key = self._get_pool_key(ip, port, tls_config)
        with self._lock:
            client = self._client_pool.pop(pool_key, None)
        await self._safe_aclose(client)
    
    async def close_all(self) -> None:
        """Close all cached clients (thread-safe). Typically called on process shutdown."""
        with self._lock:
            to_close = list(self._client_pool.values())
            self._client_pool.clear()
        for client in to_close:
            await self._safe_aclose(client)
    
    async def warmup_clients(
        self,
        endpoints: list[tuple[str, str]],  # [(ip, port), ...]
        tls_config: Optional[TLSConfig] = None,
        **client_kwargs
    ) -> dict[str, bool]:
        """Warm up clients for the given endpoints (async batch create)."""
        results = {}
        tasks = []

        for ip, port in endpoints:
            pool_key = self._get_pool_key(ip, port, tls_config)
            existing_client = self._client_pool.get(pool_key)
            if existing_client and not existing_client.is_closed:
                results[pool_key] = True
                continue

            task = self._warmup_single_client(ip, port, tls_config, pool_key, **client_kwargs)
            tasks.append((pool_key, task))

        if tasks:
            warmup_results = await asyncio.gather(
                *[task for _, task in tasks],
                return_exceptions=True
            )
            for (pool_key, _), result in zip(tasks, warmup_results):
                results[pool_key] = not isinstance(result, Exception)
        
        return results

    def get_pool_keys_for_endpoints(
        self,
        endpoints: list[tuple[str, str]],
        tls_config: Optional[TLSConfig] = None,
    ) -> set[str]:
        """Return pool_key set for given endpoints and TLS config (for cleanup)."""
        return {self._get_pool_key(ip, str(port), tls_config) for ip, port in endpoints}

    async def cleanup_unused_clients(
        self,
        active_endpoints: set[str]  # set of pool_key
    ) -> int:
        """Close clients not in active_endpoints (pool_key set); returns count closed."""
        with self._lock:
            to_remove = []
            for pool_key, client in list(self._client_pool.items()):
                if pool_key not in active_endpoints:
                    to_remove.append((pool_key, client))
            for pool_key, _ in to_remove:
                del self._client_pool[pool_key]

        cleanup_tasks = []
        for _pool_key, client in to_remove:
            if client and not client.is_closed:
                cleanup_tasks.append(client.aclose())
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        return len(to_remove)

    def _get_pool_key(self, ip: str, port: str, tls_config: Optional[TLSConfig] = None) -> str:
        """Build pool key from ip, port and TLS config (with hash cache)."""
        tls_hash = ""
        if tls_config:
            tls_id = id(tls_config)
            if tls_id in self._tls_hash_cache:
                tls_hash = self._tls_hash_cache[tls_id]
            else:
                tls_str = f"{tls_config.enable_tls}_{tls_config.ca_file}_{tls_config.cert_file}_{tls_config.key_file}"
                tls_hash = hashlib.md5(tls_str.encode()).hexdigest()[:8]
                self._tls_hash_cache[tls_id] = tls_hash

        return f"{ip}:{port}:{tls_hash}"

    async def _safe_aclose(self, client: httpx.AsyncClient | None) -> None:
        """Close client outside lock; ignore errors."""
        if not client or client.is_closed:
            return
        try:
            await client.aclose()
        except Exception as e:
            logger.warning("Ignored error closing HTTP client: %s", e)

    async def _warmup_single_client(
        self,
        ip: str,
        port: str,
        tls_config: Optional[TLSConfig],
        pool_key: str,
        **client_kwargs
    ) -> None:
        """Warm up a single endpoint client (thread-safe)."""
        with self._lock:
            client = self._client_pool.get(pool_key)
            if client and not client.is_closed:
                return

            address = f"{ip}:{port}"
            client = AsyncSafeHTTPSClient.create_client(
                address=address,
                tls_config=tls_config,
                **client_kwargs
            )
            self._client_pool[pool_key] = client
