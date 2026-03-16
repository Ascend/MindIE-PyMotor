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
from unittest import mock
import os as real_os
import sys


class TestPrometheusUtils:
    """Tests for prometheus multiprocess setup utility"""

    @pytest.fixture(autouse=True)
    def fresh_module(self):
        module_name = 'motor.engine_server.utils.prometheus'
        sys.modules.pop(module_name, None)
        real_os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)
        yield
        real_os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)
        sys.modules.pop(module_name, None)
        if module_name in sys.modules:
            prom_module = sys.modules[module_name]
            prom_module._prometheus_multiproc_dir = None

    def test_setup_multiprocess_prometheus_tempdir_creation_error(self, monkeypatch):
        """Test error handling when temp dir creation fails"""
        mock_logger = mock.MagicMock()
        import motor.engine_server.utils.prometheus as prom_module
        monkeypatch.setattr(
            prom_module,
            'get_logger',
            lambda name: mock_logger
        )

        # Mock os.environ
        mock_environ = {}
        mock_os = mock.MagicMock()
        mock_os.environ = mock_environ
        monkeypatch.setattr(
            prom_module,
            'os',
            mock_os
        )

        monkeypatch.setattr(
            'tempfile.TemporaryDirectory',
            mock.MagicMock(side_effect=OSError("Permission denied"))
        )

        from motor.engine_server.utils.prometheus import setup_multiprocess_prometheus

        with pytest.raises(OSError) as excinfo:
            setup_multiprocess_prometheus()

        assert "Permission denied" in str(excinfo.value)
        assert "PROMETHEUS_MULTIPROC_DIR" not in mock_environ

        from motor.engine_server.utils import prometheus
        assert prometheus._prometheus_multiproc_dir is None