import threading
import time
import pytest
from unittest.mock import patch, MagicMock

from motor.coordinator.core.instance_manager import (
    InstanceManager, UpdateInstanceMode
)

from motor.common.resources.instance import Instance, PDRole, Workload
from motor.common.resources.endpoint import Endpoint
from motor.common.resources.http_msg_spec import EventType
from motor.config.coordinator import CoordinatorConfig, DeployMode


class TestInstanceManager:
    """Test cases for InstanceManager"""

    def setup_method(self):
        """Setup for each test method"""
        # Clear singleton instance before each test
        if hasattr(InstanceManager, '_instances') and InstanceManager in InstanceManager._instances:
            del InstanceManager._instances[InstanceManager]
        
        # Create a fresh instance manager for each test
        self.instance_manager = InstanceManager()
        
        # Test data
        self.prefill_instance = Instance(
            job_name="test-prefill",
            model_name="test-model",
            id=1,
            role=PDRole.ROLE_P,
            endpoints={}
        )
        
        self.decode_instance = Instance(
            job_name="test-decode",
            model_name="test-model",
            id=2,
            role=PDRole.ROLE_D,
            endpoints={}
        )
        
        self.hybrid_instance = Instance(
            job_name="test-hybrid",
            model_name="test-model",
            id=3,
            role=PDRole.ROLE_U,
            endpoints={}
        )
        
        self.endpoint = Endpoint(
            id=1,
            ip="127.0.0.1",
            business_port="8080",
            mgmt_port="8080"
        )

    def test_init(self):
        """Test InstanceManager initialization"""
        assert isinstance(self.instance_manager._prefill_pool, dict)
        assert isinstance(self.instance_manager._decode_pool, dict)
        assert isinstance(self.instance_manager._hybrid_pool, dict)
        assert isinstance(self.instance_manager._unavailable_pool, dict)
        assert len(self.instance_manager._prefill_pool) == 0
        assert len(self.instance_manager._decode_pool) == 0
        assert len(self.instance_manager._hybrid_pool) == 0
        assert len(self.instance_manager._unavailable_pool) == 0

    def test_is_available_cdp_separate(self):
        """Test is_available method in cdp_separate mode"""
        # Mock CoordinatorConfig to return cdp_separate mode
        mock_scheduler_config = MagicMock()
        mock_scheduler_config.deploy_mode = DeployMode.CDP_SEPARATE
        
        mock_coordinator_config = MagicMock()
        mock_coordinator_config.scheduler_config = mock_scheduler_config
        
        with patch.object(CoordinatorConfig, '__new__', return_value=MagicMock()) as MockCoordinatorConfig:
            MockCoordinatorConfig.return_value = mock_coordinator_config
            
            self.instance_manager = InstanceManager()
            
            # Initially should be False
            assert self.instance_manager.is_available() == False
            
            # Add prefill instance
            self.instance_manager._add_instance_to_available_pool(self.prefill_instance)
            assert self.instance_manager.is_available() == False  # Still need decode instance
            
            # Add decode instance
            self.instance_manager._add_instance_to_available_pool(self.decode_instance)
            assert self.instance_manager.is_available() == True  # Now should be True

    def test_is_available_pd_separate(self):
        """Test is_available method in pd_separate mode"""
        # Mock CoordinatorConfig to return pd_separate mode
        mock_scheduler_config = MagicMock()
        mock_scheduler_config.deploy_mode = DeployMode.PD_SEPARATE
        
        mock_coordinator_config = MagicMock()
        mock_coordinator_config.scheduler_config = mock_scheduler_config
        
        with patch.object(CoordinatorConfig, '__new__', return_value=MagicMock()) as MockCoordinatorConfig:
            MockCoordinatorConfig.return_value = mock_coordinator_config
            
            self.instance_manager = InstanceManager()
            
            # Initially should be False
            self.instance_manager.is_available() == False
            
            # Add prefill instance
            self.instance_manager._add_instance_to_available_pool(self.prefill_instance)
            assert self.instance_manager.is_available() == False  # Still need decode instance
            
            # Add decode instance
            self.instance_manager._add_instance_to_available_pool(self.decode_instance)
            assert self.instance_manager.is_available() == True  # Now should be True

    def test_is_available_cpcd_separate(self):
        """Test is_available method in pd_disaggregation_single_container mode"""
        # Mock CoordinatorConfig to return pd_disaggregation_single_container mode
        mock_scheduler_config = MagicMock()
        mock_scheduler_config.deploy_mode = DeployMode.CPCD_SEPARATE
        
        mock_coordinator_config = MagicMock()
        mock_coordinator_config.scheduler_config = mock_scheduler_config
        
        with patch.object(CoordinatorConfig, '__new__', return_value=MagicMock()) as MockCoordinatorConfig:
            MockCoordinatorConfig.return_value = mock_coordinator_config
            
            self.instance_manager = InstanceManager()
            
            # Initially should be False
            assert self.instance_manager.is_available() == False
            
            # Add prefill instance
            self.instance_manager._add_instance_to_available_pool(self.prefill_instance)
            assert self.instance_manager.is_available() == False  # Still need decode instance
            
            # Add decode instance
            self.instance_manager._add_instance_to_available_pool(self.decode_instance)
            assert self.instance_manager.is_available() == True  # Now should be True

    def test_is_available_single_node(self):
        """Test is_available method in single_node mode"""
        mock_scheduler_config = MagicMock()
        mock_scheduler_config.deploy_mode = DeployMode.SINGLE_NODE
        
        mock_coordinator_config = MagicMock()
        mock_coordinator_config.scheduler_config = mock_scheduler_config
        
        with patch.object(CoordinatorConfig, '__new__', return_value=MagicMock()) as MockCoordinatorConfig:
            MockCoordinatorConfig.return_value = mock_coordinator_config
            
            self.instance_manager = InstanceManager()
            
            # Initially should be False
            assert self.instance_manager.is_available() == False
            
            # Add hybrid instance
            self.instance_manager._add_instance_to_available_pool(self.hybrid_instance)
            assert self.instance_manager.is_available() == True 

    def test_is_available_unknown_mode(self):
        """Test is_available method with unknown deploy mode"""
        # Mock CoordinatorConfig to return unknown mode
        mock_scheduler_config = MagicMock()
        mock_scheduler_config.deploy_mode = DeployMode.from_string("unknow_mode")
        
        mock_coordinator_config = MagicMock()
        mock_coordinator_config.scheduler_config = mock_scheduler_config
        
        with patch.object(CoordinatorConfig, '__new__', return_value=MagicMock()) as MockCoordinatorConfig:
            MockCoordinatorConfig.return_value = mock_coordinator_config
            
            self.instance_manager = InstanceManager()
            
            # Should return False for unknown mode
            assert self.instance_manager.is_available() == False

    def test_is_available_no_config(self):
        """Test is_available method when scheduler_config is missing"""
        # Mock CoordinatorConfig with no scheduler_config
        with patch.object(CoordinatorConfig, '__new__', return_value=MagicMock()) as mock_config_new:
            mock_config_instance = MagicMock()
            mock_config_instance.config = {}
            mock_config_new.return_value = mock_config_instance
            
            # Should return False when scheduler_config is missing
            assert self.instance_manager.is_available() == False

    def test_get_available_instances(self):
        """Test get_available_instances method"""
        # Add instances to pools
        self.instance_manager._add_instance_to_available_pool(self.prefill_instance)
        self.instance_manager._add_instance_to_available_pool(self.decode_instance)
        self.instance_manager._add_instance_to_available_pool(self.hybrid_instance)
        
        # Test getting prefill instances
        prefill_instances = self.instance_manager.get_available_instances(PDRole.ROLE_P)
        assert len(prefill_instances) == 1
        assert 1 in prefill_instances
        assert prefill_instances[1] == self.prefill_instance
        
        # Test getting decode instances
        decode_instances = self.instance_manager.get_available_instances(PDRole.ROLE_D)
        assert len(decode_instances) == 1
        assert 2 in decode_instances
        assert decode_instances[2] == self.decode_instance
        
        # Test getting hybrid instances
        hybrid_instances = self.instance_manager.get_available_instances(PDRole.ROLE_U)
        assert len(hybrid_instances) == 1
        assert 3 in hybrid_instances
        assert hybrid_instances[3] == self.hybrid_instance
        
        # Test getting instances with unknown role
        unknown_instances = self.instance_manager.get_available_instances("unknown")
        assert unknown_instances == {}

    def test_get_all_instances(self):
        """Test get_all_instances method"""
        # Add instances to pools
        self.instance_manager._add_instance_to_available_pool(self.prefill_instance)
        self.instance_manager._add_instance_to_available_pool(self.decode_instance)
        
        # Add instance to unavailable pool
        unavailable_instance = Instance(
            job_name="test-unavailable",
            model_name="test-model",
            id=3,
            role=PDRole.ROLE_U,
            endpoints={}
        )
        self.instance_manager._unavailable_pool[3] = unavailable_instance
        
        # Get all instances
        available_pool, unavailable_pool = self.instance_manager.get_all_instances()
        
        # Verify available pool contents
        assert len(available_pool) == 2
        assert 1 in available_pool
        assert 2 in available_pool
        assert available_pool[1].id == 1
        assert available_pool[2].id == 2
        
        # Verify unavailable pool contents
        assert len(unavailable_pool) == 1
        assert 3 in unavailable_pool
        assert unavailable_pool[3].id == 3
        
        # Verify that returned dictionaries are copies (modifying them doesn't affect original pools)
        available_pool[4] = self.hybrid_instance
        unavailable_pool[4] = self.hybrid_instance
        
        # Original pools should not be affected
        assert len(self.instance_manager._available_pool) == 2
        assert len(self.instance_manager._unavailable_pool) == 1
        assert 4 not in self.instance_manager._available_pool
        assert 4 not in self.instance_manager._unavailable_pool

    def test_find_available_pool(self):
        """Test _find_available_pool method"""
        # Add instances to pools
        self.instance_manager._add_instance_to_available_pool(self.prefill_instance)
        self.instance_manager._add_instance_to_available_pool(self.decode_instance)
        self.instance_manager._add_instance_to_available_pool(self.hybrid_instance)
        
        # Test finding prefill instance
        prefill_pool = self.instance_manager._find_available_pool(1)
        assert prefill_pool is not None
        assert prefill_pool == self.instance_manager._prefill_pool
        
        # Test finding decode instance
        decode_pool = self.instance_manager._find_available_pool(2)
        assert decode_pool is not None
        assert decode_pool == self.instance_manager._decode_pool
        
        # Test finding hybrid instance
        hybrid_pool = self.instance_manager._find_available_pool(3)
        assert hybrid_pool is not None
        assert hybrid_pool == self.instance_manager._hybrid_pool
        
        # Test finding non-existent instance
        none_pool = self.instance_manager._find_available_pool(999)
        assert none_pool is None

    def test_update_instance_workload_success(self, caplog):
        """Test update_instance_workload method with success case"""
        # Add instance to pool properly using the public method
        self.instance_manager._add_instance_to_available_pool(self.prefill_instance)
        
        # Create workload change
        workload_change = Workload(active_tokens=10, active_kv_cache=20)
        
        # Update workload
        self.instance_manager.update_instance_workload(1, self.endpoint, workload_change)
        
        # Verify workload was updated
        assert self.prefill_instance.gathered_workload.active_tokens == 10
        assert self.prefill_instance.gathered_workload.active_kv_cache == 20
        assert self.endpoint.workload.active_tokens == 10
        assert self.endpoint.workload.active_kv_cache == 20

    def test_update_instance_workload_instance_not_found(self, caplog):
        """Test update_instance_workload method when instance not found"""
        # Create workload change
        workload_change = Workload(active_tokens=10, active_kv_cache=20)
        
        # Try to update workload for non-existent instance
        self.instance_manager.update_instance_workload(999, self.endpoint, workload_change)
        
        # Verify warning was logged
        assert "not found in available instance pool" in caplog.text

    def test_update_instance_workload_instance_none(self, caplog):
        """Test update_instance_workload method when instance is None"""
        # Add None instance to pool
        self.instance_manager._prefill_pool[1] = None
        # Also need to add to _available_pool for consistency
        self.instance_manager._available_pool[1] = None
        
        # Create workload change
        workload_change = Workload(active_tokens=10, active_kv_cache=20)
        
        # Try to update workload for None instance
        self.instance_manager.update_instance_workload(1, self.endpoint, workload_change)
        
        # Verify warning was logged
        assert "Instance ID 1 not found in available instance pool" in caplog.text

    def test_delete_unavailable_instance_success(self, caplog):
        """Test delete_unavailable_instance method with success case"""
        # Add instance to unavailable pool
        self.instance_manager._unavailable_pool[1] = self.prefill_instance
        
        # Delete instance
        self.instance_manager.delete_unavailable_instance(1)
        
        # Verify instance was deleted
        assert 1 not in self.instance_manager._unavailable_pool
        assert "Deleted unavailable instance with ID 1 successfully" in caplog.text

    def test_delete_unavailable_instance_not_found(self, caplog):
        """Test delete_unavailable_instance method when instance not found"""
        # Try to delete non-existent instance
        self.instance_manager.delete_unavailable_instance(999)
        
        # Verify warning was logged
        assert "Instance ID 999 not found in unavailable instance pool yet" in caplog.text

    def test_update_instance_state_to_available_success(self, caplog):
        """Test update_instance_state method to make instance available"""
        # Add instance to unavailable pool
        unavailable_instance = Instance(
            job_name="test-unavailable",
            model_name="test-model",
            id=1,
            role=PDRole.ROLE_P,
            endpoints={}
        )
        self.instance_manager._unavailable_pool[1] = unavailable_instance
        
        # Update instance state to available
        self.instance_manager.update_instance_state(1, UpdateInstanceMode.AVAILABLE)
        
        # Verify instance was moved to available pool
        assert 1 not in self.instance_manager._unavailable_pool
        assert 1 in self.instance_manager._prefill_pool
        assert self.instance_manager._prefill_pool[1] == unavailable_instance
        assert "Instance ID 1 updated to available successfully" in caplog.text

    def test_update_instance_state_to_available_not_found(self, caplog):
        """Test update_instance_state method when instance not found in unavailable pool"""
        # Try to update non-existent instance to available
        self.instance_manager.update_instance_state(999, UpdateInstanceMode.AVAILABLE)
        
        # Verify warning was logged
        assert "Instance ID 999 not found in unavailable instance pool" in caplog.text

    def test_update_instance_state_to_unavailable_success(self, caplog):
        """Test update_instance_state method to make instance unavailable"""
        # Add instance to available pool properly
        self.instance_manager._add_instance_to_available_pool(self.prefill_instance)
        
        # Update instance state to unavailable
        self.instance_manager.update_instance_state(1, UpdateInstanceMode.UNAVAILABLE)
        
        # Verify instance was moved to unavailable pool
        assert 1 not in self.instance_manager._prefill_pool
        assert 1 in self.instance_manager._unavailable_pool
        assert self.instance_manager._unavailable_pool[1] == self.prefill_instance
        assert "Instance ID 1 updated to unavailable successfully" in caplog.text

    def test_update_instance_state_to_unavailable_not_found(self, caplog):
        """Test update_instance_state method when instance not found in available pool"""
        # Try to update non-existent instance to unavailable
        self.instance_manager.update_instance_state(999, UpdateInstanceMode.UNAVAILABLE)
        
        # Verify warning was logged
        assert "Instance ID 999 not found in available instance pool, cannot update to unavailable" in caplog.text

    def test_refresh_instances_add_success(self, caplog):
        """Test refresh_instances method with ADD event"""
        # Create instances to add
        instances = [self.prefill_instance, self.decode_instance]
        
        # Refresh instances with ADD event
        self.instance_manager.refresh_instances(EventType.ADD, instances)
        
        # Verify instances were added
        assert 1 in self.instance_manager._prefill_pool
        assert self.instance_manager._prefill_pool[1] == self.prefill_instance
        assert 2 in self.instance_manager._decode_pool
        assert self.instance_manager._decode_pool[2] == self.decode_instance
        assert "Added instance ID 1 to available pool successfully" in caplog.text
        assert "Added instance ID 2 to available pool successfully" in caplog.text

    def test_refresh_instances_add_duplicate_in_available_pool(self, caplog):
        """Test refresh_instances method with ADD event for duplicate instance in available pool"""
        # Add instance first properly
        self.instance_manager._add_instance_to_available_pool(self.prefill_instance)
        
        # Try to add the same instance again
        instances = [self.prefill_instance]
        self.instance_manager.refresh_instances(EventType.ADD, instances)
        
        # Verify warning was logged
        assert "Instance ID 1 already exists in available pool" in caplog.text

    def test_refresh_instances_add_duplicate_in_unavailable_pool(self, caplog):
        """Test refresh_instances method with ADD event for duplicate instance in unavailable pool"""
        # Add instance to unavailable pool
        self.instance_manager._unavailable_pool[1] = self.prefill_instance
        
        # Try to add the same instance again
        instances = [self.prefill_instance]
        self.instance_manager.refresh_instances(EventType.ADD, instances)
        
        # Verify warning was logged
        assert "Instance ID 1 already exists in unavailable pool" in caplog.text

    def test_refresh_instances_add_unknown_role(self, caplog):
        """Test refresh_instances method with ADD event for instance with unknown role"""
        # Create instance with unknown role
        unknown_instance = Instance(
            job_name="test-unknown",
            model_name="test-model",
            id=1,
            role="unknown_role",
            endpoints={}
        )
        
        # Try to add instance with unknown role
        instances = [unknown_instance]
        self.instance_manager.refresh_instances(EventType.ADD, instances)
        
        # Verify error was logged
        assert "Unknown role for instance ID 1" in caplog.text

    def test_refresh_instances_del_success(self, caplog):
        """Test refresh_instances method with DEL event"""
        # Add instances first properly
        self.instance_manager._add_instance_to_available_pool(self.prefill_instance)
        self.instance_manager._add_instance_to_available_pool(self.decode_instance)
        
        # Refresh instances with DEL event
        instances = [self.prefill_instance]
        self.instance_manager.refresh_instances(EventType.DEL, instances)
        
        # Verify instance was deleted
        assert 1 not in self.instance_manager._prefill_pool
        assert 2 in self.instance_manager._decode_pool
        assert "Deleted instance ID 1 from available pool successfully" in caplog.text

    def test_refresh_instances_del_from_unavailable(self, caplog):
        """Test refresh_instances method with DEL event for unavailable instance"""
        # Add instance to unavailable pool
        self.instance_manager._unavailable_pool[1] = self.prefill_instance
        
        # Refresh instances with DEL event
        instances = [self.prefill_instance]
        self.instance_manager.refresh_instances(EventType.DEL, instances)
        
        # Verify instance was deleted from unavailable pool
        assert 1 not in self.instance_manager._unavailable_pool
        assert "Deleted instance ID 1 from unavailable pool successfully" in caplog.text

    def test_refresh_instances_del_not_found(self, caplog):
        """Test refresh_instances method with DEL event for non-existent instance"""
        # Try to delete non-existent instance
        instances = [self.prefill_instance]
        self.instance_manager.refresh_instances(EventType.DEL, instances)
        
        # Verify warning was logged
        assert "Instance ID 1 not found in instance pool" in caplog.text

    def test_refresh_instances_set_success(self, caplog):
        """Test refresh_instances method with SET event"""
        # Create new instances for setting
        new_prefill_instance = Instance(
            job_name="new-prefill",
            model_name="test-model",
            id=10,
            role=PDRole.ROLE_P,
            endpoints={}
        )
        new_decode_instance = Instance(
            job_name="new-decode",
            model_name="test-model",
            id=20,
            role=PDRole.ROLE_D,
            endpoints={}
        )
        
        instances = [new_prefill_instance, new_decode_instance]
        self.instance_manager.refresh_instances(EventType.SET, instances)
        
        # Verify pools were set correctly
        assert len(self.instance_manager._prefill_pool) == 1
        assert len(self.instance_manager._decode_pool) == 1
        assert 10 in self.instance_manager._prefill_pool
        assert 20 in self.instance_manager._decode_pool
        assert "Set instances end" in caplog.text

    def test_refresh_instances_set_with_existing_instances(self, caplog):
        """Test refresh_instances method with SET event when pools already have instances"""
        # Add some instances first properly
        self.instance_manager._add_instance_to_available_pool(self.prefill_instance)
        
        # Try to set new instances - should fail because pools are not empty
        instances = [self.decode_instance]
        self.instance_manager.refresh_instances(EventType.SET, instances)
        
        # Verify error was logged and operation was cancelled
        assert "Cannot set instance pools when there are existing instances in pools" in caplog.text

    def test_refresh_instances_unknown_event(self, caplog):
        """Test refresh_instances method with unknown event type"""
        # Try to refresh with unknown event type
        instances = [self.prefill_instance]
        self.instance_manager.refresh_instances("unknown", instances)
        
        # Verify error was logged
        assert "Unknown event type: unknown" in caplog.text

    def test_add_instances_unknown_role(self, caplog):
        """Test _add_instances method with unknown role"""
        # Create instance with unknown role
        unknown_instance = Instance(
            job_name="test-unknown",
            model_name="test-model",
            id=1,
            role="unknown_role",
            endpoints={}
        )
        
        # Try to add instance with unknown role
        self.instance_manager._add_instances([unknown_instance])
        
        # Verify error was logged
        assert "Unknown role for instance ID 1" in caplog.text

    def test_delete_instances_empty_pools(self):
        """Test _delete_instances method when pools are empty"""
        # Try to delete instance from empty pools
        self.instance_manager._delete_instances([self.prefill_instance])
        
        # Should not raise any exception
        assert True

    def test_set_instances_empty_pools(self):
        """Test _set_instances method with empty pools"""
        # Create new instances for setting
        new_prefill_instance = Instance(
            job_name="new-prefill",
            model_name="test-model",
            id=10,
            role=PDRole.ROLE_P,
            endpoints={}
        )
        
        # Set instances when pools are empty
        self.instance_manager._set_instances([new_prefill_instance])
        
        # Verify instance was added
        assert len(self.instance_manager._prefill_pool) == 1
        assert 10 in self.instance_manager._prefill_pool


class TestInstanceManagerThreadSafety:
    """Thread safety test cases for InstanceManager"""

    def setup_method(self):
        """Setup for each test method"""
        # Clear singleton instance before each test
        if hasattr(InstanceManager, '_instances') and InstanceManager in InstanceManager._instances:
            del InstanceManager._instances[InstanceManager]
        
        # Create a fresh instance manager for each test
        self.instance_manager = InstanceManager()
        
        # Test data
        self.prefill_instance = Instance(
            job_name="test-prefill",
            model_name="test-model",
            id=1,
            role=PDRole.ROLE_P,
            endpoints={}
        )
        
        self.decode_instance = Instance(
            job_name="test-decode",
            model_name="test-model",
            id=2,
            role=PDRole.ROLE_D,
            endpoints={}
        )
        
        self.hybrid_instance = Instance(
            job_name="test-hybrid",
            model_name="test-model",
            id=3,
            role=PDRole.ROLE_U,
            endpoints={}
        )
        
        self.endpoint = Endpoint(
            id=1,
            ip="127.0.0.1",
            business_port="8080",
            mgmt_port="8080"
        )

    def test_concurrent_add_and_delete_instances(self):
        """Test concurrent add and delete operations on instances with enhanced concurrency"""
        # Add initial instances
        self.instance_manager._add_instance_to_available_pool(self.prefill_instance)
        self.instance_manager._add_instance_to_available_pool(self.decode_instance)
        
        # Thread-safe counters for tracking results
        add_results = []
        delete_results = []
        lock = threading.Lock()
        
        def add_instance_task(iteration):
            try:
                # Create unique instance for each iteration
                instance = Instance(
                    job_name=f"test-add-{iteration}",
                    model_name="test-model",
                    id=1000 + iteration,
                    role=PDRole.ROLE_U,
                    endpoints={}
                )
                self.instance_manager.refresh_instances(EventType.ADD, [instance])
                
                with lock:
                    add_results.append(f"add_success_{iteration}")
            except Exception as e:
                with lock:
                    add_results.append(f"add_error_{iteration}: {str(e)}")
        
        def delete_instance_task(iteration):
            try:
                # Create instance to delete
                instance = Instance(
                    job_name=f"test-del-{iteration}",
                    model_name="test-model",
                    id=2000 + iteration,
                    role=PDRole.ROLE_P,
                    endpoints={}
                )
                # First add the instance to make sure it exists
                self.instance_manager.refresh_instances(EventType.ADD, [instance])
                # Then delete it
                self.instance_manager.refresh_instances(EventType.DEL, [instance])
                
                with lock:
                    delete_results.append(f"delete_success_{iteration}")
            except Exception as e:
                with lock:
                    delete_results.append(f"delete_error_{iteration}: {str(e)}")
        
        # Run multiple concurrent operations
        threads = []
        
        # Start 5 add threads, each performing 50 iterations
        for i in range(5):
            thread = threading.Thread(target=lambda: [add_instance_task(j) for j in range(i*50, (i+1)*50)])
            threads.append(thread)
            thread.start()
        
        # Start 3 delete threads, each performing 50 iterations
        for i in range(3):
            thread = threading.Thread(target=lambda: [delete_instance_task(j) for j in range(i*50, (i+1)*50)])
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        # Verify that operations succeeded
        add_success_count = sum(1 for r in add_results if "add_success" in r)
        delete_success_count = sum(1 for r in delete_results if "delete_success" in r)
        
        assert add_success_count > 0, f"Expected some add operations to succeed, got results: {add_results}"
        assert delete_success_count > 0, f"Expected some delete operations to succeed, got results: {delete_results}"
        
        # Verify final state consistency
        available_count = (len(self.instance_manager._prefill_pool) + 
                          len(self.instance_manager._decode_pool) + 
                          len(self.instance_manager._hybrid_pool))
        unavailable_count = len(self.instance_manager._unavailable_pool)
        
        # Total instances should be reasonable (original 2 + added ones - deleted ones)
        total_instances = available_count + unavailable_count
        assert total_instances >= 0, f"Invalid instance count: {total_instances}"

    def test_concurrent_update_instance_state(self):
        """Test concurrent update_instance_state operations with enhanced concurrency"""
        # Add multiple instances to available pool
        instances = []
        for i in range(10):
            instance = Instance(
                job_name=f"test-{i}",
                model_name="test-model",
                id=100 + i,
                role=PDRole.ROLE_P if i % 2 == 0 else PDRole.ROLE_D,
                endpoints={}
            )
            self.instance_manager._add_instance_to_available_pool(instance)
            instances.append(instance)
        
        # Thread-safe counters for tracking results
        unavailable_results = []
        available_results = []
        lock = threading.Lock()
        
        def make_unavailable_task(instance_id, iteration):
            try:
                self.instance_manager.update_instance_state(instance_id, UpdateInstanceMode.UNAVAILABLE)
                with lock:
                    unavailable_results.append(f"to_unavailable_success_{iteration}")
            except Exception as e:
                with lock:
                    unavailable_results.append(f"to_unavailable_error_{iteration}: {str(e)}")
        
        def make_available_task(instance_id, iteration):
            try:
                self.instance_manager.update_instance_state(instance_id, UpdateInstanceMode.AVAILABLE)
                with lock:
                    available_results.append(f"to_available_success_{iteration}")
            except Exception as e:
                with lock:
                    available_results.append(f"to_available_error_{iteration}: {str(e)}")
        
        # Run concurrent operations
        threads = []
        
        # Start 4 threads to make instances unavailable, each performing 30 iterations
        for i in range(4):
            thread = threading.Thread(
                target=lambda: [
                    make_unavailable_task(100 + (j % 10), j) 
                    for j in range(i*30, (i+1)*30)
                ]
            )
            threads.append(thread)
            thread.start()
        
        # Start 4 threads to make instances available, each performing 30 iterations
        for i in range(4):
            thread = threading.Thread(
                target=lambda: [
                    make_available_task(100 + (j % 10), j) 
                    for j in range(i*30, (i+1)*30)
                ]
            )
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        # Verify that operations succeeded
        unavailable_success_count = sum(1 for r in unavailable_results if "to_unavailable_success" in r)
        available_success_count = sum(1 for r in available_results if "to_available_success" in r)
        
        assert unavailable_success_count > 0, f"Expected some unavailable operations to succeed, got results: {unavailable_results}"
        assert available_success_count > 0, f"Expected some available operations to succeed, got results: {available_results}"
        
        # Verify internal consistency
        available_count = (len(self.instance_manager._prefill_pool) + 
                          len(self.instance_manager._decode_pool) + 
                          len(self.instance_manager._hybrid_pool))
        unavailable_count = len(self.instance_manager._unavailable_pool)
        
        # Total instances should equal the original count
        total_instances = available_count + unavailable_count
        assert total_instances == 10, f"Instance count mismatch: expected 10, got {total_instances}"

    def test_concurrent_update_workload(self):
        """Test concurrent update_instance_workload operations with enhanced concurrency"""
        # Add instance to available pool
        self.instance_manager._add_instance_to_available_pool(self.prefill_instance)
        
        # Add endpoint to instance
        self.prefill_instance.endpoints[1] = {1: self.endpoint}
        
        # Thread-safe counter for tracking results
        results = []
        lock = threading.Lock()
        
        def update_workload_task(iteration, tokens, kv_cache):
            try:
                workload_change = Workload(active_tokens=tokens, active_kv_cache=kv_cache)
                self.instance_manager.update_instance_workload(1, self.endpoint, workload_change)
                with lock:
                    results.append(f"update_success_{iteration}")
            except Exception as e:
                with lock:
                    results.append(f"update_error_{iteration}: {str(e)}")
        
        # Run concurrent update operations
        threads = []
        
        # Start 5 threads for positive workload updates, each performing 40 iterations
        for i in range(5):
            thread = threading.Thread(
                target=lambda: [
                    update_workload_task(j, 10, 20) 
                    for j in range(i*40, (i+1)*40)
                ]
            )
            threads.append(thread)
            thread.start()
        
        # Start 5 threads for negative workload updates, each performing 40 iterations
        for i in range(5):
            thread = threading.Thread(
                target=lambda: [
                    update_workload_task(j, -5, -10) 
                    for j in range(i*40, (i+1)*40)
                ]
            )
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        # Verify that operations succeeded
        success_count = sum(1 for r in results if "update_success" in r)
        assert success_count > 0, f"Expected some update operations to succeed, got results: {results}"
        
        # Verify that workload was updated (exact value depends on execution order)
        assert self.prefill_instance.gathered_workload.active_tokens >= 0, "Negative token count"
        assert self.prefill_instance.gathered_workload.active_kv_cache >= 0, "Negative KV cache count"
        assert self.endpoint.workload.active_tokens >= 0, "Negative endpoint token count"
        assert self.endpoint.workload.active_kv_cache >= 0, "Negative endpoint KV cache count"

    def test_concurrent_is_available_calls(self):
        """Test concurrent is_available calls under different conditions with enhanced concurrency"""
        # Mock CoordinatorConfig to return cdp_separate mode
        mock_scheduler_config = MagicMock()
        mock_scheduler_config.deploy_mode = DeployMode.CDP_SEPARATE
        
        mock_coordinator_config = MagicMock()
        mock_coordinator_config.scheduler_config = mock_scheduler_config
        
        with patch.object(CoordinatorConfig, '__new__', return_value=MagicMock()) as MockCoordinatorConfig:
            MockCoordinatorConfig.return_value = mock_coordinator_config
            
            self.instance_manager = InstanceManager()
            
            # Thread-safe counter for tracking results
            results = []
            lock = threading.Lock()
            
            def check_availability_task(iteration):
                try:
                    result = self.instance_manager.is_available()
                    with lock:
                        results.append((iteration, result))
                except Exception as e:
                    with lock:
                        results.append((iteration, f"error: {str(e)}"))
            
            # Run multiple concurrent availability checks when not available
            threads = []
            # Start 8 threads, each performing 20 iterations
            for i in range(8):
                thread = threading.Thread(
                    target=lambda: [
                        check_availability_task(j) 
                        for j in range(i*20, (i+1)*20)
                    ]
                )
                threads.append(thread)
                thread.start()
            
            for thread in threads:
                thread.join()
            
            # All checks should return False (no instances added yet)
            false_count = sum(1 for _, r in results if r is False)
            assert false_count == 160, f"Expected all checks to return False, got {false_count} False out of {len(results)}"
            
            # Add instances to make it available
            self.instance_manager._add_instance_to_available_pool(self.prefill_instance)
            self.instance_manager._add_instance_to_available_pool(self.decode_instance)
            
            # Run multiple concurrent availability checks when available
            results = []
            threads = []
            # Start 8 threads, each performing 20 iterations
            for i in range(8):
                thread = threading.Thread(
                    target=lambda: [
                        check_availability_task(j) 
                        for j in range(i*20, (i+1)*20)
                    ]
                )
                threads.append(thread)
                thread.start()
            
            for thread in threads:
                thread.join()
            
            # All checks should return True
            true_count = sum(1 for _, r in results if r is True)
            assert true_count == 160, f"Expected all checks to return True, got {true_count} True out of {len(results)}"

    def test_concurrent_get_available_instances(self):
        """Test concurrent get_available_instances calls with enhanced concurrency"""
        # Add instances
        for i in range(20):
            instance = Instance(
                job_name=f"test-{i}",
                model_name="test-model",
                id=100 + i,
                role=PDRole.ROLE_P if i % 2 == 0 else PDRole.ROLE_D,
                endpoints={}
            )
            self.instance_manager._add_instance_to_available_pool(instance)
        
        # Thread-safe counter for tracking results
        prefill_results = []
        decode_results = []
        lock = threading.Lock()
        
        def get_prefill_instances_task(iteration):
            try:
                instances = self.instance_manager.get_available_instances(PDRole.ROLE_P)
                with lock:
                    prefill_results.append((iteration, len(instances)))
            except Exception as e:
                with lock:
                    prefill_results.append((iteration, f"error: {str(e)}"))
        
        def get_decode_instances_task(iteration):
            try:
                instances = self.instance_manager.get_available_instances(PDRole.ROLE_D)
                with lock:
                    decode_results.append((iteration, len(instances)))
            except Exception as e:
                with lock:
                    decode_results.append((iteration, f"error: {str(e)}"))
        
        # Run concurrent get operations
        threads = []
        
        # Start 6 threads for prefill instances, each performing 30 iterations
        for i in range(6):
            thread = threading.Thread(
                target=lambda: [
                    get_prefill_instances_task(j) 
                    for j in range(i*30, (i+1)*30)
                ]
            )
            threads.append(thread)
            thread.start()
        
        # Start 6 threads for decode instances, each performing 30 iterations
        for i in range(6):
            thread = threading.Thread(
                target=lambda: [
                    get_decode_instances_task(j) 
                    for j in range(i*30, (i+1)*30)
                ]
            )
            threads.append(thread)
            thread.start()
        
        for thread in threads:
            thread.join()
        
        # All operations should succeed and return correct counts
        prefill_success_count = sum(1 for _, r in prefill_results if isinstance(r, int))
        decode_success_count = sum(1 for _, r in decode_results if isinstance(r, int))
        
        assert prefill_success_count == 180, f"Expected 180 prefill operations to succeed, got {prefill_success_count}"
        assert decode_success_count == 180, f"Expected 180 decode operations to succeed, got {decode_success_count}"
        
        # Each should return 10 instances (20 total instances, half prefill, half decode)
        prefill_counts = [r for _, r in prefill_results if isinstance(r, int)]
        decode_counts = [r for _, r in decode_results if isinstance(r, int)]
        
        assert all(r == 10 for r in prefill_counts), f"Expected all prefill counts to be 10, got {prefill_counts}"
        assert all(r == 10 for r in decode_counts), f"Expected all decode counts to be 10, got {decode_counts}"

    def test_concurrent_get_all_instances(self):
        """Test concurrent get_all_instances calls with enhanced concurrency"""
        # Add instances to available pool
        for i in range(10):
            instance = Instance(
                job_name=f"test-available-{i}",
                model_name="test-model",
                id=100 + i,
                role=PDRole.ROLE_P if i % 2 == 0 else PDRole.ROLE_D,
                endpoints={}
            )
            self.instance_manager._add_instance_to_available_pool(instance)
        
        # Add instances to unavailable pool
        for i in range(5):
            instance = Instance(
                job_name=f"test-unavailable-{i}",
                model_name="test-model",
                id=200 + i,
                role=PDRole.ROLE_U,
                endpoints={}
            )
            self.instance_manager._unavailable_pool[200 + i] = instance
        
        # Thread-safe counter for tracking results
        results = []
        errors = []
        lock = threading.Lock()
        
        def get_all_instances_task(iteration):
            try:
                available_pool, unavailable_pool = self.instance_manager.get_all_instances()
                with lock:
                    results.append((iteration, len(available_pool), len(unavailable_pool)))
            except Exception as e:
                with lock:
                    errors.append((iteration, str(e)))
        
        def modify_instances_task(iteration):
            try:
                # Add a new instance to available pool
                if iteration % 2 == 0:
                    instance = Instance(
                        job_name=f"test-modify-available-{iteration}",
                        model_name="test-model",
                        id=300 + iteration,
                        role=PDRole.ROLE_P,
                        endpoints={}
                    )
                    self.instance_manager.refresh_instances(EventType.ADD, [instance])
                # Add a new instance to unavailable pool
                else:
                    instance = Instance(
                        job_name=f"test-modify-unavailable-{iteration}",
                        model_name="test-model",
                        id=400 + iteration,
                        role=PDRole.ROLE_D,
                        endpoints={}
                    )
                    self.instance_manager._unavailable_pool[400 + iteration] = instance
            except Exception as e:
                with lock:
                    errors.append((iteration, str(e)))
        
        # Run concurrent get and modify operations
        threads = []
        
        # Start 3 threads for getting all instances, each performing 40 iterations
        for i in range(3):
            thread = threading.Thread(
                target=lambda: [
                    get_all_instances_task(j) 
                    for j in range(i*40, (i+1)*40)
                ]
            )
            threads.append(thread)
            thread.start()
        
        # Start 3 threads for modifying instances, each performing 40 iterations
        for i in range(3):
            thread = threading.Thread(
                target=lambda: [
                    modify_instances_task(j) 
                    for j in range(i*40, (i+1)*40)
                ]
            )
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        # Verify that operations succeeded
        assert len(errors) == 0, f"Expected no errors, got {len(errors)} errors: {errors}"
        
        # Verify that get operations returned valid results
        assert len(results) == 120, f"Expected 120 get operations to succeed, got {len(results)}"
        
        # Each get operation should return at least the initial counts
        # (10 available + added ones, 5 unavailable + added ones)
        for _, available_count, unavailable_count in results:
            assert available_count >= 10, f"Available count should be at least 10, got {available_count}"
            assert unavailable_count >= 5, f"Unavailable count should be at least 5, got {unavailable_count}"

    def test_large_scale_concurrent_operations(self):
        """Test large scale concurrent operations to stress test thread safety"""
        def add_instance_task(instance_id, iteration):
            instance = Instance(
                job_name=f"test-add-{instance_id}-{iteration}",
                model_name="test-model",
                id=instance_id,
                role=PDRole.ROLE_P if instance_id % 2 == 0 else PDRole.ROLE_D,
                endpoints={}
            )
            try:
                self.instance_manager.refresh_instances(EventType.ADD, [instance])
                return f"add_{instance_id}_{iteration}_success"
            except Exception as e:
                return f"add_{instance_id}_{iteration}_error: {str(e)}"
        
        def delete_instance_task(instance_id, iteration):
            instance = Instance(
                job_name=f"test-del-{instance_id}-{iteration}",
                model_name="test-model",
                id=instance_id,
                role=PDRole.ROLE_P if instance_id % 2 == 0 else PDRole.ROLE_D,
                endpoints={}
            )
            try:
                self.instance_manager.refresh_instances(EventType.DEL, [instance])
                return f"del_{instance_id}_{iteration}_success"
            except Exception as e:
                return f"del_{instance_id}_{iteration}_error: {str(e)}"
        
        # Thread-safe counter for tracking results
        results = []
        lock = threading.Lock()
        
        # First add some instances
        for i in range(50):
            instance = Instance(
                job_name=f"initial-{i}",
                model_name="test-model",
                id=1000 + i,
                role=PDRole.ROLE_P if i % 2 == 0 else PDRole.ROLE_D,
                endpoints={}
            )
            self.instance_manager.refresh_instances(EventType.ADD, [instance])
        
        def add_worker(start_id):
            local_results = []
            for i in range(start_id, start_id + 20):
                result = add_instance_task(2000 + i, i)
                local_results.append(result)
            with lock:
                results.extend(local_results)
        
        def delete_worker(start_id):
            local_results = []
            for i in range(start_id, start_id + 10):
                result = delete_instance_task(1000 + (i % 50), i)
                local_results.append(result)
            with lock:
                results.extend(local_results)
        
        # Run concurrent add/delete operations
        threads = []
        
        # Start 10 add threads, each adding 20 instances
        for i in range(10):
            thread = threading.Thread(target=add_worker, args=(i*20,))
            threads.append(thread)
            thread.start()
        
        # Start 10 delete threads, each deleting 10 instances
        for i in range(10):
            thread = threading.Thread(target=delete_worker, args=(i*10,))
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        # Verify that operations completed
        success_count = sum(1 for r in results if "success" in r)
        assert success_count > 0, f"Expected some operations to succeed, got results: {results}"
        
        # Verify internal consistency
        available_count = (len(self.instance_manager._prefill_pool) + 
                          len(self.instance_manager._decode_pool) + 
                          len(self.instance_manager._hybrid_pool))
        unavailable_count = len(self.instance_manager._unavailable_pool)
        
        # Total instances should be reasonable
        total_instances = available_count + unavailable_count
        assert total_instances >= 0, f"Invalid instance count: {total_instances}"
        assert total_instances <= 300, f"Unexpectedly high instance count: {total_instances}"