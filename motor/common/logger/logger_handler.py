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

import glob
import gzip
import logging
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

FILE_ATTRIBUTE_SIZE = 'size'
FILE_ATTRIBUTE_CREATE_TIME = 'mtime'


class CompressedRotatingFileHandler(RotatingFileHandler):
    """
    Timestamp compression rotating processor:
    1. Rotate based on single file size
    2. After rotation, compress the old files, and include the timestamp in the file name
    3. Limit the total size of the compressed files
    """

    def __init__(self, filename, maxBytes=10 * 1024 * 1024, backupCount=10,
                 encoding='utf-8', compress=True, compress_level=6,
                 max_total_size=200 * 1024 * 1024,
                 timestamp_format='%Y-%m-%d_%H-%M-%S',
                 time_zone_info=timezone.utc,
                 keep_uncompressed_count=2,
                 cleanup_interval=1800):

        super().__init__(filename, maxBytes=maxBytes, backupCount=backupCount,
                         encoding=encoding)

        self.backup_count = backupCount
        self.compress = compress
        self.compress_level = compress_level
        self.max_total_size = max_total_size
        self.time_zone_info = time_zone_info
        self.timestamp_format = timestamp_format
        self.keep_uncompressed_count = keep_uncompressed_count
        self.cleanup_interval = cleanup_interval

        self.compress_queue = []
        self.cleanup_queue = []
        self.lock = threading.RLock()
        self.last_cleanup_time = time.time()

        self._start_background_threads()

        # first cleanup
        self._perform_cleanup()

    def doRollover(self):
        """do rollover """
        with self.lock:
            # 1. perform standard rollover
            super().doRollover()

            # 2. get rollover file
            dir_name = os.path.dirname(self.baseFilename)
            base_name = os.path.basename(self.baseFilename)

            for i in range(1, self.backup_count + 1):
                backup_file = os.path.join(dir_name, f"{base_name}.{i}")
                if os.path.exists(backup_file):
                    # 3. add compress
                    timestamp = datetime.now(tz=timezone.utc).strftime(self.timestamp_format)
                    base_name = base_name.removesuffix(".log")
                    backup_file_new_name = os.path.join(dir_name, f"{base_name}_{timestamp}.log")

                    os.rename(backup_file, backup_file_new_name)
                    if self.compress:
                        self.compress_queue.append(backup_file_new_name)
                    break

            # 4. perform cleanup
            self._perform_cleanup()

    def close(self):
        with self.lock:
            while self.compress_queue:
                file_path = self.compress_queue.pop(0)
                self._compress_file(file_path)

            while self.cleanup_queue:
                file_path = self.cleanup_queue.pop(0)
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        logging.error("Failed to compress %s", file_path, e)

        # call super close
        super().close()

    def _start_background_threads(self):
        """start background threads"""
        # compress thread
        if self.compress:
            logging.debug("Starting compression thread")
            compress_thread = threading.Thread(target=self._compression_worker, daemon=True)
            compress_thread.start()

        # cleanup thread
        cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True)
        cleanup_thread.start()

    def _compression_worker(self):
        """compress worker thread"""
        while True:
            try:
                time.sleep(2)
                self._process_compress_queue()
            except Exception as e:
                logging.error("Failed to compress", e)

    def _cleanup_worker(self):
        """cleanup worker thread"""
        while True:
            try:
                time.sleep(10)
                current_time = time.time()

                with self.lock:
                    if current_time - self.last_cleanup_time >= self.cleanup_interval:
                        self._perform_cleanup()
                        self.last_cleanup_time = current_time

                    self._process_cleanup_queue()
            except Exception as e:
                logging.error("Failed to cleanup", e)

    def _get_compressed_filename(self, file_path):
        # get dir_name and log file name
        dir_name, base_name = os.path.split(file_path)

        # construct new compressed file name: log-file-name_timestamp.gz
        compressed_name = f"{base_name}.gz"
        compressed_path = os.path.join(dir_name, compressed_name)

        return compressed_path

    def _compress_file(self, file_path):
        if not os.path.exists(file_path):
            return None

        try:
            # construct file name
            compressed_path = self._get_compressed_filename(file_path)

            # if compressed file is already exist, add random code into file_name
            counter = 1
            while os.path.exists(compressed_path):
                dir_name, compressed_name = os.path.split(compressed_path)
                name_without_ext, ext = os.path.splitext(compressed_name)
                compressed_path = os.path.join(dir_name, f"{name_without_ext}_{counter}{ext}")
                counter += 1

            # use gzip
            with open(file_path, 'rb') as f_in:
                with gzip.open(compressed_path, 'wb', compresslevel=self.compress_level) as f_out:
                    shutil.copyfileobj(f_in, f_out)

            if os.path.exists(compressed_path) and os.path.getsize(compressed_path) > 0:
                # delete old .log file
                os.remove(file_path)
                logging.debug("Successful to compress %s -> %s", file_path, compressed_path)
                return compressed_path
            else:
                if os.path.exists(compressed_path):
                    os.remove(compressed_path)
                return None

        except Exception as e:
            logging.error("Failed to compress %s", file_path, e)
            return None

    def _process_compress_queue(self):
        """process compress queue"""
        with self.lock:
            if not self.compress_queue:
                return

            file_path = self.compress_queue.pop(0)

        compressed_path = self._compress_file(file_path)
        logging.debug("Process compress queue %s", compressed_path)

    def _process_cleanup_queue(self):
        file_path_list = []
        with self.lock:
            logging.debug("Processing to cleanup: %d", len(self.cleanup_queue))
            if not self.cleanup_queue:
                return
            while self.cleanup_queue and len(self.cleanup_queue) > 0:
                file_path_list.append(self.cleanup_queue.pop(0))

        for file_path in file_path_list:
            try:
                if os.path.exists(file_path):
                    file_size = os.path.getsize(file_path)
                    os.remove(file_path)
                    logging.debug("Successful to cleanup %s, size: %.2f MB", file_path, file_size / 1024 / 1024)
            except Exception as e:
                logging.error("Failed to cleanup %s", file_path, e)

    def _get_all_related_files(self):
        dir_name = os.path.dirname(self.baseFilename)
        # temp_log_name(baseFilename) = hostname.log
        base_name = os.path.basename(self.baseFilename).removesuffix(".log")
        # so have to remove suffix(.log) to get base_name

        # Get all matching files
        pattern = os.path.join(dir_name, f"{base_name}*")
        files = []

        for file_path in glob.glob(pattern):
            # Skip the file that is currently being written.
            if file_path == self.baseFilename:
                continue

            try:
                if os.path.exists(file_path):
                    stat = os.stat(file_path)

                    compressed = False
                    if file_path.endswith('.gz'):
                        compressed = True

                    files.append({
                        'path': file_path,
                        FILE_ATTRIBUTE_SIZE: stat.st_size,
                        FILE_ATTRIBUTE_CREATE_TIME: stat.st_mtime,
                        'compressed': compressed
                    })
            except OSError:
                continue

        return files

    def _perform_cleanup(self):
        with self.lock:
            all_files = self._get_all_related_files()

            # Calculate the current total size
            total_size = sum(f[FILE_ATTRIBUTE_SIZE] for f in all_files)
            logging.debug("Performing cleanup, total size: %.2f MB, files count: %d", total_size / 1024 / 1024,
                          len(all_files))

            # If the total size does not exceed the limit, no cleaning will be performed.
            if total_size <= self.max_total_size and len(all_files) <= self.backup_count:
                return

            # Sort by modification date (the most recent ones come first)
            all_files.sort(key=lambda x: x[FILE_ATTRIBUTE_CREATE_TIME], reverse=True)

            # Make sure to retain at least the uncompressed files
            uncompressed_files = [f for f in all_files if not f['compressed']]
            keep_files = []

            if uncompressed_files:
                uncompressed_files.sort(key=lambda x: x[FILE_ATTRIBUTE_CREATE_TIME], reverse=True)
                keep_files.extend(uncompressed_files[:self.keep_uncompressed_count])

            # Calculate the number of files that need to be cleaned up
            cleanup_candidates = []
            current_total = sum(f[FILE_ATTRIBUTE_SIZE] for f in keep_files)

            for file_info in all_files:
                if file_info in keep_files:
                    continue

                # If adding this file still exceeds the limit, then it should be placed in the cleanup queue.
                if current_total + file_info[FILE_ATTRIBUTE_SIZE] > self.max_total_size or len(
                        keep_files) >= self.backup_count:
                    cleanup_candidates.append(file_info)
                else:
                    current_total += file_info[FILE_ATTRIBUTE_SIZE]
                    keep_files.append(file_info)
            for file_info in cleanup_candidates:
                self.cleanup_queue.append(file_info['path'])
