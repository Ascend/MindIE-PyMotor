# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 license for more details.

"""Tests for KvCacheAffinity"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import sys
import os

from motor.coordinator.scheduler.policy.kv_cache_affinity import KvCacheAffinityPolicy, TokenizerManager
from motor.common.resources.instance import Instance, PDRole
from motor.common.resources.endpoint import Endpoint
from motor.coordinator.models.request import RequestInfo
from motor.coordinator.api_client.conductor_api_client import ConductorApiClient, TENANT_ID
from motor.config.coordinator import CoordinatorConfig


class TestKvCacheAffinityPolicy(unittest.TestCase):
    """Test KvCacheAffinityPolicy Class"""

    def setUp(self):
        """Settings before the test"""
        self.mock_instance_provider = Mock()
        self.policy = KvCacheAffinityPolicy(self.mock_instance_provider)

    def test_init(self):
        """Test initialization."""
        self.assertEqual(self.policy._instance_provider, self.mock_instance_provider)


    @patch('motor.coordinator.scheduler.policy.kv_cache_affinity.ConductorApiClient.query_conductor')
    @patch('motor.coordinator.scheduler.policy.kv_cache_affinity.TokenizerManager')
    def test_select_endpoint_from_list_with_messages(self, mock_tokenizer_manager, mock_query_conductor):
        """Test select_endpoint_from_list function - use messages"""
        # Preparing Test Data
        mock_instance = Mock()
        mock_instance.id = "instance-1"
        instances = [mock_instance]
        
        mock_endpoint = Mock()
        mock_endpoint.id = "endpoint-1"
        mock_instance.endpoints = {"group": {"endpoint-1": mock_endpoint}}
        
        mock_req_info = Mock()
        mock_req_info.req_data = {"messages": [{"role": "user", "content": "hello"}]}
        
        # Mock the return value of TokenizerManager.
        mock_tokenizer = Mock()
        mock_tokenizer.apply_chat_template.return_value = [1, 2, 3]
        mock_tokenizer_manager.return_value = mock_tokenizer
        
        # Mock ConductorApiClient return value
        mock_query_conductor.return_value = {
            TENANT_ID: {
                "vllm-prefill-instance-1": {
                    "GPU": 100,
                    "DP": {"endpoint-1": 50}
                }
            }
        }
        
        # Performing the test
        result = KvCacheAffinityPolicy.select_endpoint_from_list(instances, mock_req_info)
        
        # verification result
        self.assertIsNotNone(result)
        self.assertEqual(result[0].id, "instance-1")
        self.assertEqual(result[1].id, "endpoint-1")

    @patch('motor.coordinator.scheduler.policy.kv_cache_affinity.ConductorApiClient.query_conductor')
    @patch('motor.coordinator.scheduler.policy.kv_cache_affinity.TokenizerManager')
    def test_select_endpoint_from_list_with_prompt(self, mock_tokenizer_manager, mock_query_conductor):
        """Test select_endpoint_from_list function - 使用 prompt"""
        # Preparing Test Data
        mock_instance = Mock()
        mock_instance.id = "instance-2"
        instances = [mock_instance]
        
        mock_endpoint = Mock()
        mock_endpoint.id = "endpoint-2"
        mock_instance.endpoints = {"group": {"endpoint-2": mock_endpoint}}
        
        mock_req_info = Mock()
        mock_req_info.req_data = {"prompt": "hello"}
        
        # Mock the return value of TokenizerManager.
        mock_tokenizer = Mock()
        mock_tokenizer.encode.return_value = [1, 2, 3]
        mock_tokenizer_manager.return_value = mock_tokenizer
        
        # Mock ConductorApiClient return value
        mock_query_conductor.return_value = {
            TENANT_ID: {
                "vllm-prefill-instance-2": {
                    "GPU": 200,
                    "DP": {"endpoint-2": 100}
                }
            }
        }
        
        # Performing the test
        result = KvCacheAffinityPolicy.select_endpoint_from_list(instances, mock_req_info)
        
        # verification result
        self.assertIsNotNone(result)
        self.assertEqual(result[0].id, "instance-2")
        self.assertEqual(result[1].id, "endpoint-2")

    @patch('motor.coordinator.api_client.conductor_api_client.ConductorApiClient.query_conductor')
    @patch('motor.coordinator.scheduler.policy.kv_cache_affinity.TokenizerManager')
    def test_select_endpoint_from_list_no_messages_or_prompt(self, mock_tokenizer_manager, mock_query_conductor):
        """Test select_endpoint_from_list function - 没有 messages 或 prompt"""
        # Preparing Test Data
        mock_instance = Mock()
        mock_instance.id = "instance-3"
        instances = [mock_instance]
        
        mock_req_info = Mock()
        mock_req_info.req_data = {}
        
        # Mock the return value of TokenizerManager.
        mock_tokenizer = Mock()
        mock_tokenizer_manager.return_value = mock_tokenizer
        
        mock_query_conductor.return_value = {}

        # Performing the test
        result = KvCacheAffinityPolicy.select_endpoint_from_list(instances, mock_req_info)
        
        # verification result
        self.assertIsNone(result)

    @patch('motor.coordinator.scheduler.policy.kv_cache_affinity.ConductorApiClient.query_conductor')
    @patch('motor.coordinator.scheduler.policy.kv_cache_affinity.TokenizerManager')
    def test_select_endpoint_from_list_no_tenant(self, mock_tokenizer_manager, mock_query_conductor):
        """Test select_endpoint_from_list function - 没有 tenant"""
        # Preparing Test Data
        mock_instance = Mock()
        mock_instance.id = "instance-4"
        instances = [mock_instance]
        
        mock_req_info = Mock()
        mock_req_info.req_data = {"prompt": "hello"}
        
        # Mock the return value of TokenizerManager.
        mock_tokenizer = Mock()
        mock_tokenizer.encode.return_value = [1, 2, 3]
        mock_tokenizer_manager.return_value = mock_tokenizer
        
        # Mock ConductorApiClient return value（没有 tenant）
        mock_query_conductor.return_value = {}
        
        # Performing the test
        result = KvCacheAffinityPolicy.select_endpoint_from_list(instances, mock_req_info)
        
        # verification result
        self.assertIsNone(result)

    @patch('motor.coordinator.scheduler.policy.kv_cache_affinity.ConductorApiClient.query_conductor')
    @patch('motor.coordinator.scheduler.policy.kv_cache_affinity.TokenizerManager')
    def test_select_endpoint_from_list_no_instance_data(self, mock_tokenizer_manager, mock_query_conductor):
        """Test select_endpoint_from_list function - no instance data"""
        # Preparing Test Data
        mock_instance = Mock()
        mock_instance.id = "instance-5"
        instances = [mock_instance]
        
        mock_req_info = Mock()
        mock_req_info.req_data = {"prompt": "hello"}
        
        # Mock the return value of TokenizerManager.
        mock_tokenizer = Mock()
        mock_tokenizer.encode.return_value = [1, 2, 3]
        mock_tokenizer_manager.return_value = mock_tokenizer
        
        # Mock ConductorApiClient return value
        mock_query_conductor.return_value = {
            TENANT_ID: {
                "vllm-prefill-instance-6": {
                    "GPU": 100,
                    "DP": {"endpoint-1": 50}
                }
            }
        }
        
        # Performing the test
        result = KvCacheAffinityPolicy.select_endpoint_from_list(instances, mock_req_info)
        
        # verification result
        self.assertIsNone(result)

    @patch('motor.coordinator.scheduler.policy.kv_cache_affinity.ConductorApiClient.query_conductor')
    @patch('motor.coordinator.scheduler.policy.kv_cache_affinity.TokenizerManager')
    def test_select_endpoint_from_list_no_selected_instance(self, mock_tokenizer_manager, mock_query_conductor):
        """Test the select_endpoint_from_list method. No instance is selected."""
        # Preparing Test Data
        mock_instance = Mock()
        mock_instance.id = "instance-7"
        instances = [mock_instance]
        
        mock_req_info = Mock()
        mock_req_info.req_data = {"prompt": "hello"}
        
        # Mock the return value of TokenizerManager.
        mock_tokenizer = Mock()
        mock_tokenizer.encode.return_value = [1, 2, 3]
        mock_tokenizer_manager.return_value = mock_tokenizer
        
        # Mock the return value of ConductorApiClient.
        mock_query_conductor.return_value = {
            TENANT_ID: {
                "instance-7": {
                    "GPU": 100,
                    "DP": {"endpoint-1": 50}
                }
            }
        }
        
        # Performing the test
        result = KvCacheAffinityPolicy.select_endpoint_from_list(instances, mock_req_info)
        
        # verification result
        self.assertIsNone(result)

    @patch('motor.coordinator.scheduler.policy.kv_cache_affinity.ConductorApiClient.query_conductor')
    @patch('motor.coordinator.scheduler.policy.kv_cache_affinity.TokenizerManager')
    def test_select_endpoint_from_list_no_selected_endpoint(self, mock_tokenizer_manager, mock_query_conductor):
        """Test select_endpoint_from_list function - Not selected endpoint"""
        # Preparing Test Data
        mock_instance = Mock()
        mock_instance.id = "instance-8"
        instances = [mock_instance]
        
        mock_endpoint = Mock()
        mock_endpoint.id = "endpoint-1"
        mock_instance.endpoints = {"group": {"endpoint-1": mock_endpoint}}
        
        mock_req_info = Mock()
        mock_req_info.req_data = {"prompt": "hello"}
        
        # Mock the return value of TokenizerManager.
        mock_tokenizer = Mock()
        mock_tokenizer.encode.return_value = [1, 2, 3]
        mock_tokenizer_manager.return_value = mock_tokenizer
        
        # Mock ConductorApiClient return value（DP is none）
        mock_query_conductor.return_value = {
            TENANT_ID: {
                "vllm-prefill-instance-8": {
                    "GPU": 100,
                    "DP": {}
                }
            }
        }
        
        # Performing the test
        result = KvCacheAffinityPolicy.select_endpoint_from_list(instances, mock_req_info)
        
        # verification result
        self.assertIsNone(result)

    def test_select_instance(self):
        """Test _select_instance function"""
        result = self.policy._select_instance()
        self.assertIsNone(result)

    def test_select_endpoint(self):
        """Test _select_endpoint function"""
        mock_instance = Mock()
        result = self.policy._select_endpoint(mock_instance)
        self.assertIsNone(result)


class TestTokenizerManagerFunction(unittest.TestCase):
    """Test TokenizerManager class"""

    @patch('motor.coordinator.scheduler.policy.kv_cache_affinity.CoordinatorConfig')
    @patch('transformers.AutoTokenizer')
    def test_init_with_model_path(self, mock_auto_tokenizer, mock_config_class):
        """Test tokenizer manager"""
        mock_config = Mock()
        mock_config.prefill_kv_event_config.conductor_service = "test_service"
        mock_config.prefill_kv_event_config.model_path = "/path/to/model"
        mock_config_class.return_value = mock_config
        
        # Mock tokenizer
        mock_tokenizer = Mock()
        mock_tokenizer.apply_chat_template.return_value = [1, 2, 3]
        mock_tokenizer.encode.return_value = [4, 5, 6]
        mock_auto_tokenizer.from_pretrained.return_value = mock_tokenizer
        
        # Create TokenizerManager
        tokenizer_manager = TokenizerManager(mock_config)
        
        # Verifying Initialization
        self.assertTrue(hasattr(tokenizer_manager, '_initialized'))
        self.assertEqual(tokenizer_manager.tokenizer, mock_tokenizer)


        # Performing the test
        result = tokenizer_manager.apply_chat_template([{"role": "user", "content": "hello"}])

        # verification result
        self.assertEqual(result, [1, 2, 3])

        # Performing the test
        result = tokenizer_manager.encode("hello")
        
        # verification result
        self.assertEqual(result, [4, 5, 6])

        
        # Set tokenizer None
        tokenizer_manager.tokenizer = None
        
        # Performing the test
        result = tokenizer_manager.apply_chat_template([{"role": "user", "content": "hello"}])
        
        # verification result
        self.assertEqual(result, [])

        # Performing the test
        result = tokenizer_manager.encode("hello")
        
        # verification result
        self.assertEqual(result, [])


class TestTokenizerManagerInitialize(unittest.TestCase):
    """Test TokenizerManager class"""

    def setUp(self):
        """Test setting"""
        # Clear singleton instance
        if hasattr(TokenizerManager, '_instance'):
            delattr(TokenizerManager, '_instance')

    @patch('motor.coordinator.scheduler.policy.kv_cache_affinity.CoordinatorConfig')
    def test_init_with_empty_conductor_service(self, mock_config_class):
        """Test initialize - null conductor_service"""
        mock_config = Mock()
        mock_config.prefill_kv_event_config.conductor_service = ""
        mock_config_class.return_value = mock_config
        
        # Create TokenizerManager
        tokenizer_manager = TokenizerManager(mock_config)
        
        # Verifying Initialization
        self.assertTrue(hasattr(tokenizer_manager, '_initialized'))
        self.assertIsNone(tokenizer_manager.tokenizer)

    def test_singleton_pattern(self):
        """Test singleton instance"""
        # First creation
        instance1 = TokenizerManager()
        
        # Second creation
        instance2 = TokenizerManager()
        
        # Verify that the instances are the same.
        self.assertIs(instance1, instance2)