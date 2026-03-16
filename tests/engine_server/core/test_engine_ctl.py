# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import pytest
from motor.engine_server.core.engine_ctl import EngineController


class ConcreteEngineController(EngineController):
    """Concrete implementation of EngineController for testing"""
    
    def __init__(self):
        self.control_called = False
        self.stop_called = False
        self.last_cmd = None
        
    def control(self, cmd):
        self.control_called = True
        self.last_cmd = cmd
        return f"Processed command: {cmd}"
    
    def stop(self):
        self.stop_called = True
        return "Engine stopped"


class TestEngineController:
    """Tests for EngineController abstract base class"""
    
    def test_engine_controller_instantiation_fails(self):
        """Test that instantiating the abstract EngineController directly raises TypeError"""
        with pytest.raises(TypeError) as excinfo:
            EngineController()
        
        # Check that the error message indicates abstract methods are not implemented
        assert "abstract method" in str(excinfo.value)
    
    def test_concrete_implementation_instantiation(self):
        """Test that concrete implementation can be instantiated"""
        controller = ConcreteEngineController()
        assert isinstance(controller, EngineController)
        assert isinstance(controller, ConcreteEngineController)
    
    def test_control_method_called(self):
        """Test that control method is called correctly"""
        controller = ConcreteEngineController()
        test_cmd = "START"
        
        result = controller.control(test_cmd)
        
        assert controller.control_called is True
        assert controller.last_cmd == test_cmd
        assert result == f"Processed command: {test_cmd}"
    
    def test_stop_method_called(self):
        """Test that stop method is called correctly"""
        controller = ConcreteEngineController()
        
        result = controller.stop()
        
        assert controller.stop_called is True
        assert result == "Engine stopped"
