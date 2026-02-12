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

from dataclasses import dataclass


@dataclass
class TLSConfig:
    """TLS configuration class"""

    # TLS enable/disable
    enable_tls: bool = False

    # certificate paths
    ca_file: str = 'security/mgmt/cert/ca.crt'
    cert_file: str = 'security/mgmt/cert/server.crt'
    key_file: str = 'security/mgmt/keys/server.key'
    passwd_file: str = 'security/mgmt/keys/key_pwd.txt'
    crl_file: str = ''

    @classmethod
    def from_dict(cls, config_dict):
        """Update configuration object fields from dictionary, only for existing keys"""
        return cls(**{k: v for k, v in config_dict.items() if k in cls.__dataclass_fields__})