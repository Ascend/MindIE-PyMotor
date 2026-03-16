#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.

"""Unit tests for RestartLimiter (restart storm protection)."""

import time
from unittest.mock import patch

import pytest

from motor.coordinator.daemon.subprocess_supervisor import RestartLimiter, MAX_RESTART_PER_MINUTE


def test_restart_limiter_initial_can_restart():
    """Initially can_restart returns True."""
    limiter = RestartLimiter(max_per_minute=3)
    assert limiter.can_restart() is True


def test_restart_limiter_under_limit():
    """Can restart when under max_per_minute."""
    limiter = RestartLimiter(max_per_minute=5)
    for _ in range(4):
        limiter.record()
    assert limiter.can_restart() is True


def test_restart_limiter_at_limit():
    """Cannot restart when at max_per_minute."""
    limiter = RestartLimiter(max_per_minute=3)
    for _ in range(3):
        limiter.record()
    assert limiter.can_restart() is False


def test_restart_limiter_over_limit():
    """Cannot restart when over max_per_minute."""
    limiter = RestartLimiter(max_per_minute=2)
    for _ in range(5):
        limiter.record()
    assert limiter.can_restart() is False


def test_restart_limiter_window_recovery():
    """After window expires, can restart again."""
    with patch('motor.coordinator.daemon.subprocess_supervisor.RESTART_WINDOW_SECONDS', 0.02):
        limiter = RestartLimiter(max_per_minute=2)
        limiter.record()
        limiter.record()
        assert limiter.can_restart() is False
        time.sleep(0.03)  # Wait for window to expire
        assert limiter.can_restart() is True


def test_restart_limiter_default_max():
    """Default uses MAX_RESTART_PER_MINUTE."""
    limiter = RestartLimiter()
    for _ in range(MAX_RESTART_PER_MINUTE):
        limiter.record()
    assert limiter.can_restart() is False
