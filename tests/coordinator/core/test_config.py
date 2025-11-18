import pytest
from pytest import MonkeyPatch
import json
import os
from unittest.mock import patch, mock_open
from motor.config.coordinator import CoordinatorConfig
from motor.common.utils.singleton import ThreadSafeSingleton
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Complete configuration template
COMPLETE_CONFIG = {
    "prometheus_metrics_config": {
        "reuse_time": 3
    },
    "exception_config": {
        "max_retry": 5,
        "retry_delay": 0.2,
        "first_token_timeout": 60,
        "infer_timeout": 300,
    },
    "tls_config": {
        "controller_server_tls_enable": True,
        "controller_server_tls_items": {
            "ca_cert": "ca.pem",
            "tls_cert": "server.pem",
            "tls_key": "server.key",
            "tls_passwd": "password",
            "tls_crl": "crl.pem",
            "kmcKsfMaster": "master_key",
            "kmcKsfStandby": "standby_key"
        }
    },
    "scheduler_config": {
        "deploy_mode": "single_node",
        "scheduler_type": "load_balance"
    },
    "health_check_config": {
        "dummy_request_interval": 5.0,
        "max_consecutive_failures": 3,
        "dummy_request_timeout": 10.0,
        "controller_api_dns": "mindie-ms-controller-service.mindie.svc.cluster.local",
        "controller_api_port": 57675
    },
    "server_config": {
        "combined_mode": False,
        "combined_host": "0.0.0.0",
        "combined_port": 9999,
        "mgmt_host": "0.0.0.0",
        "mgmt_port": 9998,
        "inference_host": "0.0.0.0",
        "inference_port": 9999
    },
}

@pytest.fixture
def reset_singleton():
    """Reset singleton instance to ensure test isolation"""

    # Remove the CoordinatorConfig class from the _instances dictionary
    if CoordinatorConfig in ThreadSafeSingleton._instances:
        del ThreadSafeSingleton._instances[CoordinatorConfig]
    yield
    # Clean up after the test
    if CoordinatorConfig in ThreadSafeSingleton._instances:
        del ThreadSafeSingleton._instances[CoordinatorConfig]

def create_coordinator_with_config(config_data, env_vars=None):
    """Create CoordinatorConfig instance with mock configuration"""
    # Set up environment variables first if provided
    if env_vars:
        for key, value in env_vars.items():
            os.environ[key] = str(value)
    
    with patch('builtins.open', mock_open(read_data=json.dumps(config_data))):
        with patch('os.path.exists', return_value=True):
            return CoordinatorConfig()

def create_coordinator_with_file_not_found():
    """Create CoordinatorConfig instance when file doesn't exist"""
    with patch('os.path.exists', return_value=False):
        return CoordinatorConfig()

def create_coordinator_with_invalid_config(config_data):
    """Create CoordinatorConfig instance with invalid configuration, expecting exception"""
    with patch('builtins.open', mock_open(read_data=json.dumps(config_data))):
        with patch('os.path.exists', return_value=True):
            try:
                coordinator = CoordinatorConfig()
                return coordinator, None
            except Exception as e:
                return None, e

class TestCoordinatorConfig:
    
    @pytest.mark.usefixtures("reset_singleton")
    def test_init_success(self):
        """Test successful initialization"""
        config = COMPLETE_CONFIG.copy()
        coordinator = create_coordinator_with_config(config)
        
        assert coordinator.prometheus_metrics_config.reuse_time == 3
        assert coordinator.exception_config.max_retry == 5
        assert coordinator.health_check_config.dummy_request_interval == 5.0

    @pytest.mark.usefixtures("reset_singleton") 
    def test_init_file_not_found(self):
        """Test configuration file not found scenario"""
        # This should raise FileNotFoundError during initialization
        with pytest.raises(FileNotFoundError) as exc_info:
            create_coordinator_with_file_not_found()
        assert "Configuration file not found" in str(exc_info.value)


    @pytest.mark.usefixtures("reset_singleton")
    def test_metrics_config(self):
        """Test Metrics configuration"""
        config = COMPLETE_CONFIG.copy()
        coordinator = create_coordinator_with_config(config)
        
        assert coordinator.prometheus_metrics_config.reuse_time == 3

    @pytest.mark.usefixtures("reset_singleton")
    def test_exception_config(self):
        """Test exception configuration"""
        config = COMPLETE_CONFIG.copy()
        coordinator = create_coordinator_with_config(config)
        
        assert coordinator.exception_config.max_retry == 5
        assert coordinator.exception_config.first_token_timeout == 60
        assert coordinator.exception_config.infer_timeout == 300

    @pytest.mark.usefixtures("reset_singleton")
    def test_health_check_config(self):
        """Test health check configuration"""
        config = COMPLETE_CONFIG.copy()
        coordinator = create_coordinator_with_config(config)
        
        assert coordinator.health_check_config.dummy_request_interval == 5.0
        assert coordinator.health_check_config.max_consecutive_failures == 3
        assert coordinator.health_check_config.dummy_request_timeout == 10.0

    
    @pytest.mark.usefixtures("reset_singleton")
    def test_health_check_config_full(self):
        """Test full health check configuration"""
        config = COMPLETE_CONFIG.copy()
        coordinator = create_coordinator_with_config(config)
        
        assert coordinator.health_check_config.dummy_request_interval == 5.0
        assert coordinator.health_check_config.max_consecutive_failures == 3
        assert coordinator.health_check_config.dummy_request_timeout == 10.0
        assert coordinator.health_check_config.controller_api_dns == "mindie-ms-controller-service.mindie.svc.cluster.local"
        assert coordinator.health_check_config.controller_api_port == 57675

    @pytest.mark.usefixtures("reset_singleton")
    def test_server_config_full(self):
        """Test full server configuration"""
        config = COMPLETE_CONFIG.copy()
        coordinator = create_coordinator_with_config(config)
        
        assert coordinator.server_config.combined_mode is False
        assert coordinator.server_config.combined_host == "0.0.0.0"
        assert coordinator.server_config.combined_port == 9999
        assert coordinator.server_config.mgmt_host == "0.0.0.0"
        assert coordinator.server_config.mgmt_port == 9998
        assert coordinator.server_config.inference_host == "0.0.0.0"
        assert coordinator.server_config.inference_port == 9999

    @pytest.mark.usefixtures("reset_singleton")
    def test_invalid_deploy_mode(self):
        """Test invalid deployment mode"""
        config = COMPLETE_CONFIG.copy()
        config["scheduler_config"]["deploy_mode"] = "invalid_mode"
        
        coordinator, exception = create_coordinator_with_invalid_config(config)
        assert coordinator is None
        assert exception is not None

    @pytest.mark.usefixtures("reset_singleton")
    def test_invalid_scheduler_type(self):
        """Test invalid scheduler type"""
        config = COMPLETE_CONFIG.copy()
        config["scheduler_config"]["scheduler_type"] = "invalid_scheduler"
        
        coordinator, exception = create_coordinator_with_invalid_config(config)
        assert coordinator is None
        assert exception is not None

    @pytest.mark.usefixtures("reset_singleton")
    def test_missing_required_fields(self):
        """Test missing required fields"""
        config = COMPLETE_CONFIG.copy()
        config["scheduler_config"] = {
            "deploy_mode": "single_node",
            # Missing scheduler_type
        }
        
        coordinator, exception = create_coordinator_with_invalid_config(config)
        assert coordinator is None
        assert exception is not None

    @pytest.mark.usefixtures("reset_singleton")
    def test_default_values(self):
        """Test default values"""
        # Use minimal configuration without http_config to test defaults
        minimal_config = {
            "scheduler_config": {
                "deploy_mode": "single_node", 
                "scheduler_type": "load_balance",
            }
        }
        
        coordinator = create_coordinator_with_config(minimal_config)
        assert coordinator.exception_config.max_retry == 5  # Default from ExceptionConfig
