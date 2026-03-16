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

import uuid

import motor.common.etcd.proto.rpc_pb2 as rpc__pb2
from motor.common.utils.logger import get_logger

logger = get_logger(__name__)
bytes_types = (bytes, bytearray)
UTF8_ENCODING = "utf-8"


class Lock(object):
    lock_prefix = '/locks/'

    def __init__(self, name, ttl: int = 60, etcd_client=None):
        self.name = name
        self.ttl = ttl
        if etcd_client is not None:
            self.etcd_client = etcd_client
            self.kv_stub = etcd_client.kv_stub
            self.lease_stub = etcd_client.lease_stub

        self.key = self.lock_prefix + self.name
        self.lease_id = None
        self.uuid = uuid.uuid1().bytes

    def acquire(self, timeout: int = 5):
        """Acquire the lock.

        :params timeout: Maximum time to wait before returning. `None` means
                         forever, any other value equal or greater than 0 is
                         the number of seconds.
        :returns: True if the lock has been acquired, False otherwise.

        """
        try:
            # step1: create lease
            lease_req = rpc__pb2.LeaseGrantRequest(TTL=self.ttl, ID=0)
            lease_resp = self.lease_stub.LeaseGrant(lease_req, timeout=timeout)
            self.lease_id = lease_resp.ID

            logger.debug("lease id is: %s", self.lease_id)

            # step2: write key when key not exist
            compare = rpc__pb2.Compare(
                key=self.key.encode(UTF8_ENCODING),
                result=rpc__pb2.Compare.EQUAL,
                target=rpc__pb2.Compare.CREATE,
                create_revision=0
            )

            success_request = rpc__pb2.RequestOp(
                request_put=rpc__pb2.PutRequest(
                    key=self.key.encode(UTF8_ENCODING),
                    value=self.uuid,  # 锁值可为空或填入 owner 信息
                    lease=self.lease_id
                )
            )

            failure_request = rpc__pb2.RequestOp(
                request_range=rpc__pb2.RangeRequest(key=self.key.encode(UTF8_ENCODING)),
            )

            txn_req = rpc__pb2.TxnRequest(
                compare=[compare],
                success=[success_request],
                failure=[failure_request]
            )

            txn_resp = self.kv_stub.Txn(txn_req)
            if txn_resp.succeeded:
                # acquire lock success
                return True
            else:
                # acquire lock failed, revoke lease
                self._revoke_lease_silent(self.lease_id)
                return False
        except Exception as e:
            self._revoke_lease_silent(self.lease_id)
            logger.error("Failed to acquire lock : %s ", e)
            return False

    def _revoke_lease_silent(self, lease_id: int):
        """Silently revoke the lease (ignore errors)."""
        try:
            req = rpc__pb2.LeaseRevokeRequest(ID=lease_id)
            self.lease_stub.LeaseRevoke(req)
        except Exception as e:
            logger.error("Failed to _revoke_lease_silent lock %s : %s ", lease_id, e)
            pass  # The lease may have expired; ignore it.
