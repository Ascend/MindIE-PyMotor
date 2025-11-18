# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import asyncio
import json
import ssl
import logging
from typing import Optional, Any
from datetime import datetime, timezone
from functools import wraps
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from motor.common.resources.http_msg_spec import InsEventMsg
from motor.common.utils.logger import get_logger, ApiAccessFilter
from motor.common.utils.cert_util import CoordinatorCertUtil
from motor.coordinator.core.instance_manager import InstanceManager
from motor.coordinator.core.instance_healthchecker import InstanceHealthChecker
from motor.coordinator.middleware.fastapi_middleware import (
    SimpleRateLimitMiddleware, 
    create_simple_rate_limit_middleware, 
)
from motor.config.coordinator import CoordinatorConfig, RateLimitConfig
from motor.coordinator.models.request import RequestType, RequestResponse
from motor.coordinator.router.router import handle_request, handle_metaserver_request


logger = get_logger(__name__)


# Constants for OpenAI request fields
FIELD_MESSAGES = "messages"
FIELD_PROMPT = "prompt"
FIELD_MODEL = "model"
FIELD_STREAM = "stream"
FIELD_ROLE = "role"
FIELD_CONTENT = "content"

# HTTP Status Code Constants
HTTP_STATUS_BAD_REQUEST = 400
HTTP_STATUS_UNAUTHORIZED = 401
HTTP_STATUS_FORBIDDEN = 403
HTTP_STATUS_INTERNAL_SERVER_ERROR = 500
HTTP_STATUS_SERVICE_UNAVAILABLE = 503
HTTP_STATUS_GATEWAY_TIMEOUT = 504

# Timeout Constants
GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 30
SERVER_SHUTDOWN_SLEEP_SECONDS = 0.1
REQUEST_BODY_PREVIEW_LENGTH = 200

# Uvicorn Config Key Constants
UVICORN_KEY_APP = "app"
UVICORN_KEY_HOST = "host"
UVICORN_KEY_PORT = "port"
UVICORN_KEY_LOG_LEVEL = "log_level"
UVICORN_LOG_LEVEL_INFO = "info"
UVICORN_KEY_ACCESS_LOG = "access_log"
UVICORN_KEY_LIFESPAN = "lifespan"
UVICORN_LIFESPAN_ON = "on"
UVICORN_KEY_TIMEOUT_KEEP_ALIVE = "timeout_keep_alive"
UVICORN_KEY_TIMEOUT_GRACEFUL_SHUTDOWN = "timeout_graceful_shutdown"
UVICORN_KEY_SSL_KEYFILE = "ssl_keyfile"
UVICORN_KEY_SSL_CERTFILE = "ssl_certfile"
UVICORN_KEY_SSL_CA_CERTS = "ssl_ca_certs"

# JSON Field Constants
JSON_FIELD_ENDPOINTS = "endpoints"
JSON_FIELD_INSTANCES = "instances"

# Encoding Constants
ENCODING_UTF8 = "utf-8"
FILE_MODE_READ_BINARY = "rb"

NOT_A_DICT = "not a dict"


class SSLConfig:
    def __init__(self):
        self.enabled = False
        self.cert_file = ""
        self.key_file = ""
        self.ca_file = ""
        self.password = ""
        self.verify_mode = ssl.CERT_REQUIRED
        self.check_hostname = True


class CoordinatorServer:
    
    def __init__(
        self,
        coordinator_config: Optional[CoordinatorConfig] = None
    ):
        self._initialize_config(coordinator_config)
        self._log_configuration()
        self.instance_manager = InstanceManager()
        self._create_apps()
        self._setup_cors_middleware()
        self._register_routes()
    
    @staticmethod
    def _openai_is_stream(body_json: dict[str, Any]) -> bool:
        if FIELD_STREAM in body_json:
            stream_value = body_json[FIELD_STREAM]
            if isinstance(stream_value, str):
                return stream_value.lower() in ("true", "1", "yes")
            return bool(stream_value)
        return False
    
    @staticmethod
    def _validate_openai_request(body_json: dict[str, Any], request_type: RequestType):
        if FIELD_MODEL not in body_json:
            raise HTTPException(
                status_code=HTTP_STATUS_BAD_REQUEST,
                detail=f"Missing required field: {FIELD_MODEL}"
            )
        
        if request_type != RequestType.OPENAI:
            return
        
        if FIELD_PROMPT not in body_json and FIELD_MESSAGES not in body_json:
            raise HTTPException(
                status_code=HTTP_STATUS_BAD_REQUEST,
                detail=f"Missing required field: {FIELD_PROMPT} or {FIELD_MESSAGES}"
            )
        
        if FIELD_MESSAGES not in body_json:
            return
        
        if not isinstance(body_json[FIELD_MESSAGES], list) or len(body_json[FIELD_MESSAGES]) == 0:
            raise HTTPException(
                status_code=HTTP_STATUS_BAD_REQUEST,
                detail=f"Invalid {FIELD_MESSAGES} field: must be a non-empty array"
            )
        
        for i, message in enumerate(body_json[FIELD_MESSAGES]):
            if not isinstance(message, dict):
                raise HTTPException(
                    status_code=HTTP_STATUS_BAD_REQUEST,
                    detail=f"Invalid message format at index {i}: must be an object"
                )
            if FIELD_ROLE not in message or FIELD_CONTENT not in message:
                raise HTTPException(
                    status_code=HTTP_STATUS_BAD_REQUEST,
                    detail=f"Invalid message at index {i}: missing {FIELD_ROLE} or {FIELD_CONTENT}"
                )
            if message[FIELD_ROLE] not in ["system", "user", "assistant"]:
                raise HTTPException(
                    status_code=HTTP_STATUS_BAD_REQUEST,
                    detail=(
                        f"Invalid {FIELD_ROLE} '{message[FIELD_ROLE]}' at index {i}: must be system, "
                        "user, or assistant"
                    )
                )
    
    @staticmethod
    def _copy_routes(src_app: FastAPI, dst_app: FastAPI, skip_paths: Optional[list[str]] = None):
        if skip_paths is None:
            skip_paths = []
        reserved_paths = set(["/docs", "/redoc", "/openapi.json", "/favicon.ico"]) | set(skip_paths)
        for route in src_app.router.routes:
            try:
                path = getattr(route, "path", None)
                if not path:
                    continue
                
                if any(path == reserved_path or path.startswith(reserved_path + "/") 
                       for reserved_path in reserved_paths):
                    continue
                
                dst_app.router.routes.append(route)
            except Exception as e:
                logger.warning(f"Failed to copy route: {e}", exc_info=True)
                continue

    @staticmethod
    def _normalize_instance_endpoints(body: dict[str, Any]) -> None:
        """normalize instance endpoints, convert endpoint_id from string to int"""
        if not isinstance(body, dict) or JSON_FIELD_INSTANCES not in body:
            return
        
        instances = body.get(JSON_FIELD_INSTANCES, [])
        if not isinstance(instances, list):
            return
        
        for instance in instances:
            if isinstance(instance, dict) and JSON_FIELD_ENDPOINTS in instance:
                endpoints = instance[JSON_FIELD_ENDPOINTS]
                if isinstance(endpoints, dict):
                    instance[JSON_FIELD_ENDPOINTS] = CoordinatorServer._convert_endpoints(endpoints)
    
    @staticmethod
    def _convert_endpoints(endpoints: dict[str, Any]) -> dict[str, Any]:
        """convert endpoints dictionary, convert endpoint_id from string to int"""
        converted_endpoints = {}
        for pod_ip, endpoint_dict in endpoints.items():
            if isinstance(endpoint_dict, dict):
                converted_endpoints[pod_ip] = CoordinatorServer._convert_endpoint_dict(endpoint_dict)
            else:
                converted_endpoints[pod_ip] = endpoint_dict
        return converted_endpoints
    
    @staticmethod
    def _convert_endpoint_dict(endpoint_dict: dict[str, Any]) -> dict[Any, Any]:
        """convert single endpoint_dict, convert endpoint_id from string to int"""
        converted = {}
        for endpoint_id_str, endpoint_data in endpoint_dict.items():
            endpoint_id = CoordinatorServer._convert_endpoint_id(endpoint_id_str)
            converted[endpoint_id] = endpoint_data
        return converted
    
    @staticmethod
    def _convert_endpoint_id(endpoint_id_str: Any) -> Any:
        """convert endpoint_id from string to int, keep original value if conversion fails"""
        try:
            return int(endpoint_id_str)
        except (ValueError, TypeError) as e:
            logger.warning(
                f"Failed to convert endpoint_id '{endpoint_id_str}' to int: {e}, keeping as string",
                exc_info=True
            )
            return endpoint_id_str
    
    @staticmethod
    def _create_base_uvicorn_config(app: FastAPI, host: str, port: int) -> dict[str, Any]:
        # Create ApiAccessFilter for health endpoint
        api_filter = ApiAccessFilter({"/health": logging.ERROR})

        # Configure uvicorn access logger with filter
        uvicorn_access_logger = logging.getLogger("uvicorn.access")
        uvicorn_access_logger.addFilter(api_filter)

        return {
            UVICORN_KEY_APP: app,
            UVICORN_KEY_HOST: host,
            UVICORN_KEY_PORT: port,
            UVICORN_KEY_LOG_LEVEL: UVICORN_LOG_LEVEL_INFO,
            UVICORN_KEY_ACCESS_LOG: True,
            UVICORN_KEY_LIFESPAN: UVICORN_LIFESPAN_ON
        }

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI):
        logger.info("Coordinator server is starting...")
        try:
            yield
        except asyncio.CancelledError:
            logger.info("Coordinator server startup was cancelled")
        except Exception as e:
            logger.error(f"Coordinator server startup failed: {e}")
            raise
        finally:
            logger.info("Coordinator server is shutting down...")
            try:
                await asyncio.sleep(SERVER_SHUTDOWN_SLEEP_SECONDS)
            except asyncio.CancelledError:
                logger.info("Coordinator server shutdown was cancelled")
            except Exception as e:
                logger.warning(f"Error occurred during coordinator server shutdown: {e}", exc_info=True)

    def verify_api_key(self, request: Request) -> bool:
        if not self.api_key_config.enabled:
            return True
        
        if request.url.path in self.api_key_config.skip_paths:
            return True
        
        authorization = request.headers.get(self.api_key_config.header_name)
        
        if not authorization:
            logger.warning(f"API Key validation failed: missing Authorization header")
            raise HTTPException(
                status_code=HTTP_STATUS_UNAUTHORIZED,
                detail="Missing Authorization header",
                headers={"WWW-Authenticate": "Bearer"}
            )
        
        api_key = authorization
        if self.api_key_config.key_prefix and authorization.startswith(self.api_key_config.key_prefix):
            api_key = authorization[len(self.api_key_config.key_prefix):]
        
        if api_key not in self.api_key_config.valid_keys:
            logger.warning("API Key validation failed: invalid key")
            raise HTTPException(
                status_code=HTTP_STATUS_FORBIDDEN,
                detail="Invalid API Key"
            )
        
        logger.debug(f"API Key validation successful")
        return True
    
    def setup_rate_limiting(
        self,
        rate_limit_config: Optional[RateLimitConfig] = None
    ):
        try:
            if rate_limit_config is None:
                rate_limit_config = self.coordinator_config.rate_limit_config

            if not rate_limit_config.enabled:
                logger.info("Rate limiting is disabled in configuration")
                return

            middleware = create_simple_rate_limit_middleware(
                app=self.inference_app,
                max_requests=rate_limit_config.max_requests,
                window_size=rate_limit_config.window_size
            )

            self.inference_app.add_middleware(
                SimpleRateLimitMiddleware,
                rate_limiter=middleware.rate_limiter,
                skip_paths=rate_limit_config.skip_paths,
                error_message=rate_limit_config.error_message,
                error_status_code=rate_limit_config.error_status_code
            )

            logger.info(
                "Rate limiting middleware enabled (Inference): max_requests="
                f"{rate_limit_config.max_requests}/{rate_limit_config.window_size}s"
            )

        except Exception as e:
            logger.error(f"Failed to setup rate limiting middleware (Inference): {e}", exc_info=True)
    
    def create_unified_app(
        self,
        rate_limit_config: Optional[RateLimitConfig] = None
    ):
        unified_app = FastAPI(
            title="Motor Coordinator Server",
            description="Management and Inference APIs served on a single port",
            version="1.0.0",
            lifespan=self._lifespan
        )

        unified_app.add_middleware(
            CORSMiddleware, 
            allow_origins=["*"], 
            allow_credentials=True, 
            allow_methods=["*"], 
            allow_headers=["*"]
        )

        try:
            if rate_limit_config is None:
                rate_limit_config = self.coordinator_config.rate_limit_config

            if not rate_limit_config.enabled:
                logger.info("Rate limiting is disabled in configuration")
            else:
                middleware = create_simple_rate_limit_middleware(
                    app=unified_app,
                    max_requests=rate_limit_config.max_requests,
                    window_size=rate_limit_config.window_size
                )

                unified_app.add_middleware(
                    SimpleRateLimitMiddleware,
                    rate_limiter=middleware.rate_limiter,
                    skip_paths=rate_limit_config.skip_paths,
                    error_message=rate_limit_config.error_message,
                    error_status_code=rate_limit_config.error_status_code
                )

                logger.info(
                    "Rate limiting middleware enabled (Unified): max_requests="
                    f"{rate_limit_config.max_requests}/{rate_limit_config.window_size}s"
                )

        except Exception as e:
            logger.error(f"Failed to setup rate limiting middleware (Unified): {e}", exc_info=True)

        self._copy_routes(self.management_app, unified_app)
        self._copy_routes(self.inference_app, unified_app)

        return unified_app
    
    async def run(self):
        combined_mode = self.coordinator_config.server_config.combined_mode
        rate_limit_config = self.coordinator_config.rate_limit_config
        
        mgmt_server = None
        inference_server = None
        unified_server = None
        try:
            if combined_mode:
                unified_server = await self._run_unified_server(rate_limit_config)
            else:
                mgmt_server, inference_server = await self._run_separate_servers(rate_limit_config)
        except asyncio.CancelledError:
            logger.info("Server tasks were cancelled")
            await self._shutdown_servers(mgmt_server, inference_server, unified_server)
            raise
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt signal")
            await self._shutdown_servers(mgmt_server, inference_server, unified_server)
            raise
        except Exception as e:
            logger.error(f"Server run failed: {e}", exc_info=True)
            await self._shutdown_servers(mgmt_server, inference_server, unified_server)
            raise
    
    def _initialize_config(self, coordinator_config: Optional[CoordinatorConfig]):
        if coordinator_config is None:
            try:
                coordinator_config = CoordinatorConfig()
                logger.info("CoordinatorConfig initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize CoordinatorConfig: {e}")
                raise RuntimeError("Failed to initialize CoordinatorConfig") from e
        
        self.coordinator_config = coordinator_config
        self.timeout_config = coordinator_config.timeout_config
        self.api_key_config = coordinator_config.api_key_config
        self.ssl_config = SSLConfig()
        self._load_ssl_config()
    
    def _log_configuration(self):
        logger.info(
            "Timeout configuration: request=%ss, connection=%ss, read=%ss, write=%ss, keep_alive=%ss",
            self.timeout_config.request_timeout,
            self.timeout_config.connection_timeout,
            self.timeout_config.read_timeout,
            self.timeout_config.write_timeout,
            self.timeout_config.keep_alive_timeout
        )
        
        if self.api_key_config.enabled and not self.api_key_config.valid_keys:
            logger.warning("API Key validation enabled but no valid keys configured!")
        
        logger.info(
            "API Key validation enabled: %s, valid keys count: %s, header: %s, prefix: %s, skip paths: %s",
            self.api_key_config.enabled,
            len(self.api_key_config.valid_keys),
            self.api_key_config.header_name,
            self.api_key_config.key_prefix,
            len(self.api_key_config.skip_paths)
        )
        
        if self.ssl_config.enabled:
            logger.info(
                "SSL configuration enabled: cert_file=%s, key_file=%s, ca_file=%s",
                self.ssl_config.cert_file,
                self.ssl_config.key_file,
                self.ssl_config.ca_file
            )
        else:
            logger.info("SSL configuration disabled")
    
    def _create_apps(self):
        self.management_app = FastAPI(
            title="Motor Coordinator Management Server",
            description="Management plane: health, readiness, metrics, instance refresh",
            version="1.0.0",
            lifespan=self._lifespan
        )
        
        self.inference_app = FastAPI(
            title="Motor Coordinator Inference Server",
            description="Inference API endpoints (OpenAI-compatible and more)",
            version="1.0.0",
            lifespan=self._lifespan
        )
    
    def _setup_cors_middleware(self):
        cors_config = {
            "allow_origins": ["*"],
            "allow_credentials": True,
            "allow_methods": ["*"],
            "allow_headers": ["*"]
        }
        
        self.management_app.add_middleware(CORSMiddleware, **cors_config)
        self.inference_app.add_middleware(CORSMiddleware, **cors_config)
    
    def _apply_timeout_to_config(self, config_kwargs: dict[str, Any]):
        if self.timeout_config:
            config_kwargs[UVICORN_KEY_TIMEOUT_KEEP_ALIVE] = self.timeout_config.keep_alive_timeout
            config_kwargs[UVICORN_KEY_TIMEOUT_GRACEFUL_SHUTDOWN] = GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS
    
    def _apply_ssl_to_unified_config(self, config_kwargs: dict[str, Any]):
        if not (self.ssl_config and self.ssl_config.enabled):
            return
        
        ssl_context = CoordinatorCertUtil.create_ssl_context(
            cert_file=self.ssl_config.cert_file,
            key_file=self.ssl_config.key_file,
            ca_file=self.ssl_config.ca_file,
            password=self.ssl_config.password
        )
        if ssl_context:
            config_kwargs[UVICORN_KEY_SSL_KEYFILE] = self.ssl_config.key_file
            config_kwargs[UVICORN_KEY_SSL_CERTFILE] = self.ssl_config.cert_file
            config_kwargs[UVICORN_KEY_SSL_CA_CERTS] = self.ssl_config.ca_file
            logger.info("HTTPS support enabled for unified server")
        else:
            logger.warning("SSL configuration failed, using HTTP mode")
    
    def _apply_ssl_to_separate_configs(
        self,
        mgmt_config_kwargs: dict[str, Any],
        inference_config_kwargs: dict[str, Any]
    ):
        if not (self.ssl_config and self.ssl_config.enabled):
            return
        
        mgmt_ssl_context = CoordinatorCertUtil.create_ssl_context_no_client_cert(
            cert_file=self.ssl_config.cert_file,
            key_file=self.ssl_config.key_file,
            ca_file=self.ssl_config.ca_file,
            password=self.ssl_config.password
        )
        inference_ssl_context = CoordinatorCertUtil.create_ssl_context(
            cert_file=self.ssl_config.cert_file,
            key_file=self.ssl_config.key_file,
            ca_file=self.ssl_config.ca_file,
            password=self.ssl_config.password
        )
        
        if mgmt_ssl_context:
            mgmt_config_kwargs[UVICORN_KEY_SSL_KEYFILE] = self.ssl_config.key_file
            mgmt_config_kwargs[UVICORN_KEY_SSL_CERTFILE] = self.ssl_config.cert_file
            logger.info("HTTPS support enabled for management server (no client cert verification)")
        else:
            logger.warning("SSL configuration failed for management server, using HTTP mode")
        
        if inference_ssl_context:
            inference_config_kwargs[UVICORN_KEY_SSL_KEYFILE] = self.ssl_config.key_file
            inference_config_kwargs[UVICORN_KEY_SSL_CERTFILE] = self.ssl_config.cert_file
            inference_config_kwargs[UVICORN_KEY_SSL_CA_CERTS] = self.ssl_config.ca_file
            logger.info("HTTPS support enabled for inference server")
        else:
            logger.warning("SSL configuration failed for inference server, using HTTP mode")
    
    async def _run_unified_server(self, rate_limit_config: Optional[RateLimitConfig]):
        unified_app = self.create_unified_app(rate_limit_config=rate_limit_config)
        logger.info(
            "Starting Unified server %s:%s",
            self.coordinator_config.server_config.combined_host,
            self.coordinator_config.server_config.combined_port
        )
        
        unified_config_kwargs = self._create_base_uvicorn_config(
            unified_app,
            self.coordinator_config.server_config.combined_host,
            self.coordinator_config.server_config.combined_port
        )
        
        self._apply_timeout_to_config(unified_config_kwargs)
        self._apply_ssl_to_unified_config(unified_config_kwargs)
        
        unified_server = uvicorn.Server(uvicorn.Config(**unified_config_kwargs))
        await unified_server.serve()
        return unified_server
    
    async def _run_separate_servers(self, rate_limit_config: Optional[RateLimitConfig]):
        self.setup_rate_limiting(rate_limit_config=rate_limit_config)
        
        logger.info(
            "Starting Management server %s:%s",
            self.coordinator_config.server_config.mgmt_host,
            self.coordinator_config.server_config.mgmt_port
        )
        logger.info(
            "Starting Inference server %s:%s",
            self.coordinator_config.server_config.inference_host,
            self.coordinator_config.server_config.inference_port
        )
        
        mgmt_config_kwargs = self._create_base_uvicorn_config(
            self.management_app,
            self.coordinator_config.server_config.mgmt_host,
            self.coordinator_config.server_config.mgmt_port
        )
        
        inference_config_kwargs = self._create_base_uvicorn_config(
            self.inference_app,
            self.coordinator_config.server_config.inference_host,
            self.coordinator_config.server_config.inference_port
        )
        
        self._apply_timeout_to_config(mgmt_config_kwargs)
        self._apply_timeout_to_config(inference_config_kwargs)
        self._apply_ssl_to_separate_configs(mgmt_config_kwargs, inference_config_kwargs)
        
        mgmt_server = uvicorn.Server(uvicorn.Config(**mgmt_config_kwargs))
        inference_server = uvicorn.Server(uvicorn.Config(**inference_config_kwargs))
        
        await asyncio.gather(
            mgmt_server.serve(),
            inference_server.serve(),
        )
        return mgmt_server, inference_server
    
    async def _shutdown_servers(self, mgmt_server, inference_server, unified_server):
        for srv in (mgmt_server, inference_server, unified_server):
            if srv:
                srv.should_exit = True
        await asyncio.sleep(SERVER_SHUTDOWN_SLEEP_SECONDS)
    
    def _load_ssl_config(self):
        request_tls = self.coordinator_config.request_server_tls
        
        if request_tls.tls_enable:
            self.ssl_config.enabled = True
            tls_items = request_tls.items
            self.ssl_config.cert_file = tls_items.get("tls_cert", "")
            self.ssl_config.key_file = tls_items.get("tls_key", "")
            self.ssl_config.ca_file = tls_items.get("ca_cert", "")
            self.ssl_config.password = tls_items.get("tls_passwd", "")
        else:
            self.ssl_config.enabled = False
    
    def _timeout_handler(self, timeout_seconds: Optional[float] = None):
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                actual_timeout = timeout_seconds if timeout_seconds is not None else self.timeout_config.request_timeout
                
                try:
                    return await asyncio.wait_for(
                        func(*args, **kwargs),
                        timeout=actual_timeout
                    )
                except asyncio.TimeoutError as e:
                    logger.warning(f"Request timeout after {actual_timeout}s: {func.__name__}")
                    raise HTTPException(
                        status_code=HTTP_STATUS_GATEWAY_TIMEOUT,
                        detail=f"Request timed out after {actual_timeout} seconds"
                    ) from e
                except HTTPException:
                    raise
                except Exception as e:
                    logger.error(f"Unexpected error in {func.__name__}: {e}", exc_info=True)
                    raise
            
            return wrapper
        return decorator
    
    def _register_routes(self):
        @self.inference_app.post("/v1/completions")
        @self._timeout_handler()
        async def openai_completions(request: Request):
            self.verify_api_key(request)
            return await self._handle_openai_request(request, RequestType.OPENAI)
        
        @self.inference_app.post("/v1/chat/completions")
        @self._timeout_handler()
        async def openai_chat_completions(request: Request):
            self.verify_api_key(request)
            return await self._handle_openai_request(request, RequestType.OPENAI)
        
        @self.management_app.get("/startup")
        async def startup_probe():
            logger.debug("Received startup probe request")
            return {"status": "ok", "message": "Coordinator is starting up"}
        
        @self.management_app.get("/health")
        async def health_check():
            logger.debug("Received health check request, Coordinator is healthy")
            return {"status": "ok", "message": "Coordinator is healthy"}
        
        @self.management_app.get("/readiness")
        async def readiness_check():
            if not InstanceManager().is_available():
                raise HTTPException(
                    status_code=HTTP_STATUS_SERVICE_UNAVAILABLE,
                    detail="Service is not ready"
                )
            return {"status": "ok", "message": "Coordinator is ready"}
        
        @self.management_app.get("/metrics")
        async def get_metrics():
            return {
                "status": "ok",
                "metrics": {
                    "total_requests": 0,
                    "active_requests": 0,
                    "error_count": 0,
                    "uptime": "0s"
                }
            }
        
        @self.management_app.post("/instances/refresh", response_model=RequestResponse)
        @self._timeout_handler()
        async def refresh_instances(request: Request) -> RequestResponse:
            return await self._handle_refresh_instances(request)
        
        @self.management_app.post("/v1/metaserver")
        @self._timeout_handler()
        async def metaserver(request: Request):
            """MetaServer API"""
            return await self._handle_metaserver_request(request)
        
        @self.management_app.get("/")
        async def root():
            return {
                "service": "Motor Coordinator Server",
                "version": "1.0.0",
                "description": "coordinator server, management and inference APIs",
                "endpoints": {
                    "# Inference API": {
                        "POST /v1/completions": "OpenAI Completion API",
                        "POST /v1/chat/completions": "OpenAI Chat Completion API"
                    },
                    "# monitoring and health check": {
                        "GET /health": "health check",
                        "GET /startup": "startup probe",
                        "GET /readiness": "readiness check",
                        "GET /metrics": "get metrics"
                    },
                    "# instance refresh": {
                        "POST /instances/refresh": "refresh instances"
                    }
                }
            }
    
    async def _handle_refresh_instances(self, request: Request) -> RequestResponse:
        try:
            raw_body = await request.body()
            if not raw_body:
                logger.error("Request body is empty")
                raise HTTPException(status_code=HTTP_STATUS_BAD_REQUEST, detail="Request body cannot be empty")
            
            body = json.loads(raw_body.decode(ENCODING_UTF8))
            if not body:
                logger.error("Parsed JSON body is empty")
                raise HTTPException(status_code=HTTP_STATUS_BAD_REQUEST, detail="Request body cannot be empty")
            
            logger.debug(
                "Request body keys: %s",
                list(body.keys()) if isinstance(body, dict) else NOT_A_DICT
            )
        except HTTPException:
            raise
        except json.JSONDecodeError as e:
            logger.error("Failed to parse request body as JSON: %s", e)
            preview_text = (
                raw_body.decode(ENCODING_UTF8, errors="ignore")[:REQUEST_BODY_PREVIEW_LENGTH]
                if raw_body
                else "empty"
            )
            logger.error(
                "Request body (first %s chars): %s",
                REQUEST_BODY_PREVIEW_LENGTH,
                preview_text
            )
            raise HTTPException(
                status_code=HTTP_STATUS_BAD_REQUEST,
                detail=f"Invalid JSON format: {str(e)}"
            ) from e
        except Exception as e:
            logger.error("Failed to parse request body: %s, type: %s", e, type(e))
            raise HTTPException(
                status_code=HTTP_STATUS_BAD_REQUEST,
                detail=f"Failed to parse request body: {str(e)}"
            ) from e
        
        logger.info(
            "Received instance refresh request, body keys: %s",
            list(body.keys()) if isinstance(body, dict) else NOT_A_DICT
        )
        
        CoordinatorServer._normalize_instance_endpoints(body)
        
        try:
            event_msg = InsEventMsg(**body)
        except Exception as e:
            logger.error(
                "Failed to parse InsEventMsg: %s, body keys: %s",
                e,
                list(body.keys()) if isinstance(body, dict) else NOT_A_DICT
            )
            raise HTTPException(
                status_code=HTTP_STATUS_BAD_REQUEST,
                detail=f"Invalid request format: {str(e)}"
            ) from e
        
        InstanceManager().refresh_instances(event_msg.event, event_msg.instances)
        is_ready = InstanceHealthChecker().check_state_alarm()
        
        return RequestResponse(
            request_id="refresh_request",
            status="success",
            message="Instance refresh completed",
            data={
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event_type": event_msg.event.value,
                "instance_count": len(event_msg.instances),
                "is_ready": is_ready
            }
        )
    
    async def _handle_openai_request(self, request: Request, request_type: RequestType):
        try:
            body = await request.body()
            body_json = json.loads(body.decode(ENCODING_UTF8))
            
            self._validate_openai_request(body_json, request_type)
            if not self.instance_manager.is_available():
                raise HTTPException(status_code=HTTP_STATUS_SERVICE_UNAVAILABLE, detail="Service is not available")

            return await handle_request(request)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to process OpenAI request: {e}", exc_info=True)
            raise HTTPException(status_code=HTTP_STATUS_INTERNAL_SERVER_ERROR, detail=str(e)) from e

    async def _handle_metaserver_request(self, request: Request):
        """Handle MetaServer request"""
        try:
            if not self.instance_manager.is_available():
                raise HTTPException(status_code=503, detail="Service is not available")

            # Use router to handle requests
            return await handle_metaserver_request(request)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to process MetaServer request: {e}")
            raise HTTPException(status_code=HTTP_STATUS_INTERNAL_SERVER_ERROR, detail=str(e)) from e