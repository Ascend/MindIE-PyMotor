# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import os
import tempfile

from motor.common.utils.logger import get_logger

logger = get_logger("engine_server")

_prometheus_multiproc_dir: tempfile.TemporaryDirectory | None = None


def setup_multiprocess_prometheus():
    global _prometheus_multiproc_dir

    if "PROMETHEUS_MULTIPROC_DIR" not in os.environ:
        # Make TemporaryDirectory for prometheus multiprocessing
        # Note: global TemporaryDirectory will be automatically
        # cleaned up upon exit.
        _prometheus_multiproc_dir = tempfile.TemporaryDirectory()
        os.environ["PROMETHEUS_MULTIPROC_DIR"] = _prometheus_multiproc_dir.name
        logger.info(
            "Created PROMETHEUS_MULTIPROC_DIR at %s", _prometheus_multiproc_dir.name
        )
    else:
        logger.warning(
            "Found PROMETHEUS_MULTIPROC_DIR was set by user. "
            "This directory must be wiped between engine_server runs or "
            "you will find inaccurate metrics. Unset the variable "
            "and engine_server will properly handle cleanup."
        )
