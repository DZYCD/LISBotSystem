#!/usr/bin/env python3
'''
Bot 启动脚本 - skaye_996 (Skaye)
使用 Skaye 专属启动模板。
'''

import os
import sys

_FILE_DIR = os.path.dirname(os.path.abspath(__file__))
LIS_V2_ROOT = os.path.dirname(os.path.dirname(_FILE_DIR))
sys.path.insert(0, LIS_V2_ROOT)

from tll_protocol.templates.start_skaye import main

if __name__ == '__main__':
    main(_FILE_DIR)
