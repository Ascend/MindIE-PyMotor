# coding=utf-8
# Copyright (c) 2025, HUAWEI CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import time
import queue
import threading
from dataclasses import dataclass

from motor.config.controller import ControllerConfig
from motor.resources.instance import Instance
from motor.resources.http_msg_spec import InsEventMsg, EventType
from motor.utils.http_client import SafeHTTPSClient
from motor.utils.logger import get_logger
from motor.controller.core.observer import Observer, ObserverEvent

logger = get_logger(__name__)


@dataclass
class Event:
    event_type: EventType
    instance: Instance


class EventPusher(Observer):
    def __init__(self, config: ControllerConfig | None = None) -> None:
        super().__init__()
        self.is_coordinator_reset = False
        self.event_queue = queue.Queue()
        self.instances: dict[str, Instance] = {}
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.base_url = f"{config.coordinator_api_dns}:{config.coordinator_api_port}"

        self.heart_client = SafeHTTPSClient(
            base_url=self.base_url,
            cert_file=None,
            key_file=None,
            ca_file=None,
            timeout=0.5
        )
        self.event_consumer_thread = threading.Thread(
            target=self._event_consumer,
            daemon=True,
            name="EventConsumer"
        )
        self.heartbeat_detector_thread = threading.Thread(
            target=self._coordinator_heartbeat_detector,
            daemon=True,
            name="HeartbeatDetector"
        )

        logger.info("EventPusher initialized.")

    def update(self, instance: Instance, event: ObserverEvent) -> None:
        if event == ObserverEvent.INSTANCE_ADDED:
            with self.lock:
                self.instances[instance.job_name] = instance
            event = Event(EventType.ADD, instance)
            logger.info("Instance added: %s", instance.job_name)
        elif event == ObserverEvent.INSTANCE_SEPERATED:
            with self.lock:
                if instance.job_name in self.instances:
                    del self.instances[instance.job_name]
            event = Event(EventType.DEL, instance)
            logger.info("Instance removed: %s", instance.job_name)
        elif event == ObserverEvent.INSTANCE_REMOVED:
            # Separated event is already notified coordinator
            # to remove instance. so we don't need to notify again.
            return
        else:
            raise ValueError(f"Unknown event type: {event}")

        self.event_queue.put(event)

    def start(self) -> None:
        """Start the event pusher threads"""
        self.event_consumer_thread.start()
        self.heartbeat_detector_thread.start()
        logger.info("EventPusher started.")

    def stop(self) -> None:
        self.stop_event.set()
        if self.event_queue.qsize() == 0:
            # Put a element into queue to make thread exit.
            self.event_queue.put(None)
        # Only join threads that have been started
        if self.event_consumer_thread.is_alive():
            self.event_consumer_thread.join()
        if self.heartbeat_detector_thread.is_alive():
            self.heartbeat_detector_thread.join()
        self.heart_client.close()
        logger.info("EventPusher stopped.")

    def _event_consumer(self) -> None:
        while not self.stop_event.is_set():
            event = self.event_queue.get()
            if event is not None:
                try:
                    client = SafeHTTPSClient(
                        base_url=self.base_url,
                        timeout=0.5
                    )
                    event_type = event.event_type
                    if event_type == EventType.ADD:
                        event_msg = InsEventMsg(event=event_type, instances=[event.instance])
                    elif event_type == EventType.DEL:
                        event_msg = InsEventMsg(event=event_type, instances=[event.instance])
                    elif event_type == EventType.SET:
                        with self.lock:
                            event_msg = InsEventMsg(
                                event=event_type,
                                instances=list(self.instances.values())
                            )
                    else:
                        logger.error("Unknown event type: %s", event_type)
                        continue

                    response = client.post("/coordinator/active", data=event_msg.model_dump())
                    response_text = response.get("text")
                    if event.instance is not None:
                        logger.info("Event pushed type: %s, job name: %s, response: %s",
                                    event_type, event.instance.job_name, response_text)
                    else:
                        logger.info("Event pushed type: %s, push all instances, response: %s",
                                    event_type, response_text)
                except Exception as e:
                    logger.error("Exception occurred while pushing event: %s", e)
                finally:
                    client.close()
                
            time.sleep(1)

    def _coordinator_heartbeat_detector(self) -> None:
        """detect coordinator heartbeat"""
        hb_loss_cnt = 0
        while not self.stop_event.is_set():
            try:
                response = self.heart_client.get("/coordinator/heartbeat", params={"status": "normal"})
                if self.is_coordinator_reset:
                    # SET event means push all instances to coordinator,
                    # so job_name is not a instance job_name, it is "coordinator_restart".
                    event = Event(EventType.SET, None)
                    self.event_queue.put(event)
                    self.is_coordinator_reset = False
                    hb_loss_cnt = 0

            except Exception as e:
                hb_loss_cnt += 1
                if hb_loss_cnt >= 2:
                    self.is_coordinator_reset = True
                    logger.warning("Coordinator heartbeat lost. Possible restart detected.")
                    hb_loss_cnt = 0
                logger.warning("Send Coordinator heartbeat failed, Exception occurred %s", e)

            time.sleep(0.5)
