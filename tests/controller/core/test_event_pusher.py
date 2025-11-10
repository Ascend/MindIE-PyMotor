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
import pytest
import queue
from unittest.mock import Mock, patch, MagicMock

from motor.config.controller import ControllerConfig
from motor.controller.core.event_pusher import EventPusher, Event
from motor.resources.instance import Instance
from motor.controller.core.observer import ObserverEvent
from motor.resources.http_msg_spec import EventType


@pytest.fixture
def event_pusher():
    """create EventPusher object fixture"""
    with patch('motor.controller.core.event_pusher.SafeHTTPSClient') as mock_client_class:
        with patch('threading.Thread') as mock_thread_class:
            mock_thread = MagicMock()
            mock_thread_class.return_value = mock_thread

            mock_client = Mock()
            mock_client_class.return_value = mock_client
            # Create EventPusher instance (threads are created in __init__)
            config = ControllerConfig()
            return EventPusher(config)


@pytest.fixture
def mock_instance():
    """mock Instance fixture"""
    instance = Mock(spec=Instance)
    instance.job_name = "test_job"
    return instance


@pytest.fixture
def mock_http_client():
    """mock HTTP client fixture"""
    with patch('motor.controller.core.event_pusher.SafeHTTPSClient') as mock_client_class:
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        yield mock_client

def test_init(event_pusher):
    """init test case"""
    assert event_pusher.is_coordinator_reset == False
    assert isinstance(event_pusher.event_queue, queue.Queue)
    assert event_pusher.instances == {}

    # check thread is alive
    assert event_pusher.event_consumer_thread.is_alive()
    assert event_pusher.heartbeat_detector_thread.is_alive()
    assert event_pusher.event_consumer_thread.daemon
    assert event_pusher.heartbeat_detector_thread.daemon

def test_event_consumer_add_event(event_pusher, mock_http_client):
    """test event consumer add event"""
    # set mock
    mock_client_instance = Mock()
    mock_http_client.return_value = mock_client_instance
    mock_response = Mock(status_code=200, text="ok")
    mock_http_client.get.return_value = "success"
    mock_http_client.post.return_value = mock_response

    # add instances
    test_instance = Instance(job_name="test_job", model_name="test_model", id=1, role="prefill")
    event_pusher.instances["test_job"] = test_instance

    test_event = Event(
        event_type=EventType.ADD,
        instance=test_instance
    )
    event_pusher.event_queue.put(test_event)
    # send stop single
    event_pusher.event_queue.put(None)

    # Call the event consumer (since it's an infinite loop, we need to control it to execute only once)
    def mock_stop_sleep(seconds):
        if event_pusher.event_queue.qsize() > 0:
            raise StopIteration

    with patch('motor.controller.core.event_pusher.time') as mock_time:
        mock_time.sleep.side_effect = mock_stop_sleep
        try:
            event_pusher._event_consumer()
        except StopIteration as e:
            pass

        # check post is call
        mock_http_client.post.assert_called_once()

def test_event_consumer_del_event(event_pusher, mock_http_client):
    """test event consumer del event"""
    # set mock
    mock_client_instance = Mock()
    mock_http_client.return_value = mock_client_instance
    mock_response = Mock(status_code=200, text="ok")
    mock_http_client.get.return_value = "success"
    mock_http_client.post.return_value = mock_response

    # add instances
    test_instance = Instance(job_name="test_job", model_name="test_model", id=1, role="prefill")
    event_pusher.instances["test_job"] = test_instance

    test_event = Event(
        event_type=EventType.DEL,
        instance=test_instance
    )
    event_pusher.event_queue.put(test_event)
    # send stop single
    event_pusher.event_queue.put(None)

    # Call the event consumer (since it's an infinite loop, we need to control it to execute only once)
    def mock_sleep(seconds):
        if event_pusher.event_queue.qsize() > 0:
            raise StopIteration

    with patch('motor.controller.core.event_pusher.time') as mock_time:
        mock_time.sleep.side_effect = mock_sleep
        try:
            event_pusher._event_consumer()
        except StopIteration:
            pass

        # check post is call
        mock_http_client.post.assert_called_once()

def test_event_consumer_set_event(event_pusher, mock_http_client):
    """test event consumer set event"""
    # set mock
    mock_client_instance = Mock()
    mock_http_client.return_value = mock_client_instance
    mock_response = Mock(status_code=200, text="ok")
    mock_http_client.get.return_value = "success"
    mock_http_client.post.return_value = mock_response

    # add multi instance
    for i in range(3):
        job_name = "test_job" + str(i)
        test_instance = Instance(job_name=job_name, model_name="test_model", id=i, role="prefill")
        event_pusher.instances[job_name] = test_instance

    test_event = Event(
        event_type=EventType.SET,
        instance=None
    )

    event_pusher.event_queue.put(test_event)
    event_pusher.event_queue.put(None)

    # Call the event consumer (since it's an infinite loop, we need to control it to execute only once)
    def mock_sleep(seconds):
        if event_pusher.event_queue.qsize() > 0:
            raise StopIteration

    with patch('motor.controller.core.event_pusher.time') as mock_time:
        mock_time.sleep.side_effect = mock_sleep
        try:
            event_pusher._event_consumer()
        except StopIteration:
            pass

        # check post is call
        mock_http_client.post.assert_called_once()

def test_event_consumer_exception_handling(event_pusher, mock_http_client):
    """test event consumer exception handling"""
    # set mock
    def mock_post(endpoint: str, data: dict):
        raise Exception("Network error")
    mock_client_instance = Mock()
    mock_http_client.return_value = mock_client_instance
    mock_http_client.post.side_effect = mock_post

    # add instances
    test_instance = Instance(job_name="test_job", model_name="test_model", id=1, role="prefill")
    event_pusher.instances["test_job"] = test_instance

    test_event = Event(
        event_type=EventType.ADD,
        instance=test_instance
    )

    event_pusher.event_queue.put(test_event)
    event_pusher.event_queue.put(None)

    def mock_sleep(seconds):
        if event_pusher.event_queue.qsize() > 0:
            raise StopIteration

    with patch('motor.controller.core.event_pusher.logger') as mock_logger:
        with patch('motor.controller.core.event_pusher.time') as mock_time:
            mock_time.sleep.side_effect = mock_sleep
            try:
                event_pusher._event_consumer()
            except StopIteration:
                pass

            # check post is call
            mock_http_client.post.assert_called_once()

            # check exception log
            mock_logger.error.assert_called_once()
            args, kwargs = mock_logger.error.call_args
            assert args[0] == "Exception occurred while pushing event: %s"
            assert isinstance(args[1], Exception)
            assert str(args[1]) == "Network error"

def test_heartbeat_detector_normal(event_pusher, mock_http_client):
    """test heartbeat detector"""
    mock_client_instance = Mock()
    mock_http_client.return_value = mock_client_instance
    mock_response = Mock(status_code=200, text="ok")
    mock_http_client.get.return_value = mock_response

    # mock reset flag，重置为 True 时应发送一次 SET 事件并清零标志
    event_pusher.is_coordinator_reset = True

    # set loop count
    call_count = 0

    def mock_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise StopIteration

    with patch('motor.controller.core.event_pusher.time') as mock_time:
        mock_time.sleep.side_effect = mock_sleep

        try:
            event_pusher._coordinator_heartbeat_detector()
        except StopIteration:
            pass

        # check reset flag
        assert event_pusher.is_coordinator_reset == False
        # 当检测到重置时，应推送一次 SET 事件
        assert not event_pusher.event_queue.empty()
        evt = event_pusher.event_queue.get()
        assert evt.event_type == EventType.SET
        assert evt.instance is None

def test_heartbeat_detector_failure(event_pusher, mock_http_client):
    """test heartbeat detector failure"""
    def mock_get(endpoint: str, params: dict = None):
        raise Exception("Connection failed")

    mock_client_instance = Mock()
    mock_http_client.return_value = mock_client_instance
    event_pusher.heart_client.get.side_effect = mock_get

    call_count = 0
    def mock_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            raise StopIteration

    with patch('motor.controller.core.event_pusher.logger') as mock_logger:
        with patch('motor.controller.core.event_pusher.time') as mock_time:
            mock_time.sleep.side_effect = mock_sleep
            try:
                event_pusher._coordinator_heartbeat_detector()
            except StopIteration:
                pass

            # test reset flag is change
            assert event_pusher.is_coordinator_reset == True
            mock_logger.warning.assert_called()

def test_update_add_instance(event_pusher):
    """test update add instance"""
    test_instance = Instance(job_name="test_job", model_name="test_model", id=1, role="prefill")
    event_pusher.update(test_instance, ObserverEvent.INSTANCE_ADDED)

    # Verify that the instance was added to the dictionary
    assert test_instance.job_name in event_pusher.instances
    assert event_pusher.instances[test_instance.job_name] == test_instance

    # Verify that the event has been placed in the queue
    assert not event_pusher.event_queue.empty()
    event = event_pusher.event_queue.get()
    assert event.event_type == EventType.ADD
    assert event.instance.job_name == test_instance.job_name

def test_update_remove_instance(event_pusher):
    """test update remove instance"""
    test_instance = Instance(job_name="test_job", model_name="test_model", id=1, role="prefill")
    event_pusher.instances[test_instance.job_name] = test_instance

    event_pusher.update(test_instance, ObserverEvent.INSTANCE_REMOVED)

    # INSTANCE_REMOVED 分支不再推送事件
    assert event_pusher.event_queue.empty()

def test_update_seperated_instance(event_pusher):
    """test update seperated instance"""
    test_instance = Instance(job_name="test_job_seperated", model_name="test_model", id=1, role="prefill")
    event_pusher.instances[test_instance.job_name] = test_instance

    event_pusher.update(test_instance, ObserverEvent.INSTANCE_SEPERATED)

    # Verify that the event has been placed in the queue
    assert not event_pusher.event_queue.empty()
    event = event_pusher.event_queue.get()
    # INSTANCE_SEPERATED 应作为 DEL 事件通知
    assert event.event_type == EventType.DEL
    assert event.instance.job_name == test_instance.job_name

def test_update_seperated_instance_recovery(event_pusher):
    """test update seperated instance recovery"""
    test_instance = Instance(job_name="test_job_recovery", model_name="test_model", id=1, role="prefill")
    event_pusher.instances[test_instance.job_name] = test_instance

    # First separate the instance
    event_pusher.update(test_instance, ObserverEvent.INSTANCE_SEPERATED)
    # Clear the queue
    while not event_pusher.event_queue.empty():
        event_pusher.event_queue.get()

    # Then recover the instance
    event_pusher.update(test_instance, ObserverEvent.INSTANCE_ADDED)

    # Verify that the recovery event has been placed in the queue
    assert not event_pusher.event_queue.empty()
    event = event_pusher.event_queue.get()
    assert event.event_type == EventType.ADD
    assert event.instance.job_name == test_instance.job_name
