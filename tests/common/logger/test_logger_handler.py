#  #!/usr/bin/env python3
#  -*- coding: utf-8 -*-
#  Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
#  MindIE is licensed under Mulan PSL v2.
#  You can use this software according to the terms and conditions of the Mulan PSL v2.
#  You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
#  THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
#  EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
#  MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
#  See the Mulan PSL v2 for more details.
import os
import tempfile
import unittest
from unittest.mock import patch

from motor.common.logger.logger_handler import CompressedRotatingFileHandler


class TestCompressedRotatingFileHandler(unittest.TestCase):
    def setUp(self):
        # create temp directory to store logs
        self.temp_dir = tempfile.mkdtemp()
        self.log_file = os.path.join(self.temp_dir, "test.log")

    def tearDown(self):
        # cleanup temp directory
        if os.path.exists(self.temp_dir):
            for file in os.listdir(self.temp_dir):
                print("***", file)
                os.remove(os.path.join(self.temp_dir, file))
            os.rmdir(self.temp_dir)

    @patch("motor.common.logger.logger_handler.datetime")
    def test_do_rollover_generates_timestamped_backup(self, mock_datetime):
        """
        测试日志轮转时是否生成带时间戳的备份文件。
        """
        mock_datetime.now.return_value.strftime.return_value = "2025-04-05_12-00-00"
        handler = CompressedRotatingFileHandler(self.log_file, maxBytes=1, backupCount=1, compress=False)

        # 触发轮转
        handler.doRollover()
        handler.close()

        # 验证是否生成了带时间戳的备份文件
        expected_backup = os.path.join(self.temp_dir, "test_2025-04-05_12-00-00.log")
        self.assertTrue(os.path.exists(expected_backup))

    def test_compress_file_creates_gz_file(self):
        """
        测试文件压缩是否成功生成 .gz 文件。
        """
        handler = CompressedRotatingFileHandler(self.log_file)
        test_file = os.path.join(self.temp_dir, "test1.log")
        with open(test_file, "w") as f:
            f.write("sample content")

        result = handler._compress_file(test_file)
        handler.close()

        # 验证是否调用了 gzip.open
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith(".gz"))
        expected_backup = os.path.join(self.temp_dir, "test1.log.gz")
        self.assertTrue(os.path.exists(expected_backup))

    def test_process_cleanup_queue_removes_files(self):
        """
        测试清理队列是否能正确删除文件。
        """
        handler = CompressedRotatingFileHandler(self.log_file)
        test_file = os.path.join(self.temp_dir, "test_to_delete.log")
        with open(test_file, "w") as f:
            f.write("sample content")

        handler.cleanup_queue.append(test_file)
        handler._process_cleanup_queue()
        handler.close()

        # 验证文件已被删除
        self.assertFalse(os.path.exists(test_file))

    def test_perform_cleanup_respects_size_limit(self):
        """
        测试清理逻辑是否尊重总大小限制。
        """
        handler = CompressedRotatingFileHandler(
            self.log_file,
            maxBytes=10,
            backupCount=2,
            max_total_size=50  # 50 bytes
        )

        # 创建多个大文件
        for i in range(5):
            file_path = os.path.join(self.temp_dir, f"test_{i}.log")
            with open(file_path, "w") as f:
                f.write("A" * 20)  # 每个文件20字节

        handler._perform_cleanup()
        handler._process_cleanup_queue()
        handler.close()

        # 验证清理后文件数量不超过限制
        remaining_files = [f for f in os.listdir(self.temp_dir) if f.startswith("test_")]
        print(remaining_files)
        self.assertLessEqual(len(remaining_files), 2)

    def test_close_cleans_up_resources(self):
        """
        测试关闭时是否正确清理压缩和清理队列。
        """
        handler = CompressedRotatingFileHandler(self.log_file)
        test_file = os.path.join(self.temp_dir, "test_close.log")
        with open(test_file, "w") as f:
            f.write("sample content")
        test_file_cleanup = os.path.join(self.temp_dir, "test_cleanup.log")
        with open(test_file_cleanup, "w") as f:
            f.write("sample content")

        handler.compress_queue.append(test_file)
        handler.cleanup_queue.append(test_file_cleanup)
        handler.close()

        # 验证队列为空且文件被压缩
        self.assertEqual(len(handler.compress_queue), 0)
        self.assertEqual(len(handler.cleanup_queue), 0)

        self.assertTrue(any(f.endswith(".gz") for f in os.listdir(self.temp_dir)))
        self.assertFalse(os.path.exists(test_file_cleanup))
