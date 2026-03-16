# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import os
import threading

from motor.common.resources.instance import Instance, PDRole
from motor.common.resources.endpoint import Endpoint
from motor.coordinator.domain import InstanceProvider
from motor.coordinator.scheduler.policy.base import BaseSchedulingPolicy
from motor.config.coordinator import CoordinatorConfig
from motor.common.utils.logger import get_logger
from motor.coordinator.models.request import RequestInfo
from motor.coordinator.api_client.conductor_api_client import ConductorApiClient, TENANT_ID
from motor.common.utils.singleton import ThreadSafeSingleton


logger = get_logger(__name__)


class KvCacheAffinityPolicy(BaseSchedulingPolicy):
    """
    KvCache Affinity Scheduler Policy implementation.
    Selects instances and endpoints in a kvcache-affinity fashion.
    """

    def __init__(self, instance_provider: InstanceProvider):
        super().__init__(instance_provider=instance_provider)
        self._instance_provider = instance_provider

        logger.info("KvCacheAffinityPolicy started.")

    @staticmethod
    def select_endpoint_from_list(
        instances: list[Instance],
        req_info: RequestInfo
    ) -> tuple[Instance, Endpoint] | None:
        """
        Select an endpoint with the least workload from the given instance.
        """
        encoded_ids = []
        messages = req_info.req_data.get("messages", None)
        if messages is not None:
            encoded_ids = TokenizerManager().apply_chat_template(messages)
        else:
            prompt = req_info.req_data.get("prompt", None)
            if prompt is not None:
                encoded_ids = TokenizerManager().encode(prompt)

        rsp = ConductorApiClient.query_conductor(instances, encoded_ids)
        tenant = rsp.get(TENANT_ID, None)
        if tenant is None:
            logger.warning(f"tenant is none")
            return None

        max_kv_gpu = 0
        max_kv_dp = 0
        selected_instance = None
        selected_endpoint = None
        selected_data_dp = {}
        for instance in instances:
            instance_data = tenant.get(f"vllm-prefill-{instance.id}", None)
            if instance_data is None:
                continue

            data_gpu = instance_data.get("GPU", 0)
            if data_gpu < max_kv_gpu:
                continue

            max_kv_gpu = data_gpu
            selected_instance = instance
            selected_data_dp = instance_data.get("DP", {})

        if selected_instance is None:
            logger.warning(f"selected_instance is None")
            return None

        if not selected_data_dp:
            logger.warning(f"selected_data_dp is None")
            return None

        for endpoint in selected_instance.endpoints.values():
            for ep in endpoint.values():
                kv_dp = selected_data_dp.get(f"{ep.id}", 0)
                if kv_dp < max_kv_dp:
                    continue

                max_kv_dp = kv_dp
                selected_endpoint = ep

        if selected_endpoint is None:
            logger.warning(f"selected_endpoint is None")
            return None
        logger.info(f"select_endpoint: {selected_instance.id}-{selected_endpoint.id}  max_kv_gpu:{max_kv_gpu}")
        return (selected_instance, selected_endpoint)

    def _select_instance(self, _: PDRole = None) -> Instance | None:
        """
        Select an instance with the least workload.
        """
        return None

    def _select_endpoint(self, _: Instance) -> Endpoint | None:
        """
        Select an endpoint with the least workload from the given instance.
        """
        return None


class TokenizerManager(ThreadSafeSingleton):
    """
    Tracer Manager class, Singleton class
    """

    def __init__(self, config: CoordinatorConfig | None = None):
        """TracerManager init"""
        # If the instance manager is already initialized, return.
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        self.config_lock = threading.RLock()

        if config is None:
            config = CoordinatorConfig()

        self.endpoint = config.tracer_config.endpoint

        self.tokenizer = None

        if config.prefill_kv_event_config.conductor_service == "":
            logger.info("conductor_service is empty. disable TokenizerManager!")
            return

        model_path = config.prefill_kv_event_config.model_path
        if model_path:
            os.environ['TORCH_DEVICE_BACKEND_AUTOLOAD'] = '0'
            from transformers import AutoTokenizer
            self.tokenizer = AutoTokenizer.from_pretrained(model_path)

        logger.info(f"TokenizerManager init.(model_path:{model_path})")

    def apply_chat_template(self, messages: str) -> list[int]:
        """
        When the inference API /v1/chat/completions is called, 
        this method is used for encoding.
        """
        if self.tokenizer is None:
            return []
        result = self.tokenizer.apply_chat_template(messages)
        return result

    def encode(self, prompt: str) -> list[int]:
        """
        When the inference API /v1/completions is called, 
        this method is used for encoding.
        """
        if self.tokenizer is None:
            return []
        result = self.tokenizer.encode(prompt)
        return result
