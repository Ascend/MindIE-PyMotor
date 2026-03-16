# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
#
# MindIE is licensed under both the Mulan PSL v2 and the Apache License, Version 2.0.
# You may choose to use this software under the terms of either license.
#
# ---------------------------------------------------------------------------
# Mulan PSL v2:
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
#
# Apache License, Version 2.0:
# You may obtain a copy of the License at:
#         http://www.apache.org/licenses/LICENSE-2.0
# ---------------------------------------------------------------------------
#
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the respective licenses for more details.

from contextlib import asynccontextmanager
from fastapi import FastAPI

from vllm.entrypoints.openai.api_server import build_async_engine_client
from vllm.usage.usage_lib import UsageContext
from vllm.entrypoints.openai.models.serving import OpenAIServingModels
from vllm.entrypoints.logger import RequestLogger
from vllm.entrypoints.openai.models.protocol import BaseModelPath
from vllm.entrypoints.utils import process_lora_modules
from vllm.entrypoints.chat_utils import load_chat_template

from motor.engine_server.openai.vllm.serving_chat import OpenAIServingChat
from motor.engine_server.openai.vllm.serving_completion import OpenAIServingCompletion
from motor.engine_server.config.base import IConfig
from motor.common.utils.logger import get_logger

logger = get_logger("engine_server")


async def vllm_health_checker(engine_client) -> bool:
    try:
        await engine_client.check_health()
        return True
    except Exception:
        return False


def create_vllm_lifespan(config: IConfig, init_params: dict):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("HttpServer lifespan: Creating engine_client...")
        client_config = init_params
        args = config.get_args()
        try:
            async with build_async_engine_client(
                args,
                usage_context=UsageContext.OPENAI_API_SERVER,
                client_config=client_config,
            ) as engine_client:
                logger.info("HttpServer lifespan: Engine_client created successfully")

                app.state.health_checker = vllm_health_checker
                app.state.engine_client = engine_client

                supported_tasks = await engine_client.get_supported_tasks()
                resolved_chat_template = load_chat_template(args.chat_template)
                vllm_config = engine_client.vllm_config

                default_mm_loras = (
                    vllm_config.lora_config.default_mm_loras
                    if vllm_config.lora_config is not None
                    else {}
                )
                lora_modules = process_lora_modules(args.lora_modules, default_mm_loras)

                if args.served_model_name is not None:
                    served_model_names = args.served_model_name
                else:
                    served_model_names = [args.model]

                if args.enable_log_requests:
                    request_logger = RequestLogger(max_log_len=args.max_log_len)
                else:
                    request_logger = None

                base_model_paths = [BaseModelPath(name=name, model_path=args.model) for name in served_model_names]

                openai_serving_models = OpenAIServingModels(
                    engine_client=engine_client,
                    base_model_paths=base_model_paths,
                    lora_modules=lora_modules,
                )

                try:
                    app.state.openai_serving_chat = OpenAIServingChat(
                        engine_client=engine_client,
                        models=openai_serving_models,
                        response_role=args.response_role,
                        request_logger=request_logger,
                        chat_template=resolved_chat_template,
                        chat_template_content_format=args.chat_template_content_format,
                    ) if "generate" in supported_tasks else None

                    app.state.openai_serving_completion = OpenAIServingCompletion(
                        engine_client=engine_client,
                        models=openai_serving_models,
                        request_logger=request_logger,
                    ) if "generate" in supported_tasks else None

                    logger.info("HttpServer lifespan: Serving components created successfully")
                except Exception as e:
                    logger.error(f"HttpServer lifespan: Failed to create serving components: {e}")
                    raise

                yield

            logger.info("HttpServer lifespan: Engine_client cleanup completed")
        except Exception as e:
            logger.error(f"HttpServer lifespan: Failed to create or manage engine_client: {e}")
            raise

    return lifespan
