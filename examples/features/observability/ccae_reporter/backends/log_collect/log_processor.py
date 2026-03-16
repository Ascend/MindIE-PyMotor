# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.


from ccae_reporter.backends.log_collect.data_class import LogData, LogFile
from ccae_reporter.thread_safe_util import ThreadSafeFactory

CLIENT_COMPONENT_TYPE = ["Controller", "Coordinator"]


class LogDataProcessor:
    def __init__(self):
        self.watch_files: dict[str, LogFile] = dict()
        self.modified_log_files = ThreadSafeFactory.make_threadsafe_instance(set)

    def get_log_data(self, component_type=CLIENT_COMPONENT_TYPE[0]):
        if component_type not in CLIENT_COMPONENT_TYPE:
            raise ValueError(
                f"Invalid component_type: {component_type}. "
                f"Must be one of {CLIENT_COMPONENT_TYPE}"
            )
        metadata_list = []
        with self.modified_log_files.lock:
            modified_log = self.modified_log_files.copy()
            self.modified_log_files.clear()
        for filename in modified_log:
            logfile = self.watch_files.get(filename, None)
            if logfile is None:
                continue
            metadata_list.append(logfile.get_meta_data())
        if not metadata_list:
            return None
        return LogData(
            component_type=component_type,
            meta_data_list=metadata_list
        )
