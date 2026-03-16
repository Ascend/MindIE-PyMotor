# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

from confluent_kafka import Producer

from ccae_reporter.common.logging import Log
from ccae_reporter.common.util import PathCheck


class KafkaProducer:
    def __init__(self, kafka_config=None):
        self.logger = Log(__name__).getlog()
        self.kafka_config = kafka_config
        self.kafka_config_whitelist_check()
        self.producer = Producer(self.kafka_config)
        self.running = True
        self.get_messages = None

    def kafka_config_whitelist_check(self):
        params_whitelist = {
            'acks': lambda x: x in ['all', '-1', '0', '1'],
            'compression.type': lambda x: x in ['none', 'gzip', 'snappy', 'lz4', 'zstd'],
            'retries': lambda x: isinstance(x, int) and x >= 0,
            'security.protocol': lambda x: x in ['PLAINTEXT', 'ssl', 'SASL_SSL', 'SASL_PLAINTEXT']
        }

        # 除PLAINTEXT模式，其余模式对ssl相关入参进行校验
        if self.kafka_config.get("security.protocol", "") != "PLAINTEXT":
            params_whitelist.update({
                'ssl.ca.location': lambda x: isinstance(x, str) and PathCheck.check_path_full(x),
                'ssl.certificate.location': lambda x: isinstance(x, str) and PathCheck.check_path_full(x),
                'ssl.key.location': lambda x: isinstance(x, str) and PathCheck.check_path_full(x),
                'ssl.key.password': lambda x: isinstance(x, str) and len(x) > 0,
                'ssl.crl.location': lambda x: isinstance(x, str) and PathCheck.check_path_full(x)
            })

        for param, value in self.kafka_config.items():
            if param in params_whitelist:
                if not params_whitelist[param](value):
                    err_msg = f"[CCAE Reporter] Invalid value in kafka producer config for parameter {param}"
                    raise ValueError(err_msg)

    def send(self, topic, message):
        self.producer.produce(
            topic=topic,
            value=message,
            callback=self._delivery_report
        )
        self.producer.flush()

    def _delivery_report(self, err, msg):
        if err is not None:
            self.logger.error("[CCAE Reporter] message send failed, the reason is: %s" % err)
        else:
            self.logger.debug(
                f"[CCAE Reporter] message send successfully topic=%s, partition=%s", msg.topic(), msg.partition())
