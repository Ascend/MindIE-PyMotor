# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import json
from abc import ABC, abstractmethod

from ccae_reporter.backends.log_collect.log_collector import Collector
from ccae_reporter.backends.log_collect.data_class import LogRequestMessage
from ccae_reporter.common.logging import Log
from ccae_reporter.common.util import get_local_ip


class BaseBackend(ABC):
    def __init__(self, identity):
        self.identity = identity
        self.log_collector = Collector(identity=identity)
        self.logger = Log(__name__).getlog()

    @abstractmethod
    def fetch_alarm_info(self) -> list:
        pass

    @abstractmethod
    def fetch_inventory_info(self, model_id: str) -> str:
        pass

    @abstractmethod
    def is_alive(self) -> bool:
        pass

    def fetch_log_messages(self):
        log_data = self.log_collector.collect_handler.log_processor.get_log_data(self.identity)
        if log_data is None:
            self.logger.debug(f"No log data read from backend!")
            return None
        log_request_message = json.dumps(LogRequestMessage(log_data_list=[log_data], server_ip=get_local_ip()).format())
        self.logger.debug(f"Log data read from backend is: {log_request_message}")
        return log_request_message
