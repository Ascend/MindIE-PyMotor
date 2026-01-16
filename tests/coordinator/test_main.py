from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from motor.coordinator.main import on_become_standby, on_become_master, main, on_config_updated


@pytest.fixture
def setup_modules(monkeypatch):
    modules = {}
    monkeypatch.setattr('motor.coordinator.main.modules', modules)
    return modules


@patch('motor.coordinator.main.logger')
def test_on_become_standby_normal(mock_logger, setup_modules):
    """
    Test the situation where the stop method is correctly called under normal circumstances
    """
    # Create a mock module with a 'stop' method
    mock_module = MagicMock()
    mock_module.stop = MagicMock()

    setup_modules['MetricsListener'] = mock_module

    on_become_standby()

    mock_module.stop.assert_called_once()
    mock_logger.info.assert_any_call("Stopping MetricsListener...")
    mock_logger.info.assert_any_call("Becoming standby, stopping all modules...")
    mock_logger.info.assert_called_with("All modules stopped.")


@patch('motor.coordinator.main.logger')
def test_on_become_standby_exception(mock_logger, setup_modules):
    """
    Test whether errors can be caught and logged when the stop method throws an exception.
    """
    # Create a mock module that will throw an exception when 'stop'
    mock_module = MagicMock()
    mock_module.stop.side_effect = Exception("Stop failed")

    setup_modules['MetricsListener'] = mock_module

    on_become_standby()

    mock_module.stop.assert_called_once()
    mock_logger.error.assert_called_with("Error stopping MetricsListener: Stop failed")
    mock_logger.info.assert_called_with("All modules stopped.")


@patch('motor.coordinator.main.logger')
def test_on_become_standby_no_stop_method(mock_logger, setup_modules):
    """
    Testing a module without a stop method will not cause an error.
    """
    # Create a mock module without a 'stop' method
    mock_module = MagicMock()
    del mock_module.stop

    setup_modules['MetricsListener'] = mock_module

    on_become_standby()

    assert not hasattr(mock_module, 'stop') or mock_module.stop.call_count == 0
    mock_logger.info.assert_called_with("All modules stopped.")


@patch('motor.coordinator.main.logger')
def test_on_become_master_normal(mock_logger, setup_modules):
    """
    Test the situation where the stop method is correctly called under normal circumstances
    """
    # Create a mock module with a 'start' method
    mock_module = MagicMock()
    mock_module.start = MagicMock()

    setup_modules['MetricsListener'] = mock_module

    on_become_master()

    mock_module.start.assert_called_once()
    mock_logger.info.assert_any_call("Becoming master, starting all modules...")
    mock_logger.info.assert_any_call("Starting MetricsListener...")

    mock_logger.info.assert_called_with("All modules started.")


@patch('motor.coordinator.main.logger')
def test_on_become_master_exception(mock_logger, setup_modules):
    """
    Test whether errors can be caught and logged when the start method throws an exception.
    """
    # Create a mock module that will throw an exception when 'stop'
    mock_module = MagicMock()
    mock_module.start.side_effect = Exception("Start failed")

    setup_modules['MetricsListener'] = mock_module

    on_become_master()

    mock_module.start.assert_called_once()
    mock_logger.error.assert_called_with("Error starting MetricsListener: Start failed")
    mock_logger.info.assert_called_with("All modules started.")


@patch('motor.coordinator.main.config', new_callable=lambda: MagicMock())
@patch('motor.coordinator.main.logger')
@patch('motor.coordinator.main.initialize_components')
def test_on_become_master_instance(mock_initialize_components, mock_logger, mock_config, setup_modules):
    """
    Test the situation where the stop method is correctly called under normal circumstances
    """
    # Mock the config to have standby disabled
    mock_config.standby_config.enable_master_standby = False

    # Create mock modules
    mock_instance_manager = MagicMock()
    mock_request_manager = MagicMock()
    mock_metrics_collector = MagicMock()
    mock_coordinator_server = MagicMock()

    # Mock initialize_components to populate modules with our mocks
    def mock_init_components():
        from motor.coordinator.main import modules
        modules["InstanceManager"] = mock_instance_manager
        modules["RequestManager"] = mock_request_manager
        modules["MetricsListener"] = mock_metrics_collector
        modules["CoordinatorServer"] = mock_coordinator_server

    mock_initialize_components.side_effect = mock_init_components

    on_become_master()

    mock_logger.info.assert_any_call("Becoming master, starting all modules...")
    mock_logger.info.assert_called_with("All modules started.")

    # Verify that start() was called on the mocked modules
    mock_instance_manager.start.assert_called_once()
    mock_request_manager.start.assert_called_once()
    mock_metrics_collector.start.assert_called_once()
    mock_coordinator_server.start.assert_called_once()


@pytest.mark.asyncio
async def test_main_master_standby_enabled():
    """
    测试场景：启用主备模式时的行为
    """
    with patch.dict('os.environ', {'MOTOR_COORDINATOR_CONFIG_PATH': '/fake/config.json'}), \
            patch('motor.config.coordinator.CoordinatorConfig.from_json') as mock_from_json, \
            patch('motor.coordinator.main.modules', {"CoordinatorServer": AsyncMock(run=AsyncMock())}), \
            patch('motor.coordinator.main.os.path.exists', return_value=True), \
            patch('motor.coordinator.main.ConfigWatcher'), \
            patch('motor.coordinator.main.StandbyManager') as mock_standby_manager_class, \
            patch('motor.coordinator.main.initialize_components'), \
            patch('motor.coordinator.main.on_become_master'), \
            patch('motor.coordinator.main.on_become_standby'), \
            patch('motor.coordinator.main.stop_all_modules'), \
            patch('motor.coordinator.main.logger') as mock_logger:
        # Mock config object
        mock_config = MagicMock()
        mock_config.config_path = '/fake/config.json'
        mock_config.standby_config.enable_master_standby = True
        mock_from_json.return_value = mock_config

        mock_standby_manager_instance = MagicMock()
        mock_standby_manager_class.return_value = mock_standby_manager_instance

        await main()

        mock_logger.info.assert_any_call("Loaded configuration from: /fake/config.json")
        mock_logger.info.assert_any_call("Master/standby feature is enabled, running in master-standby mode")
        mock_standby_manager_instance.start.assert_called_once()


@patch('motor.coordinator.main.config', new_callable=lambda: MagicMock())
@patch('motor.coordinator.main.logger')
def test_on_config_updated_success(mock_logger, mock_config, setup_modules):
    """
    Test successful configuration update callback
    """
    # Create mock modules with update_config methods
    mock_module1 = MagicMock()
    mock_module1.update_config = MagicMock()
    mock_module2 = MagicMock()
    mock_module2.update_config = MagicMock()
    mock_module_without_update = MagicMock()

    setup_modules['Module1'] = mock_module1
    setup_modules['Module2'] = mock_module2
    setup_modules['ModuleWithoutUpdate'] = mock_module_without_update

    on_config_updated()

    # Verify update_config was called for modules that have it
    mock_module1.update_config.assert_called_once_with(mock_config)
    mock_module2.update_config.assert_called_once_with(mock_config)

    # Verify logging
    mock_logger.info.assert_any_call("Updating configuration for all modules...")
    mock_logger.info.assert_any_call("Updated configuration for Module1")
    mock_logger.info.assert_any_call("Updated configuration for Module2")


@patch('motor.coordinator.main.config', None)
@patch('motor.coordinator.main.logger')
def test_on_config_updated_no_config(mock_logger, setup_modules):
    """
    Test configuration update when config is None
    """
    on_config_updated()

    mock_logger.error.assert_called_once_with("Configuration is None in config update callback")
    mock_logger.info.assert_not_called()


@patch('motor.coordinator.main.config', new_callable=lambda: MagicMock())
@patch('motor.coordinator.main.logger')
def test_on_config_updated_module_exception(mock_logger, mock_config, setup_modules):
    """
    Test configuration update when a module's update_config raises exception
    """
    # Create mock module that raises exception
    mock_module = MagicMock()
    mock_module.update_config.side_effect = Exception("Update failed")

    setup_modules['FailingModule'] = mock_module

    on_config_updated()

    # Verify error was logged
    mock_logger.error.assert_called_once_with("Failed to update configuration for FailingModule: Update failed")
    mock_logger.info.assert_called()
