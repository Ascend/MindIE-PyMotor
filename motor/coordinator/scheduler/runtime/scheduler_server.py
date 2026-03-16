# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# WITHOUT WARRANTIES OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

"""
Async Scheduler standalone process server.
Uses zmq.asyncio for fully async ZMQ I/O and avoids main-loop serialization bottlenecks.
"""

import asyncio
import os
import time
from typing import Awaitable, Callable

import zmq.asyncio

from motor.common.resources.endpoint import Endpoint, WorkloadAction, Workload
from motor.common.resources.http_msg_spec import EventType
from motor.common.resources.instance import PDRole, Instance
from motor.common.utils.logger import get_logger
from motor.config.coordinator import CoordinatorConfig
from motor.coordinator.domain import UpdateWorkloadParams
from motor.coordinator.models.constants import DEFAULT_REQUEST_ID, REQUEST_ID_KEY
from motor.coordinator.domain.instance_manager import InstanceManager
from motor.coordinator.scheduler.scheduler import Scheduler
from motor.coordinator.scheduler.runtime.workload_shm import WorkloadSharedMemoryWriter
from motor.coordinator.scheduler.runtime.workload_shm.layout import DEFAULT_WORKLOAD_SHM_MAX_ENTRIES
from motor.coordinator.scheduler.runtime.zmq_protocol import (
    SchedulerRequest, SchedulerResponse, SchedulerRequestType, SchedulerResponseType,
    INSTANCE_CHANGE_TOPIC,
    pack_send_frames, unpack_recv_payload,
)

logger = get_logger(__name__)

# Hot-path scheduling log sampling: ~1% of requests to reduce I/O and CPU at high QPS
_SCHEDULING_LOG_SAMPLE_RATE = 100

# Display string for unknown/hybrid role in logs
_ROLE_DISPLAY_HYBRID = "hybrid"

# Response data keys for allocate_only / select_and_allocate (avoid duplicate string literals)
_KEY_INSTANCE = "instance"
_KEY_ENDPOINT = "endpoint"


def _should_log_scheduling_sample(sample_key: str) -> bool:
    """Return True for ~1/_SCHEDULING_LOG_SAMPLE_RATE of requests (hot-path info sampling)."""
    return bool(sample_key) and hash(sample_key) % _SCHEDULING_LOG_SAMPLE_RATE == 0

# ==================== Serialization (module-level, shared by Server / Broadcaster) ====================


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


def _serialize_instance_minimal(instance: Instance | None) -> dict:
    """Serialize minimal fields for select/allocate result (forward and release); reduce ZMQ payload."""
    if instance is None:
        return {}
    return {
        "id": instance.id,
        "role": instance.role,
        "job_name": instance.job_name,
        "model_name": instance.model_name,
    }


def _serialize_endpoint_minimal(endpoint: Endpoint | None) -> dict:
    """Serialize minimal fields for select/allocate result (forward and release)."""
    if endpoint is None:
        return {}
    out = {
        "id": endpoint.id,
        "ip": endpoint.ip,
        "business_port": endpoint.business_port,
        "mgmt_port": getattr(endpoint, "mgmt_port", "") or "",
    }
    if hasattr(endpoint, "status") and endpoint.status is not None:
        out["status"] = endpoint.status.value if hasattr(endpoint.status, "value") else str(endpoint.status)
    return out


# ==================== Request dispatch ====================


class _SchedulerRequestDispatcher:
    """
    Route by request_type to handlers; holds instance_manager, scheduler, config and callbacks.
    """

    def __init__(
        self,
        instance_manager: InstanceManager,
        scheduler: Scheduler,
        config: CoordinatorConfig,
        workload_writer: WorkloadSharedMemoryWriter | None = None,
        on_instance_refresh_done: Callable[[], None | Awaitable[None]] | None = None,
    ):
        self._instance_manager = instance_manager
        self._scheduler = scheduler
        self._config = config
        self._workload_writer = workload_writer
        self._on_instance_refresh_done = on_instance_refresh_done

    async def dispatch(self, request: SchedulerRequest) -> SchedulerResponse:
        """Dispatch request to the appropriate handler (async handlers supported)."""
        # Scheduler process uses its local InstanceManager for read-only; only Workers use GET_AVAILABLE_INSTANCES here.
        handlers = {
            SchedulerRequestType.UPDATE_WORKLOAD.value: self._handle_update_workload,
            SchedulerRequestType.GET_AVAILABLE_INSTANCES.value: self._handle_get_available_instances,
            SchedulerRequestType.REFRESH_INSTANCES.value: self._handle_refresh_instances,
            SchedulerRequestType.ALLOCATE_ONLY.value: self._handle_allocate_only,
        }
        handler = handlers.get(request.request_type)
        if handler:
            result = handler(request)
            if asyncio.iscoroutine(result):
                return await result
            return result
        return SchedulerResponse(
            response_type=SchedulerResponseType.ERROR,
            request_id=request.request_id,
            error=f"Unknown request type: {request.request_type}",
        )

    async def _handle_update_workload(self, request: SchedulerRequest) -> SchedulerResponse:
        instance_id = request.data.get("instance_id")
        endpoint_id = request.data.get("endpoint_id")
        role_str = request.data.get("role")
        req_id = request.data.get("req_id")
        workload_action_str = request.data.get("workload_action")
        workload_change_data = request.data.get("workload_change")

        if instance_id is None or endpoint_id is None:
            return SchedulerResponse(
                response_type=SchedulerResponseType.ERROR,
                request_id=request.request_id,
                error="Missing instance_id or endpoint_id in request data",
            )
        if not workload_change_data:
            return SchedulerResponse(
                response_type=SchedulerResponseType.ERROR,
                request_id=request.request_id,
                error="Missing workload_change in request data",
            )
        try:
            workload_change = Workload.model_validate(workload_change_data)
        except Exception as e:
            return SchedulerResponse(
                response_type=SchedulerResponseType.ERROR,
                request_id=request.request_id,
                error=f"Invalid workload_change format: {e}",
            )
        workload_action = WorkloadAction(workload_action_str)
        role = PDRole(role_str) if role_str else PDRole.ROLE_U
        params = UpdateWorkloadParams(
            instance_id=int(instance_id),
            endpoint_id=int(endpoint_id),
            role=role,
            req_id=req_id or "",
            workload_action=workload_action,
            workload_change=workload_change,
        )
        success = await self._scheduler.update_workload(params)
        if success and self._workload_writer:
            await self._workload_writer.write_single_entry(int(instance_id), int(endpoint_id))
        return SchedulerResponse(
            response_type=SchedulerResponseType.SUCCESS,
            request_id=request.request_id,
            data={"success": success},
        )

    def _handle_get_available_instances(self, request: SchedulerRequest) -> SchedulerResponse:
        role_str = request.data.get("role")
        role = PDRole(role_str) if role_str else None
        instances = self._instance_manager.get_available_instances(role)
        instances_data = [_instance_to_dict(inst) for inst in instances.values()]
        data: dict = {
            "instances": instances_data,
        }
        if self._workload_writer:
            data["workload_shm_name"] = self._workload_writer.shm_name
        return SchedulerResponse(
            response_type=SchedulerResponseType.SUCCESS,
            request_id=request.request_id,
            data=data,
        )


    async def _handle_refresh_instances(self, request: SchedulerRequest) -> SchedulerResponse:
        event_type_str = request.data.get("event_type")
        instances_data = request.data.get("instances", [])
        event_type = EventType(event_type_str) if event_type_str else None
        instances = [_instance_from_dict(d) for d in instances_data]
        instances = [inst for inst in instances if inst is not None]
        if not event_type:
            return SchedulerResponse(
                response_type=SchedulerResponseType.ERROR,
                request_id=request.request_id,
                error=f"Invalid event type: {event_type_str}",
            )
        changed = await self._instance_manager.refresh_instances(event_type, instances)
        if changed:
            if self._workload_writer:
                self._workload_writer.write_snapshot()
            if self._on_instance_refresh_done:
                try:
                    result = self._on_instance_refresh_done()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.warning("Failed to publish instance change: %s", e)
        return SchedulerResponse(
            response_type=SchedulerResponseType.SUCCESS,
            request_id=request.request_id,
            data={"message": f"Refreshed {len(instances)} instances", "changed": changed},
        )

    async def _handle_allocate_only(self, request: SchedulerRequest) -> SchedulerResponse:
        """Worker selects locally; Scheduler only allocates. Validate (instance_id, endpoint_id) exists."""
        instance_id = request.data.get("instance_id")
        endpoint_id = request.data.get("endpoint_id")
        req_id = request.data.get("req_id", "")
        workload_data = request.data.get("workload")
        role_str = request.data.get("role")

        if instance_id is None or endpoint_id is None:
            return SchedulerResponse(
                response_type=SchedulerResponseType.ERROR,
                request_id=request.request_id,
                error="Missing instance_id or endpoint_id in request data",
            )
        if not workload_data:
            return SchedulerResponse(
                response_type=SchedulerResponseType.ERROR,
                request_id=request.request_id,
                error="Missing workload in request data",
            )
        try:
            workload = Workload.model_validate(workload_data)
        except Exception as e:
            return SchedulerResponse(
                response_type=SchedulerResponseType.ERROR,
                request_id=request.request_id,
                error=f"Invalid workload format: {e}",
            )
        iid, eid = int(instance_id), int(endpoint_id)
        exists = await self._instance_manager.has_instance_endpoint(iid, eid)
        if not exists:
            logger.warning(
                "ALLOCATE_ONLY instance_id=%s endpoint_id=%s not in available pool req_id=%s",
                iid, eid, req_id,
            )
            return SchedulerResponse(
                response_type=SchedulerResponseType.SUCCESS,
                request_id=request.request_id,
                data={_KEY_INSTANCE: None, _KEY_ENDPOINT: None},
            )
        role = PDRole(role_str) if role_str in ("prefill", "decode", "both") else PDRole.ROLE_U
        params = UpdateWorkloadParams(
            instance_id=iid,
            endpoint_id=eid,
            role=role,
            req_id=req_id,
            workload_action=WorkloadAction.ALLOCATION,
            workload_change=workload,
        )
        success = await self._scheduler.update_workload(params)
        if not success:
            return SchedulerResponse(
                response_type=SchedulerResponseType.SUCCESS,
                request_id=request.request_id,
                data={_KEY_INSTANCE: None, _KEY_ENDPOINT: None},
            )
        if self._workload_writer:
            await self._workload_writer.write_single_entry(iid, eid)
        instance = None
        endpoint = None
        for r in (PDRole.ROLE_P, PDRole.ROLE_D, PDRole.ROLE_U):
            inst = self._instance_manager.get_available_instances(r).get(iid)
            if inst:
                instance = inst
                for pod_eps in (inst.endpoints or {}).values():
                    for ep in (pod_eps or {}).values():
                        if ep.id == eid:
                            endpoint = ep
                            break
                if endpoint:
                    break
        instance_data = _serialize_instance_minimal(instance) if instance else None
        endpoint_data = _serialize_endpoint_minimal(endpoint) if endpoint else None
        if _should_log_scheduling_sample(req_id or request.request_id):
            logger.info(
                "ALLOCATE_ONLY req_id=%s ins=%s ep=%s",
                req_id, iid, eid,
            )
        return SchedulerResponse(
            response_type=SchedulerResponseType.SUCCESS,
            request_id=request.request_id,
            data={_KEY_INSTANCE: instance_data, _KEY_ENDPOINT: endpoint_data},
        )

# ==================== Transport (ROUTER frontend) ====================


class _SchedulerFrontendTransport:
    """
    ZMQ ROUTER socket: bind, recv(client_id + payload_frames), lock-protected send, disconnect.
    """

    def __init__(self, context: zmq.asyncio.Context) -> None:
        self._context = context
        self._socket: zmq.asyncio.Socket | None = None
        self._send_lock = asyncio.Lock()

    async def bind(self, address: str) -> None:
        """Create ROUTER socket and bind."""
        self._socket = self._context.socket(zmq.ROUTER)
        self._socket.bind(address)

    async def recv(self) -> tuple[bytes | None, list]:
        """Receive one request; return (client_id, payload_frames). Return (None, []) if format invalid."""
        if not self._socket:
            return (None, [])
        parts = await self._socket.recv_multipart()
        if len(parts) < 3:
            logger.warning("Invalid frontend message format: %d parts", len(parts))
            return (None, [])
        return (parts[0], parts[2:])

    async def send(self, client_id: bytes, response_frames: list) -> None:
        """Send response (lock-protected, concurrent-safe)."""
        if not self._socket:
            return
        send_frames = pack_send_frames([client_id, b""], response_frames)
        async with self._send_lock:
            await self._socket.send_multipart(send_frames)

    async def disconnect(self) -> None:
        """Close socket; do not term context (Server owns context)."""
        if self._socket:
            try:
                self._socket.close()
            except Exception as e:
                logger.warning("Error closing frontend socket: %s", e)
            self._socket = None


class AsyncSchedulerServer:
    """
    Fully async Scheduler Server (zmq.asyncio).
    """

    def __init__(
        self,
        config: CoordinatorConfig,
        frontend_address: str = "ipc:///tmp/scheduler_frontend",
    ):
        """
        Args:
            config: Coordinator config
            frontend_address: Frontend address (receives API Server process requests, IPC)
        """
        self.config = config
        self.frontend_address = frontend_address

        # Scheduler process holds InstanceManager and Scheduler (single source of truth)
        self.instance_manager = InstanceManager(config)
        self.scheduler = Scheduler(instance_provider=self.instance_manager, config=config)

        # Async ZMQ context and sockets
        self.context: zmq.asyncio.Context | None = None
        self._transport: _SchedulerFrontendTransport | None = None

        # Background task refs
        self._active_tasks: set[asyncio.Task] = set()
        self._stop_event = asyncio.Event()

        # Serializer (instance-level, shared by all tasks for cache reuse)
        # Encode/decode locks separate so encode and decode can run concurrently
        from motor.coordinator.scheduler.runtime.zmq_protocol import ZMQMessageSerializer
        self._serializer = ZMQMessageSerializer()
        self._encode_lock = asyncio.Lock()
        self._decode_lock = asyncio.Lock()

        # Dispatch timeout to avoid thread-pool exhaustion from long blocks
        self._dispatch_timeout = 5.0

        # Set in start() (G.CLS.08: declare in __init__)
        self._dispatcher: _SchedulerRequestDispatcher | None = None
        self._workload_shm = None
        self._workload_writer: WorkloadSharedMemoryWriter | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._pub_socket: zmq.asyncio.Socket | None = None

    async def stop(self):
        """Stop the async server."""
        logger.info("Stopping async scheduler server...")

        self._stop_event.set()

        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        # Wait for all active request-handling tasks to finish
        if self._active_tasks:
            logger.info("Waiting for %s active request tasks to complete...", len(self._active_tasks))
            # Cancel all unfinished tasks
            for task in self._active_tasks:
                if not task.done():
                    task.cancel()
            # Wait for all tasks (including cancelled)
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
            self._active_tasks.clear()

        # Close shared memory (release writer's buffer first to avoid BufferError: exported pointers exist)
        if self._workload_writer:
            self._workload_writer.release()
            self._workload_writer = None
        if self._workload_shm:
            try:
                self._workload_shm.close()
                self._workload_shm.unlink()
            except Exception as e:
                logger.warning("Error closing workload shared memory: %s", e)
            self._workload_shm = None
        if self._pub_socket:
            try:
                self._pub_socket.close()
            except Exception as e:
                logger.warning("Error closing instance PUB socket: %s", e)
            self._pub_socket = None
        if self._transport:
            await self._transport.disconnect()
        if self.context:
            try:
                # term() is synchronous on zmq.asyncio.Context; do not await.
                self.context.term()
            except Exception as e:
                logger.warning("Error terminating context: %s", e)

        logger.info("Async scheduler server stopped")

    async def start(self):
        """Start the async Scheduler server."""
        from multiprocessing import shared_memory
        from motor.coordinator.scheduler.runtime.workload_shm import total_size

        # Create async ZMQ context and ROUTER transport
        self.context = zmq.asyncio.Context()
        self._transport = _SchedulerFrontendTransport(self.context)
        await self._transport.bind(self.frontend_address)

        from motor.config.coordinator import DEFAULT_SCHEDULER_PROCESS_CONFIG
        instance_pub_address = DEFAULT_SCHEDULER_PROCESS_CONFIG.instance_pub_address
        if instance_pub_address:
            self._pub_socket = self.context.socket(zmq.PUB)
            self._pub_socket.bind(instance_pub_address)
            logger.info("Instance change PUB bound: %s", instance_pub_address)

        max_entries = DEFAULT_WORKLOAD_SHM_MAX_ENTRIES
        shm_name = f"mindie_workload_{os.getpid()}"
        shm_size = total_size(max_entries)
        self._workload_shm = shared_memory.SharedMemory(
            name=shm_name, create=True, size=shm_size
        )
        self._workload_writer = WorkloadSharedMemoryWriter(
            self._workload_shm,
            self.instance_manager,
            max_entries=max_entries,
        )
        self._workload_writer.write_snapshot()
        logger.info("Workload shared memory enabled: %s (%d entries)", shm_name, max_entries)

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        self._dispatcher = _SchedulerRequestDispatcher(
            self.instance_manager,
            self.scheduler,
            self.config,
            workload_writer=self._workload_writer,
            on_instance_refresh_done=self._publish_instance_changed,
        )

        logger.info("Async scheduler server started, frontend: %s", self.frontend_address)

        # Async main loop (fully non-blocking)
        try:
            await self._run_async_loop()
        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        finally:
            await self.stop()

    async def _publish_instance_changed(self) -> None:
        """Publish instance list changed + version to SUB clients (no-op if PUB not enabled)."""
        if not self._pub_socket:
            return
        version = self._workload_writer.instance_version if self._workload_writer else 0
        try:
            await self._pub_socket.send_multipart([INSTANCE_CHANGE_TOPIC, str(version).encode()])
        except Exception as e:
            logger.warning("Failed to publish instance change: %s", e)

    async def _heartbeat_loop(self) -> None:
        """Write heartbeat to shm every 1s so Infer can detect Scheduler restart (stale = no change)."""
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(1.0)
                if self._stop_event.is_set() or not self._workload_writer:
                    break
                self._workload_writer.write_heartbeat()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Workload heartbeat error: %s", e)

    async def _run_async_loop(self):
        """Async main loop: handle all requests concurrently; main loop never blocks."""
        logger.info("Async main loop started")

        while not self._stop_event.is_set():
            try:
                client_id, payload_frames = await self._transport.recv()
                if client_id is None:
                    continue
                task = asyncio.create_task(
                    self._handle_request_async(client_id, payload_frames, self._serializer)
                )
                # Track tasks to avoid leaks
                self._active_tasks.add(task)
                task.add_done_callback(self._active_tasks.discard)

            except asyncio.CancelledError:
                logger.info("Main loop cancelled")
                break
            except Exception as e:
                logger.error("Error in main loop: %s", e, exc_info=True)
                # Brief sleep then continue
                await asyncio.sleep(0.01)

    async def _handle_request_async(self, client_id: bytes, payload_frames: list, ser):
        """Handle a single request asynchronously (does not block main loop)."""
        serializer = ser or self._serializer
        request = None
        handle_start = time.time()

        try:
            payload = unpack_recv_payload([b"", b""] + payload_frames, payload_start=2)
            async with self._decode_lock:
                request = serializer.deserialize_request(payload)

            log_req_id = (request.data or {}).get(REQUEST_ID_KEY) or request.request_id
            logger.debug(
                "Scheduler request received request_type=%s req_id=%s",
                request.request_type,
                log_req_id,
            )

            response = await asyncio.wait_for(
                self._dispatcher.dispatch(request),
                timeout=self._dispatch_timeout,
            )

            async with self._encode_lock:
                response_frames = serializer.serialize_response(response)
            await self._transport.send(client_id, response_frames)

            elapsed_ms = (time.time() - handle_start) * 1000
            logger.debug(
                "Scheduler request done request_type=%s req_id=%s elapsed_ms=%.1f",
                request.request_type,
                log_req_id,
                elapsed_ms,
            )

        except asyncio.CancelledError:
            logger.debug("Request handling cancelled")
        except asyncio.TimeoutError:
            elapsed_ms = (time.time() - handle_start) * 1000
            req_data = (getattr(request, "data", None) or {})
            _log_req_id = req_data.get(REQUEST_ID_KEY) or getattr(request, "request_id", DEFAULT_REQUEST_ID)
            logger.warning(
                "Dispatch request timeout request_type=%s req_id=%s elapsed_ms=%.1f",
                getattr(request, "request_type", DEFAULT_REQUEST_ID),
                _log_req_id,
                elapsed_ms,
            )
            try:
                error_response = SchedulerResponse(
                    response_type=SchedulerResponseType.ERROR,
                    request_id=request.request_id if request else DEFAULT_REQUEST_ID,
                    error="dispatch timeout",
                )
                async with self._encode_lock:
                    error_frames = serializer.serialize_response(error_response)
                await self._transport.send(client_id, error_frames)
            except Exception as e2:
                logger.error("Error sending timeout response: %s", e2, exc_info=True)
        except Exception as e:
            elapsed_ms = (time.time() - handle_start) * 1000
            req_data = (getattr(request, "data", None) or {})
            _log_req_id = req_data.get(REQUEST_ID_KEY) or getattr(request, "request_id", DEFAULT_REQUEST_ID)
            logger.error(
                "Error handling request request_type=%s req_id=%s elapsed_ms=%.1f error=%s",
                getattr(request, "request_type", DEFAULT_REQUEST_ID),
                _log_req_id,
                elapsed_ms,
                e,
                exc_info=True,
            )
            try:
                error_response = SchedulerResponse(
                    response_type=SchedulerResponseType.ERROR,
                    request_id=request.request_id if request else DEFAULT_REQUEST_ID,
                    error=str(e)
                )
                async with self._encode_lock:
                    error_frames = serializer.serialize_response(error_response)
                await self._transport.send(client_id, error_frames)
            except Exception as e2:
                logger.error("Error sending error response: %s", e2, exc_info=True)


# ==================== Entry points ====================

async def run_async_scheduler_server(config: CoordinatorConfig):
    """Run Scheduler server asynchronously (asyncio entry)."""
    # Set process title
    try:
        import setproctitle
        setproctitle.setproctitle("AsyncSchedulerServer")
    except ImportError:
        pass

    logger.info("Async scheduler server process starting (PID: %s)", os.getpid())

    from motor.config.coordinator import DEFAULT_SCHEDULER_PROCESS_CONFIG
    frontend_address = DEFAULT_SCHEDULER_PROCESS_CONFIG.frontend_address

    # Create and start async server
    server = AsyncSchedulerServer(config, frontend_address)

    try:
        await server.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        await server.stop()


def run_async_scheduler_server_proc(config: CoordinatorConfig) -> None:
    """Async Scheduler server process entry (for sync entry points)."""
    asyncio.run(run_async_scheduler_server(config))


# Backward compat: scheduler_manager (process/) etc. import SchedulerServer from this module
SchedulerServer = AsyncSchedulerServer
