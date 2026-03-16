#!/usr/bin/env python3
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

import os
import re
import stat

from motor.common.utils.logger import get_logger
logger = get_logger(__name__)


def safe_open(file, *args, **kwargs):
    if not PathCheck.check_path_full(file):
        raise OSError("Failed to open file %s" % file)
    return open(os.path.realpath(file), *args, **kwargs)


class PathCheck(object):

    @classmethod
    def check_path_full(cls, path: str, is_support_root: bool = True, mode: int = None):
        return cls.check_name_valid(path) and cls.check_soft_link(path) \
            and cls.check_exists(path) and cls.check_owner_group(path, is_support_root) \
            and (cls.check_path_mode(mode, path) if mode is not None else True)

    @classmethod
    def check_exists(cls, path: str):
        if not os.path.exists(path):
            return cls._log_error_and_return_false(f"The path {path} does not exist")
        return True

    @classmethod
    def check_soft_link(cls, path: str):
        if os.path.islink(os.path.normpath(path)):
            return cls._log_error_and_return_false(f"The path {path} is a soft link")
        return True

    @classmethod
    def check_owner_group(cls, path: str, is_support_root: bool = True):
        cur_user_id = os.getuid()
        cur_group_id = os.getgid()

        file_info = os.stat(path)
        file_user_id = file_info.st_uid
        file_group_id = file_info.st_gid

        is_owner_match = file_user_id == cur_user_id and file_group_id == cur_group_id
        is_root_owned = file_user_id == 0 and file_group_id == 0

        if is_owner_match or (is_support_root and is_root_owned):
            return True

        return cls._log_error_and_return_false(f"Check the path {path} owner and group failed")

    @classmethod
    def check_path_mode(cls, mode: int, path: str):
        cur_stat = os.stat(path)
        cur_mode = stat.S_IMODE(cur_stat.st_mode)

        if cur_mode == mode:
            return True

        return cls._log_error_and_return_false(
            f"Check the path {path} mode failed, expected mode: {oct(mode)}, current mode: {oct(cur_mode)}"
        )

    @classmethod
    def check_name_valid(cls, path: str):
        if not path:
            return cls._log_error_and_return_false(f"The path {path} is empty")

        if len(path) > 2048:
            return cls._log_error_and_return_false(f"The length of path {path} exceeds 2048 characters")

        pattern_name = re.compile(r"[^0-9a-zA-Z_./-]")
        if pattern_name.findall(path):
            return cls._log_error_and_return_false(f"The path {path} contains special characters")

        return True

    @classmethod
    def _log_error_and_return_false(cls, error_message: str) -> bool:
        logger.error(error_message)
        return False