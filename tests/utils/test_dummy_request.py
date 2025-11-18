import pytest
from unittest.mock import Mock, patch
from requests.exceptions import Timeout, RequestException
from motor.common.resources.endpoint import Endpoint
from motor.common.utils.dummy_request import DummyRequestUtil


class TestDummyRequestUtil:
    """DummyRequestUtil unit test class"""

    @pytest.fixture
    def mock_endpoint(self):
        """Create mock Endpoint"""
        endpoint = Mock(spec=Endpoint)
        endpoint.id = 1
        endpoint.ip = "127.0.0.1"
        endpoint.business_port = "8080"
        return endpoint

    @pytest.fixture
    def mock_config(self):
        """Create mock config"""
        config = Mock()
        config.dummy_request_timeout = 5.0
        config.dummy_request_endpoint = "/v1/completions"
        config.dummy_request_body = {
            'model': 'test-model',
            'prompt': 'Health check. Please respond with OK only.',
            'max_tokens': 3,
            'temperature': 0.1,
            'top_p': 0.9,
            'stream': False
        }
        return config

    @pytest.fixture
    def dummy_request_util(self, mock_config):
        """Create DummyRequestUtil instance"""
        with patch('motor.config.coordinator.CoordinatorConfig') as mock_config_class:
            mock_config_instance = Mock()
            mock_config_instance.health_check_config = mock_config
            mock_config_class.return_value = mock_config_instance
            
            util = DummyRequestUtil()
            return util

    def test_init_creates_http_session(self, dummy_request_util):
        """Test that HTTP session is created during init"""
        assert hasattr(dummy_request_util, '_http_session')
        assert dummy_request_util._http_session is not None

    def test_send_dummy_request_success(self, dummy_request_util, mock_endpoint):
        """Test successful dummy request"""
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {"text": "OK"}
            ]
        }
        
        with patch.object(dummy_request_util._http_session, 'post', return_value=mock_response):
            result = dummy_request_util.send_dummy_request(mock_endpoint)
            
            assert result is True
            # Verify correct URL was constructed
            dummy_request_util._http_session.post.assert_called_once()
            call_args = dummy_request_util._http_session.post.call_args
            assert f"http://{mock_endpoint.ip}:{mock_endpoint.business_port}{dummy_request_util.config.dummy_request_endpoint}" in call_args[0]

    def test_send_dummy_request_missing_ip_port(self, dummy_request_util):
        """Test dummy request with missing IP or port"""
        endpoint = Mock(spec=Endpoint)
        endpoint.id = 1
        endpoint.ip = None
        endpoint.port = "8080"
        
        result = dummy_request_util.send_dummy_request(endpoint)
        assert result is False
        
        endpoint.ip = "127.0.0.1"
        endpoint.port = None
        
        result = dummy_request_util.send_dummy_request(endpoint)
        assert result is False

    def test_send_dummy_request_http_error(self, dummy_request_util, mock_endpoint):
        """Test dummy request with HTTP error"""
        # Mock error response
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.json.return_value = {}
        
        with patch.object(dummy_request_util._http_session, 'post', return_value=mock_response):
            result = dummy_request_util.send_dummy_request(mock_endpoint)
            
            assert result is False

    def test_send_dummy_request_timeout(self, dummy_request_util, mock_endpoint):
        """Test dummy request timeout"""
        with patch.object(dummy_request_util._http_session, 'post', side_effect=Timeout()):
            result = dummy_request_util.send_dummy_request(mock_endpoint)
            
            assert result is False

    def test_send_dummy_request_connection_error(self, dummy_request_util, mock_endpoint):
        """Test dummy request connection error"""
        with patch.object(dummy_request_util._http_session, 'post', side_effect=RequestException("Connection error")):
            result = dummy_request_util.send_dummy_request(mock_endpoint)
            
            assert result is False

    def test_send_dummy_request_unexpected_error(self, dummy_request_util, mock_endpoint):
        """Test dummy request with unexpected error"""
        with patch.object(dummy_request_util._http_session, 'post', side_effect=Exception("Unexpected error")):
            result = dummy_request_util.send_dummy_request(mock_endpoint)
            
            assert result is False

    def test_validate_response_success(self, dummy_request_util):
        """Test successful response validation"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {"text": "OK response"}
            ]
        }
        
        result = dummy_request_util._validate_response(mock_response)
        assert result is True

    def test_validate_response_missing_choices(self, dummy_request_util):
        """Test response validation with missing choices"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "other_field": "value"
        }
        
        result = dummy_request_util._validate_response(mock_response)
        assert result is False

    def test_validate_response_empty_choices(self, dummy_request_util):
        """Test response validation with empty choices"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": []
        }
        
        result = dummy_request_util._validate_response(mock_response)
        assert result is False

    def test_validate_response_missing_text(self, dummy_request_util):
        """Test response validation with missing text"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {"other_field": "value"}
            ]
        }
        
        result = dummy_request_util._validate_response(mock_response)
        assert result is False

    def test_validate_response_empty_text(self, dummy_request_util):
        """Test response validation with empty text"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {"text": ""}
            ]
        }
        
        result = dummy_request_util._validate_response(mock_response)
        assert result is False

    def test_validate_response_whitespace_text(self, dummy_request_util):
        """Test response validation with whitespace-only text"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {"text": "   "}
            ]
        }
        
        result = dummy_request_util._validate_response(mock_response)
        assert result is False

    def test_validate_response_json_error(self, dummy_request_util):
        """Test response validation with JSON error"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")
        
        result = dummy_request_util._validate_response(mock_response)
        assert result is False

    def test_validate_response_wrong_status_code(self, dummy_request_util):
        """Test response validation with wrong status code"""
        mock_response = Mock()
        mock_response.status_code = 400
        
        result = dummy_request_util._validate_response(mock_response)
        assert result is False

    def test_get_completion_request(self, dummy_request_util):
        """Test completion request generation"""
        request_data = dummy_request_util._get_completion_request()
        
        expected_fields = ["model", "prompt", "max_tokens", "temperature", "top_p", "stream"]
        for field in expected_fields:
            assert field in request_data
        
        # Verify values match config
        assert request_data["model"] == dummy_request_util.config.dummy_request_body['model']
        assert request_data["prompt"] == dummy_request_util.config.dummy_request_body['prompt']
        assert request_data["max_tokens"] == dummy_request_util.config.dummy_request_body['max_tokens']

    def test_get_completion_request_default_values(self, mock_config):
        """Test completion request with default values"""
        # Create config without dummy_request_body
        mock_config.dummy_request_body = {}
        
        with patch('motor.config.coordinator.CoordinatorConfig') as mock_config_class:
            mock_config_instance = Mock()
            mock_config_instance.health_check_config = mock_config
            mock_config_class.return_value = mock_config_instance
            
            util = DummyRequestUtil()
            request_data = util._get_completion_request()
            
            # Should use default values
            assert request_data["model"] == "test-model"
            assert "Health check" in request_data["prompt"]
            assert request_data["max_tokens"] == 3

    def test_close(self, dummy_request_util):
        """Test closing HTTP session"""
        with patch.object(dummy_request_util._http_session, 'close') as mock_close:
            dummy_request_util.close()
            mock_close.assert_called_once()

    def test_request_headers(self, dummy_request_util, mock_endpoint):
        """Test that correct headers are sent"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {"text": "OK"}
            ]
        }
        
        with patch.object(dummy_request_util._http_session, 'post', return_value=mock_response):
            dummy_request_util.send_dummy_request(mock_endpoint)
            
            # Verify headers include Content-Type
            call_kwargs = dummy_request_util._http_session.post.call_args[1]
            assert "headers" in call_kwargs
            assert call_kwargs["headers"]["Content-Type"] == "application/json"

    def test_request_timeout(self, dummy_request_util, mock_endpoint):
        """Test that timeout is set correctly"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {"text": "OK"}
            ]
        }
        
        with patch.object(dummy_request_util._http_session, 'post', return_value=mock_response):
            dummy_request_util.send_dummy_request(mock_endpoint)
            
            # Verify timeout is set
            call_kwargs = dummy_request_util._http_session.post.call_args[1]
            assert "timeout" in call_kwargs
            assert call_kwargs["timeout"] == dummy_request_util.config.dummy_request_timeout