#!/usr/bin/env python3
'''
Skaye 专属启动模板
'''

import os
sys_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys_path not in __import__('sys').path:
    __import__('sys').path.insert(0, sys_path)

from tll_protocol.templates.start import run_bot


def skaye_setup(bot):
    # 强制装载 list_eiar_robots 调用许可（仅 Skaye 族）
    if bot.config.group == 'Skaye':
        llm_conf = bot.config.llm
        if isinstance(llm_conf, dict) and 'role_prompt' in llm_conf:
            role = llm_conf['role_prompt']
            if 'list_eiar_robots' not in role:
                llm_conf['role_prompt'] = role + '\n你可以通过调用 agent/skaye_sv 的 list_eiar_robots 获取所有 EiAr 工具机器人的联系方式和工具函数。'
    return bot


def main(bot_path=None):
    if bot_path is None:
        bot_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return run_bot(bot_path, extra_setup=skaye_setup)


if __name__ == '__main__':
    main()
