#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import uvloop
import vllm.envs as envs

from vllm.reasoning import ReasoningParserManager
from vllm.entrypoints.openai.tool_parsers import ToolParserManager
from vllm.entrypoints.launcher import serve_http
from vllm.entrypoints.openai.api_server import load_log_config
from vllm.entrypoints.openai.api_server import build_async_engine_client
from vllm.entrypoints.openai.api_server import maybe_register_tokenizer_info_endpoint
from vllm.entrypoints.openai.api_server import build_app
from vllm.entrypoints.openai.api_server import init_app_state

from motor.common.utils.logger import get_logger
from motor.engine_server.core.vllm.vllm_engine_control import VllmEngineController
from motor.engine_server.utils.util import func_has_parameter

logger = get_logger("engine_server")


def engine_server_run_api_server_worker_proc(
        listen_address, sock, args, client_config=None, **uvicorn_kwargs
) -> None:
    client_address_config = client_config or {}
    api_server_index = client_address_config.get("client_index", 0)
    try:
        from vllm.utils.system_utils import decorate_logs, set_process_title
    except Exception as e:
        from vllm.utils import decorate_logs, set_process_title
    set_process_title("APIServer", str(api_server_index))
    decorate_logs()

    uvloop.run(
        engine_server_run_server_worker(listen_address, sock, args, client_address_config, **uvicorn_kwargs)
    )


async def engine_server_run_server_worker(
        address, listen_sock, args, ipc_config=None, **uvicorn_kwargs
) -> None:
    if hasattr(args, 'tool_parser_plugin') and args.tool_parser_plugin and len(args.tool_parser_plugin) > 3:
        ToolParserManager.import_tool_parser(args.tool_parser_plugin)

    if (hasattr(args, 'reasoning_parser_plugin') and args.reasoning_parser_plugin
            and len(args.reasoning_parser_plugin) > 3):
        ReasoningParserManager.import_reasoning_parser(args.reasoning_parser_plugin)

    logging_config = load_log_config(args.log_config_file)
    if logging_config is not None:
        uvicorn_kwargs["log_config"] = logging_config

    async with build_async_engine_client(
            args,
            client_config=ipc_config,
    ) as engine_client:
        maybe_register_tokenizer_info_endpoint(args)

        application = build_app(args)

        init_state_params = []
        if func_has_parameter(init_app_state, 'vllm_config'):
            vllm_cfg = await engine_client.get_vllm_config()
            init_state_params = [engine_client, vllm_cfg, application.state, args]
        else:
            init_state_params = [engine_client, application.state, args]

        await init_app_state(*init_state_params)
        logger.info("Engine client controller stored in application state")

        data_parallel_rank = 0
        if hasattr(args, 'data_parallel_rank') and args.data_parallel_rank is not None:
            data_parallel_rank = args.data_parallel_rank

        application.state.engine_ctl_client = VllmEngineController(dp_rank=data_parallel_rank)

        logger.info("API server starting on %s", address)

        api_server = await serve_http(
            application,
            port=args.port,
            sock=listen_sock,
            enable_ssl_refresh=args.enable_ssl_refresh,
            host=args.host,
            h11_max_incomplete_event_size=args.h11_max_incomplete_event_size,
            h11_max_header_count=args.h11_max_header_count,
            timeout_keep_alive=envs.VLLM_HTTP_TIMEOUT_KEEP_ALIVE,
            ssl_keyfile=args.ssl_keyfile,
            ssl_certfile=args.ssl_certfile,
            ssl_ca_certs=args.ssl_ca_certs,
            ssl_cert_reqs=args.ssl_cert_reqs,
            log_level=args.uvicorn_log_level,
            access_log=not args.disable_uvicorn_access_log,
            **uvicorn_kwargs,
        )

    try:
        await api_server
    finally:
        listen_sock.close()
