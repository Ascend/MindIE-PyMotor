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
import hashlib
from typing import Any
from pydantic import BaseModel, Field

from motor.common.utils.logger import get_logger


logger = get_logger(__name__)


class PersistentState(BaseModel):
    """
    Unified persistent state with version control and data integrity

    This class provides comprehensive data persistence with version control and
    data integrity verification via checksums.
    """
    data: dict[str, Any] = Field(..., description="Data dictionary to persist")
    version: int = Field(..., description="Version number for version control")
    timestamp: float = Field(..., description="Timestamp when the state was created")
    checksum: str = Field(..., description="Checksum for data integrity verification")

    def is_valid(self) -> bool:
        """ Validate data integrity using checksum. """
        try:
            current_checksum = self.calculate_checksum()
            logger.debug("Validating data integrity - stored checksum: %s, calculated checksum: %s",
                         self.checksum, current_checksum)
            logger.debug("Validation data - version: %s, timestamp: %s", self.version, self.timestamp)

            if self.checksum == current_checksum:
                logger.debug("Checksum validation passed")
                return True
            else:
                logger.error("Validation failed - stored checksum: %s, calculated checksum: %s",
                             self.checksum, current_checksum)
                logger.debug("Data type information: %s", {k: type(v).__name__ for k, v in self.data.items()})
                return False
        except Exception as e:
            logger.error("Exception occurred during checksum validation: %s, Data content: %s", e, self.data)
            return False

    def calculate_checksum(self) -> str:
        """ Calculate checksum for data integrity verification.
        Returns:
            str: Checksum string
        """
        try:
            data_str = f"{str(list(self.data.items()))}{self.version}{self.timestamp}"
            return hashlib.sha256(data_str.encode()).hexdigest()
        except Exception as e:
            logger.error("Error calculating checksum: %s", e)
            logger.error("Data content: %s", self.data)
            return ""
