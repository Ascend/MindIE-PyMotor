# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

from io import StringIO
from unittest import mock
import pytest
from motor.engine_server.utils.aicore import (
    get_aicore_usage,
    _parse_usage_from_line,
    _read_first_aicore_usage_from_watch,
)


def test_parse_usage_from_line():
    assert _parse_usage_from_line("NpuID(Idx)  ChipId(Idx) AI Core(%)") is None
    assert _parse_usage_from_line("0           0           50") == 50


def test_read_first_aicore_usage_stops_after_first_row():
    lines = [
        "NpuID(Idx)  ChipId(Idx) AI Core(%)\n",
        "0           0           42\n",
        "0           1           99\n",
    ]
    mock_proc = mock.MagicMock()
    mock_proc.stdout = StringIO("".join(lines))
    mock_proc.stdout.fileno = lambda: 0
    mock_proc.poll.return_value = None

    with mock.patch("select.select", return_value=([0], [], [])):
        usage = _read_first_aicore_usage_from_watch(mock_proc)
    assert usage == 42


def test_get_aicore_usage_success():
    mock_proc = mock.MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.__enter__.return_value = mock_proc
    with mock.patch("subprocess.Popen", return_value=mock_proc):
        with mock.patch(
            "motor.engine_server.utils.aicore._read_first_aicore_usage_from_watch",
            return_value=50,
        ):
            assert get_aicore_usage() == 50
    mock_proc.kill.assert_called()


def test_get_aicore_usage_timeout():
    mock_proc = mock.MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.__enter__.return_value = mock_proc
    mock_proc.stdout = StringIO("NpuID(Idx)  ChipId(Idx) AI Core(%)\n")
    mock_proc.stdout.fileno = lambda: 0

    with mock.patch("subprocess.Popen", return_value=mock_proc):
        with mock.patch("select.select", return_value=([], [], [])):
            with pytest.raises(RuntimeError) as cm:
                get_aicore_usage()
    assert "not found" in str(cm.value)
    mock_proc.kill.assert_called()
