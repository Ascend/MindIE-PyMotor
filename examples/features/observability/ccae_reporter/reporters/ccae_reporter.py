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
import time

from ccae_reporter.common.util import safe_open
from ccae_reporter.thread_safe_util import ThreadSafeFactory
from ccae_reporter.reporters.base_reporter import BaseReporter

import motor

CATEGORY_STR = "category"
ALARM_ID_STR = "alarmId"
MODEL_ID_STR = "modelID"
INVENTORIES_STR = "inventories"
METRICS_STR = "metrics"


def response_raise_for_status(response, interface_name: str):
    if response.status_code >= 400:
        raise RuntimeError(f"Response from {interface_name} failed, status is {response.status_code}, "
                           f"content is {response.text}")


def check_element(item: dict, key: str):
    if key not in item.keys():
        raise ValueError(f"Failed to read http response, lack key `{key}`")


class CCAEReporter(BaseReporter):
    def __init__(self, backend_name: str, identity: str):
        super().__init__(backend_name, identity)
        # model_id_period 值为一个三元list
        # 第一位 bool 代表是否需要立即上报
        # 第二位 int 代表上报的时间间隔，以秒为单位
        # 第三位 float 代表上一次上报的时间戳，以秒为单位
        self.model_id_period = ThreadSafeFactory.make_threadsafe_instance(dict)
        self.alarm_cache = ThreadSafeFactory.make_threadsafe_instance(dict)
        for _ in range(1):
            model_id = os.getenv("SERVICE_ID")
            if model_id is None:
                raise RuntimeError("Environment variable $SERVICE_ID is not set.")
            max_env_len = 256
            if len(model_id) > max_env_len:
                raise RuntimeError("Environment variable $SERVICE_ID is not correct.")
            self.model_id_period[model_id] = [False, 1, time.time()]
        self.version = self.fetch_version_info()
        self.component_type = -1
        if identity == "Coordinator":
            self.component_type = 1
        elif identity == "Controller":
            self.component_type = 0

    def fetch_version_info(self) -> str:
        # Fetch version information from version.info file
        server_dir = os.path.dirname(motor.__file__)
        if server_dir is None:
            raise RuntimeError("Environment variable $MOTOR_INSTALL_PATH is not set.")
        with safe_open(os.path.join(server_dir, "version.info")) as f:
            for line in f:
                if "motor_version" in line:
                    return line.split(":")[-1].strip()
        self.logger.error("Failed to fetch version info.")
        return "UNKNOWN VERSION"

    def send_heart_beat(self):
        url = "/rest/ccaeommgmt/v1/managers/mindie/register"
        request_data = {
            "timeStamp": int(time.time() * 1000),
            "modelServiceInfo": [],
            "componentType": self.component_type,
            "version": self.version,
        }
        for model_id, _ in self.model_id_period.items():
            request_data["modelServiceInfo"].append({
                MODEL_ID_STR: model_id,
                "modelName": os.getenv("MODEL_NAME"),
            })
        try:
            self.logger.debug(f"Sending heartbeat to {url} with data: {request_data}")
            response = self.http_client.do_post(url, request_data)
            response_raise_for_status(response, "heartbeat")
            self.logger.debug("Response from heartbeat is: %s", response.json())
        except Exception as e:
            self.heart_beat_ready.clear()
            self.logger.error(e)
            return
        response_json = response.json()
        if response_json["retCode"] != 0:
            raise RuntimeError(f"Failed to send heartbeat! Return message from ccae is: {response_json['retMsg']}")
        check_element(response_json, "reqList")
        for req in response_json["reqList"]:
            check_element(req, MODEL_ID_STR)
            model_id = req[MODEL_ID_STR]
            check_element(req, INVENTORIES_STR)
            check_element(req[INVENTORIES_STR], "forceUpdate")
            check_element(req, METRICS_STR)
            check_element(req[METRICS_STR], "metricPeriod")
            self.model_id_period[model_id][0] = req[INVENTORIES_STR]["forceUpdate"]
            self.model_id_period[model_id][1] = req[METRICS_STR]["metricPeriod"]
            self.log_topic = req["logsServer"]["topic"]
            self.log_ports = req["logsServer"]["servicePort"]
        self.heart_beat_ready.set()

    def fetch_models_and_update(self) -> list:
        models_to_upload = []
        for model_id, send_tuple in self.model_id_period.items():
            if not send_tuple[0] and time.time() < send_tuple[1] + send_tuple[2]:
                continue
            self.model_id_period[model_id][0] = False
            self.model_id_period[model_id][2] = time.time()
            models_to_upload.append(model_id)
        return models_to_upload

    def upload_alarm(self, alarm_list) -> bool:
        for item in alarm_list:
            if CATEGORY_STR not in item:
                raise ValueError(f"Failed to send alarms, lack key `{CATEGORY_STR}`")
            if ALARM_ID_STR not in item:
                raise ValueError(f"Failed to send alarms, lack key `{ALARM_ID_STR}`")
            # a new alarm
            if item[CATEGORY_STR] == 1:
                self.alarm_cache[item[ALARM_ID_STR]] = item
            # cancel an alarm
            elif item[CATEGORY_STR] == 2:
                if item[ALARM_ID_STR] in self.alarm_cache.keys():
                    del self.alarm_cache[item[ALARM_ID_STR]]
        self.logger.info(f"Uploading alarms: {alarm_list}")
        url = "/rest/ccaeommgmt/v1/managers/mindie/events"
        try:
            response = self.http_client.do_post(url, alarm_list)
            response_raise_for_status(response, "alarm")
            self.logger.info("Response from alarm is: %s", response.json())
            return True
        except Exception as e:
            self.logger.error(f"Failed to upload alarms, error: {e}")
            return False

    def upload_inventory(self, inventories):
        self.logger.debug(f"Uploading inventory: {inventories}")
        url = "/rest/ccaeommgmt/v1/managers/mindie/inventory"
        try:
            response = self.http_client.do_post(url, inventories)
            response_raise_for_status(response, "inventory")
            self.logger.debug("Response from inventory is: %s", response.json())
        except Exception as e:
            self.logger.error(f"Failed to upload inventory, error: {e}")

    def upload_log(self, log_request_message: dict):
        try:
            self.producer.send(self.log_topic, log_request_message)
        except Exception as e:
            self.logger.error(e)

    def fetch_alarm_cache(self) -> list:
        return list(self.alarm_cache.values())
