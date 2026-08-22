#!/usr/bin/env python3
'''
EiAr 专属启动模板
'''

import os
sys_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys_path not in __import__('sys').path:
    __import__('sys').path.insert(0, sys_path)

from tll_protocol.templates.start import run_bot


def main(bot_path=None):
    if bot_path is None:
        bot_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return run_bot(bot_path)


if __name__ == '__main__':
    main()
