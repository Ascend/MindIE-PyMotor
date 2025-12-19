#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

import ipaddress


def ip_valid_check(ip_str: str) -> None:
    try:
        parsed_ip = ipaddress.ip_address(ip_str)
    except ValueError as e:
        raise ValueError(f"{ip_str} parse to ip failed") from e

    if parsed_ip.is_unspecified:
        raise ValueError(f"{ip_str} is all zeros ip")

    if parsed_ip.is_multicast:
        raise ValueError(f"{ip_str} is multicast ip")


def port_valid_check(port: int) -> None:
    if port < 1024 or port > 65535:
        raise ValueError(f"{port} port must be between 1024 and 65535")


def is_valid_ipv6_address(address: str) -> bool:
    try:
        ipaddress.IPv6Address(address)
        return True
    except ValueError:
        return False
