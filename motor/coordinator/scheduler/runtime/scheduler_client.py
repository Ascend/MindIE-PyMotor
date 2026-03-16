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

"""Async Scheduler client (zmq.asyncio, works with AsyncSchedulerServer)."""

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import zmq

from motor.common.resources.instance import Instance, PDRole
from motor.common.resources.endpoint import Endpoint, Workload
from motor.coordinator.domain import InstanceReadiness, UpdateWorkloadParams
from motor.coordinator.scheduler.runtime.zmq_protocol import (
    SchedulerRequest, SchedulerResponse, SchedulerRequestType, SchedulerResponseType,
    INSTANCE_CHANGE_TOPIC,
    pack_send_frames, unpack_recv_payload,
    ZMQMessageSerializer,
)
from motor.common.utils.logger import get_logger
from motor.config.coordinator import DeployMode
from motor.coordinator.scheduler.policy.load_balance import LoadBalancePolicy
from motor.coordinator.scheduler.policy.round_robin import RoundRobinPolicy
from motor.coordinator.scheduler.policy.kv_cache_affinity import KvCacheAffinityPolicy
from motor.coordinator.router.workload_action_handler import calculate_demand_workload
from motor.coordinator.models.request import RequestInfo

logger = get_logger(__name__)

# Callback signature: receives active endpoint list [(ip, port), ...], returns None
OnInstanceRefreshedCallback = Callable[[list[tuple[str, str]]], Awaitable[None]]


def _collect_active_endpoints_from_cache(cache: "_SchedulerInstanceCache") -> list[tuple[str, str]]:
    """
    Extract status=normal (ip, business_port) from SchedulerInstanceCache.
    Filter logic aligned with BaseRouter._select_endpoint_from_instance.
    """
    endpoints: list[tuple[str, str]] = []
    for role in (PDRole.ROLE_P, PDRole.ROLE_D, PDRole.ROLE_U):
        for inst in cache.get_instances(role):
            if not inst or not inst.endpoints:
                continue
            for pod_eps in (inst.endpoints or {}).values():
                for ep in (pod_eps or {}).values():
                    status_val = (
                        ep.status.value
                        if hasattr(ep.status, "value")
                        else str(ep.status)
                    )
                    if status_val == "normal":
                        endpoints.append((ep.ip, str(ep.business_port)))
    return endpoints


def _instance_to_dict(instance: Instance | None) -> dict:
    """Instance -> dict for ZMQ (model_dump)."""
    return instance.model_dump(mode="json") if instance else {}


def _instance_from_dict(data: dict) -> Instance | None:
    """Dict -> Instance for ZMQ (model_validate)."""
    if not data:
        return None
    try:
        return Instance.model_validate(data)
    except Exception as e:
        logger.error("Failed to deserialize instance: %s", e, exc_info=True)
        return None


def _endpoint_from_dict(data: dict) -> Endpoint | None:
    """Dict -> Endpoint for ZMQ (model_validate)."""
    if not data:
        return None
    try:
        return Endpoint.model_validate(data)
    except Exception as e:
        logger.error("Failed to deserialize endpoint: %s", e, exc_info=True)
        return None


class _SchedulerInstanceCache:
    """
    Instance cache with lock-free reads, incremental role updates, and workload patch from shm.
    """

    def __init__(self):
        self._instance_cache: dict[PDRole, list[Instance]] = {
            PDRole.ROLE_P: [],
            PDRole.ROLE_D: [],
            PDRole.ROLE_U: [],
        }
        self._instance_map: dict[PDRole, dict[int, Instance]] = {
            PDRole.ROLE_P: {},
            PDRole.ROLE_D: {},
            PDRole.ROLE_U: {},
        }
        self._endpoint_map: dict[tuple[int, int], Endpoint] = {}
        self._lock = asyncio.Lock()

    def get_instances(self, role: PDRole) -> list[Instance]:
        return self._instance_cache.get(role, [])

    async def replace_all(self, role: PDRole, instances: list[Instance]) -> None:
        """Update cache for one role only; incremental map update to reduce lock hold time."""
        async with self._lock:
            self._apply_role_under_lock(role, instances)

    def patch_workload_from_shm(
        self,
        instance_id: int,
        endpoint_id: int,
        role: PDRole,
        active_tokens: float,
        active_kv_cache: float,
    ) -> None:
        """Patch single endpoint workload from shared memory. Skip if not in cache."""
        role_map = self._instance_map.get(role) or {}
        cached_instance = role_map.get(instance_id)
        if not cached_instance:
            return
        cached_endpoint = self._endpoint_map.get((instance_id, endpoint_id))
        if not cached_endpoint:
            return
        cached_endpoint.workload = Workload(
            active_tokens=active_tokens,
            active_kv_cache=active_kv_cache,
        )
        gathered = Workload()
        for (iid, eid), ep in self._endpoint_map.items():
            if iid == instance_id:
                gathered += ep.workload
        cached_instance.gathered_workload = gathered

    def _apply_role_under_lock(self, role: PDRole, instances: list[Instance]) -> None:
        """Update cache and maps for one role. Must be called with _lock held."""
        old_ids_role = set((self._instance_map.get(role) or {}).keys())
        self._instance_cache[role] = instances
        self._instance_map[role] = {inst.id: inst for inst in instances}
        for key in list(self._endpoint_map.keys()):
            if key[0] in old_ids_role:
                del self._endpoint_map[key]
        for inst in instances:
            if inst.endpoints:
                for pod_eps in (inst.endpoints or {}).values():
                    for ep in (pod_eps or {}).values():
                        self._endpoint_map[(inst.id, ep.id)] = ep


class _SchedulerTransport:

    def __init__(
        self,
        scheduler_address: str,
        timeout: float,
        serializer: Any | None = None,
    ) -> None:
        self._scheduler_address = scheduler_address
        self._timeout = timeout
        self._serializer = serializer or ZMQMessageSerializer()
        self._cleanup_delay = timeout * 2

        self._context: zmq.asyncio.Context | None = None
        self._socket: zmq.asyncio.Socket | None = None
        self.connected = False
        self._connect_lock = asyncio.Lock()
        self._pending_requests: dict[str, tuple[asyncio.Event | None, float] | None] = {}
        self._pending_responses: dict[str, SchedulerResponse] = {}
        self._request_lock = asyncio.Lock()
        self._encode_lock = asyncio.Lock()
        self._decode_lock = asyncio.Lock()
        self._receive_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def connect(self) -> bool:
        async with self._connect_lock:
            if self.connected:
                return True
            try:
                self._context = zmq.asyncio.Context()
                self._socket = self._context.socket(zmq.DEALER)
                self._socket.connect(self._scheduler_address)
                self.connected = True
                self._receive_task = asyncio.create_task(self._receive_loop())
                logger.info("Scheduler transport connected to %s", self._scheduler_address)
                return True
            except Exception as e:
                logger.error("Failed to connect scheduler transport: %s", e, exc_info=True)
                await self._close_connection()
                return False

    async def disconnect(self) -> None:
        self._stop_event.set()
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None
        await self._close_connection()

    async def send_request(self, request: SchedulerRequest) -> SchedulerResponse | None:
        if not self.connected or not self._socket:
            logger.error("Scheduler transport not connected")
            return None
        event = asyncio.Event()
        request_timestamp = time.time()
        async with self._request_lock:
            self._pending_requests[request.request_id] = (event, request_timestamp)
        log_req_id = (request.data or {}).get("req_id") or request.request_id
        logger.debug(
            "Scheduler request sent request_type=%s req_id=%s",
            request.request_type,
            log_req_id,
        )
        try:
            async with self._encode_lock:
                serialized = self._serializer.serialize_request(request)
            await self._socket.send_multipart(pack_send_frames([b""], serialized))
            try:
                await asyncio.wait_for(event.wait(), timeout=self._timeout)
            except asyncio.TimeoutError:
                elapsed_ms = (time.time() - request_timestamp) * 1000
                logger.warning(
                    "Scheduler request timeout request_type=%s req_id=%s elapsed_ms=%.1f",
                    request.request_type,
                    log_req_id,
                    elapsed_ms,
                )
                async with self._request_lock:
                    if request.request_id in self._pending_requests:
                        self._pending_requests[request.request_id] = (
                            None,
                            request_timestamp,
                        )
                return None
            async with self._request_lock:
                pending_info = self._pending_requests.get(request.request_id)
                if pending_info:
                    pending_event, _ = pending_info
                    if pending_event and pending_event.is_set():
                        response = self._pending_responses.pop(request.request_id, None)
                        self._pending_requests.pop(request.request_id, None)
                    else:
                        response = None
                        self._pending_responses.pop(request.request_id, None)
                        self._pending_requests.pop(request.request_id, None)
                else:
                    response = None
                    self._pending_responses.pop(request.request_id, None)
                elapsed_ms = (time.time() - request_timestamp) * 1000
                logger.debug(
                    "Scheduler request done request_type=%s req_id=%s elapsed_ms=%.1f",
                    request.request_type,
                    log_req_id,
                    elapsed_ms,
                )
                return response
        except asyncio.CancelledError:
            logger.warning(
                "Scheduler request cancelled request_type=%s req_id=%s",
                request.request_type,
                log_req_id,
            )
            async with self._request_lock:
                self._pending_requests.pop(request.request_id, None)
                self._pending_responses.pop(request.request_id, None)
            return None
        except Exception as e:
            elapsed_ms = (time.time() - request_timestamp) * 1000
            logger.error(
                "Scheduler request error request_type=%s req_id=%s elapsed_ms=%.1f error=%s",
                request.request_type,
                log_req_id,
                elapsed_ms,
                e,
                exc_info=True,
            )
            async with self._request_lock:
                self._pending_requests.pop(request.request_id, None)
                self._pending_responses.pop(request.request_id, None)
            return None

    async def _close_connection(self) -> None:
        async with self._connect_lock:
            self.connected = False
            if self._socket:
                try:
                    self._socket.close()
                except Exception as e:
                    logger.warning("Error closing scheduler transport socket: %s", e)
                self._socket = None
            if self._context:
                try:
                    # term() is synchronous on zmq.asyncio.Context; do not await.
                    self._context.term()
                except Exception as e:
                    logger.warning("Error terminating scheduler transport context: %s", e)
                self._context = None

    async def _receive_loop(self) -> None:
        try:
            while not self._stop_event.is_set() and self.connected and self._socket:
                try:
                    parts = await asyncio.wait_for(
                        self._socket.recv_multipart(),
                        timeout=self._timeout,
                    )
                    if len(parts) < 2:
                        continue
                    async with self._decode_lock:
                        response = self._serializer.deserialize_response(
                            unpack_recv_payload(parts)
                        )
                    async with self._request_lock:
                        pending_info = self._pending_requests.get(response.request_id)
                        if pending_info is None:
                            self._pending_responses.pop(response.request_id, None)
                            continue
                        event, req_timestamp = pending_info
                        current_time = time.time()
                        if event is None:
                            if current_time - req_timestamp > self._cleanup_delay:
                                self._pending_requests.pop(response.request_id, None)
                                self._pending_responses.pop(response.request_id, None)
                            else:
                                logger.debug(
                                    "Received delayed response for request %s (timeout: %.3fs)",
                                    response.request_id,
                                    current_time - req_timestamp,
                                )
                        elif not event.is_set():
                            self._pending_responses[response.request_id] = response
                            event.set()
                        else:
                            self._pending_responses.pop(response.request_id, None)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            logger.debug("Scheduler transport receive loop cancelled")
        except Exception as e:
            if not self._stop_event.is_set():
                logger.error("Scheduler transport receive loop error: %s", e, exc_info=True)


# Callback when instance list change is received from Scheduler PUB; arg: instance_version (int | None)
OnInstanceChangeNotify = Callable[[int | None], Awaitable[None]]

# ZMQ PUB does not queue; SUB must be ready before PUB sends. Short delay after connect.
_INSTANCE_PUB_SUB_SETTLE_MS = 150


class _InstancePushSubscriber:
    """
    SUB socket that listens for instance-change notifications from Scheduler PUB.
    On each message, parses optional instance_version and invokes on_notify(version).
    Uses its own ZMQ context to avoid coupling with DEALER transport.
    """

    def __init__(self, sub_address: str, on_notify: OnInstanceChangeNotify) -> None:
        self._sub_address = sub_address
        self._on_notify = on_notify
        self._context: zmq.asyncio.Context | None = None
        self._socket: zmq.asyncio.Socket | None = None
        self._stop_event = asyncio.Event()
        self._recv_task: asyncio.Task | None = None

    async def connect(self) -> bool:
        # Idempotent: if already connected or half-closed, disconnect first so recv_loop can run again.
        if self._recv_task or self._socket or self._context:
            await self.disconnect()
        self._stop_event.clear()
        try:
            self._context = zmq.asyncio.Context()
            self._socket = self._context.socket(zmq.SUB)
            self._socket.connect(self._sub_address)
            self._socket.subscribe(b"")
            # ZMQ PUB does not buffer; allow connection to settle so we don't miss the next message.
            await asyncio.sleep(_INSTANCE_PUB_SUB_SETTLE_MS / 1000.0)
            self._recv_task = asyncio.create_task(self._recv_loop())
            logger.info("Instance push SUB connected to %s", self._sub_address)
            return True
        except Exception as e:
            logger.warning("Failed to connect instance push SUB to %s: %s", self._sub_address, e)
            await self.disconnect()
            return False

    async def disconnect(self) -> None:
        self._stop_event.set()
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            self._recv_task = None
        if self._socket:
            try:
                self._socket.close()
            except Exception as e:
                logger.debug("Error closing instance push SUB socket: %s", e)
            self._socket = None
        if self._context:
            try:
                # term() is synchronous on zmq.asyncio.Context; do not await.
                self._context.term()
            except Exception as e:
                logger.debug("Error terminating instance push context: %s", e)
            self._context = None

    async def _recv_loop(self) -> None:
        try:
            while not self._stop_event.is_set() and self._socket:
                try:
                    frames = await self._socket.recv_multipart()
                    # Multipart: [INSTANCE_CHANGE_TOPIC, version_bytes] -> version; single frame -> None
                    version = None
                    if len(frames) >= 2 and frames[0] == INSTANCE_CHANGE_TOPIC:
                        try:
                            version = int(frames[1].decode())
                        except ValueError:
                            pass
                    await self._on_notify(version)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning("Instance push SUB recv/notify error: %s", e)
                    await asyncio.sleep(1.0)  # Avoid tight loop on persistent errors
        except asyncio.CancelledError:
            logger.debug("Instance push SUB recv loop cancelled")
        except Exception as e:
            if not self._stop_event.is_set():
                logger.error("Instance push SUB recv loop error: %s", e, exc_info=True)


@dataclass
class SchedulerClientConfig:
    """
    Config for AsyncSchedulerClient (G.FNM.03: encapsulate many related args).
    """
    scheduler_address: str = "ipc:///tmp/scheduler_frontend"
    instance_pub_address: str = ""  # SUB to Scheduler PUB for instance-change push; empty disables
    timeout: float = 5.0
    reconnect_interval: float = 5.0
    scheduler_type: str | None = None
    client_index: int = 0
    client_count: int = 1
    tls_config: Any | None = None
    deploy_mode: Any | None = None
    on_instance_refreshed: OnInstanceRefreshedCallback | None = None


class AsyncSchedulerClient:
    """
    Fully async Scheduler client (works with AsyncSchedulerServer).
    Implements SchedulingFacade (select_and_allocate, update_workload) for BaseRouter injection.
    """

    def __init__(self, config: SchedulerClientConfig):
        self.scheduler_address = config.scheduler_address
        self.timeout = config.timeout
        self._client_index = max(0, config.client_index)
        self._client_count = max(1, config.client_count)
        self._deploy_mode = config.deploy_mode

        self._serializer = ZMQMessageSerializer()
        self._transport = _SchedulerTransport(
            config.scheduler_address, config.timeout, self._serializer
        )
        self._cache = _SchedulerInstanceCache()
        self._instance_rr_counters: dict[PDRole, int] = {}
        self._endpoint_rr_counters: dict[int, int] = {}
        self._scheduler_type: str = config.scheduler_type or "round_robin"
        self._workload_reader = None
        self._last_instance_version: int | None = None
        self._on_instance_refreshed = config.on_instance_refreshed

        instance_pub = (config.instance_pub_address or "").strip()
        self._push_subscriber = _InstancePushSubscriber(
            instance_pub, self._on_instance_change_notify
        ) if instance_pub else None

    @property
    def connected(self) -> bool:
        return self._transport.connected

    async def connect(self) -> bool:
        success = await self._transport.connect()
        if success:
            await self._init_cache()
        if success and self._push_subscriber:
            sub_ok = await self._push_subscriber.connect()
            if sub_ok:
                # Initial sync after SUB is ready (covers any message lost during connect).
                try:
                    await self.get_available_instances(None)
                except Exception as e:
                    logger.debug("Post-SUB connect sync failed: %s", e)
            else:
                logger.debug("Instance push SUB disabled; cache will refresh on next request/shm")
        if success:
            logger.info("Async scheduler client connected to %s", self.scheduler_address)
        return success

    async def disconnect(self) -> None:
        try:
            if self._push_subscriber:
                await self._push_subscriber.disconnect()
            if self._workload_reader:
                self._workload_reader.detach()
                self._workload_reader = None
        finally:
            # Always close transport so ZMQ context is terminated even if above steps raise.
            await self._transport.disconnect()

    async def select_instance_and_endpoint(self, req_info: RequestInfo, role: PDRole | None = None):
        """Select instance and endpoint from cache or GET_AVAILABLE_INSTANCES. Returns (Instance, Endpoint) or None."""
        cache_role = role if role is not None else PDRole.ROLE_U
        cached_instances = self._cache.get_instances(cache_role)
        if cached_instances:
            # Cache stores instances sorted by id (see replace_all call sites); use as-is for RR
            selected = self._select_instance_and_endpoint_from_list(cached_instances, cache_role, req_info)
            if selected:
                logger.debug("Selected instance from cache (role=%s, policy=%s)", role, self._scheduler_type)
                return selected
        instances = await self.get_available_instances(role)
        if not instances:
            return None

        # get_available_instances already wrote sorted list to cache; build sorted list once for this path
        instance_list = sorted(instances.values(), key=lambda i: i.id)
        selected = self._select_instance_and_endpoint_from_list(instance_list, cache_role, req_info)
        if selected:
            logger.debug("Selected instance from fresh fetch (role=%s, policy=%s)", role, self._scheduler_type)
        return selected

    async def select_and_allocate(
        self,
        role: "PDRole",
        req_info: RequestInfo
    ) -> tuple[Instance, Endpoint, Workload] | None:
        """Select instance locally + ALLOCATE_ONLY RPC. Allocation workload is decided here (RR=zero, LB=demand)."""
        role_str = role.value if role is not None else (getattr(PDRole.ROLE_U, "value", "both"))
        cache_role = role if role is not None else PDRole.ROLE_U

        if self._workload_reader:
            current_version, heartbeat_stale = self._workload_reader.read_and_patch_cache(
                self._cache
            )
            if heartbeat_stale:
                try:
                    await self.get_available_instances(None)
                except Exception as e:
                    logger.warning(
                        "Failed to refresh instances on stale heartbeat: %s", e
                    )
                else:
                    self._last_instance_version = current_version
                    if self._on_instance_refreshed:
                        active_endpoints = _collect_active_endpoints_from_cache(self._cache)
                        if active_endpoints:
                            try:
                                await self._on_instance_refreshed(active_endpoints)
                            except Exception as e:
                                logger.warning(
                                    "on_instance_refreshed callback failed: %s", e
                                )
            elif current_version is not None:
                if (
                    self._last_instance_version is not None
                    and current_version != self._last_instance_version
                ):
                    try:
                        await self.get_available_instances(None)
                    except Exception as e:
                        logger.warning(
                            "Failed to refresh instances on version change: %s", e
                        )
                    else:
                        self._last_instance_version = current_version
                        if self._on_instance_refreshed:
                            active_endpoints = _collect_active_endpoints_from_cache(
                                self._cache
                            )
                            if active_endpoints:
                                try:
                                    await self._on_instance_refreshed(active_endpoints)
                                except Exception as e:
                                    logger.warning(
                                        "on_instance_refreshed callback failed: %s", e
                                    )
                else:
                    self._last_instance_version = current_version

        selected = await self.select_instance_and_endpoint(req_info, role)
        if not selected:
            return None
        instance, endpoint = selected

        # Allocation workload: RR does not use load, so use zero; LB uses demand for accounting.
        workload = (
            Workload()
            if (self._scheduler_type or "round_robin") == "round_robin"
            else calculate_demand_workload(role, req_info.req_len)
        )

        request_id = str(uuid.uuid4())
        request = SchedulerRequest(
            request_type=SchedulerRequestType.ALLOCATE_ONLY,
            request_id=request_id,
            data={
                "instance_id": instance.id,
                "endpoint_id": endpoint.id,
                "role": role_str,
                "req_id": req_info.req_id,
                "workload": workload.model_dump(mode="json"),
            },
        )
        response = await self._transport.send_request(request)
        if not response or response.response_type != SchedulerResponseType.SUCCESS:
            if response:
                logger.error(
                    "ALLOCATE_ONLY failed: role=%s req_id=%s error=%s",
                    role_str, req_info.req_id, response.error
                )
            return None
        data = response.data or {}
        instance_data = data.get("instance")
        endpoint_data = data.get("endpoint")
        if not instance_data:
            return None
        out_instance = _instance_from_dict(instance_data)
        if not out_instance:
            return None
        if endpoint_data:
            out_endpoint = _endpoint_from_dict(endpoint_data)
            if out_endpoint:
                logger.debug(
                    "select_and_allocate success role=%s instance_id=%s endpoint_id=%s",
                    role_str, out_instance.id, out_endpoint.id
                )
                return (out_instance, out_endpoint, workload)
        return None

    async def update_workload(self, params: UpdateWorkloadParams) -> bool:
        role_str = params.role.value if hasattr(params.role, "value") else str(params.role)
        request_id = str(uuid.uuid4())
        request = SchedulerRequest(
            request_type=SchedulerRequestType.UPDATE_WORKLOAD,
            request_id=request_id,
            data={
                'instance_id': params.instance_id,
                'endpoint_id': params.endpoint_id,
                'role': role_str,
                'req_id': params.req_id,
                'workload_action': params.workload_action.value,
                'workload_change': params.workload_change
            }
        )

        response = await self._transport.send_request(request)

        if response and response.response_type == SchedulerResponseType.SUCCESS:
            success = (response.data or {}).get('success', False)
            if not success:
                logger.warning(
                    "Update workload returned success=False from scheduler: "
                    "instance_id=%s endpoint_id=%s role=%s req_id=%s action=%s",
                    params.instance_id, params.endpoint_id, role_str, params.req_id,
                    params.workload_action.value
                )
            return success

        if response:
            logger.error(
                "Failed to update workload: instance_id=%s endpoint_id=%s role=%s req_id=%s error=%s",
                params.instance_id, params.endpoint_id, role_str, params.req_id, response.error
            )
        else:
            logger.error(
                "Update workload got no response (timeout or connection): "
                "instance_id=%s endpoint_id=%s role=%s req_id=%s",
                params.instance_id, params.endpoint_id, role_str, params.req_id
            )
        return False

    async def get_available_instances(self, role: PDRole | None = None) -> dict[int, Instance]:
        request_id = str(uuid.uuid4())
        request = SchedulerRequest(
            request_type=SchedulerRequestType.GET_AVAILABLE_INSTANCES,
            request_id=request_id,
            data={'role': role.value if hasattr(role, 'value') else (str(role) if role else None)}
        )

        response = await self._transport.send_request(request)

        if response and response.response_type == SchedulerResponseType.SUCCESS:
            data = response.data or {}
            instances_data = data.get('instances', [])
            instances = {}
            for inst_data in instances_data:
                instance = _instance_from_dict(inst_data)
                if instance:
                    instances[instance.id] = instance

            shm_name = data.get('workload_shm_name')
            if shm_name:
                need_attach = (
                    not self._workload_reader
                    or getattr(self._workload_reader, '_shm_name', None) != shm_name
                )
                if need_attach:
                    if self._workload_reader:
                        self._workload_reader.detach()
                    from motor.coordinator.scheduler.runtime.workload_shm import WorkloadSharedMemoryReader
                    self._workload_reader = WorkloadSharedMemoryReader(shm_name)
                    try:
                        self._workload_reader.attach()
                    except FileNotFoundError:
                        logger.debug("Workload shm %s not ready, will retry on next get_available_instances", shm_name)
                        self._workload_reader = None
                    else:
                        self._last_instance_version = None

            if instances:
                # Store sorted by instance.id so round-robin order is stable without sorting on each select
                if role is not None:
                    await self._cache.replace_all(
                        role, sorted(instances.values(), key=lambda i: i.id)
                    )
                else:
                    role_to_list: dict[PDRole, list] = {
                        PDRole.ROLE_P: [],
                        PDRole.ROLE_D: [],
                        PDRole.ROLE_U: [],
                    }
                    _role_map = {
                        "prefill": PDRole.ROLE_P,
                        "decode": PDRole.ROLE_D,
                        "both": PDRole.ROLE_U,
                        "hybrid": PDRole.ROLE_U,
                    }
                    for inst in instances.values():
                        r = getattr(inst, "role", None)
                        if r is None:
                            continue
                        role_enum = _role_map.get(r) if isinstance(r, str) else (r if r in role_to_list else None)
                        if role_enum is not None:
                            role_to_list[role_enum].append(inst)
                    for r, lst in role_to_list.items():
                        await self._cache.replace_all(
                            r, sorted(lst, key=lambda i: i.id)
                        )

            return instances

        if response:
            logger.error(f"Failed to get available instances: {response.error}")
        return {}

    async def has_required_instances(self) -> InstanceReadiness:
        """Return InstanceReadiness from cache; warm-up fetch if needed."""
        mode = self._deploy_mode
        if mode is None:
            mode = DeployMode.PD_SEPARATE
        elif isinstance(mode, str):
            mode = DeployMode.from_string(mode) or DeployMode.PD_SEPARATE

        def _status(p_list: list, d_list: list, u_list: list) -> InstanceReadiness:
            if mode in (DeployMode.CDP_SEPARATE, DeployMode.CPCD_SEPARATE, DeployMode.PD_SEPARATE, \
                    DeployMode.PD_DISAGGREGATION_SINGLE_CONTAINER):
                has_p, has_d = len(p_list) > 0, len(d_list) > 0
                if has_p and has_d:
                    return InstanceReadiness.REQUIRED_MET
                if has_p:
                    return InstanceReadiness.ONLY_PREFILL
                if has_d:
                    return InstanceReadiness.ONLY_DECODE
                return InstanceReadiness.NONE
            if mode == DeployMode.SINGLE_NODE:
                return InstanceReadiness.REQUIRED_MET if len(u_list) > 0 else InstanceReadiness.NONE
            return InstanceReadiness.UNKNOWN

        p_list = self._cache.get_instances(PDRole.ROLE_P)
        d_list = self._cache.get_instances(PDRole.ROLE_D)
        u_list = self._cache.get_instances(PDRole.ROLE_U)
        status = _status(p_list, d_list, u_list)
        if status.is_ready():
            return status
        try:
            await self.get_available_instances(None)
        except Exception as e:
            logger.debug("has_required_instances: warm-up get_available_instances failed: %s", e)
        p_list = self._cache.get_instances(PDRole.ROLE_P)
        d_list = self._cache.get_instances(PDRole.ROLE_D)
        u_list = self._cache.get_instances(PDRole.ROLE_U)
        return _status(p_list, d_list, u_list)

    async def get_all_instances(self) -> tuple[dict[int, Instance], dict[int, Instance]]:
        """Interface compat; returns empty (Mgmt process uses local InstanceManager)."""
        return {}, {}

    async def refresh_instances(self, event_type, instances: list[Instance]) -> None:
        request_id = str(uuid.uuid4())
        request = SchedulerRequest(
            request_type=SchedulerRequestType.REFRESH_INSTANCES,
            request_id=request_id,
            data={
                'event_type': event_type.value if hasattr(event_type, 'value') else str(event_type),
                'instances': [_instance_to_dict(inst) for inst in instances]
            }
        )

        response = await self._transport.send_request(request)

        if response and response.response_type == SchedulerResponseType.SUCCESS:
            logger.info(f"Successfully refreshed instances: {(response.data or {}).get('message', '')}")
        elif response:
            logger.error(f"Failed to refresh instances: {response.error}")

    async def _on_instance_change_notify(self, version: int | None) -> None:
        """Called when SUB receives instance-change from Scheduler; dedup by version, then refresh cache."""
        if version is not None and self._last_instance_version is not None and version == self._last_instance_version:
            return
        try:
            await self.get_available_instances(None)
            if version is not None:
                self._last_instance_version = version
            if self._on_instance_refreshed:
                active_endpoints = _collect_active_endpoints_from_cache(self._cache)
                if active_endpoints:
                    await self._on_instance_refreshed(active_endpoints)
        except Exception as e:
            logger.warning("Instance change notify refresh failed: %s", e)

    def _select_instance_and_endpoint_from_list(
        self, instances: list[Instance], role: PDRole, req_info: RequestInfo
    ) -> tuple[Instance, Endpoint] | None:
        if not instances:
            return None
        st = self._scheduler_type or "round_robin"
        selected_instance = None
        if st == "load_balance":
            selected_instance = self._select_instance_and_endpoint_by_load_balance(instances, role)
            if selected_instance is not None:
                return self._select_endpoint_for_instance(selected_instance)
            logger.warning("load_balance failed, falling back to round-robin")
        elif st == "kv_cache_affinity":
            if role is PDRole.ROLE_P:
                selected = KvCacheAffinityPolicy.select_endpoint_from_list(instances, req_info)
                if selected is not None:
                    return selected
                logger.warning("kv_cache_affinity failed, falling back to load_balance")
                selected_instance = self._select_instance_and_endpoint_by_load_balance(instances, role)
                if selected_instance is not None:
                    return self._select_endpoint_for_instance(selected_instance)
                logger.warning("load_balance also failed, falling back to round-robin")
            else:
                selected_instance = self._select_instance_and_endpoint_by_load_balance(instances, role)
            if selected_instance is not None:
                return self._select_endpoint_for_instance(selected_instance)
            logger.warning("kv_cache_affinity failed, falling back to round-robin")
        # Round-robin path: default policy or load_balance fallback
        if role not in self._instance_rr_counters:
            self._instance_rr_counters[role] = 0
        n = len(instances)
        start_offset = (n * self._client_index) // self._client_count if n else 0
        counter = self._instance_rr_counters[role]
        effective_counter = counter + start_offset
        selected_instance, next_counter = RoundRobinPolicy.select_instance_from_list(
            instances, effective_counter
        )
        self._instance_rr_counters[role] = next_counter - start_offset
        if not selected_instance:
            return None
        return self._select_endpoint_for_instance(selected_instance)

    def _select_endpoint_for_instance(
        self, instance: Instance
    ) -> tuple[Instance, Endpoint] | None:
        if not instance:
            return None
        all_endpoints = instance.get_all_endpoints()
        if not all_endpoints:
            return None
        st = self._scheduler_type or "round_robin"
        if st == "load_balance":
            ep = LoadBalancePolicy.select_endpoint_from_instance(instance)
            if ep:
                return (instance, ep)
            return (instance, all_endpoints[0])
        ep = RoundRobinPolicy.select_endpoint_from_instance(
            instance, self._endpoint_rr_counters
        )
        return (instance, ep) if ep else None

    async def _init_cache(self) -> None:
        """Load initial instance cache via GET_AVAILABLE_INSTANCES."""
        try:
            await self.get_available_instances(None)
        except Exception as e:
            logger.warning("Failed to initialize instance cache: %s", e, exc_info=True)

    def _select_instance_and_endpoint_by_load_balance(
        self, instances: list[Instance], role: PDRole
    ) -> Instance | None:
        n = len(instances)
        start_index = (n * self._client_index) // self._client_count if n else 0
        selected_instance = LoadBalancePolicy.select_instance_from_list(
            instances, role, start_index=start_index
        )
        return selected_instance