#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

from motor.engine_server.config.base import ServerConfig
from motor.engine_server.parser.config_parser import ConfigParser
from motor.engine_server.factory.core_factory import ServerCoreFactory
from motor.common.utils.logger import get_logger

logger = get_logger("engine_server")


def main():
    server_config = ServerConfig.init_engine_server_config()
    config_parser = ConfigParser(server_config=server_config)
    config = config_parser.parse()
    logger.info(f"successfully parsed {server_config.engine_type} engine configuration")

    factory = ServerCoreFactory()
    server_core = factory.create_server_core(config=config)

    server_core.initialize()
    server_core.run()
    server_core.join()


if __name__ == "__main__":
    main()
