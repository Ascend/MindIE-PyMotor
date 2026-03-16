# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List

from ccae_reporter.common.util import safe_open

ENV_MODEL_NAME = "MODEL_NAME"
DEFAULT_MODEL_NAME = "UNKNOWN_MODEL_NAME"
ENV_SERVICE_ID = "SERVICE_ID"
DEFAULT_SERVICE_ID = "UNKNOWN_SERVICE_ID"
DEFAULT_LOG_TYPE = "UNKNOWN_LOG_TYPE"
LOG_TYPE = ["Controller", "Coordinator"]


@dataclass
class MetaData:
    collect_time: str
    file_path: str
    log_type: str
    meta_data: list

    def format(self):
        return {
            "collectTime": self.collect_time,
            "filePath": self.file_path,
            "logType": self.log_type,
            "metaData": self.meta_data,
        }


@dataclass
class LogData:
    component_type: str
    meta_data_list: List[MetaData]

    def format(self):
        return {
            "componentType": self.component_type,
            "metaData": [metadata.format() for metadata in self.meta_data_list]
        }


@dataclass
class LogRequestMessage:
    log_data_list: List[LogData]
    server_ip: str
    model_name: str = os.environ.get(ENV_MODEL_NAME, DEFAULT_MODEL_NAME)
    model_id: str = os.environ.get(ENV_SERVICE_ID, DEFAULT_SERVICE_ID)
    time_stamp: str = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    def format(self):
        return {
            "logData": [log_data.format() for log_data in self.log_data_list],
            "serverIP": self.server_ip,
            "modelName": self.model_name,
            "modelID": self.model_id,
            "timeStamp": self.time_stamp
        }


@dataclass
class LogFile:
    file_path: str
    log_type: str = ""
    last_read_position: int = 0
    first_read: bool = True

    def __post_init__(self):
        self.log_type = self._parse_log_type()

    def get_meta_data(self):
        return MetaData(
            collect_time=datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            file_path=self.file_path,
            log_type=self.log_type,
            meta_data=[log_line for log_line in self.read_new_lines()]
        )

    def read_new_lines(self, max_line_num_for_once=100):
        with safe_open(self.file_path, 'r', encoding='utf-8') as f:
            f.seek(self.last_read_position)
            # 首次监控的日志文件，跳过存量已有日志文本，只读取增量日志
            if self.first_read:
                self.first_read = False
                return

            read_line = 0
            while read_line < max_line_num_for_once:
                line = f.readline().strip()
                if line:
                    read_line += 1
                    self.last_read_position = f.tell()
                    yield line
                else:
                    break

    def _parse_log_type(self):
        for log_type_key in LOG_TYPE:
            if log_type_key.lower() in self.file_path:
                return f"mindie-{log_type_key}"
        return DEFAULT_LOG_TYPE
