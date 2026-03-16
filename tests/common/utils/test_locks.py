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

from unittest.mock import MagicMock

import pytest

from motor.common.etcd.locks import Lock


class TestLock:
    def test_init_with_etcd_client(self):
        # Arrange
        name = "test_lock"
        ttl = 120
        etcd_client = MagicMock()
        etcd_client.kv_stub = MagicMock()
        etcd_client.lease_stub = MagicMock()

        # Act
        lock = Lock(name, ttl, etcd_client)

        # Assert
        assert lock.name == name
        assert lock.ttl == ttl
        assert lock.etcd_client == etcd_client
        assert lock.kv_stub == etcd_client.kv_stub
        assert lock.lease_stub == etcd_client.lease_stub
        assert lock.key == lock.lock_prefix + name
        assert lock.lease_id is None

    @pytest.fixture
    def lock(self):
        name = "test_lock"
        lock = Lock(name, ttl=60, etcd_client=MagicMock())
        lock.lease_stub = MagicMock()
        lock.kv_stub = MagicMock()
        return lock

    def test_acquire_success(self, lock):
        # mock LeaseGrant success
        lease_resp = MagicMock()
        lease_resp.ID = 123
        lock.lease_stub.LeaseGrant.return_value = lease_resp

        # mock Txn success
        txn_resp = MagicMock()
        txn_resp.succeeded = True
        lock.kv_stub.Txn.return_value = txn_resp

        assert lock.acquire() is True
        lock.lease_stub.LeaseGrant.assert_called_once()
        lock.kv_stub.Txn.assert_called_once()

    def test_acquire_failure(self, lock):
        # mock LeaseGrant success
        lease_resp = MagicMock()
        lease_resp.ID = 123
        lock.lease_stub.LeaseGrant.return_value = lease_resp

        # mock Txn success
        txn_resp = MagicMock()
        txn_resp.succeeded = False
        lock.kv_stub.Txn.return_value = txn_resp

        assert lock.acquire() is False

    def test_acquire_exception(self, lock):
        """test_acquire_exception"""
        lock.lease_stub.LeaseGrant.side_effect = Exception("LeaseGrant failed")
        assert lock.acquire() is False

    def test_revoke_lease_silent_success(self, lock):
        """test_revoke_lease_silent_success"""
        lease_id = 123
        lock._revoke_lease_silent(lease_id)
        lock.lease_stub.LeaseRevoke.assert_called_once()

    def test_revoke_lease_silent_failure(self, lock):
        """test_revoke_lease_silent_failure"""
        lease_id = 123
        lock.lease_stub.LeaseRevoke.side_effect = Exception("Test exception")
        lock._revoke_lease_silent(lease_id)
        lock.lease_stub.LeaseRevoke.assert_called_once()
