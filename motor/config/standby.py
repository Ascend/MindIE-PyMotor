# coding=utf-8
# Copyright (c) 2025, HUAWEI CORPORATION.  All rights reserved.
from dataclasses import dataclass

LOCK_SLASH = "/"


@dataclass
class StandbyConfig:
    """Standby (master/standby) configuration class"""

    # master/standby feature enable/disable
    enable_master_standby: bool = False

    # master/standby check interval in seconds
    master_standby_check_interval: int = 5

    # master lock lease TTL in seconds
    master_lock_ttl: int = 10

    # master lock retry interval in seconds
    master_lock_retry_interval: int = 5

    # max consecutive lock failures
    master_lock_max_failures: int = 3

    # master lock key path in ETCD
    # Note: controller and coordinator will automatically 
    # add "/controller/" and "/coordinator/" prefixes respectively
    master_lock_key: str = "/master_lock"
