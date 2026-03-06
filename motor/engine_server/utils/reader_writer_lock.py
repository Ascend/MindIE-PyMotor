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

import threading
from contextlib import contextmanager
from typing import Optional, Generator


class ReadPriorityRWLock:
    def __init__(self):
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)

        self._reader_reentry: dict[int, int] = {}  # thread_id:reentry_times
        self._total_readers = 0

        self._writer_active = False
        self._writer_tid: Optional[int] = None
        self._writer_reentry = 0

    @contextmanager
    def gen_rlock(self) -> Generator[None, None, None]:
        try:
            self.acquire_read()
            yield
        finally:
            self.release_read()

    @contextmanager
    def gen_wlock(self) -> Generator[None, None, None]:
        try:
            self.acquire_write()
            yield
        finally:
            self.release_write()

    def acquire_read(self) -> None:
        current_tid = threading.get_ident()
        with self._lock:
            if self._writer_active and self._writer_tid == current_tid:
                raise RuntimeError("Cannot acquire read lock while holding write lock")

            if current_tid in self._reader_reentry:
                self._reader_reentry[current_tid] += 1
                return

            while self._writer_active:
                self._condition.wait()
            self._total_readers += 1
            self._reader_reentry[current_tid] = 1

    def release_read(self) -> None:
        current_tid = threading.get_ident()
        with self._lock:
            if current_tid not in self._reader_reentry:
                raise RuntimeError("Thread does not hold read lock")

            self._reader_reentry[current_tid] -= 1
            if self._reader_reentry[current_tid] > 0:
                return

            del self._reader_reentry[current_tid]
            self._total_readers -= 1

            if self._total_readers == 0:
                self._condition.notify_all()

    def acquire_write(self) -> None:
        current_tid = threading.get_ident()
        with self._lock:
            if self._writer_active and self._writer_tid == current_tid:
                self._writer_reentry += 1
                return

            if current_tid in self._reader_reentry:
                raise RuntimeError("Cannot acquire write lock while holding read lock")

            while self._total_readers > 0 or self._writer_active:
                self._condition.wait()
            self._writer_active = True
            self._writer_tid = current_tid
            self._writer_reentry = 1

    def release_write(self) -> None:
        current_tid = threading.get_ident()
        with self._lock:
            if not self._writer_active or self._writer_tid != current_tid:
                raise RuntimeError("Thread does not hold write lock")

            self._writer_reentry -= 1
            if self._writer_reentry > 0:
                return

            self._writer_active = False
            self._writer_tid = None
            self._condition.notify_all()
