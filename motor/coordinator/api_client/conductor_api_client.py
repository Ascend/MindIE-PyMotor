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

from typing import Any

from motor.common.utils.logger import get_logger
from motor.common.resources.instance import Instance, Endpoint, PDRole
from motor.common.utils.http_client import SafeHTTPSClient
from motor.config.coordinator import CoordinatorConfig


TENANT_ID = "default"
logger = get_logger(__name__)


class ConductorApiClient():
    coordinator_config = CoordinatorConfig.from_json()

    @staticmethod
    def register_kv_instance(
        instances: list[Instance]
    ) -> None:
        """
        register_kv_instance.

        :returns:
        """
        logger.info("register_kv_instance started.")

        for instance in instances:
            if instance.role != PDRole.ROLE_P:
                continue
            for endpoint in instance.endpoints.values():
                for ep in endpoint.values():
                    ConductorApiClient().register_post(instance, ep)

    @staticmethod
    def unregister_kv_instance(
        instances: list[Instance]
    ) -> None:
        """
        unregister_kv_instance.

        :returns:
        """
        logger.info("unregister_kv_instance started.")

        for instance in instances:
            if instance.role != PDRole.ROLE_P:
                continue
            for endpoint in instance.endpoints.values():
                for ep in endpoint.values():
                    ConductorApiClient().unregister_post(instance, ep)

    @classmethod
    def register_post(
        cls, instance: Instance, endpoint: Endpoint
    ) -> None:
        """
        unregister_kv_instance.

        :returns:
        """
        prefill_kv_event_config = cls.coordinator_config.prefill_kv_event_config
        kv_endpoints = prefill_kv_event_config.endpoint.split("*:")
        if kv_endpoints.__len__() != 2:
            logger.error(f"kv_endpoints size not 2  :  {prefill_kv_event_config.endpoint}")
            return

        instance_id = f"vllm-prefill-{instance.id}"
        register_data: dict = {
            "endpoint": f"{kv_endpoints[0]}{endpoint.ip}:{str(int(kv_endpoints[1]) + endpoint.id)}", 
            "type": prefill_kv_event_config.engine_type,
            "modelname": instance.model_name,
            "block_size": prefill_kv_event_config.block_size,
            "instance_id": instance_id,
            "dp_rank": endpoint.id,
        }
        if TENANT_ID != "default":
            register_data["tenant_id"] = TENANT_ID


        if prefill_kv_event_config.replay_endpoint != "":
            replay_endpoints = prefill_kv_event_config.replay_endpoint.split("*:")
            if replay_endpoints.__len__() == 2:
                replay_endpoint = f"{replay_endpoints[0]}{endpoint.ip}:{str(int(replay_endpoints[1]) + endpoint.id)}"
                register_data["replay_endpoint"] = replay_endpoint

        client_args = {
            "address": f"{prefill_kv_event_config.conductor_service}:{prefill_kv_event_config.http_server_port}"
        }
        try:
            with SafeHTTPSClient(timeout=2, **client_args) as client:
                client.post("/register", register_data)
                logger.info(f"Register success! {instance_id}")

        except Exception as e:
            logger.error(
                "Exception occurred while register to controller at %s: %s",
                client_args.get('address', 'unknown'), e
            )
        logger.info(f"register_data : {register_data}")
        return

    @classmethod
    def unregister_post(
        cls, instance: Instance, endpoint: Endpoint
    ) -> None:
        """
        unregister_kv_instance.

        :returns:
        """
        prefill_kv_event_config = cls.coordinator_config.prefill_kv_event_config
        instance_id = f"vllm-prefill-{instance.id}"
        register_data: dict = {
            "type": prefill_kv_event_config.engine_type,
            "modelname": instance.model_name,
            "block_size": prefill_kv_event_config.block_size,
            "instance_id": instance_id,
            "dp_rank": endpoint.id,
        }
        if TENANT_ID != "default":
            register_data["tenant_id"] = TENANT_ID

        client_args = {
            "address": f"{prefill_kv_event_config.conductor_service}:{prefill_kv_event_config.http_server_port}"
        }
        try:
            with SafeHTTPSClient(timeout=2, **client_args) as client:
                client.post("/unregister", register_data)
                logger.info(f"UnRegister success! {instance_id}")

        except Exception as e:
            logger.error(
                "Exception occurred while register to controller at %s: %s",
                client_args.get('address', 'unknown'), e
            )
        logger.info(f"unregister_data : {register_data}")
        return

    @classmethod
    def query_conductor(
        cls, instances: list[Instance], encoded_ids: list[int]
    ) -> dict[str, Any]:
        """
        unregister_kv_instance.

        :returns:
        """
        prefill_kv_event_config = cls.coordinator_config.prefill_kv_event_config
        query_data: dict = {
            "model": instances[0].model_name,
            "block_size": prefill_kv_event_config.block_size,
            "token_ids": encoded_ids,
        }
        if TENANT_ID != "default":
            query_data["tenant_id"] = TENANT_ID

        logger.debug(f"query_data : {query_data}")

        client_args = {
            "address": f"{prefill_kv_event_config.conductor_service}:{prefill_kv_event_config.http_server_port}"
        }
        try:
            with SafeHTTPSClient(timeout=2, **client_args) as client:
                response = client.post("/query", query_data)
                logger.info(f"query success! {response}")
                return response
        except Exception as e:
            logger.error(
                "Exception occurred while register to controller at %s: %s",
                client_args.get('address', 'unknown'), e
            )
        return {}