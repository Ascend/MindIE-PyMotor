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
from motor.common.utils.logger import get_logger

logger = get_logger(__name__)


class PasswordDecryptor:

    @staticmethod
    def decrypt(password_file: str) -> str:
        """
        Decrypt the password from the password file, and return the password as a string
        
        Args:
            password_file: The path to the password file
            
        Returns:
            The decrypted password as a string
        """
        try:
            if not os.path.exists(password_file):
                logger.error(f"Password file does not exist: {password_file}")
                return ""
            with open(password_file, "r") as f:
                password = f.read().strip()
            return password
        except Exception as e:
            logger.error(f"Failed to decrypt password: {e}")
            return ""