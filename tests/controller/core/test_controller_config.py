import os
import time
import json
import pytest
import tempfile
from unittest.mock import patch, MagicMock

from motor.config.controller import (
    ControllerConfig,
    find_config_file,
    set_config_path,
    get_config_path,
    reload_global_config,
    controller_config
)


def test_default_config_initialization():
    """Test default configuration initialization"""
    config = ControllerConfig()

    # Verify default values
    assert config.instance_assemble_timeout == 600
    assert config.instance_assembler_check_internal == 1
    assert config.instance_assembler_cmd_send_internal == 1
    assert config.max_link_number == 768
    assert config.send_cmd_retry_times == 3
    assert config.instance_manager_check_internal == 1
    assert config.instance_heartbeat_timeout == 5
    assert config.instance_expired_timeout == 300
    assert config.controller_api_host == '127.0.0.1'
    assert config.controller_api_port == 8000
    assert config.enable_fault_tolerance is True
    assert config.strategy_center_check_internal == 1


def test_config_validation_success():
    """Test successful configuration validation"""
    config = ControllerConfig(
        instance_assemble_timeout=300,
        instance_heartbeat_timeout=10,
        instance_expired_timeout=600,
        controller_api_port=9000,
        max_link_number=1000,
        send_cmd_retry_times=5
    )
    # If no exception is raised, validation passed
    assert config.controller_api_port == 9000


def test_config_validation_negative_timeout():
    """Test validation failure for negative timeout configuration"""
    with pytest.raises(ValueError, match="instance_assemble_timeout must be greater than 0"):
        ControllerConfig(instance_assemble_timeout=-1)


def test_config_validation_zero_timeout():
    """Test validation failure for zero timeout configuration"""
    with pytest.raises(ValueError, match="instance_heartbeat_timeout must be greater than 0"):
        ControllerConfig(instance_heartbeat_timeout=0)


def test_config_validation_negative_check_interval():
    """Test validation failure for negative check interval configuration"""
    with pytest.raises(ValueError, match="instance_assembler_check_internal must be greater than 0"):
        ControllerConfig(instance_assembler_check_internal=-1)


def test_config_validation_invalid_port_range():
    """Test validation failure for invalid port range configuration"""
    with pytest.raises(ValueError, match="controller_api_port must be in range 1-65535"):
        ControllerConfig(controller_api_port=0)

    with pytest.raises(ValueError, match="controller_api_port must be in range 1-65535"):
        ControllerConfig(controller_api_port=65536)


def test_config_validation_negative_retry_times():
    """Test validation failure for negative retry times configuration"""
    with pytest.raises(ValueError, match="send_cmd_retry_times cannot be negative"):
        ControllerConfig(send_cmd_retry_times=-1)


def test_config_validation_zero_max_links():
    """Test validation failure for zero max links configuration"""
    with pytest.raises(ValueError, match="max_link_number must be greater than 0"):
        ControllerConfig(max_link_number=0)


def test_config_validation_multiple_errors():
    """Test multiple configuration errors"""
    with pytest.raises(ValueError) as exc_info:
        ControllerConfig(
            instance_assemble_timeout=-1,
            controller_api_port=0,
            max_link_number=-1
        )
    error_msg = str(exc_info.value)
    assert "instance_assemble_timeout must be greater than 0" in error_msg
    assert "controller_api_port must be in range 1-65535" in error_msg
    assert "max_link_number must be greater than 0" in error_msg


def test_from_json_file_exists():
    """Test loading configuration from existing JSON file"""
    test_config = {
        "controller_api_host": "192.168.1.1",
        "controller_api_port": 9000,
        "instance_assemble_timeout": 300,
        "max_link_number": 1000,
        "enable_fault_tolerance": False
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(test_config, f)
        temp_path = f.name

    try:
        config = ControllerConfig.from_json(temp_path)
        assert config.controller_api_host == "192.168.1.1"
        assert config.controller_api_port == 9000
        assert config.instance_assemble_timeout == 300
        assert config.max_link_number == 1000
        assert config.enable_fault_tolerance is False
        assert config.config_path == temp_path
        assert config.last_modified is not None
    finally:
        os.unlink(temp_path)


def test_from_json_file_not_exists():
    """Test loading configuration from non-existent JSON file (using default values)"""
    with tempfile.TemporaryDirectory() as temp_dir:
        non_existent_path = os.path.join(temp_dir, "non_existent.json")
        config = ControllerConfig.from_json(non_existent_path)

        # Should use default values
        assert config.controller_api_host == '127.0.0.1'
        assert config.controller_api_port == 8000
        assert config.config_path == non_existent_path
        assert config.last_modified is None


def test_from_json_invalid_json():
    """Test loading configuration from invalid JSON file"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write("invalid json content")
        temp_path = f.name

    try:
        with pytest.raises(ValueError, match="Configuration file.*format error"):
            ControllerConfig.from_json(temp_path)
    finally:
        os.unlink(temp_path)


def test_reload_config_file_not_exists():
    """Test reloading non-existent configuration file"""
    config = ControllerConfig()
    # Use a temporary path that doesn't exist
    with tempfile.TemporaryDirectory() as temp_dir:
        non_existent_path = os.path.join(temp_dir, "non_existent.json")
        config.config_path = non_existent_path
        result = config.reload()
        assert result is False


def test_reload_config_file_not_modified():
    """Test reloading unmodified configuration file"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({"controller_api_port": 8000}, f)
        temp_path = f.name

    try:
        config = ControllerConfig.from_json(temp_path)
        # First reload should succeed (file not modified)
        result = config.reload()
        assert result is True
    finally:
        os.unlink(temp_path)


def test_reload_config_file_modified():
    """Test reloading modified configuration file"""
    test_config = {"controller_api_port": 8000}
    modified_config = {"controller_api_port": 9000}

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(test_config, f)
        temp_path = f.name

    try:
        config = ControllerConfig.from_json(temp_path)
        original_port = config.controller_api_port

        # Wait a short time to ensure different file modification time
        time.sleep(0.1)

        # Modify file
        with open(temp_path, 'w') as f:
            json.dump(modified_config, f)

        # Reload configuration
        result = config.reload()
        assert result is True
        assert config.controller_api_port == 9000
        assert config.controller_api_port != original_port
    finally:
        os.unlink(temp_path)


def test_reload_config_invalid_json():
    """Test reloading invalid JSON configuration file"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({"controller_api_port": 8000}, f)
        temp_path = f.name

    try:
        config = ControllerConfig.from_json(temp_path)

        time.sleep(0.01)

        # Write invalid JSON
        with open(temp_path, 'w') as f:
            f.write("invalid json")

        # Manually update file modification time
        current_time = time.time()
        os.utime(temp_path, (current_time, current_time))

        result = config.reload()
        assert result is False
    finally:
        os.unlink(temp_path)


def test_to_dict():
    """Test conversion to dictionary"""
    config = ControllerConfig(
        controller_api_host="192.168.1.1",
        controller_api_port=9000,
        instance_assemble_timeout=300
    )

    config_dict = config.to_dict()

    assert config_dict["controller_api_host"] == "192.168.1.1"
    assert config_dict["controller_api_port"] == 9000
    assert config_dict["instance_assemble_timeout"] == 300
    assert "_config_path" not in config_dict
    assert "_last_modified" not in config_dict


def test_save_to_json_success():
    """Test successful saving configuration to JSON file"""
    config = ControllerConfig(controller_api_port=9000)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_path = f.name

    try:
        result = config.save_to_json(temp_path)
        assert result is True

        # Verify file content
        with open(temp_path, 'r') as f:
            saved_config = json.load(f)
        assert saved_config["controller_api_port"] == 9000
    finally:
        os.unlink(temp_path)


def test_save_to_json_no_path():
    """Test saving configuration to unspecified path"""
    config = ControllerConfig()
    result = config.save_to_json()
    assert result is False


def test_save_to_json_write_error():
    """Test write error when saving configuration"""
    config = ControllerConfig()
    # Use a temporary path for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        test_path = os.path.join(temp_dir, "config.json")
        config.config_path = test_path

        with patch('builtins.open', side_effect=PermissionError("Permission denied")):
            result = config.save_to_json()
            assert result is False


def test_get_config_summary():
    """Test getting configuration summary"""
    config = ControllerConfig(
        controller_api_host="192.168.1.1",
        controller_api_port=9000,
        instance_assemble_timeout=300,
        max_link_number=1000,
        enable_fault_tolerance=False
    )
    # Use a temporary path for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        test_path = os.path.join(temp_dir, "config.json")
        config.config_path = test_path

        summary = config.get_config_summary()

        assert "192.168.1.1:9000" in summary
        assert "300 seconds" in summary
        assert "1000" in summary
        assert "Disabled" in summary
        assert test_path in summary


def test_get_config_summary_no_path():
    """Test getting configuration summary (no path)"""
    config = ControllerConfig()
    summary = config.get_config_summary()
    assert "Not set" in summary


def test_find_config_file_package_exists():
    """Test finding configuration file in package directory"""
    with patch('os.path.exists', return_value=True) as mock_exists:
        with patch('os.path.dirname') as mock_dirname:
            # Mock the package directory path
            mock_dirname.return_value = "/mock/package/dir"
            result = find_config_file()
            expected_path = "/mock/package/dir/controller_config.json"
            assert result == expected_path
            mock_exists.assert_called()


def test_find_config_file_package_not_exists():
    """Test not finding configuration file in package directory, search project root"""
    def mock_exists(path):
        # Return False for package config, True for project root config
        if path.endswith("controller_config.json") and "motor/config" in path:
            return False
        elif "controller_config.json" in path:
            return True
        return False

    with patch('os.path.exists', side_effect=mock_exists):
        with patch('os.path.dirname') as mock_dirname:
            # Simulate searching up directory structure
            def mock_dirname_side_effect(path):
                if "motor/config" in path:
                    return "/mock/project/root/motor/config"
                elif path == "/mock/project/root/motor/config":
                    return "/mock/project/root/motor"
                elif path == "/mock/project/root/motor":
                    return "/mock/project/root"
                else:
                    return "/mock/project/root"

            mock_dirname.side_effect = mock_dirname_side_effect
            result = find_config_file()
            assert "controller_config.json" in result
            assert "motor/config" in result


def test_find_config_file_not_found():
    """Test not finding any configuration file"""
    with patch('os.path.exists', return_value=False):
        with patch('os.path.dirname') as mock_dirname:
            # Mock the package directory path
            mock_dirname.return_value = "/mock/package/dir"
            result = find_config_file()
            expected_path = "/mock/package/dir/controller_config.json"
            assert result == expected_path


def test_config_with_extreme_values():
    """Test extreme value configuration"""
    config = ControllerConfig(
        instance_assemble_timeout=1,
        instance_heartbeat_timeout=1,
        instance_expired_timeout=1,
        controller_api_port=1,
        max_link_number=1,
        send_cmd_retry_times=0
    )
    assert config.controller_api_port == 1
    assert config.max_link_number == 1
    assert config.send_cmd_retry_times == 0


def test_config_with_maximum_values():
    """Test maximum value configuration"""
    config = ControllerConfig(
        controller_api_port=65535,
        max_link_number=999999,
        send_cmd_retry_times=100
    )
    assert config.controller_api_port == 65535
    assert config.max_link_number == 999999
    assert config.send_cmd_retry_times == 100


def test_config_boolean_values():
    """Test boolean value configuration"""
    config_true = ControllerConfig(enable_fault_tolerance=True)
    config_false = ControllerConfig(enable_fault_tolerance=False)

    assert config_true.enable_fault_tolerance is True
    assert config_false.enable_fault_tolerance is False


def test_config_string_values():
    """Test string value configuration"""
    config = ControllerConfig(controller_api_host="0.0.0.0")
    assert config.controller_api_host == "0.0.0.0"


def test_config_partial_json_loading():
    """Test partial JSON configuration loading"""
    partial_config = {
        "controller_api_port": 9000,
        "instance_assemble_timeout": 300
        # Other fields use default values
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(partial_config, f)
        temp_path = f.name

    try:
        config = ControllerConfig.from_json(temp_path)
        assert config.controller_api_port == 9000
        assert config.instance_assemble_timeout == 300
        # Other fields should be default values
        assert config.controller_api_host == '127.0.0.1'
        assert config.max_link_number == 768
    finally:
        os.unlink(temp_path)


def test_config_extra_fields_in_json():
    """Test extra fields in JSON"""
    config_with_extra = {
        "controller_api_port": 9000,
        "extra_field": "should_be_ignored",
        "another_extra": 123
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(config_with_extra, f)
        temp_path = f.name

    try:
        config = ControllerConfig.from_json(temp_path)
        assert config.controller_api_port == 9000
        # Extra fields should be ignored
        assert not hasattr(config, 'extra_field')
        assert not hasattr(config, 'another_extra')
    finally:
        os.unlink(temp_path)


def test_config_reload_preserves_internal_fields():
    """Test that reloading configuration preserves internal fields"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({"controller_api_port": 8000}, f)
        temp_path = f.name

    try:
        config = ControllerConfig.from_json(temp_path)
        original_path = config.config_path
        original_modified = config.last_modified

        # Use a shorter wait time and manually touch the file to ensure different modification time
        time.sleep(0.01)

        # Modify configuration
        with open(temp_path, 'w') as f:
            json.dump({"controller_api_port": 9000}, f)

        # Manually update file modification time to ensure it's different
        current_time = time.time()
        os.utime(temp_path, (current_time, current_time))

        config.reload()

        # Internal fields should be updated
        assert config.config_path == original_path
        assert config.last_modified >= original_modified
    finally:
        os.unlink(temp_path)


def test_config_validation_with_none_values():
    """Test handling None values in configuration validation"""
    # These tests ensure validation logic correctly handles various edge cases
    with pytest.raises(TypeError):
        ControllerConfig(instance_assemble_timeout=None)


def test_config_unicode_handling():
    """Test Unicode character handling"""
    unicode_config = {
        "controller_api_host": "Test Host",
        "controller_api_port": 8000
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
        json.dump(unicode_config, f, ensure_ascii=False)
        temp_path = f.name

    try:
        config = ControllerConfig.from_json(temp_path)
        assert config.controller_api_host == "Test Host"
    finally:
        os.unlink(temp_path)


def _reset_config_path_override():
    """Helper function to reset global config path override"""
    import motor.config.controller as config_module
    config_module.CONFIG_PATH_OVERRIDE = None


def test_set_config_path():
    """Test setting configuration path override"""
    _reset_config_path_override()

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_path = os.path.join(temp_dir, "config.json")
            set_config_path(test_path)

            # Verify path is set
            assert get_config_path() == test_path
    finally:
        _reset_config_path_override()


def test_find_config_file_with_override():
    """Test finding config file with path override"""
    _reset_config_path_override()

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_path = os.path.join(temp_dir, "override.json")
            set_config_path(test_path)

            result = find_config_file()
            assert result == test_path
    finally:
        _reset_config_path_override()


def test_find_config_file_without_override():
    """Test finding config file without override"""
    _reset_config_path_override()

    result = find_config_file()
    # Should return default path
    assert result is not None
    assert "controller_config.json" in result


def test_get_config_path_with_override():
    """Test getting config path with override"""
    _reset_config_path_override()

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_path = os.path.join(temp_dir, "config.json")
            set_config_path(test_path)

            result = get_config_path()
            assert result == test_path
    finally:
        _reset_config_path_override()


def test_get_config_path_without_override():
    """Test getting config path without override"""
    _reset_config_path_override()

    result = get_config_path()
    assert result is not None


@patch('motor.config.controller.ControllerConfig.from_json')
def test_reload_global_config_success(mock_from_json):
    """Test successful global config reload"""
    mock_config = MagicMock()
    mock_from_json.return_value = mock_config

    result = reload_global_config()

    assert result is True
    mock_from_json.assert_called_once()


@patch('motor.config.controller.ControllerConfig.from_json')
def test_reload_global_config_failure(mock_from_json):
    """Test global config reload failure"""
    mock_from_json.side_effect = Exception("Config load failed")

    result = reload_global_config()

    assert result is False


def test_set_config_path_logging():
    """Test configuration path setting logging"""
    _reset_config_path_override()

    try:
        with patch('motor.config.controller.logger') as mock_logger:
            with tempfile.TemporaryDirectory() as temp_dir:
                test_path = os.path.join(temp_dir, "config.json")
                set_config_path(test_path)

                mock_logger.info.assert_called_with(f"Configuration path override set to: {test_path}")
    finally:
        _reset_config_path_override()


def test_reload_global_config_logging_success():
    """Test reload global config success logging"""
    with patch('motor.config.controller.ControllerConfig.from_json') as mock_from_json:
        with patch('motor.config.controller.logger') as mock_logger:
            mock_config = MagicMock()
            mock_from_json.return_value = mock_config

            reload_global_config()

            mock_logger.info.assert_called_with("Global configuration reloaded successfully")


def test_reload_global_config_logging_failure():
    """Test reload global config failure logging"""
    with patch('motor.config.controller.ControllerConfig.from_json') as mock_from_json:
        with patch('motor.config.controller.logger') as mock_logger:
            mock_from_json.side_effect = Exception("Config load failed")

            reload_global_config()

            mock_logger.error.assert_called_with("Global configuration reload failed: Config load failed")


def test_config_path_override_priority():
    """Test that path override takes priority over auto-detection"""
    _reset_config_path_override()

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            # Set override
            override_path = os.path.join(temp_dir, "override.json")
            set_config_path(override_path)

            # Even if auto-detection would find another file, override should be used
            result = find_config_file()
            assert result == override_path
    finally:
        _reset_config_path_override()


def test_multiple_set_config_path():
    """Test setting config path multiple times"""
    _reset_config_path_override()

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            path1 = os.path.join(temp_dir, "config1.json")
            path2 = os.path.join(temp_dir, "config2.json")

            set_config_path(path1)
            assert get_config_path() == path1

            set_config_path(path2)
            assert get_config_path() == path2
    finally:
        _reset_config_path_override()


def test_config_path_with_none():
    """Test setting config path to None"""
    _reset_config_path_override()

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_path = os.path.join(temp_dir, "config.json")
            set_config_path(test_path)
            assert get_config_path() == test_path

            # Reset to None (no override)
            import motor.config.controller as config_module
            config_module.CONFIG_PATH_OVERRIDE = None

            result = get_config_path()
            assert result is not None  # Should fall back to auto-detection
    finally:
        _reset_config_path_override()


def test_controller_config_with_custom_path():
    """Test ControllerConfig creation with custom path"""
    _reset_config_path_override()

    try:
        test_config = {
            "controller_api_port": 9000,
            "instance_assemble_timeout": 300
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(test_config, f)
            temp_path = f.name

        try:
            # Set the path override
            set_config_path(temp_path)

            # Create new config instance
            config = ControllerConfig.from_json(temp_path)
            assert config.controller_api_port == 9000
            assert config.instance_assemble_timeout == 300
        finally:
            os.unlink(temp_path)
    finally:
        _reset_config_path_override()


def test_global_config_reload_with_path_override():
    """Test global config reload with path override"""
    _reset_config_path_override()

    try:
        test_config = {
            "controller_api_port": 9000,
            "enable_fault_tolerance": False
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(test_config, f)
            temp_path = f.name

        try:
            # Set path override
            set_config_path(temp_path)

            # Reload global config
            result = reload_global_config()
            assert result is True

            # Verify global config is updated
            assert controller_config.controller_api_port == 9000
            assert controller_config.enable_fault_tolerance is False
        finally:
            os.unlink(temp_path)
    finally:
        _reset_config_path_override()


def test_config_path_override_persistence():
    """Test that config path override persists across calls"""
    _reset_config_path_override()

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_path = os.path.join(temp_dir, "persistent.json")
            set_config_path(test_path)

            # Multiple calls should return the same override
            assert get_config_path() == test_path
            assert find_config_file() == test_path
            assert get_config_path() == test_path
    finally:
        _reset_config_path_override()


def test_find_config_file_fallback_behavior():
    """Test find_config_file fallback behavior without override"""
    _reset_config_path_override()

    result = find_config_file()

    # Should return a valid path (even if file doesn't exist)
    assert result is not None
    assert isinstance(result, str)
    assert len(result) > 0
