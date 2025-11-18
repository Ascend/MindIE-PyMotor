import pytest
import time
import threading
from unittest.mock import Mock, patch
from motor.common.resources.instance import Instance, PDRole
from motor.common.resources.endpoint import Endpoint
from motor.coordinator.core.instance_manager import InstanceManager, UpdateInstanceMode
from motor.coordinator.core.instance_healthchecker import InstanceHealthChecker
from motor.common.utils.dummy_request import DummyRequestUtil


class TestInstanceHealthChecker:
    """InstanceHealthChecker unit test class"""

    def setup_method(self):
        """Reset singleton before each test"""
        # Completely reset the singleton instance
        if hasattr(InstanceHealthChecker, "_instance"):
            delattr(InstanceHealthChecker, "_instance")
        if hasattr(InstanceHealthChecker, "_initialized"):
            delattr(InstanceHealthChecker, "_initialized")
        
        # Also reset InstanceManager singleton if needed
        if hasattr(InstanceManager, "_instance"):
            delattr(InstanceManager, "_instance")

    @pytest.fixture
    def mock_instance(self):
        """Create mock Instance"""
        instance = Mock(spec=Instance)
        instance.id = 1
        instance.role = PDRole.ROLE_P
        instance.job_name = "test_job"
        instance.endpoints = {}
        instance.gathered_workload = Mock()
        return instance

    @pytest.fixture
    def mock_endpoint(self):
        """Create mock Endpoint"""
        endpoint = Mock(spec=Endpoint)
        endpoint.id = 1
        endpoint.ip = "127.0.0.1"
        endpoint.port = "8080"
        endpoint.device_infos = []
        endpoint.workload = Mock()
        return endpoint

    @pytest.fixture
    def mock_instance_manager(self):
        """Create mock InstanceManager"""
        manager = Mock(spec=InstanceManager)
        manager.update_instance_state = Mock()
        manager.delete_unavailable_instance = Mock()
        manager.is_available = Mock(return_value=True)
        return manager

    @pytest.fixture
    def mock_config(self):
        """Create mock config"""
        config = Mock()
        config.max_consecutive_failures = 2
        config.dummy_request_interval = 0.1
        config.dummy_request_timeout = 5
        config.controller_api_dns =  "mindie-ms-controller-service.mindie.svc.cluster.local"
        config.controller_api_port = 57675
        config.alarm_endpoint = "/v1/alarm/coordinator"
        config.alarm_timeout = 5.0
        config.terminate_instance_endpoint = "/controller/terminate_instance"
        config.thread_join_timeout = 5.0
        config.error_retry_interval = 1.0
        return config

    @pytest.fixture
    def mock_dummy_request_util(self):
        """Create mock DummyRequestUtil"""
        util = Mock(spec=DummyRequestUtil)
        util.send_dummy_request = Mock(return_value=True)
        util.close = Mock()
        return util

    @pytest.fixture
    def health_checker(self, mock_instance_manager, mock_config, mock_dummy_request_util):
        """Create health checker instance with fresh state"""
        with patch('motor.coordinator.core.instance_healthchecker.CoordinatorConfig') as mock_config_class, \
             patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager), \
             patch('motor.coordinator.core.instance_healthchecker.DummyRequestUtil', return_value=mock_dummy_request_util), \
             patch('threading.Thread') as mock_thread_class, \
             patch('threading.Event') as mock_event_class:
            
            mock_config_instance = Mock()
            mock_config_instance.health_check_config = mock_config
            mock_config_class.return_value = mock_config_instance
            
            # Mock threading.Event
            mock_shutdown_event = Mock()
            mock_shutdown_event.is_set.return_value = False
            mock_shutdown_event.wait.return_value = None
            mock_event_class.return_value = mock_shutdown_event
            
            # Mock threading.Thread
            mock_thread = Mock()
            mock_thread_class.return_value = mock_thread
            
            # Create instance directly, bypassing singleton complexity
            checker = InstanceHealthChecker.__new__(InstanceHealthChecker)
            
            # Manually initialize the instance with required attributes
            checker.config = mock_config
            checker._monitored_instances = {}
            checker._consecutive_failures = {}
            checker._shutdown_event = mock_shutdown_event
            checker._monitoring_thread = mock_thread
            checker._lock = threading.RLock()
            checker._dummy_request_util = mock_dummy_request_util
            
            # Mark as initialized
            checker._initialized = True
            
            return checker

    def test_init_starts_monitoring_thread(self, mock_instance_manager, mock_config, mock_dummy_request_util):
        """Test that monitoring thread starts automatically in init"""
        with patch('motor.coordinator.core.instance_healthchecker.CoordinatorConfig') as mock_config_class, \
             patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager), \
             patch('motor.coordinator.core.instance_healthchecker.DummyRequestUtil', return_value=mock_dummy_request_util), \
             patch('threading.Thread') as mock_thread_class, \
             patch('threading.Event') as mock_event_class:
            
            mock_config_instance = Mock()
            mock_config_instance.health_check_config = mock_config
            mock_config_class.return_value = mock_config_instance
            
            # Mock threading.Event
            mock_shutdown_event = Mock()
            mock_shutdown_event.is_set.return_value = False
            mock_event_class.return_value = mock_shutdown_event
            
            # Mock threading.Thread
            mock_thread = Mock()
            mock_thread_class.return_value = mock_thread
            
            # Create instance
            checker = InstanceHealthChecker()
            
            # Verify thread was created with correct parameters
            mock_thread_class.assert_called_once_with(
                target=checker._monitoring_loop,
                name="InstanceHealthChecker",
                daemon=True
            )
            
            # Verify thread was started
            mock_thread.start.assert_called_once()
            
            # Verify DummyRequestUtil was initialized
            mock_dummy_request_util.close.assert_not_called()

    def test_stop_functionality(self, health_checker, mock_dummy_request_util):
        """Test stop functionality"""
        # Create a real mock for shutdown event that tracks set() calls
        mock_shutdown_event = Mock()
        mock_shutdown_event.is_set.return_value = False
        health_checker._shutdown_event = mock_shutdown_event
        
        # Mock the monitoring thread
        mock_thread = Mock()
        mock_thread.is_alive.return_value = True
        health_checker._monitoring_thread = mock_thread
        
        # Test stop
        health_checker.stop()
        
        # Verify shutdown event was set
        mock_shutdown_event.set.assert_called_once()
        
        # Verify thread join was called
        mock_thread.join.assert_called_once_with(timeout=health_checker.config.thread_join_timeout)
        
        # Verify DummyRequestUtil was closed
        mock_dummy_request_util.close.assert_called_once()

    def test_push_exception_instance(self, health_checker, mock_instance, mock_endpoint, mock_instance_manager):
        """Test receiving abnormal instance"""
        # Push abnormal instance
        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            health_checker.push_exception_instance(mock_instance, mock_endpoint)

        # Verify instance was added to monitoring list
        assert mock_instance.id in health_checker._monitored_instances
        monitoring_info = health_checker._monitored_instances[mock_instance.id]
        assert monitoring_info["instance"] == mock_instance
        assert monitoring_info["endpoint"] == mock_endpoint
        assert health_checker._consecutive_failures[mock_instance.id] == 0

        # Verify instanceManager was called to isolate instance
        mock_instance_manager.update_instance_state.assert_called_once_with(
            mock_instance.id, UpdateInstanceMode.UNAVAILABLE
        )

    def test_push_exception_instance_duplicate(self, health_checker, mock_instance, mock_endpoint, mock_instance_manager):
        """Test pushing duplicate instance"""
        # Push first instance
        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            health_checker.push_exception_instance(mock_instance, mock_endpoint)

        # Reset mock call count
        mock_instance_manager.update_instance_state.reset_mock()

        # Push same instance again
        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            health_checker.push_exception_instance(mock_instance, mock_endpoint)

        # Should not call instanceManager again for the same instance
        mock_instance_manager.update_instance_state.assert_not_called()

    def test_push_exception_instance_manager_error(self, health_checker, mock_instance, mock_endpoint, mock_instance_manager):
        """Test error handling when instance manager fails"""
        # Mock instanceManager throwing exception
        mock_instance_manager.update_instance_state.side_effect = Exception("Test error")

        # Should not raise exception, but log error
        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            health_checker.push_exception_instance(mock_instance, mock_endpoint)

        # Instance should still be added to monitoring list despite manager error
        assert mock_instance.id in health_checker._monitored_instances

    def test_check_state_alarm_available(self, health_checker, mock_instance_manager):
        """Test availability check (with available instances)"""
        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            result = health_checker.check_state_alarm()

        assert result is True
        mock_instance_manager.is_available.assert_called_once()

    def test_check_state_alarm_unavailable(self, health_checker, mock_instance_manager):
        """Test availability check (no available instances)"""
        mock_instance_manager.is_available.return_value = False

        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager), \
             patch.object(health_checker, '_call_controller_alarm') as mock_alarm:
            
            result = health_checker.check_state_alarm()

        assert result is False
        mock_instance_manager.is_available.assert_called_once()
        
        # Verify controller alarm was called with correct parameters
        mock_alarm.assert_called_once_with(
            alarm_type="no_available_instances",
            message="No available P and D instances found",
            severity="critical"
        )

    def test_check_state_alarm_exception(self, health_checker, mock_instance_manager):
        """Test availability check when exception occurs"""
        mock_instance_manager.is_available.side_effect = Exception("Test error")

        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            result = health_checker.check_state_alarm()

        assert result is False

    def test_monitoring_loop_normal_operation(self, health_checker, mock_dummy_request_util):
        """Test monitoring loop normal operation"""
        mock_shutdown_event = Mock()
        # First two calls return False, third returns True to exit loop
        mock_shutdown_event.is_set.side_effect = [False, False, True]
        mock_shutdown_event.wait.return_value = None
        health_checker._shutdown_event = mock_shutdown_event
        
        # Mock the check method
        with patch.object(health_checker, '_check_monitored_instances') as mock_check:
            health_checker._monitoring_loop()
            
            # Should call check twice before shutdown
            assert mock_check.call_count == 2
            # Should wait for the configured interval
            mock_shutdown_event.wait.assert_called_with(health_checker.config.dummy_request_interval)

    def test_monitoring_loop_with_exception(self, health_checker):
        """Test monitoring loop exception handling"""
        mock_shutdown_event = Mock()
        # First call returns False, second returns True to exit loop
        mock_shutdown_event.is_set.side_effect = [False, True]
        mock_shutdown_event.wait.return_value = None
        health_checker._shutdown_event = mock_shutdown_event
        
        # Mock the check method to raise exception
        with patch.object(health_checker, '_check_monitored_instances', side_effect=Exception("Test error")):
            # Should not raise exception
            health_checker._monitoring_loop()
            
            # Should wait for error retry interval after exception
            mock_shutdown_event.wait.assert_called_with(health_checker.config.error_retry_interval)

    def test_check_monitored_instances_empty(self, health_checker):
        """Test checking empty monitored instances"""
        health_checker._monitored_instances = {}
        
        # Mock the single instance check
        with patch.object(health_checker, '_check_single_instance') as mock_single_check:
            health_checker._check_monitored_instances()
            
            # Should not call single check for empty list
            mock_single_check.assert_not_called()

    def test_check_monitored_instances_with_instances(self, health_checker, mock_instance, mock_endpoint, mock_instance_manager):
        """Test checking multiple monitored instances"""
        # Add multiple instances
        instances = []
        for i in range(3):
            instance = Mock(spec=Instance)
            instance.id = i
            instance.role = PDRole.ROLE_P
            
            endpoint = Mock(spec=Endpoint)
            endpoint.id = i
            
            health_checker._monitored_instances[i] = {
                "instance": instance,
                "endpoint": endpoint,
                "start_time": time.time(),
                "last_check_time": time.time()
            }
            instances.append((instance, endpoint))

        # Mock shutdown event to not trigger
        health_checker._shutdown_event.is_set.return_value = False
        
        # Mock the single instance check
        with patch.object(health_checker, '_check_single_instance') as mock_single_check:
            health_checker._check_monitored_instances()
            
            # Should call single check for each instance
            assert mock_single_check.call_count == 3
            for i in range(3):
                mock_single_check.assert_any_call(i)

    def test_check_single_instance_success(self, health_checker, mock_instance, mock_endpoint, mock_instance_manager, mock_dummy_request_util):
        """Test successful instance check leading to recovery"""
        # Add monitored instance
        instance_id = 1
        health_checker._monitored_instances[instance_id] = {
            "instance": mock_instance,
            "endpoint": mock_endpoint,
            "start_time": time.time(),
            "last_check_time": time.time()
        }
        health_checker._consecutive_failures[instance_id] = 1
        
        # Mock successful dummy request
        mock_dummy_request_util.send_dummy_request.return_value = True
        
        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            health_checker._check_single_instance(instance_id)
        
        # Verify instance was recovered
        mock_instance_manager.update_instance_state.assert_called_once_with(
            instance_id, UpdateInstanceMode.AVAILABLE
        )
        
        # Verify instance was removed from monitoring
        assert instance_id not in health_checker._monitored_instances
        assert instance_id not in health_checker._consecutive_failures

    def test_check_single_instance_failure_below_threshold(self, health_checker, mock_instance, mock_endpoint, mock_dummy_request_util):
        """Test failed instance check below threshold"""
        # Add monitored instance
        instance_id = 1
        health_checker._monitored_instances[instance_id] = {
            "instance": mock_instance,
            "endpoint": mock_endpoint,
            "start_time": time.time(),
            "last_check_time": time.time()
        }
        initial_failures = 1
        health_checker._consecutive_failures[instance_id] = initial_failures
        
        # Mock failed dummy request
        mock_dummy_request_util.send_dummy_request.return_value = False
        
        health_checker._check_single_instance(instance_id)
        
        # Verify failure count was incremented
        assert health_checker._consecutive_failures[instance_id] == initial_failures + 1
        # Verify instance is still monitored
        assert instance_id in health_checker._monitored_instances

    def test_check_single_instance_removed_during_check(self, health_checker, mock_instance, mock_endpoint, mock_dummy_request_util):
        """Test checking instance that gets removed during the check process"""
        # Add monitored instance
        instance_id = 1
        health_checker._monitored_instances[instance_id] = {
            "instance": mock_instance,
            "endpoint": mock_endpoint,
            "start_time": time.time(),
            "last_check_time": time.time()
        }
        
        # Mock the scenario where instance is removed after getting endpoint but before sending request
        def mock_send_dummy_request(endpoint):
            # Remove instance during the health check
            with health_checker._lock:
                if instance_id in health_checker._monitored_instances:
                    del health_checker._monitored_instances[instance_id]
            return False
        
        mock_dummy_request_util.send_dummy_request.side_effect = mock_send_dummy_request
        
        # This should not raise an exception
        health_checker._check_single_instance(instance_id)
        
        # Instance should be removed
        assert instance_id not in health_checker._monitored_instances

    def test_recover_instance(self, health_checker, mock_instance_manager):
        """Test instance recovery"""
        instance_id = 1
        health_checker._monitored_instances[instance_id] = {"instance": Mock(), "endpoint": Mock()}
        health_checker._consecutive_failures[instance_id] = 2
        
        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            health_checker._recover_instance(instance_id)
        
        # Verify instance was recovered
        mock_instance_manager.update_instance_state.assert_called_once_with(
            instance_id, UpdateInstanceMode.AVAILABLE
        )
        
        # Verify instance was removed from monitoring
        assert instance_id not in health_checker._monitored_instances
        assert instance_id not in health_checker._consecutive_failures

    def test_recover_instance_error(self, health_checker, mock_instance_manager):
        """Test instance recovery with error"""
        instance_id = 1
        health_checker._monitored_instances[instance_id] = {"instance": Mock(), "endpoint": Mock()}
        
        # Mock instanceManager throwing exception
        mock_instance_manager.update_instance_state.side_effect = Exception("Test error")
        
        with patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            health_checker._recover_instance(instance_id)
        
        # Instance should still be in monitoring due to error
        assert instance_id in health_checker._monitored_instances

    def test_terminate_instance_success(self, health_checker, mock_instance_manager):
        """Test successful instance termination"""
        instance_id = 1
        health_checker._monitored_instances[instance_id] = {"instance": Mock(), "endpoint": Mock()}
        health_checker._consecutive_failures[instance_id] = 2
        
        with patch.object(health_checker, '_call_controller_terminate', return_value=True), \
             patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            
            health_checker._terminate_instance(instance_id)
            
            # Verify controller was called
            health_checker._call_controller_terminate.assert_called_once()
            # Verify instanceManager was called to delete instance
            mock_instance_manager.delete_unavailable_instance.assert_called_once_with(instance_id)
        
        # Verify instance was removed from monitoring
        assert instance_id not in health_checker._monitored_instances
        assert instance_id not in health_checker._consecutive_failures

    def test_terminate_instance_controller_failure(self, health_checker, mock_instance_manager):
        """Test instance termination when controller fails"""
        instance_id = 1
        health_checker._monitored_instances[instance_id] = {"instance": Mock(), "endpoint": Mock()}
        health_checker._consecutive_failures[instance_id] = 2
        
        with patch.object(health_checker, '_call_controller_terminate', return_value=False), \
             patch('motor.coordinator.core.instance_healthchecker.InstanceManager', return_value=mock_instance_manager):
            
            health_checker._terminate_instance(instance_id)
            
            # Verify controller was called but failed
            health_checker._call_controller_terminate.assert_called_once()
            # InstanceManager should not be called to delete instance
            mock_instance_manager.delete_unavailable_instance.assert_not_called()
        
        # Instance should still be in monitoring due to controller failure
        assert instance_id in health_checker._monitored_instances
        assert instance_id in health_checker._consecutive_failures

    def test_call_controller_alarm_success(self, health_checker):
        """Test successful controller alarm call"""
        with patch('requests.post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response
            
            result = health_checker._call_controller_alarm("test_type", "test message", "critical")
            
            assert result is True
            mock_post.assert_called_once()
            # Verify correct URL was constructed
            call_args = mock_post.call_args
            assert health_checker.config.controller_api_dns in call_args[0][0]
            assert health_checker.config.alarm_endpoint in call_args[0][0]

    def test_call_controller_alarm_failure(self, health_checker):
        """Test failed controller alarm call"""
        with patch('requests.post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 500
            mock_post.return_value = mock_response
            
            result = health_checker._call_controller_alarm("test_type", "test message", "critical")
            
            assert result is False

    def test_call_controller_alarm_exception(self, health_checker):
        """Test controller alarm call with exception"""
        with patch('requests.post', side_effect=Exception("Test error")):
            result = health_checker._call_controller_alarm("test_type", "test message", "critical")
            
            assert result is False

    def test_call_controller_terminate_success(self, health_checker):
        """Test successful controller terminate call"""
        with patch('requests.post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response
            
            result = health_checker._call_controller_terminate(123, "test reason")
            
            assert result is True
            mock_post.assert_called_once()
            # Verify correct URL was constructed
            call_args = mock_post.call_args
            assert health_checker.config.controller_api_dns in call_args[0][0]
            assert health_checker.config.terminate_instance_endpoint in call_args[0][0]

    def test_call_controller_terminate_failure(self, health_checker):
        """Test failed controller terminate call"""
        with patch('requests.post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 500
            mock_post.return_value = mock_response
            
            result = health_checker._call_controller_terminate(123, "test reason")
            
            assert result is False

    def test_call_controller_terminate_exception(self, health_checker):
        """Test controller terminate call with exception"""
        with patch('requests.post', side_effect=Exception("Test error")):
            result = health_checker._call_controller_terminate(123, "test reason")
            
            assert result is False

    def test_concurrent_access(self, health_checker, mock_instance_manager):
        """Test concurrent access safety"""
        # Create instances and endpoints
        instances = []
        endpoints = []
        for i in range(10):
            instance = Mock(spec=Instance)
            instance.id = i
            instance.role = PDRole.ROLE_P
            
            endpoint = Mock(spec=Endpoint)
            endpoint.id = i
            endpoint.ip = f"127.0.0.{i}"
            endpoint.port = "8080"
            
            instances.append(instance)
            endpoints.append(endpoint)

        # Simulate multiple concurrent operations
        threads = []
        for i in range(10):
            thread = threading.Thread(
                target=lambda idx=i: health_checker.push_exception_instance(instances[idx], endpoints[idx])
            )
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify all instances were correctly added
        for i in range(10):
            assert i in health_checker._monitored_instances
            assert health_checker._consecutive_failures[i] == 0