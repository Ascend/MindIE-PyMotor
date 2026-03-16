# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import pytest
from unittest import mock

from motor.engine_server.core.health_collector import HealthCollector


@pytest.fixture
def mock_async_client():
    """Fixture to provide a mock async client instance"""
    return mock.AsyncMock()


@pytest.fixture
def mock_create_client(mock_async_client):
    """Fixture to mock AsyncSafeHTTPSClient.create_client classmethod"""
    with mock.patch('motor.engine_server.core.health_collector.AsyncSafeHTTPSClient') as mock_class:
        # Setup create_client to return an async context manager
        mock_context_manager = mock.AsyncMock()

        mock_context_manager.__aenter__ = mock.AsyncMock(return_value=mock_async_client)
        mock_context_manager.__aexit__ = mock.AsyncMock(return_value=None)

        mock_class.create_client.return_value = mock_context_manager
        yield mock_class, mock_async_client


@pytest.fixture
def mock_logger():
    """Fixture to mock the logger"""
    with mock.patch('motor.engine_server.core.health_collector.logger') as mock_logger_instance:
        yield mock_logger_instance


@pytest.fixture
def mock_config():
    """Fixture to create a properly configured mock config"""
    # Create mock config and server config
    mock_config = mock.MagicMock()
    mock_server_config = mock.MagicMock()
    mock_deploy_config = mock.MagicMock()
    mock_health_check_config = mock.MagicMock()

    mock_server_config.server_host = "127.0.0.1"
    mock_server_config.engine_port = 8080
    mock_deploy_config.infer_tls_config = None
    mock_health_check_config.health_collector_timeout = 2

    mock_deploy_config.health_check_config = mock_health_check_config
    mock_server_config.deploy_config = mock_deploy_config
    mock_config.get_server_config.return_value = mock_server_config

    return mock_config


@pytest.fixture
def health_collector(mock_config):
    """Fixture to create a HealthCollector instance with proper mock config"""
    return HealthCollector(mock_config)


class TestHealthCollector:
    """Tests for HealthCollector class"""

    def test_init(self, health_collector, mock_config):
        """Test __init__ method initialization"""
        # Verify initialization parameters
        assert health_collector.host == "127.0.0.1"
        assert health_collector.port == 8080
        assert health_collector.timeout == 2
        assert health_collector.address == "127.0.0.1:8080"
        assert health_collector._has_connected is False

        # Verify config method calls
        mock_config.get_server_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_is_healthy_success(self, health_collector, mock_create_client, mock_logger):
        """Test is_healthy method with successful health check"""
        mock_client_class, mock_async_client = mock_create_client

        # Setup mock response
        mock_response = mock.AsyncMock()
        mock_response.aread.return_value = b"True"
        mock_response.raise_for_status = mock.Mock()

        mock_async_client.get.return_value = mock_response

        # Call is_healthy method
        result = await health_collector.is_healthy()

        # Verify results
        assert result is True
        assert health_collector._has_connected is True

        # Verify HTTP client calls
        mock_client_class.create_client.assert_called_once_with(
            address="127.0.0.1:8080",
            tls_config=None,
            timeout=2,
        )
        mock_async_client.get.assert_awaited_once_with("/health")
        mock_response.raise_for_status.assert_called_once()
        mock_response.aread.assert_awaited_once()

        # Verify logger was not called (no exception)
        mock_logger.debug.assert_not_called()

    @pytest.mark.asyncio
    async def test_is_healthy_failure_healthy_false(self, health_collector, mock_create_client):
        """Test is_healthy method when health status is False"""
        mock_client_class, mock_async_client = mock_create_client

        # Setup mock response with "False"
        mock_response = mock.AsyncMock()
        mock_response.aread.return_value = b"False"
        mock_response.raise_for_status = mock.Mock()

        mock_async_client.get.return_value = mock_response

        # Call is_healthy method
        result = await health_collector.is_healthy()

        # Verify results
        assert result is False
        assert health_collector._has_connected is True

    @pytest.mark.asyncio
    async def test_is_healthy_http_error_after_connection(self, health_collector, mock_create_client, mock_logger):
        """Test is_healthy method with HTTP error after initial connection"""
        mock_client_class, mock_async_client = mock_create_client

        # Mark that we've had a successful connection before
        health_collector._has_connected = True

        # Setup mock client to raise exception
        mock_async_client.get.side_effect = Exception("Not Found")

        # Call is_healthy method
        result = await health_collector.is_healthy()

        # Verify results
        assert result is False
        assert health_collector._has_connected is True

        # Verify logger was called with debug message
        mock_logger.debug.assert_called_once()
        assert "Health check failed: Not Found" in mock_logger.debug.call_args[0][0]

    @pytest.mark.asyncio
    async def test_is_healthy_timeout_error_after_connection(self, health_collector, mock_create_client, mock_logger):
        """Test is_healthy method with timeout error after initial connection"""
        mock_client_class, mock_async_client = mock_create_client

        # Mark that we've had a successful connection before
        health_collector._has_connected = True

        # Setup mock client to raise timeout error
        mock_async_client.get.side_effect = Exception("Timeout")

        # Call is_healthy method
        result = await health_collector.is_healthy()

        # Verify results
        assert result is False
        assert health_collector._has_connected is True

        # Verify logger was called
        mock_logger.debug.assert_called_once()
        assert "Timeout" in mock_logger.debug.call_args[0][0]

    @pytest.mark.asyncio
    async def test_is_healthy_error_before_first_connection(self, health_collector, mock_create_client, mock_logger):
        """Test is_healthy method with error before first successful connection"""
        mock_client_class, mock_async_client = mock_create_client

        # Ensure _has_connected is False (default)
        assert health_collector._has_connected is False

        # Setup mock client to raise connection error
        mock_async_client.get.side_effect = Exception("Connection refused")

        # Verify exception is raised (not caught)
        with pytest.raises(Exception) as excinfo:
            await health_collector.is_healthy()

        assert "Connection refused" in str(excinfo.value)
        assert health_collector._has_connected is False

        # Verify logger was called with debug message
        mock_logger.debug.assert_called_once()
        assert "Connection refused" in mock_logger.debug.call_args[0][0]

    @pytest.mark.asyncio
    async def test_is_healthy_generic_exception_after_connection(self, health_collector, mock_create_client,
                                                                 mock_logger):
        """Test is_healthy method with generic exception after connection"""
        mock_client_class, mock_async_client = mock_create_client

        # Mark that we've had a successful connection before
        health_collector._has_connected = True

        # Setup mock client to raise generic exception
        mock_async_client.get.side_effect = Exception("Generic error")

        # Call is_healthy method
        result = await health_collector.is_healthy()

        # Verify results
        assert result is False
        assert health_collector._has_connected is True

        # Verify logger was called
        mock_logger.debug.assert_called_once()
        assert "Generic error" in mock_logger.debug.call_args[0][0]

    @pytest.mark.asyncio
    async def test_is_healthy_generic_exception_before_connection(self, health_collector, mock_create_client,
                                                                  mock_logger):
        """Test is_healthy method with generic exception before first connection"""
        mock_client_class, mock_async_client = mock_create_client

        # Setup mock client to raise generic exception
        mock_async_client.get.side_effect = Exception("Generic error")

        # Verify exception is raised
        with pytest.raises(Exception) as excinfo:
            await health_collector.is_healthy()

        assert "Generic error" in str(excinfo.value)
        assert health_collector._has_connected is False

        # Verify logger was called
        mock_logger.debug.assert_called_once()
