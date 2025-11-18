#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import requests
from requests.exceptions import RequestException, Timeout
from motor.common.utils.logger import get_logger
from motor.common.resources.endpoint import Endpoint
from motor.config.coordinator import CoordinatorConfig

logger = get_logger(__name__)


class DummyRequestUtil:
    """
    Utility class for sending dummy inference requests compatible with standard formats
    """
    
    def __init__(self):
        self._http_session = requests.Session()
        self.config = CoordinatorConfig().health_check_config

    @staticmethod
    def _validate_response(response) -> bool:
        """
        Validate response format
        
        Args:
            response: HTTP response object
            
        Returns:
            bool: True if response is valid, False otherwise
        """
        if response.status_code != 200:
            logger.warning(f"Dummy request failed with status: {response.status_code}")
            return False
        
        try:
            response_data = response.json()
            
            if not isinstance(response_data, dict):
                return False
            
            if 'choices' not in response_data:
                return False
            
            choices = response_data['choices']
            if not isinstance(choices, list) or len(choices) == 0:
                return False
            
            choice = choices[0]
            if 'text' in choice:
                text = choice['text']
                return isinstance(text, str) and len(text.strip()) > 0
            
            return False
            
        except (ValueError, KeyError, IndexError, TypeError) as e:
            logger.warning(f"Invalid response format: {e}")
            return False
            
    def send_dummy_request(self, endpoint: Endpoint) -> bool:
        """
        Send dummy inference request to instance
        
        Args:
            endpoint: Target endpoint
            
        Returns:
            bool: True if request successful, False otherwise
        """
        try:
            if not endpoint.ip or not endpoint.business_port:
                logger.warning(f"Endpoint {endpoint.id} IP or port not available")
                return False
            
            url_path = getattr(self.config, 'dummy_request_endpoint', '/v1/completions')
            request_data = self._get_completion_request()
            
            url = f"http://{endpoint.ip}:{endpoint.business_port}{url_path}"
            
            response = self._http_session.post(
                url,
                json=request_data,
                timeout=self.config.dummy_request_timeout,
                headers={"Content-Type": "application/json"}
            )
            
            return self._validate_response(response)
                
        except Timeout:
            logger.warning(f"Dummy request to endpoint {endpoint.id} timeout")
            return False
        except RequestException as e:
            logger.warning(f"Dummy request to endpoint {endpoint.id} failed: {e}")
            return False
        except Exception as e:
            logger.warning(f"Unexpected error during dummy request to endpoint {endpoint.id}: {e}")
            return False

    def close(self):
        """Close HTTP session"""
        self._http_session.close()

    def _get_completion_request(self) -> dict:
        """Get completion request for health check"""
        request_config = getattr(self.config, 'dummy_request_body', {})
        
        return {
            "model": request_config.get('model', 'test-model'),
            "prompt": request_config.get('prompt', 'Health check. Please respond with OK only.'),
            "max_tokens": request_config.get('max_tokens', 3),
            "temperature": request_config.get('temperature', 0.1),
            "top_p": request_config.get('top_p', 0.9),
            "stream": request_config.get('stream', False)
        }