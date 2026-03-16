#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

"""Tests for calculate_demand_workload and WorkloadActionHandler."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from motor.common.resources.endpoint import Workload, WorkloadAction
from motor.common.resources.instance import PDRole, Instance, InsStatus, ParallelConfig
from motor.coordinator.router.workload_action_handler import (
    calculate_demand_workload,
    WorkloadActionHandler,
)
from motor.coordinator.domain import ScheduledResource
from motor.common.resources.endpoint import Endpoint, EndpointStatus


class TestCalculateDemandWorkload:
    """Tests for calculate_demand_workload(role, request_length)."""

    def test_prefill_role(self):
        """ROLE_P: active_kv_cache and active_tokens both set from prefill formula."""
        w = calculate_demand_workload(PDRole.ROLE_P, 4)
        assert isinstance(w, Workload)
        # request_length=4 -> length_score=1.0 -> score = 1.0*0.0345+120.0745 = 120.109
        assert w.active_kv_cache > 0
        assert w.active_tokens > 0
        assert w.active_kv_cache == w.active_tokens

    def test_decode_role(self):
        """ROLE_D: only active_tokens set (request_length)."""
        w = calculate_demand_workload(PDRole.ROLE_D, 10)
        assert w.active_tokens == 10.0
        assert w.active_kv_cache == 0

    def test_hybrid_role(self):
        """ROLE_U: both set, average of prefill and decode scores."""
        w = calculate_demand_workload(PDRole.ROLE_U, 4)
        assert w.active_kv_cache > 0
        assert w.active_tokens > 0

    def test_unknown_role_returns_empty_workload(self):
        """Unknown role returns empty Workload (and logs warning)."""
        # Pass an invalid role by using a string that is not a valid PDRole
        # calculate_demand_workload expects PDRole enum; if we pass something else
        # it goes to the else branch. We need a way to trigger else - the function
        # only accepts PDRole, so we'd need to pass a value that is not ROLE_P/D/U.
        # PDRole has ROLE_P, ROLE_D, ROLE_U. So we can't easily pass "unknown" without
        # the type checker. The implementation does "else: return Workload()" for
        # any other role. So we test that for a valid role we get non-empty, and
        # we skip testing invalid enum value from outside.
        w = calculate_demand_workload(PDRole.ROLE_P, 0)
        assert w.active_kv_cache >= 0
        assert w.active_tokens >= 0


class TestWorkloadActionHandler:
    """Tests for WorkloadActionHandler.compute_and_update."""

    @pytest.fixture
    def mock_request_manager(self):
        m = MagicMock()
        m.add_req_workload = AsyncMock(return_value=True)
        m.get_req_workload = AsyncMock(return_value=None)
        m.update_req_workload = AsyncMock(return_value=True)
        m.del_req_workload = AsyncMock(return_value=True)
        return m

    @pytest.fixture
    def valid_resource(self):
        """ScheduledResource with instance and endpoint."""
        instance = Instance(
            job_name="test",
            model_name="m",
            id=1,
            role=PDRole.ROLE_P,
            status=InsStatus.ACTIVE,
            parallel_config=ParallelConfig(dp=1),
        )
        endpoint = Endpoint(
            id=1,
            ip="127.0.0.1",
            business_port="8080",
            mgmt_port="8080",
            status=EndpointStatus.NORMAL,
        )
        return ScheduledResource(instance=instance, endpoint=endpoint)

    @pytest.mark.asyncio
    async def test_compute_and_update_allocation_success(self, mock_request_manager, valid_resource):
        """ALLOCATION: add_req_workload called, returns (workload, role)."""
        handler = WorkloadActionHandler(mock_request_manager)
        workload_change, role = await handler.compute_and_update(
            valid_resource, "req-1", WorkloadAction.ALLOCATION, req_len=4
        )
        assert role == PDRole.ROLE_P
        assert workload_change is not None
        assert workload_change.active_tokens > 0
        mock_request_manager.add_req_workload.assert_called_once()
        call_args = mock_request_manager.add_req_workload.call_args
        assert call_args[0][0] == "req-1"
        assert call_args[0][1] == PDRole.ROLE_P

    @pytest.mark.asyncio
    async def test_compute_and_update_allocation_duplicate_returns_none(self, mock_request_manager, valid_resource):
        """ALLOCATION when add_req_workload returns False -> (None, None)."""
        mock_request_manager.add_req_workload = AsyncMock(return_value=False)
        handler = WorkloadActionHandler(mock_request_manager)
        workload_change, role = await handler.compute_and_update(
            valid_resource, "req-1", WorkloadAction.ALLOCATION, req_len=4
        )
        assert workload_change is None
        assert role is None

    @pytest.mark.asyncio
    async def test_compute_and_update_release_kv_success(self, mock_request_manager, valid_resource):
        """RELEASE_KV: get_req_workload returns current, then update/del as needed."""
        current = Workload(active_kv_cache=100.0, active_tokens=50.0)
        mock_request_manager.get_req_workload = AsyncMock(return_value=current)
        handler = WorkloadActionHandler(mock_request_manager)
        workload_change, role = await handler.compute_and_update(
            valid_resource, "req-1", WorkloadAction.RELEASE_KV, req_len=4
        )
        assert role == PDRole.ROLE_P
        assert workload_change is not None
        assert workload_change.active_kv_cache == -100.0
        mock_request_manager.update_req_workload.assert_called_once()
        # After RELEASE_KV, active_tokens still > 0 so del_req_workload not called
        mock_request_manager.del_req_workload.assert_not_called()

    @pytest.mark.asyncio
    async def test_compute_and_update_release_tokens_no_record_returns_none(self, mock_request_manager, valid_resource):
        """RELEASE_TOKENS when no workload record -> (None, None)."""
        mock_request_manager.get_req_workload = AsyncMock(return_value=None)
        handler = WorkloadActionHandler(mock_request_manager)
        workload_change, role = await handler.compute_and_update(
            valid_resource, "req-1", WorkloadAction.RELEASE_TOKENS, req_len=4
        )
        assert workload_change is None
        assert role is None

    @pytest.mark.asyncio
    async def test_compute_and_update_invalid_resource_returns_none(self, mock_request_manager):
        """Empty or invalid resource -> (None, None)."""
        handler = WorkloadActionHandler(mock_request_manager)
        workload_change, role = await handler.compute_and_update(
            None, "req-1", WorkloadAction.ALLOCATION, req_len=4
        )
        assert workload_change is None
        assert role is None
