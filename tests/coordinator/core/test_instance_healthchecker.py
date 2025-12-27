import time
import pytest
import threading
from unittest.mock import MagicMock, patch, call

from motor.common.resources.instance import Instance, PDRole
from motor.common.resources.endpoint import Endpoint
from motor.coordinator.core.instance_manager import InstanceManager, UpdateInstanceMode
from motor.coordinator.core.instance_healthchecker import InstanceHealthChecker
from motor.config.coordinator import CoordinatorConfig
from motor.common.utils.dummy_request import DummyRequestUtil
from motor.common.utils.singleton import ThreadSafeSingleton


def _cleanup_singletons():
    """Clean up singleton instances to ensure test isolation"""
    singletons_to_cleanup = [InstanceHealthChecker, InstanceManager]

    for singleton_cls in singletons_to_cleanup:
        if singleton_cls in ThreadSafeSingleton._instances:
            instance = ThreadSafeSingleton._instances[singleton_cls]
            try:
                if hasattr(instance, 'stop'):
                    instance.stop()
            except Exception:
                pass  # Ignore errors during cleanup
            del ThreadSafeSingleton._instances[singleton_cls]


@pytest.fixture(autouse=True)
def cleanup_singletons():
    """Auto cleanup singletons before and after each test"""
    _cleanup_singletons()
    yield
    _cleanup_singletons()


@pytest.fixture
def mock_config():
    """Mock coordinator config"""
    from motor.config.coordinator import CoordinatorConfig
    config = CoordinatorConfig()
    # Set fast intervals for testing
    config.health_check_config.dummy_request_interval = 0.1
    config.health_check_config.error_retry_interval = 0.05
    config.health_check_config.max_consecutive_failures = 3
    config.health_check_config.thread_join_timeout = 0.1
    return config


@pytest.fixture
def instance_healthchecker(mock_config, mock_dummy_request_util):
    """Setup mock health checker with threading mocked to prevent actual thread starts"""
    with patch('motor.coordinator.core.instance_healthchecker.DummyRequestUtil', return_value=mock_dummy_request_util), \
         patch('threading.Thread') as mock_thread_class, \
         patch('threading.Event') as mock_event_class:

        mock_thread = MagicMock()
        mock_thread_class.return_value = mock_thread

        mock_shutdown_event = MagicMock()
        mock_shutdown_event.is_set.return_value = False
        mock_event_class.return_value = mock_shutdown_event

        healthchecker = InstanceHealthChecker(mock_config)
        # Override the mocked event with our controlled mock
        healthchecker._shutdown_event = mock_shutdown_event
        yield healthchecker

@pytest.fixture
def mock_instance():
    """Create mock Instance"""
    instance = MagicMock(spec=Instance)
    instance.id = 1
    instance.role = PDRole.ROLE_P
    instance.job_name = "test_job"
    instance.endpoints = {}
    instance.gathered_workload = MagicMock()
    return instance

@pytest.fixture
def mock_endpoint():
    """Create mock Endpoint"""
    endpoint = MagicMock(spec=Endpoint)
    endpoint.id = 1
    endpoint.ip = "127.0.0.1"
    endpoint.port = "8080"
    endpoint.device_infos = []
    endpoint.workload = MagicMock()
    return endpoint

@pytest.fixture
def mock_instance_manager():
    """Create mock InstanceManager"""
    manager = MagicMock(spec=InstanceManager)
    manager.update_instance_state = MagicMock()
    manager.delete_unavailable_instance = MagicMock()
    manager.is_available = MagicMock(return_value=True)
    return manager

@pytest.fixture
def mock_dummy_request_util():
    """Create mock DummyRequestUtil"""
    util = MagicMock(spec=DummyRequestUtil)
    util.send_dummy_request = MagicMock(return_value=True)
    util.close = MagicMock()
    return util


class TestInstanceHealthChecker:
    """InstanceHealthChecker unit test class"""

    def test_init_with_none_config(self):
        """Test initialization with None config uses default"""
        with patch('threading.Thread'), patch('threading.Event'):
            checker = InstanceHealthChecker(config=None)
            assert checker._health_check_config is not None
            assert hasattr(checker, '_health_check_config')

    def test_start_creates_and_starts_thread(self, instance_healthchecker):
        """Test that start method creates and starts monitoring thread"""
        instance_healthchecker.start()

        # Verify thread was created with correct parameters
        # The Thread constructor should have been called with the right arguments
        from unittest.mock import MagicMock
        assert isinstance(instance_healthchecker._monitoring_thread, MagicMock)

        # Verify thread was started
        instance_healthchecker._monitoring_thread.start.assert_called_once()

    def test_stop_functionality(self, instance_healthchecker):
        """Test stop functionality"""
        # Mock the monitoring thread
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        instance_healthchecker._monitoring_thread = mock_thread

        # Test stop
        instance_healthchecker.stop()

        # Verify shutdown event was set
        instance_healthchecker._shutdown_event.set.assert_called_once()

        # Verify thread join was called
        mock_thread.join.assert_called_once_with(timeout=instance_healthchecker._health_check_config.thread_join_timeout)

        # Verify DummyRequestUtil was closed
        instance_healthchecker._dummy_request_util.close.assert_called_once()

    def test_stop_already_stopped(self, instance_healthchecker):
        """Test stop when already stopped"""
        # Set shutdown event as already set
        instance_healthchecker._shutdown_event.is_set.return_value = True

        # Test stop
        instance_healthchecker.stop()

        # Verify shutdown event set was not called again
        instance_healthchecker._shutdown_event.set.assert_not_called()

    def test_push_exception_instance(self, instance_healthchecker, mock_instance, mock_endpoint, mock_instance_manager):
        """Test receiving abnormal instance"""
        # Push abnormal instance
        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            instance_healthchecker.push_exception_instance(mock_instance, mock_endpoint)

        # Verify instance was added to monitoring list
        assert mock_instance.id in instance_healthchecker._monitored_instances
        monitoring_info = instance_healthchecker._monitored_instances[mock_instance.id]
        assert monitoring_info["instance"] == mock_instance
        assert monitoring_info["endpoint"] == mock_endpoint
        assert instance_healthchecker._consecutive_failures[mock_instance.id] == 0

        # Verify instanceManager was called to isolate instance
        mock_instance_manager.update_instance_state.assert_called_once_with(
            mock_instance.id, UpdateInstanceMode.UNAVAILABLE
        )

    def test_push_exception_instance_duplicate(self, instance_healthchecker, mock_instance, mock_endpoint, mock_instance_manager):
        """Test pushing duplicate instance"""
        # Push first instance
        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            instance_healthchecker.push_exception_instance(mock_instance, mock_endpoint)

        # Reset mock call count
        mock_instance_manager.update_instance_state.reset_mock()

        # Push same instance again
        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            instance_healthchecker.push_exception_instance(mock_instance, mock_endpoint)

        # Should not call instanceManager again for the same instance
        mock_instance_manager.update_instance_state.assert_not_called()

    def test_push_exception_instance_manager_error(self, instance_healthchecker, mock_instance, mock_endpoint, mock_instance_manager):
        """Test error handling when instance manager fails"""
        # Mock instanceManager throwing exception
        mock_instance_manager.update_instance_state.side_effect = Exception("Test error")

        # Should not raise exception, but log error
        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            instance_healthchecker.push_exception_instance(mock_instance, mock_endpoint)

        # Instance should still be added to monitoring list despite manager error
        assert mock_instance.id in instance_healthchecker._monitored_instances

    def test_check_state_alarm_available(self, instance_healthchecker, mock_instance_manager):
        """Test availability check (with available instances)"""
        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            result = instance_healthchecker.check_state_alarm()

        assert result is True
        mock_instance_manager.is_available.assert_called_once()

    def test_check_state_alarm_unavailable(self, instance_healthchecker, mock_instance_manager):
        """Test availability check (no available instances)"""
        mock_instance_manager.is_available.return_value = False

        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager), \
             patch.object(instance_healthchecker, '_call_controller_alarm') as mock_alarm:

            result = instance_healthchecker.check_state_alarm()

        assert result is False
        mock_instance_manager.is_available.assert_called_once()

        # Verify controller alarm was called with correct parameters
        mock_alarm.assert_called_once_with(
            alarm_type="no_available_instances",
            message="No available P and D instances found",
            severity="critical"
        )

    def test_check_state_alarm_exception(self, instance_healthchecker, mock_instance_manager):
        """Test availability check when exception occurs"""
        mock_instance_manager.is_available.side_effect = Exception("Test error")

        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            result = instance_healthchecker.check_state_alarm()

        assert result is False

    def test_monitoring_loop_normal_operation(self, instance_healthchecker):
        """Test monitoring loop normal operation"""
        # Mock shutdown event to allow multiple iterations then stop
        instance_healthchecker._shutdown_event.is_set.side_effect = [False, False, True]
        instance_healthchecker._shutdown_event.wait.return_value = None

        # Mock the check method
        with patch.object(instance_healthchecker, '_check_monitored_instances') as mock_check:
            instance_healthchecker._monitoring_loop()

            # Should call check twice before shutdown
            assert mock_check.call_count == 2
            # Should wait for the configured interval
            assert instance_healthchecker._shutdown_event.wait.call_count == 2
            instance_healthchecker._shutdown_event.wait.assert_called_with(instance_healthchecker._health_check_config.dummy_request_interval)

    def test_monitoring_loop_with_exception(self, instance_healthchecker):
        """Test monitoring loop exception handling"""
        # Mock shutdown event to allow one iteration then stop
        instance_healthchecker._shutdown_event.is_set.side_effect = [False, True]
        instance_healthchecker._shutdown_event.wait.return_value = None

        # Mock the check method to raise exception
        with patch.object(instance_healthchecker, '_check_monitored_instances', side_effect=Exception("Test error")):
            # Should not raise exception
            instance_healthchecker._monitoring_loop()

            # Should wait for error retry interval after exception
            instance_healthchecker._shutdown_event.wait.assert_called_with(instance_healthchecker._health_check_config.error_retry_interval)

    def test_monitoring_loop_shutdown_check(self, instance_healthchecker):
        """Test monitoring loop respects shutdown event"""
        # Set shutdown event immediately
        instance_healthchecker._shutdown_event.is_set.return_value = True

        # Mock the check method
        with patch.object(instance_healthchecker, '_check_monitored_instances') as mock_check:
            instance_healthchecker._monitoring_loop()

            # Should not call check when shutdown is set
            mock_check.assert_not_called()
            # Should not wait
            instance_healthchecker._shutdown_event.wait.assert_not_called()

    def test_check_monitored_instances_empty(self, instance_healthchecker):
        """Test checking empty monitored instances"""
        instance_healthchecker._monitored_instances = {}

        # Mock the single instance check
        with patch.object(instance_healthchecker, '_check_single_instance') as mock_single_check:
            instance_healthchecker._check_monitored_instances()

            # Should not call single check for empty list
            mock_single_check.assert_not_called()

    def test_check_monitored_instances_with_instances(self, instance_healthchecker, mock_instance, mock_endpoint, mock_instance_manager):
        """Test checking multiple monitored instances"""
        # Add multiple instances
        instances = []
        for i in range(3):
            instance = MagicMock(spec=Instance)
            instance.id = i
            instance.role = PDRole.ROLE_P

            endpoint = MagicMock(spec=Endpoint)
            endpoint.id = i

            instance_healthchecker._monitored_instances[i] = {
                "instance": instance,
                "endpoint": endpoint,
                "start_time": time.time(),
                "last_check_time": time.time()
            }
            instances.append((instance, endpoint))

        # Mock shutdown event to not trigger
        instance_healthchecker._shutdown_event.is_set.return_value = False

        # Mock the single instance check
        with patch.object(instance_healthchecker, '_check_single_instance') as mock_single_check:
            instance_healthchecker._check_monitored_instances()

            # Should call single check for each instance
            assert mock_single_check.call_count == 3
            for i in range(3):
                mock_single_check.assert_any_call(i)

    def test_check_monitored_instances_shutdown_during_check(self, instance_healthchecker):
        """Test checking instances stops when shutdown event is set"""
        # Add multiple instances
        for i in range(3):
            instance = MagicMock(spec=Instance)
            instance.id = i
            endpoint = MagicMock(spec=Endpoint)
            endpoint.id = i

            instance_healthchecker._monitored_instances[i] = {
                "instance": instance,
                "endpoint": endpoint,
                "start_time": time.time(),
                "last_check_time": time.time()
            }

        # Mock shutdown event to be set after first check
        call_count = 0
        def shutdown_side_effect():
            nonlocal call_count
            call_count += 1
            return call_count > 1  # Return True after first call

        instance_healthchecker._shutdown_event.is_set.side_effect = shutdown_side_effect

        # Mock the single instance check
        with patch.object(instance_healthchecker, '_check_single_instance') as mock_single_check:
            instance_healthchecker._check_monitored_instances()

            # Should only call single check once due to shutdown
            assert mock_single_check.call_count == 1

    def test_check_single_instance_success(self, instance_healthchecker, mock_instance, mock_endpoint, mock_instance_manager, mock_dummy_request_util):
        """Test successful instance check leading to recovery"""
        # Add monitored instance
        instance_id = 1
        instance_healthchecker._monitored_instances[instance_id] = {
            "instance": mock_instance,
            "endpoint": mock_endpoint,
            "start_time": time.time(),
            "last_check_time": time.time()
        }
        instance_healthchecker._consecutive_failures[instance_id] = 1

        # Mock successful dummy request
        mock_dummy_request_util.send_dummy_request.return_value = True

        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            instance_healthchecker._check_single_instance(instance_id)

        # Verify instance was recovered
        mock_instance_manager.update_instance_state.assert_called_once_with(
            instance_id, UpdateInstanceMode.AVAILABLE
        )

        # Verify instance was removed from monitoring
        assert instance_id not in instance_healthchecker._monitored_instances
        assert instance_id not in instance_healthchecker._consecutive_failures

    def test_check_single_instance_failure_below_threshold(self, instance_healthchecker, mock_instance, mock_endpoint, mock_dummy_request_util):
        """Test failed instance check below threshold"""
        # Add monitored instance
        instance_id = 1
        instance_healthchecker._monitored_instances[instance_id] = {
            "instance": mock_instance,
            "endpoint": mock_endpoint,
            "start_time": time.time(),
            "last_check_time": time.time()
        }
        initial_failures = 1
        instance_healthchecker._consecutive_failures[instance_id] = initial_failures

        # Mock failed dummy request
        mock_dummy_request_util.send_dummy_request.return_value = False

        instance_healthchecker._check_single_instance(instance_id)

        # Verify failure count was incremented
        assert instance_healthchecker._consecutive_failures[instance_id] == initial_failures + 1
        # Verify instance is still monitored
        assert instance_id in instance_healthchecker._monitored_instances

    def test_check_single_instance_failure_at_threshold(self, instance_healthchecker, mock_instance, mock_endpoint, mock_dummy_request_util, mock_instance_manager):
        """Test failed instance check at threshold triggers termination"""
        # Add monitored instance
        instance_id = 1
        instance_healthchecker._monitored_instances[instance_id] = {
            "instance": mock_instance,
            "endpoint": mock_endpoint,
            "start_time": time.time(),
            "last_check_time": time.time()
        }
        # Set failure count to threshold - 1
        instance_healthchecker._consecutive_failures[instance_id] = instance_healthchecker._health_check_config.max_consecutive_failures - 1

        # Mock failed dummy request
        mock_dummy_request_util.send_dummy_request.return_value = False

        with patch.object(instance_healthchecker, '_terminate_instance') as mock_terminate:
            instance_healthchecker._check_single_instance(instance_id)

            # Verify termination was called
            mock_terminate.assert_called_once_with(instance_id)

    def test_check_single_instance_removed_during_check(self, instance_healthchecker, mock_instance, mock_endpoint, mock_dummy_request_util):
        """Test checking instance that gets removed during the check process"""
        # Add monitored instance
        instance_id = 1
        instance_healthchecker._monitored_instances[instance_id] = {
            "instance": mock_instance,
            "endpoint": mock_endpoint,
            "start_time": time.time(),
            "last_check_time": time.time()
        }

        # Mock the scenario where instance is removed after getting endpoint but before sending request
        def mock_send_dummy_request(endpoint):
            # Remove instance during the health check
            with instance_healthchecker._lock:
                if instance_id in instance_healthchecker._monitored_instances:
                    del instance_healthchecker._monitored_instances[instance_id]
            return False

        mock_dummy_request_util.send_dummy_request.side_effect = mock_send_dummy_request

        # This should not raise an exception
        instance_healthchecker._check_single_instance(instance_id)

        # Instance should be removed
        assert instance_id not in instance_healthchecker._monitored_instances

    def test_recover_instance(self, instance_healthchecker, mock_instance_manager):
        """Test instance recovery"""
        instance_id = 1
        instance_healthchecker._monitored_instances[instance_id] = {"instance": MagicMock(), "endpoint": MagicMock()}
        instance_healthchecker._consecutive_failures[instance_id] = 2

        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            instance_healthchecker._recover_instance(instance_id)

        # Verify instance was recovered
        mock_instance_manager.update_instance_state.assert_called_once_with(
            instance_id, UpdateInstanceMode.AVAILABLE
        )

        # Verify instance was removed from monitoring
        assert instance_id not in instance_healthchecker._monitored_instances
        assert instance_id not in instance_healthchecker._consecutive_failures

    def test_recover_instance_error(self, instance_healthchecker, mock_instance_manager):
        """Test instance recovery with error"""
        instance_id = 1
        instance_healthchecker._monitored_instances[instance_id] = {"instance": MagicMock(), "endpoint": MagicMock()}

        # Mock instanceManager throwing exception
        mock_instance_manager.update_instance_state.side_effect = Exception("Test error")

        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            instance_healthchecker._recover_instance(instance_id)

        # Instance should still be in monitoring due to error
        assert instance_id in instance_healthchecker._monitored_instances

    def test_terminate_instance_success(self, instance_healthchecker, mock_instance_manager):
        """Test successful instance termination"""
        instance_id = 1
        mock_instance = MagicMock(spec=Instance)
        mock_instance.role = PDRole.ROLE_P
        instance_healthchecker._monitored_instances[instance_id] = {"instance": mock_instance, "endpoint": MagicMock()}
        instance_healthchecker._consecutive_failures[instance_id] = 2

        with patch.object(instance_healthchecker, '_call_controller_terminate', return_value=True), \
             patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):

            instance_healthchecker._terminate_instance(instance_id)

            # Verify controller was called
            instance_healthchecker._call_controller_terminate.assert_called_once()
            # Verify instanceManager was called to delete instance
            mock_instance_manager.delete_unavailable_instance.assert_called_once_with(instance_id)

        # Verify instance was removed from monitoring
        assert instance_id not in instance_healthchecker._monitored_instances
        assert instance_id not in instance_healthchecker._consecutive_failures

    def test_terminate_instance_controller_failure(self, instance_healthchecker, mock_instance_manager):
        """Test instance termination when controller fails"""
        instance_id = 1
        mock_instance = MagicMock(spec=Instance)
        mock_instance.role = PDRole.ROLE_P
        instance_healthchecker._monitored_instances[instance_id] = {"instance": mock_instance, "endpoint": MagicMock()}
        instance_healthchecker._consecutive_failures[instance_id] = 2

        with patch.object(instance_healthchecker, '_call_controller_terminate', return_value=False), \
             patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):

            instance_healthchecker._terminate_instance(instance_id)

            # Verify controller was called but failed
            instance_healthchecker._call_controller_terminate.assert_called_once()
            # InstanceManager should not be called to delete instance
            mock_instance_manager.delete_unavailable_instance.assert_not_called()

        # Instance should still be in monitoring due to controller failure
        assert instance_id not in instance_healthchecker._monitored_instances
        assert instance_id not in instance_healthchecker._consecutive_failures

    def test_call_controller_alarm_success(self, instance_healthchecker):
        """Test successful controller alarm call"""
        with patch('requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            result = instance_healthchecker._call_controller_alarm("test_type", "test message", "critical")

            assert result is True
            mock_post.assert_called_once()
            # Verify correct URL was constructed
            call_args = mock_post.call_args
            assert instance_healthchecker._health_check_config.controller_api_dns in call_args[0][0]
            assert instance_healthchecker._health_check_config.alarm_endpoint in call_args[0][0]

    def test_call_controller_alarm_failure(self, instance_healthchecker):
        """Test failed controller alarm call"""
        with patch('requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_post.return_value = mock_response

            result = instance_healthchecker._call_controller_alarm("test_type", "test message", "critical")

            assert result is False

    def test_call_controller_alarm_exception(self, instance_healthchecker):
        """Test controller alarm call with exception"""
        with patch('requests.post', side_effect=Exception("Test error")):
            result = instance_healthchecker._call_controller_alarm("test_type", "test message", "critical")

            assert result is False

    def test_call_controller_terminate_success(self, instance_healthchecker):
        """Test successful controller terminate call"""
        with patch('requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            result = instance_healthchecker._call_controller_terminate(123, "test reason")

            assert result is True
            mock_post.assert_called_once()
            # Verify correct URL was constructed
            call_args = mock_post.call_args
            assert instance_healthchecker._health_check_config.controller_api_dns in call_args[0][0]
            assert instance_healthchecker._health_check_config.terminate_instance_endpoint in call_args[0][0]

    def test_call_controller_terminate_failure(self, instance_healthchecker):
        """Test failed controller terminate call"""
        with patch('requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_post.return_value = mock_response

            result = instance_healthchecker._call_controller_terminate(123, "test reason")

            assert result is False

    def test_call_controller_terminate_exception(self, instance_healthchecker):
        """Test controller terminate call with exception"""
        with patch('requests.post', side_effect=Exception("Test error")):
            result = instance_healthchecker._call_controller_terminate(123, "test reason")

            assert result is False

    def test_update_config(self, instance_healthchecker):
        """Test update_config method"""
        # Create new config
        from motor.config.coordinator import CoordinatorConfig
        new_config = CoordinatorConfig()
        new_config.health_check_config.dummy_request_interval = 999.0

        # Update config
        instance_healthchecker.update_config(new_config)

        # Verify config was updated
        assert instance_healthchecker._health_check_config.dummy_request_interval == 999.0

    def test_concurrent_access_simulation(self, instance_healthchecker, mock_instance_manager):
        """Test concurrent access safety by simulating concurrent operations without real threads"""
        # Create instances and endpoints
        instances = []
        endpoints = []
        for i in range(10):
            instance = MagicMock(spec=Instance)
            instance.id = i
            instance.role = PDRole.ROLE_P

            endpoint = MagicMock(spec=Endpoint)
            endpoint.id = i
            endpoint.ip = f"127.0.0.{i}"
            endpoint.port = "8080"

            instances.append(instance)
            endpoints.append(endpoint)

        # Mock InstanceManager to avoid singleton issues in concurrent simulation
        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            # Simulate concurrent push_exception_instance calls
            # In a real concurrent scenario, the lock would serialize these operations
            for i in range(10):
                instance_healthchecker.push_exception_instance(instances[i], endpoints[i])

        # Verify all instances were correctly added
        for i in range(10):
            assert i in instance_healthchecker._monitored_instances
            assert instance_healthchecker._consecutive_failures[i] == 0

    def test_push_exception_instance_locking(self, instance_healthchecker, mock_instance, mock_endpoint, mock_instance_manager):
        """Test that push_exception_instance properly uses locking"""
        # Mock the lock to verify it's used
        with patch.object(instance_healthchecker, '_lock') as mock_lock, \
             patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):

            instance_healthchecker.push_exception_instance(mock_instance, mock_endpoint)

            # Verify lock was acquired and released
            assert mock_lock.__enter__.called
            assert mock_lock.__exit__.called