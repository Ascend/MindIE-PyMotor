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
    "logging_config": {
        "log_level": "DEBUG",
        "log_max_line_length": 4096,
        "log_file": "/tmp/test.log",
        "log_format": "%(levelname)s [%(filename)s:%(lineno)d] %(message)s",
        "log_date_format": "%Y-%m-%d %H:%M:%S"
    },
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
    "http_config": {
        "combined_mode": False,
        "coordinator_api_host": "127.0.0.1",
        "coordinator_api_infer_port": 1025,
        "coordinator_api_mgmt_port": 1026
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

# Basic configuration template for reload tests (minimal required config)
BASIC_RELOAD_CONFIG = {
    "prometheus_metrics_config": {
        "reuse_time": 3
    },
    "exception_config": {
        "max_retry": 5,
        "retry_delay": 0.2,
        "first_token_timeout": 60,
        "infer_timeout": 300,
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
    }
}

# Full configuration template for reload tests (includes server config)
FULL_RELOAD_CONFIG = {
    **BASIC_RELOAD_CONFIG,
    "server_config": {
        "combined_mode": False,
        "combined_host": "0.0.0.0",
        "combined_port": 9999,
        "mgmt_host": "0.0.0.0",
        "mgmt_port": 9998,
        "inference_host": "0.0.0.0",
        "inference_port": 9999
    }
}

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
    def test_logging_config(self):
        """Test logging configuration"""
        config = COMPLETE_CONFIG.copy()
        coordinator = create_coordinator_with_config(config)

        assert coordinator.logging_config.log_level == "DEBUG"
        assert coordinator.logging_config.log_max_line_length == 4096
        assert coordinator.logging_config.log_file == "/tmp/test.log"
        assert coordinator.logging_config.log_format == "%(levelname)s [%(filename)s:%(lineno)d] %(message)s"
        assert coordinator.logging_config.log_date_format == "%Y-%m-%d %H:%M:%S"

    @pytest.mark.usefixtures("reset_singleton")
    def test_logging_config_invalid_log_level(self):
        """Test invalid log level type"""
        config = {
            "logging_config": {
                "log_level": 123  # Invalid type
            },
            "scheduler_config": {
                "deploy_mode": "single_node",
                "scheduler_type": "load_balance"
            }
        }

        coordinator, exception = create_coordinator_with_invalid_config(config)
        assert coordinator is None
        assert exception is not None
        assert "log_level must be a string" in str(exception)

    @pytest.mark.usefixtures("reset_singleton")
    def test_logging_config_invalid_max_line_length(self):
        """Test invalid max line length"""
        config = {
            "logging_config": {
                "log_max_line_length": "invalid"  # Invalid type
            },
            "scheduler_config": {
                "deploy_mode": "single_node",
                "scheduler_type": "load_balance"
            }
        }

        coordinator, exception = create_coordinator_with_invalid_config(config)
        assert coordinator is None
        assert exception is not None
        assert "log_max_line_length must be a positive integer" in str(exception)

    @pytest.mark.usefixtures("reset_singleton")
    def test_logging_config_invalid_log_file(self):
        """Test invalid log file type"""
        config = {
            "logging_config": {
                "log_file": 123  # Invalid type
            },
            "scheduler_config": {
                "deploy_mode": "single_node",
                "scheduler_type": "load_balance"
            }
        }

        coordinator, exception = create_coordinator_with_invalid_config(config)
        assert coordinator is None
        assert exception is not None
        assert "log_file must be a string or null" in str(exception)

    @pytest.mark.usefixtures("reset_singleton")
    def test_logging_config_invalid_log_format(self):
        """Test invalid log format type"""
        config = {
            "logging_config": {
                "log_format": 123  # Invalid type
            },
            "scheduler_config": {
                "deploy_mode": "single_node",
                "scheduler_type": "load_balance"
            }
        }

        coordinator, exception = create_coordinator_with_invalid_config(config)
        assert coordinator is None
        assert exception is not None
        assert "log_format must be a string" in str(exception)

    @pytest.mark.usefixtures("reset_singleton")
    def test_logging_config_invalid_date_format(self):
        """Test invalid date format type"""
        config = {
            "logging_config": {
                "log_date_format": 123  # Invalid type
            },
            "scheduler_config": {
                "deploy_mode": "single_node",
                "scheduler_type": "load_balance"
            }
        }

        coordinator, exception = create_coordinator_with_invalid_config(config)
        assert coordinator is None
        assert exception is not None
        assert "log_date_format must be a string" in str(exception)

    @pytest.mark.usefixtures("reset_singleton")
    def test_logging_config_defaults(self):
        """Test logging configuration defaults when not specified"""
        config = {
            "scheduler_config": {
                "deploy_mode": "single_node",
                "scheduler_type": "load_balance"
            }
        }
        coordinator = create_coordinator_with_config(config)

        # Test default values
        assert coordinator.logging_config.log_level == "INFO"
        assert coordinator.logging_config.log_max_line_length == 8192
        assert coordinator.logging_config.log_file is None
        assert coordinator.logging_config.log_format == '%(asctime)s  [%(levelname)s][%(name)s][%(filename)s:%(lineno)d]  %(message)s'
        assert coordinator.logging_config.log_date_format == '%Y-%m-%d %H:%M:%S'

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
    def test_http_config_full(self):
        """Test full HTTP configuration"""
        config = COMPLETE_CONFIG.copy()
        coordinator = create_coordinator_with_config(config)
        
        assert coordinator.http_config.combined_mode is False
        assert coordinator.http_config.coordinator_api_host == "127.0.0.1"
        assert coordinator.http_config.coordinator_api_infer_port == 1025
        assert coordinator.http_config.coordinator_api_mgmt_port == 1026

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

    @pytest.mark.usefixtures("reset_singleton")
    def test_reload_success(self):
        """Test successful configuration reload"""
        # Use a fresh valid config to avoid any state pollution
        config = FULL_RELOAD_CONFIG.copy()
        coordinator = create_coordinator_with_config(config)

        # Store original values
        original_reuse_time = coordinator.prometheus_metrics_config.reuse_time
        original_max_retry = coordinator.exception_config.max_retry

        # Create modified config for reload
        modified_config = config.copy()
        modified_config["prometheus_metrics_config"]["reuse_time"] = original_reuse_time + 10
        modified_config["exception_config"]["max_retry"] = original_max_retry + 5

        # Mock file operations for reload
        with patch('builtins.open', mock_open(read_data=json.dumps(modified_config))):
            with patch('os.path.exists', return_value=True):
                result = coordinator.reload()

        assert result is True
        assert coordinator.prometheus_metrics_config.reuse_time == original_reuse_time + 10
        assert coordinator.exception_config.max_retry == original_max_retry + 5

    @pytest.mark.usefixtures("reset_singleton")
    def test_reload_config_file_not_found(self):
        """Test reload when configuration file doesn't exist"""
        # Use a fresh valid config to avoid any state pollution
        config = BASIC_RELOAD_CONFIG.copy()
        coordinator = create_coordinator_with_config(config)

        # Set config_file_path to a non-existent path
        coordinator.config_file_path = "/non/existent/path/config.json"

        result = coordinator.reload()
        assert result is False

    @pytest.mark.usefixtures("reset_singleton")
    def test_reload_invalid_json(self):
        """Test reload with invalid JSON"""
        # Use a fresh valid config to avoid any state pollution
        config = BASIC_RELOAD_CONFIG.copy()
        coordinator = create_coordinator_with_config(config)

        # Mock invalid JSON
        with patch('builtins.open', mock_open(read_data="invalid json content")):
            with patch('os.path.exists', return_value=True):
                result = coordinator.reload()

        assert result is False

    @pytest.mark.usefixtures("reset_singleton")
    def test_reload_missing_config_file_path(self):
        """Test reload when config_file_path is None"""
        # Use a fresh valid config to avoid any state pollution
        config = BASIC_RELOAD_CONFIG.copy()
        coordinator = create_coordinator_with_config(config)

        # Set config_file_path to None
        coordinator.config_file_path = None

        result = coordinator.reload()
        assert result is False
