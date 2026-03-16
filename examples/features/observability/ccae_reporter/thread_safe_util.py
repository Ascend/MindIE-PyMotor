# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import threading
import inspect


class ThreadSafeFactory:
    @staticmethod
    def make_threadsafe_instance(type_name, *instance_args, **instance_kwargs):
        class ThreadSafeCls(type_name):
            PUBLIC_WARP_METHODS = []
            WARP_METHODS_MAP = {
                set: ['add', 'pop'],
                dict: ['get', 'items', 'values', 'keys', '__getitem__']
            }

            def __init__(self, *args, **kwargs):
                self.wrap_kwargs = {
                    'wrap_all_func': kwargs.pop('wrap_all_func', False),
                    'skip_magic_func': kwargs.pop('skip_magic_func', False)
                }
                super().__init__(*args, **kwargs)
                self.lock = threading.RLock()
                self._wrap_safe_methods(**self.wrap_kwargs)

            def _wrap_safe_methods(self, wrap_all_func=False, skip_magic_func=False):
                all_methods = [name for name, _ in inspect.getmembers(self, inspect.ismethod)]
                wrap_methods = all_methods if wrap_all_func else self.WARP_METHODS_MAP.get(type_name, [])
                for method_name in wrap_methods:
                    # 魔法方法、特殊方法是否跳过
                    if skip_magic_func and (method_name.startswith('__') or method_name.startswith('_')):
                        continue
                    # 方法不存在则跳过
                    if not hasattr(self, method_name):
                        continue
                    if callable(getattr(self, method_name)):
                        setattr(self, method_name, self._create_safe_method(method_name))

            def _create_safe_method(self, name):
                with self.lock:
                    attr = getattr(super(), name)

                def wrapper(*args, **kwargs):
                    with self.lock:
                        return attr(*args, **kwargs)
                return wrapper
        return ThreadSafeCls(*instance_args, **instance_kwargs)
