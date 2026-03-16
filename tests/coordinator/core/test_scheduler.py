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
from unittest.mock import patch, AsyncMock

import pytest
import httpx

from motor.coordinator.scheduler.scheduler import Scheduler, SchedulerType
from motor.coordinator.scheduler.policy.load_balance import LoadBalancePolicy
from motor.coordinator.scheduler.policy.round_robin import RoundRobinPolicy
from motor.coordinator.domain.instance_manager import InstanceManager
from motor.coordinator.router.workload_action_handler import calculate_demand_workload
from motor.config.coordinator import CoordinatorConfig
from motor.common.resources.instance import Instance, InsStatus, PDRole, ParallelConfig
from motor.common.resources.endpoint import Endpoint, EndpointStatus, Workload, WorkloadAction
from motor.common.resources.http_msg_spec import EventType


@pytest.fixture(autouse=True)
def clear_instance_manager():
    """No-op: InstanceManager is no longer a singleton; each test creates its own."""
    yield


@pytest.fixture
def prefill_instances():
    """Create prefill instances for testing."""
    instances = []
    for i in range(3):
        instance = Instance(
            job_name=f"prefill_instance_{i+1}",
            model_name="test_model",
            id=i+1,
            role=PDRole.ROLE_P,
            status=InsStatus.ACTIVE,
            parallel_config=ParallelConfig(dp=2)
        )
        instances.append(instance)
    return instances


@pytest.fixture
def decode_instances():
    """Create decode instances for testing."""
    instances = []
    for i in range(2):
        instance = Instance(
            job_name=f"decode_instance_{i+1}",
            model_name="test_model",
            id=i+4,
            role=PDRole.ROLE_D,
            status=InsStatus.ACTIVE,
            parallel_config=ParallelConfig(dp=2)
        )
        instances.append(instance)
    return instances


@pytest.fixture
def mix_instances():
    """Create mixed role instances for testing."""
    instances = []
    for i in range(2):
        instance = Instance(
            job_name=f"mix_instance_{i+1}",
            model_name="test_model",
            id=i+6,
            role=PDRole.ROLE_U,
            status=InsStatus.ACTIVE,
            parallel_config=ParallelConfig(dp=2)
        )
        instances.append(instance)
    return instances


def mock_create_client(address, tls_config=None, **kwargs):
    client = AsyncMock()
    client.base_url = f"http://{address}"
    client.is_closed = False
    client.post = AsyncMock(return_value=httpx.Response(200))
    client.aclose = AsyncMock()
    return client


@pytest.fixture
async def scheduler_setup(prefill_instances, decode_instances, mix_instances):
    """Setup scheduler with instances and endpoints."""
    config = CoordinatorConfig()
    instance_manager = InstanceManager(config)

    available_pool, unavailable_pool = await instance_manager.get_all_instances()
    all_existing_instances = list(available_pool.values()) + list(unavailable_pool.values())
    if all_existing_instances:
        await instance_manager.refresh_instances(EventType.DEL, all_existing_instances)

    all_instances = prefill_instances + decode_instances + mix_instances
    await instance_manager.refresh_instances(EventType.DEL, all_instances)
    for instance in all_instances:
        endpoints = {}
        for j in range(2):  # 2 endpoints per instance
            endpoint = Endpoint(
                id=instance.id * 10 + j,
                ip=f"192.168.1.{instance.id}",
                business_port=f"800{j}",
                mgmt_port=f"900{j}",
                status=EndpointStatus.NORMAL,
                workload=Workload(active_tokens=0, active_kv_cache=0)
            )
            endpoints[j] = endpoint
        instance.add_endpoints(f"192.168.1.{instance.id}", endpoints)

    # refresh_instances is now async, and add no longer creates HTTP client; no patch needed
    await instance_manager.refresh_instances(EventType.ADD, all_instances)

    # Fail fast if pool was not populated (e.g. CI missing asyncio_mode or different impl)
    for role in (PDRole.ROLE_P, PDRole.ROLE_D):
        pool = instance_manager.get_available_instances(role)
        assert len(pool) > 0, (
            f"scheduler_setup: get_available_instances({role}) is empty after ADD. "
            "Check pytest asyncio_mode=auto and InstanceManager implementation."
        )

    # Return instance_manager so tests can pass it to Scheduler(..., instance_provider=...).
    return all_instances, instance_manager


@pytest.mark.asyncio
async def test_request_processing_pd_separation_scenario(scheduler_setup):
    """Test PD separation scenario with load balance policy."""
    all_instances, instance_manager = scheduler_setup
    scheduler = Scheduler(instance_provider=instance_manager, config=SchedulerType.LOAD_BALANCE)
    load_balance_scheduler = scheduler.get_scheduling_policy()
    request_length = 4
    req_id = "test_request_1"

    # 1. select prefill instance and endpoint
    result = load_balance_scheduler.select_instance_and_endpoint(role=PDRole.ROLE_P)
    assert result is not None, (
        "select_instance_and_endpoint(ROLE_P) returned None; InstanceManager pool may be empty or fixture did not run."
    )
    selected_prefill_instance, selected_prefill_endpoint = result

    assert selected_prefill_instance.role == PDRole.ROLE_P

    # 2. allocate prefill workload
    workload_p = calculate_demand_workload(PDRole.ROLE_P, request_length)
    result = await load_balance_scheduler.update_workload(
        selected_prefill_instance.id, selected_prefill_endpoint.id, req_id,
        WorkloadAction.ALLOCATION, workload_p
    )
    assert result

    assert selected_prefill_endpoint.workload.active_tokens > 0
    assert selected_prefill_endpoint.workload.active_kv_cache > 0

    # 3. release active_tokens
    release_tokens = Workload(active_tokens=-selected_prefill_endpoint.workload.active_tokens)
    result = await load_balance_scheduler.update_workload(
        selected_prefill_instance.id, selected_prefill_endpoint.id, req_id,
        WorkloadAction.RELEASE_TOKENS, release_tokens
    )
    assert result

    assert selected_prefill_endpoint.workload.active_tokens == 0
    assert selected_prefill_endpoint.workload.active_kv_cache > 0

    # 4. select decode instance and endpoint
    res_d = await scheduler.select_instance_and_endpoint(role=PDRole.ROLE_D)
    assert res_d is not None, "select_instance_and_endpoint(ROLE_D) returned None."
    selected_decode_instance, selected_decode_endpoint = res_d

    assert selected_decode_instance.role == PDRole.ROLE_D

    # 5. allocate decode workload
    workload_d = calculate_demand_workload(PDRole.ROLE_D, request_length)
    result = await load_balance_scheduler.update_workload(
        selected_decode_instance.id, selected_decode_endpoint.id, req_id,
        WorkloadAction.ALLOCATION, workload_d
    )
    assert result

    assert selected_decode_endpoint.workload.active_tokens > 0

    # 6. release decode workload
    release_d = Workload(active_tokens=-selected_decode_endpoint.workload.active_tokens)
    result = await load_balance_scheduler.update_workload(
        selected_decode_instance.id, selected_decode_endpoint.id, req_id,
        WorkloadAction.RELEASE_TOKENS, release_d
    )
    assert result

    assert selected_decode_endpoint.workload.active_tokens == 0

    # 7. release prefill kv_cache
    release_kv = Workload(active_kv_cache=-selected_prefill_endpoint.workload.active_kv_cache)
    result = await load_balance_scheduler.update_workload(
        selected_prefill_instance.id, selected_prefill_endpoint.id, req_id,
        WorkloadAction.RELEASE_KV, release_kv
    )
    assert result

    assert selected_prefill_endpoint.workload.active_kv_cache == 0


@pytest.mark.asyncio
async def test_request_processing_mix_scenario(scheduler_setup):
    """Test mixed role scenario with load balance policy."""
    all_instances, instance_manager = scheduler_setup
    scheduler = Scheduler(instance_provider=instance_manager, config=SchedulerType.LOAD_BALANCE)
    request_length = 4
    req_id = "test_request_mix_1"

    # 1. select mix instance and endpoint
    result = await scheduler.select_instance_and_endpoint(role=PDRole.ROLE_U)
    assert result is not None, "select_instance_and_endpoint(ROLE_U) returned None."
    selected_instance, selected_endpoint = result

    assert selected_instance.role == PDRole.ROLE_U

    # 2. allocate mix workload
    load_balance_scheduler = scheduler.get_scheduling_policy()
    workload_u = calculate_demand_workload(PDRole.ROLE_U, request_length)
    result = await load_balance_scheduler.update_workload(
        selected_instance.id, selected_endpoint.id, req_id,
        WorkloadAction.ALLOCATION, workload_u
    )
    assert result

    assert selected_endpoint.workload.active_tokens > 0
    assert selected_endpoint.workload.active_kv_cache > 0

    # 3. release tokens
    release_tokens = Workload(active_tokens=-selected_endpoint.workload.active_tokens)
    result = await load_balance_scheduler.update_workload(
        selected_instance.id, selected_endpoint.id, req_id,
        WorkloadAction.RELEASE_TOKENS, release_tokens
    )
    assert result

    assert selected_endpoint.workload.active_tokens == 0
    assert selected_endpoint.workload.active_kv_cache > 0

    # 4. release kv_cache
    release_kv = Workload(active_kv_cache=-selected_endpoint.workload.active_kv_cache)
    result = await load_balance_scheduler.update_workload(
        selected_instance.id, selected_endpoint.id, req_id,
        WorkloadAction.RELEASE_KV, release_kv
    )
    assert result

    assert selected_endpoint.workload.active_tokens == 0
    assert selected_endpoint.workload.active_kv_cache == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("request_length", [4, 6, 3, 8, 5])
async def test_multiple_requests_load_balancing(scheduler_setup, request_length):
    """Test multiple requests with different lengths."""
    all_instances, instance_manager = scheduler_setup
    scheduler = Scheduler(instance_provider=instance_manager, config=SchedulerType.LOAD_BALANCE)
    
    req_id = f"test_request_{request_length}"
    
    result = await scheduler.select_instance_and_endpoint(role=PDRole.ROLE_P)
    assert result is not None, "select_instance_and_endpoint returned None (pool empty)."
    selected_instance, selected_endpoint = result

    # allocate workload
    load_balance_scheduler = scheduler.get_scheduling_policy()
    workload = calculate_demand_workload(PDRole.ROLE_P, request_length)
    result = await load_balance_scheduler.update_workload(
        selected_instance.id, selected_endpoint.id, req_id,
        WorkloadAction.ALLOCATION, workload
    )
    assert result
    
    assert selected_endpoint.workload.active_tokens > 0
    assert selected_endpoint.workload.active_kv_cache > 0
    
    # release tokens
    release_tokens = Workload(active_tokens=-selected_endpoint.workload.active_tokens)
    result = await load_balance_scheduler.update_workload(
        selected_instance.id, selected_endpoint.id, req_id,
        WorkloadAction.RELEASE_TOKENS, release_tokens
    )
    assert result
    
    assert selected_endpoint.workload.active_tokens == 0
    assert selected_endpoint.workload.active_kv_cache > 0


@pytest.mark.asyncio
async def test_workload_calculation_accuracy(scheduler_setup):
    """Test workload calculation accuracy."""
    all_instances, instance_manager = scheduler_setup
    scheduler = Scheduler(instance_provider=instance_manager, config=SchedulerType.LOAD_BALANCE)
    request_length = 4
    req_id = "test_workload_calc"
    load_balance_scheduler = scheduler.get_scheduling_policy()

    # select prefill instance and endpoint
    result = await scheduler.select_instance_and_endpoint(role=PDRole.ROLE_P)
    assert result is not None, (
        "select_instance_and_endpoint returned None (pool empty). "
        "Ensure scheduler_setup fixture ran and instance_manager has instances."
    )
    selected_instance, selected_endpoint = result

    # allocate prefill workload
    workload = calculate_demand_workload(PDRole.ROLE_P, request_length)
    result = await load_balance_scheduler.update_workload(
        selected_instance.id, selected_endpoint.id, req_id,
        WorkloadAction.ALLOCATION, workload
    )
    assert result

    # calculate expected workload score
    expected_score = selected_endpoint.workload.active_tokens + selected_endpoint.workload.active_kv_cache * 0.3

    # get actual computed score
    actual_score = selected_endpoint.workload.calculate_workload_score(role=selected_instance.role)

    # verify that the computed score matches the expected score
    assert actual_score == expected_score

    # release tokens
    release_tokens = Workload(active_tokens=-selected_endpoint.workload.active_tokens)
    result = await load_balance_scheduler.update_workload(
        selected_instance.id, selected_endpoint.id, req_id,
        WorkloadAction.RELEASE_TOKENS, release_tokens
    )
    assert result

    # verify that the score after release matches the expected score
    expected_score_after_release = selected_endpoint.workload.active_tokens + selected_endpoint.workload.active_kv_cache * 0.3
    actual_score_after_release = selected_endpoint.workload.calculate_workload_score(role=selected_instance.role)
    assert actual_score_after_release == expected_score_after_release


@pytest.mark.asyncio
async def test_load_balance_policy_selection_logic(scheduler_setup):
    """Test load balance policy selection logic."""
    all_instances, instance_manager = scheduler_setup
    scheduler = Scheduler(instance_provider=instance_manager, config=SchedulerType.LOAD_BALANCE)

    result = await scheduler.select_instance_and_endpoint(role=PDRole.ROLE_P)
    assert result is not None, "select_instance_and_endpoint(ROLE_P) returned None (pool empty)."
    prefill_instance, _ = result
    assert prefill_instance is not None
    assert prefill_instance.role == PDRole.ROLE_P

    res_d = await scheduler.select_instance_and_endpoint(role=PDRole.ROLE_D)
    assert res_d is not None, "select_instance_and_endpoint(ROLE_D) returned None."
    decode_instance, _ = res_d
    assert decode_instance is not None
    assert decode_instance.role == PDRole.ROLE_D

    res_u = await scheduler.select_instance_and_endpoint(role=PDRole.ROLE_U)
    assert res_u is not None, "select_instance_and_endpoint(ROLE_U) returned None."
    mix_instance, _ = res_u
    assert mix_instance is not None
    assert mix_instance.role == PDRole.ROLE_U

    res_p2 = await scheduler.select_instance_and_endpoint(role=PDRole.ROLE_P)
    assert res_p2 is not None, "select_instance_and_endpoint(ROLE_P) returned None."
    _, endpoint = res_p2
    assert endpoint is not None
    assert endpoint in prefill_instance.get_all_endpoints()


@pytest.mark.asyncio
async def test_round_robin_instance_selection(scheduler_setup):
    """Test round robin instance selection."""
    all_instances, instance_manager = scheduler_setup
    scheduler = Scheduler(instance_provider=instance_manager, config=SchedulerType.ROUND_ROBIN)

    selected_instances = []
    # select 6 times, should round robin all 3 prefill instances each 2 times
    for _ in range(6):
        instance, _ = await scheduler.select_instance_and_endpoint(role=PDRole.ROLE_P)
        assert instance is not None
        assert instance.role == PDRole.ROLE_P
        selected_instances.append(instance.id)

    # verify that the round robin order is correct: 1, 2, 3, 1, 2, 3
    expected_order = [1, 2, 3, 1, 2, 3]
    assert selected_instances == expected_order

    # select 4 times, should round robin all 2 decode instances each 2 times
    selected_decode_instances = []
    for _ in range(4):
        instance, _ = await scheduler.select_instance_and_endpoint(role=PDRole.ROLE_D)
        assert instance is not None
        assert instance.role == PDRole.ROLE_D
        selected_decode_instances.append(instance.id)

    # verify that the round robin order is correct: 4, 5, 4, 5
    expected_decode_order = [4, 5, 4, 5]
    assert selected_decode_instances == expected_decode_order


@pytest.mark.asyncio
async def test_round_robin_endpoint_selection(scheduler_setup):
    """Test round robin endpoint selection."""
    all_instances, instance_manager = scheduler_setup
    scheduler = Scheduler(instance_provider=instance_manager, config=SchedulerType.ROUND_ROBIN)
    # select a prefill instance
    instance, endpoint = await scheduler.select_instance_and_endpoint(role=PDRole.ROLE_P)
    assert instance is not None

    # test that the endpoint selection round robin for the selected prefill instance
    selected_endpoints = []
    for _ in range(4):
        instance, endpoint = await scheduler.select_instance_and_endpoint(role=PDRole.ROLE_P)
        assert endpoint is not None
        selected_endpoints.append(endpoint.id)

    # verify that the round robin order is correct
    expected_order = [20, 30, 11, 21]
    assert selected_endpoints == expected_order


@pytest.mark.asyncio
async def test_round_robin_mixed_role_selection(scheduler_setup):
    """Test round robin mixed role selection."""
    all_instances, instance_manager = scheduler_setup
    scheduler = Scheduler(instance_provider=instance_manager, config=SchedulerType.ROUND_ROBIN)

    # test that the mixed role selection round robin works as expected
    selected_instances = []
    for _ in range(9):  # select 9 times, should round robin all 3 prefill instances, 2 decode instances, 4 mix instances each 2 times
        if len(selected_instances) % 3 == 0:
            instance, _ = await scheduler.select_instance_and_endpoint(role=PDRole.ROLE_P)
            expected_role = PDRole.ROLE_P
        elif len(selected_instances) % 3 == 1:
            instance, _ = await scheduler.select_instance_and_endpoint(role=PDRole.ROLE_D)
            expected_role = PDRole.ROLE_D
        else:
            instance, _ = await scheduler.select_instance_and_endpoint(role=PDRole.ROLE_U)
            expected_role = PDRole.ROLE_U

        assert instance is not None
        assert instance.role == expected_role
        selected_instances.append(instance.id)


@pytest.mark.asyncio
async def test_round_robin_edge_cases():
    """Test round robin edge cases (empty pool). Use explicit instance_provider; Policy is no longer singleton."""
    config = CoordinatorConfig()
    instance_manager = InstanceManager(config)
    available_pool, unavailable_pool = await instance_manager.get_all_instances()
    all_instances = list(available_pool.values()) + list(unavailable_pool.values())
    if all_instances:
        await instance_manager.refresh_instances(EventType.DEL, all_instances)

    empty_scheduler = Scheduler(instance_provider=instance_manager, config=SchedulerType.ROUND_ROBIN)

    # No instances: select_instance_and_endpoint returns None (not (None, None))
    result = await empty_scheduler.select_instance_and_endpoint(role=PDRole.ROLE_P)
    assert result is None

    result = await empty_scheduler.select_instance_and_endpoint(role=PDRole.ROLE_D)
    assert result is None

    result = await empty_scheduler.select_instance_and_endpoint(role=PDRole.ROLE_U)
    assert result is None

    # Empty scheduler: no instance => no endpoint (same as above)
    result = await empty_scheduler.select_instance_and_endpoint(role=PDRole.ROLE_P)
    assert result is None

    result = await empty_scheduler.select_instance_and_endpoint(role=PDRole.ROLE_D)
    assert result is None
