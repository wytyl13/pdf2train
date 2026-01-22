# Copyright (c) 2021 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import functools
import logging
import colorlog
import threading
from datetime import datetime, timedelta

class Logger(object):
    
    _instances = {}
    _lock = threading.Lock()
    def __new__(cls, name: str=None, timezone: str = 'Asia/Shanghai'):
        name = 'PaddleSpeech' if not name else name
        
        with cls._lock:
            if name not in cls._instances:
                instance = super().__new__(cls)
                cls._instances[name] = instance
            return cls._instances[name]
    
    def __init__(self, name: str=None, timezone: str = 'Asia/Shanghai'):
        name = 'PaddleSpeech' if not name else name
        
        if hasattr(self, 'logger'):
            return
        
        self.logger = logging.getLogger(name)
        log_config = {
            'DEBUG': 10,
            'INFO': 20,
            'TRAIN': 21,
            'EVAL': 22,
            'WARNING': 30,
            'ERROR': 40,
            'CRITICAL': 50,
            'EXCEPTION': 100,
        }
        
        for key, level in log_config.items():
            logging.addLevelName(level, key)
            if key == 'EXCEPTION':
                self.__dict__[key.lower()] = self.logger.exception
            else:
                self.__dict__[key.lower()] = functools.partial(self.__call__, level)
        
        
        # 自定义时间格式化函数
        def custom_time(*args):  # *args is required to be compatible with logging
            utc_time = datetime.utcnow()
            cst_time = utc_time + timedelta(hours=8)  # 北京时间是 UTC+8
            return cst_time.timetuple()  # 返回 struct_time
        
        self.formatter = colorlog.ColoredFormatter(
            "%(log_color)s[%(asctime)s] [%(levelname)-8s] [%(name)s] - %(message)s",
            datefmt='%Y-%m-%d %H:%M:%S',
            reset=True,
            log_colors={
                'DEBUG': 'cyan',
                'INFO': 'green',
                'TRAIN': 'blue',
                'EVAL': 'blue',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'red,bg_white',
                'EXCEPTION': 'red'
            },
            secondary_log_colors={
                'message': {
                    'ERROR': 'red',
                    'CRITICAL': 'red'
                }
            },
            style='%'
        )

        # 将自定义时间格式化函数添加到 formatter
        self.formatter.converter = custom_time

        self.handler = logging.StreamHandler()
        self.handler.setFormatter(self.formatter)

        self.logger.addHandler(self.handler)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

    def __call__(self, log_level: str, msg: str):
        self.logger.log(log_level, msg)

