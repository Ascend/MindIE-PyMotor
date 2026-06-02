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
from unittest.mock import Mock, patch
import json
import pytest
from copy import deepcopy

from motor.coordinator.scheduler.policy.kv_cache_affinity import KvCacheAffinityPolicy, TokenizerManager
from motor.coordinator.api_client.conductor_api_client import  TENANT_ID
from motor.coordinator.scheduler.policy.utils import preprocess_input, exchange_arguments, exchange_tool_content, exchange_tools


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
        mock_instance.get_all_endpoints.return_value = tuple[Mock, ...]([mock_endpoint])
        
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
        mock_instance.get_all_endpoints.return_value = tuple[Mock, ...]([mock_endpoint])
        
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


class TestExchangeArguments:
    """Test exchange_arguments function"""
    
    def test_valid_tool_call_arguments_string(self):
        """Test: Valid tool call parameter string converted to JSON object"""
        message = {
            "tool_calls": [
                {
                    "function": {
                        "arguments": '{"city": "Beijing", "temperature": 25}'
                    }
                }
            ]
        }
        exchange_arguments(message)
        
        assert isinstance(message["tool_calls"][0]["function"]["arguments"], dict)
        assert message["tool_calls"][0]["function"]["arguments"]["city"] == "Beijing"
        assert message["tool_calls"][0]["function"]["arguments"]["temperature"] == 25
    
    def test_no_tool_calls_key(self):
        """Test: message not have tool_calls key"""
        message = {"role": "user", "content": "Hello"}
        original = deepcopy(message)
        exchange_arguments(message)
        assert message == original
    
    def test_tool_calls_missing_function(self):
        """Test: tool_calls not have function key"""
        message = {
            "tool_calls": [
                {"id": "call_123", "type": "function"}
            ]
        }
        original = deepcopy(message)
        exchange_arguments(message)
        assert message == original
    
    def test_arguments_already_dict(self):
        """Test: arguments is dict"""
        message = {
            "tool_calls": [
                {
                    "function": {
                        "arguments": {"city": "Shanghai"}
                    }
                }
            ]
        }
        original = deepcopy(message)
        exchange_arguments(message)
        assert message["tool_calls"][0]["function"]["arguments"] == {"city": "Shanghai"}
    
    def test_invalid_json_string(self):
        """Test: invalid json string"""
        message = {
            "tool_calls": [
                {
                    "function": {
                        "arguments": '{"city": "Beijing", invalid json}'
                    }
                }
            ]
        }
        with pytest.raises(json.JSONDecodeError):
            exchange_arguments(message)
    
    def test_multiple_tool_calls(self):
        """Test: multiple tool calls"""
        message = {
            "tool_calls": [
                {"function": {"arguments": '{"tool": "tool1", "value": 1}'}},
                {"function": {"arguments": '{"tool": "tool2", "value": 2}'}}
            ]
        }
        exchange_arguments(message)
        
        for i, tool in enumerate(message["tool_calls"]):
            assert isinstance(tool["function"]["arguments"], dict)
            assert tool["function"]["arguments"]["tool"] == f"tool{i+1}"
            assert tool["function"]["arguments"]["value"] == i+1


class TestExchangeToolContent:
    """Test exchange_tool_content function"""
    
    def test_tool_role_with_string_content(self):
        """Test: role is tool, content is str"""
        message = {
            "role": "tool",
            "content": "Tool execution result"
        }
        exchange_tool_content(message)
        
        expected = "{'type': 'text', 'text': 'Tool execution result'}"
        assert message["content"] == expected
    
    def test_tool_role_with_dict_content(self):
        """Test: role is tool, content is dict"""
        message = {
            "role": "tool",
            "content": {"type": "image", "data": "base64data"}
        }
        original = deepcopy(message)
        exchange_tool_content(message)
        assert message["content"] == original["content"]
    
    def test_no_role_key(self):
        """Test: message not haverolekey"""
        message = {"content": "Some content"}
        original = deepcopy(message)
        exchange_tool_content(message)
        assert message == original
    
    def test_role_not_tool(self):
        """Test: role is not tool"""
        message = {
            "role": "user",
            "content": "User message"
        }
        original = deepcopy(message)
        exchange_tool_content(message)
        assert message == original
    
    def test_no_content_key(self):
        """Test: message not havecontentkey"""
        message = {"role": "tool", "tool_call_id": "call_123"}
        original = deepcopy(message)
        exchange_tool_content(message)
        assert message == original
    
    def test_empty_string_content(self):
        """Test: content is "" """
        message = {
            "role": "tool",
            "content": ""
        }
        exchange_tool_content(message)
        expected = "{'type': 'text', 'text': ''}"
        assert message["content"] == expected


class TestExchangeTools:
    """Test exchange_tools function"""
    
    def test_sort_tool_fields_by_priority(self):
        """Test: Sort tool fields by priority"""
        tool = {
            "function": {
                "parameters": {"type": "object"},
                "description": "Test tool description",
                "name": "test_tool"
            }
        }
        exchange_tools(tool)
        
        function_keys = list(tool["function"].keys())
        assert function_keys == ["name", "description", "parameters"]
    
    def test_partial_fields(self):
        """Test: Only some fields"""
        tool = {
            "function": {
                "parameters": {"type": "object"},
                "name": "partial_tool"
            }
        }
        exchange_tools(tool)
        
        function_keys = list(tool["function"].keys())
        assert function_keys == ["name", "parameters"]
    
    def test_no_function_key(self):
        """Test: tool not have function key"""
        tool = {"type": "custom", "id": "tool_123"}
        original = deepcopy(tool)
        exchange_tools(tool)
        assert tool == original
    
    def test_unknown_fields(self):
        """Test: Case with unknown fields"""
        tool = {
            "function": {
                "name": "test",
                "custom_field": "value",
                "description": "desc",
                "another_field": 123
            }
        }
        exchange_tools(tool)
        
        function_keys = list(tool["function"].keys())

        assert function_keys[0] == "name"
        assert function_keys[1] == "description"
    
    def test_all_priority_fields(self):
        """Test: Includes all priority fields"""
        tool = {
            "function": {
                "extra": "extra_value",
                "name": "test",
                "description": "desc",
                "parameters": {"type": "object"}
            }
        }
        exchange_tools(tool)
        
        function_keys = list(tool["function"].keys())
        assert function_keys[:3] == ["name", "description", "parameters"]


class TestPreprocessInput:
    """Test preprocess_input function"""
    
    def test_basic_message_processing(self):
        """Test: basic message processing"""
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "arguments": '{"city": "Beijing"}'
                        }
                    }
                ]
            },
            {
                "role": "tool",
                "content": "Weather data"
            }
        ]
        
        processed_messages, processed_tools = preprocess_input(messages)
        
        # test tool_calls arguments exchange
        assert isinstance(processed_messages[1]["tool_calls"][0]["function"]["arguments"], dict)
        # test tool role content exchange
        assert processed_messages[2]["content"] == "{'type': 'text', 'text': 'Weather data'}"
        assert processed_tools is None
    
    def test_with_tools(self):
        "Test: List of included tools"
        messages = [{"role": "user", "content": "Call a tool"}]
        tools = [
            {
                "function": {
                    "parameters": {"type": "object"},
                    "description": "Test tool",
                    "name": "test_tool"
                }
            }
        ]
        
        processed_messages, processed_tools = preprocess_input(messages, tools)
        
        assert processed_tools is not None
        assert list(processed_tools[0]["function"].keys()) == ["name", "description", "parameters"]
    
    def test_deep_copy_messages(self):
        """Test: Original message will not be modified"""
        original_messages = [
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"arguments": '{"key": "value"}'}}
                ]
            }
        ]
        
        processed_messages, _ = preprocess_input(original_messages)
        
        assert isinstance(original_messages[1]["tool_calls"][0]["function"]["arguments"], str)
        assert isinstance(processed_messages[1]["tool_calls"][0]["function"]["arguments"], dict)
    
    def test_deep_copy_tools(self):
        """Test: The original tool list will not be modified"""
        original_tools = [
            {
                "function": {
                    "parameters": {"type": "object"},
                    "description": "desc",
                    "name": "tool"
                }
            }
        ]
        
        _, processed_tools = preprocess_input([{"role": "user", "content": "hi"}], original_tools)
        
        original_keys = list(original_tools[0]["function"].keys())
        assert original_keys == ["parameters", "description", "name"]
        
        processed_keys = list(processed_tools[0]["function"].keys())
        assert processed_keys == ["name", "description", "parameters"]
    
    def test_empty_messages(self):
        """Test: Empty message list"""
        messages = []
        processed_messages, processed_tools = preprocess_input(messages)
        
        assert processed_messages == []
        assert processed_tools is None
    
    def test_none_tools(self):
        """Test: tools is None"""
        messages = [{"role": "user", "content": "test"}]
        processed_messages, processed_tools = preprocess_input(messages, None)
        
        assert processed_messages == messages
        assert processed_tools is None
    
    def test_empty_tools_list(self):
        """Test: Empty tools list"""
        messages = [{"role": "user", "content": "test"}]
        processed_messages, processed_tools = preprocess_input(messages, [])
        
        assert processed_messages == messages
        assert processed_tools == None
    
    def test_complex_scenario(self):
        """Test: Complex Scenario - Multiple messages and multiple tools"""
        messages = [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "Get weather and time"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"arguments": '{"city": "Beijing"}'}},
                    {"function": {"arguments": '{"timezone": "UTC"}'}}
                ]
            },
            {"role": "tool", "content": "Weather: 25°C"},
            {"role": "tool", "content": "Time: 14:00"}
        ]
        
        tools = [
            {"function": {"parameters": {}, "description": "Weather tool", "name": "get_weather"}},
            {"function": {"parameters": {}, "description": "Time tool", "name": "get_time"}}
        ]
        
        processed_messages, processed_tools = preprocess_input(messages, tools)
        
        # test message processe
        for tool_call in processed_messages[2]["tool_calls"]:
            assert isinstance(tool_call["function"]["arguments"], dict)
        
        for msg in processed_messages[3:]:
            if msg["role"] == "tool":
                assert "type" in msg["content"] and "text" in msg["content"]
        
        # test tool processe
        for tool in processed_tools:
            assert list(tool["function"].keys())[0] == "name"