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
from fastapi import Request
from motor.engine_server.core.http_server import HttpServer

@pytest.fixture(autouse=True)
def setup_test_fixtures(
        request,
        mock_uvicorn,
        mock_factories,
        mock_multiprocessing,
        mock_logger,
        mock_cert_util,
        mock_fastapi_app
):
    request.cls.mock_uvicorn = mock_uvicorn
    request.cls.mock_factories = mock_factories
    request.cls.mock_multiprocessing = mock_multiprocessing
    request.cls.mock_logger = mock_logger
    request.cls.mock_cert_util = mock_cert_util
    request.cls.mock_fastapi_app = mock_fastapi_app
    yield


@pytest.fixture
def mock_uvicorn():
    """Mock uvicorn module"""
    with mock.patch('motor.engine_server.core.http_server.uvicorn') as mock_uvicorn_module:
        mock_config = mock.MagicMock()
        mock_server = mock.MagicMock()
        mock_uvicorn_module.Config.return_value = mock_config
        mock_uvicorn_module.Server.return_value = mock_server
        yield mock_uvicorn_module, mock_config, mock_server


@pytest.fixture
def mock_factories():
    """Mock factory classes"""
    with mock.patch('motor.engine_server.core.http_server.LifespanFactory') as mock_lifespan_factory_cls, \
            mock.patch('motor.engine_server.core.http_server.ProtocolFactory') as mock_protocol_factory_cls:
        mock_lifespan_factory = mock.MagicMock()
        mock_protocol_factory = mock.MagicMock()
        mock_lifespan_factory_cls.return_value = mock_lifespan_factory
        mock_protocol_factory_cls.return_value = mock_protocol_factory
        yield mock_lifespan_factory_cls, mock_lifespan_factory, mock_protocol_factory_cls, mock_protocol_factory


@pytest.fixture
def mock_multiprocessing():
    """Mock multiprocessing.Event and Process in the target module"""
    with mock.patch('motor.engine_server.core.http_server.multiprocessing.Event') as mock_event_cls, \
            mock.patch('motor.engine_server.core.http_server.multiprocessing.Process') as mock_process_cls:
        mock_event = mock.MagicMock()
        mock_process = mock.MagicMock()
        mock_process.is_alive.return_value = False
        mock_event_cls.return_value = mock_event
        mock_process_cls.return_value = mock_process
        yield mock_event_cls, mock_event, mock_process_cls, mock_process


@pytest.fixture
def mock_logger():
    with mock.patch('motor.engine_server.core.http_server.logger') as mock_logger_instance:
        yield mock_logger_instance


@pytest.fixture
def mock_cert_util():
    """Mock CertUtil"""
    with mock.patch('motor.engine_server.core.http_server.CertUtil') as mock_cert_util_cls:
        yield mock_cert_util_cls


@pytest.fixture
def mock_fastapi_app():
    """Mock FastAPI app and its methods"""
    with mock.patch('motor.engine_server.core.http_server.FastAPI') as mock_fastapi_cls:
        mock_app = mock.MagicMock()
        mock_app.state = mock.MagicMock()
        mock_fastapi_cls.return_value = mock_app

        registered_handlers = {}

        def post_decorator(path):
            def wrapper(endpoint):
                registered_handlers[path] = endpoint
                return endpoint

            return wrapper

        def get_decorator(path):
            def wrapper(endpoint):
                registered_handlers[path] = endpoint
                return endpoint

            return wrapper

        mock_app.post.side_effect = post_decorator
        mock_app.get.side_effect = get_decorator

        mock_app.registered_handlers = registered_handlers
        yield mock_fastapi_cls, mock_app, registered_handlers


class TestHttpServer:
    """Tests for HttpServer class"""

    def setup_method(self):
        """Setup test fixtures before each test method"""
        # Create mock config and server config
        self.mock_config = mock.MagicMock()
        self.mock_server_config = mock.MagicMock()
        self.mock_deploy_config = mock.MagicMock()

        # Set default server config values
        self.mock_server_config.server_host = "0.0.0.0"
        self.mock_server_config.engine_port = 8000
        self.mock_server_config.engine_type = "VLLM"
        self.mock_server_config.deploy_config = self.mock_deploy_config
        self.mock_deploy_config.infer_tls_config = None
        self.mock_config.get_server_config.return_value = self.mock_server_config

        # Create mock init params
        self.mock_init_params = {"input_address": "ipc:///tmp/input", "output_address": "ipc:///tmp/output"}

        # Unpack factory mocks
        (mock_lifespan_factory_cls, mock_lifespan_factory,
         mock_protocol_factory_cls, mock_protocol_factory) = self.mock_factories
        self.mock_lifespan = mock.MagicMock()
        mock_lifespan_factory.get_lifespan.return_value = self.mock_lifespan

        self.mock_chat_request = mock.MagicMock()
        self.mock_completion_request = mock.MagicMock()
        mock_protocol_factory.load_protocol_classes.return_value = (
            self.mock_chat_request, self.mock_completion_request
        )

        # Unpack multiprocessing mocks
        mock_event_cls, mock_event, mock_process_cls, mock_process = self.mock_multiprocessing
        self.mock_event = mock_event
        self.mock_process = mock_process

        # Unpack FastAPI mocks
        mock_fastapi_cls, mock_app, registered_handlers = self.mock_fastapi_app
        self.mock_app = mock_app
        self.registered_handlers = registered_handlers

        # Create HttpServer instance
        self.http_server = HttpServer(self.mock_config, self.mock_init_params)

    def test_init(self):
        """Test __init__ method initialization"""
        mock_event_cls, mock_event, mock_process_cls, mock_process = self.mock_multiprocessing
        mock_fastapi_cls, mock_app, _ = self.mock_fastapi_app

        # Verify basic attributes
        assert self.http_server.host == "0.0.0.0"
        assert self.http_server.port == 8000
        assert self.http_server.engine_type == "VLLM"
        assert self.http_server.lifespan == self.mock_lifespan

        # Verify FastAPI app creation
        mock_fastapi_cls.assert_called_once_with(
            title="EngineServer HttpServer",
            lifespan=self.mock_lifespan
        )

        # Verify protocol classes are loaded as instance attributes
        assert self.http_server.chat_completion_request == self.mock_chat_request
        assert self.http_server.completion_request == self.mock_completion_request

        # Verify multiprocessing setup
        mock_event_cls.assert_called_once()
        mock_process_cls.assert_called_once_with(
            target=self.http_server._run_server,
            name="http_server_process",
            daemon=True
        )

    def test_load_protocol_classes(self):
        """Test _load_protocol_classes method"""
        _, _, _, mock_protocol_factory = self.mock_factories
        mock_protocol_factory.load_protocol_classes.reset_mock()
        self.http_server._load_protocol_classes()
        mock_protocol_factory.load_protocol_classes.assert_called_once_with("VLLM")

    def test_run_server_not_running(self):
        """Test run method when server process is not running"""
        mock_event_cls, mock_event, mock_process_cls, mock_process = self.mock_multiprocessing

        # Setup: process not alive
        mock_process.is_alive.return_value = False
        self.http_server._server_process = mock_process

        # Call run method
        self.http_server.run()

        # Verify process start
        mock_process.start.assert_called_once()

        self.mock_logger.info.assert_any_call(
            "HttpServer started in process: http://0.0.0.0:8000"
        )

    def test_run_server_already_running(self):
        """Test run method when server process is already running"""
        mock_event_cls, mock_event, mock_process_cls, mock_process = self.mock_multiprocessing

        mock_process.is_alive.return_value = True
        self.http_server._server_process = mock_process

        self.http_server.run()

        mock_process.start.assert_not_called()
        expected_log = "HttpServer started in process: http://0.0.0.0:8000"
        log_calls = [call[0][0] for call, _ in self.mock_logger.info.call_args_list]
        assert expected_log not in log_calls

    def test_shutdown_no_server(self):
        """Test shutdown method with no active server"""
        mock_event_cls, mock_event, mock_process_cls, mock_process = self.mock_multiprocessing

        mock_process.is_alive.return_value = False
        self.http_server._server = None
        self.http_server._server_process = mock_process

        self.http_server.shutdown()

        mock_event.set.assert_called_once()
        mock_process.terminate.assert_not_called()
        self.mock_logger.info.assert_any_call("HttpServer stopped completely")

    def test_run_server_method(self):
        """Test _run_server method without TLS"""
        mock_uvicorn_module, mock_config, mock_server = self.mock_uvicorn
        mock_event_cls, mock_event, mock_process_cls, mock_process = self.mock_multiprocessing

        mock_event.is_set.return_value = False

        self.http_server._run_server()

        mock_uvicorn_module.Config.assert_called_once_with(
            app=self.http_server.app,
            host="0.0.0.0",
            port=8000,
            log_level="warning",
            workers=1,
            loop="uvloop",
            http="httptools"
        )
        mock_config.load.assert_called_once()
        mock_uvicorn_module.Server.assert_called_once_with(mock_config)
        mock_server.run.assert_called_once()
        assert self.http_server._server == mock_server
        self.mock_logger.info.assert_called_with("HttpServer started: http://0.0.0.0:8000")

    def test_run_server_stop_event_set(self):
        """Test _run_server method when stop event is set"""
        mock_uvicorn_module, mock_config, mock_server = self.mock_uvicorn
        mock_event_cls, mock_event, mock_process_cls, mock_process = self.mock_multiprocessing

        mock_event.is_set.return_value = True
        self.http_server._run_server()

        mock_uvicorn_module.Server.assert_called_once_with(mock_config)
        mock_server.run.assert_not_called()

    def test_run_server_with_tls(self):
        """Test _run_server method with TLS enabled"""
        mock_uvicorn_module, mock_config, mock_server = self.mock_uvicorn
        mock_event_cls, mock_event, mock_process_cls, mock_process = self.mock_multiprocessing

        mock_tls_config = mock.MagicMock()
        mock_tls_config.enable_tls = True
        self.http_server.infer_tls_config = mock_tls_config

        mock_ssl_context = mock.MagicMock()
        self.mock_cert_util.create_ssl_context.return_value = mock_ssl_context
        mock_event.is_set.return_value = False

        self.http_server._run_server()

        self.mock_cert_util.create_ssl_context.assert_called_once_with(mock_tls_config)
        assert mock_config.ssl == mock_ssl_context
        self.mock_logger.info.assert_called_with("HttpServer started: https://0.0.0.0:8000")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_chat_completion_route(self):
        """Test /v1/chat/completions route handler"""
        mock_fastapi_cls, mock_app, registered_handlers = self.mock_fastapi_app

        chat_route_handler = registered_handlers.get("/v1/chat/completions")
        assert chat_route_handler is not None, "Route handler not registered"

        # Setup mock request
        mock_raw_request = mock.AsyncMock(spec=Request)
        mock_request_dict = {"messages": [{"role": "user", "content": "test"}]}
        mock_raw_request.json = mock.AsyncMock(return_value=mock_request_dict)

        mock_chat_request_instance = mock.MagicMock()
        self.http_server.chat_completion_request.model_validate.return_value = mock_chat_request_instance

        mock_serving_chat = mock.AsyncMock()
        expected_response = {"id": "test", "choices": []}
        mock_serving_chat.handle_request = mock.AsyncMock(return_value=expected_response)
        mock_app.state.openai_serving_chat = mock_serving_chat

        result = await chat_route_handler(raw_request=mock_raw_request)

        # Verify
        mock_raw_request.json.assert_awaited_once()
        self.http_server.chat_completion_request.model_validate.assert_called_once_with(mock_request_dict)
        mock_serving_chat.handle_request.assert_awaited_once_with(mock_chat_request_instance, mock_raw_request)
        assert result == expected_response

    @pytest.mark.asyncio(loop_scope="function")
    async def test_completion_route(self):
        """Test /v1/completions route handler"""
        mock_fastapi_cls, mock_app, registered_handlers = self.mock_fastapi_app

        completion_route_handler = registered_handlers.get("/v1/completions")
        assert completion_route_handler is not None

        mock_raw_request = mock.AsyncMock(spec=Request)
        mock_request_dict = {"prompt": "test prompt", "max_tokens": 100}
        mock_raw_request.json = mock.AsyncMock(return_value=mock_request_dict)

        mock_completion_request_instance = mock.MagicMock()
        self.http_server.completion_request.model_validate.return_value = mock_completion_request_instance

        mock_serving_completion = mock.AsyncMock()
        expected_response = {"id": "test", "choices": []}
        mock_serving_completion.handle_request = mock.AsyncMock(return_value=expected_response)
        mock_app.state.openai_serving_completion = mock_serving_completion

        result = await completion_route_handler(raw_request=mock_raw_request)

        mock_raw_request.json.assert_awaited_once()
        self.http_server.completion_request.model_validate.assert_called_once_with(mock_request_dict)
        mock_serving_completion.handle_request.assert_awaited_once_with(
            mock_completion_request_instance, mock_raw_request
        )
        assert result == expected_response

    @pytest.mark.asyncio(loop_scope="function")
    async def test_health_route(self):
        """Test /health route handler"""
        mock_fastapi_cls, mock_app, registered_handlers = self.mock_fastapi_app

        health_route_handler = registered_handlers.get("/health")
        assert health_route_handler is not None

        mock_raw_request = mock.AsyncMock(spec=Request)
        mock_engine_client = mock.MagicMock()
        mock_health_checker = mock.AsyncMock(return_value=True)

        mock_app.state.health_checker = mock_health_checker
        mock_app.state.engine_client = mock_engine_client

        result = await health_route_handler(raw_request=mock_raw_request)

        mock_health_checker.assert_awaited_once_with(mock_engine_client)
        assert result is True