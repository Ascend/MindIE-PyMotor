# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

def set_process_title(
    name: str,
    suffix: str = "",
    prefix: str = "InferenceWorker-",
) -> None:
    """Set the current process title with optional suffix."""
    try:
        import setproctitle
    except ImportError:
        return

    if suffix:
        name = f"{name}_{suffix}"

    setproctitle.setproctitle(f"{prefix}::{name}")
