# -*- coding: utf-8 -*-
"""
TLL 统一 chat 工具 - 所有机器人共用

核心逻辑：
1. 接收文本，调用本机 LLM 生成 reply+commands 计划。
2. 若计划中含 commands，则根据数量分别处理：
   - 单命令：返回 continue，由 executor 复用同一 task 转发（保持 task_id）。
   - 多命令：将第一个命令作为 continue 转发，其余命令存入 task.command_queue，
             由队列推进逻辑逐条执行（同一 task_id），最后汇总结果。
3. 最终结果通过 executor 自动回传机制返回给调用方，不单独使用 reply 命令。
"""

import time
import json


def _wait_completion(bot, task_id, timeout=6000):
    """等待 outbound_tasks 中某个任务完成（返回 output 或抛超时）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rec = bot.outgoing_tasks.get(task_id)
        if rec and rec.get('completed_at'):
            return rec
        time.sleep(0.3)
    raise TimeoutError(f"等待任务 {task_id} 超时")


def _extract_reply(result):
    """从委托结果中提取 reply 文本（兼容多种格式）"""
    text = ''
    if isinstance(result, str):
        try:
            data = json.loads(result)
        except Exception:
            return result
    else:
        data = result
    if isinstance(data, dict):
        text = data.get('reply') or data.get('info') or ''
    elif isinstance(data, list):
        text = ', '.join(str(x) for x in data)
    return text


def _normalize_command(cmd):
    """规范化 LLM 输出的命令对象，返回 (target, command, params)"""
    if not isinstance(cmd, dict):
        return None, None, None
    if 'func_name' in cmd:
        command = cmd.get('func_name')
        raw_params = cmd.get('params', {}) or {}
        params = {}
        target = None
        for key, val in raw_params.items():
            if isinstance(val, dict) and 'value' in val:
                value = val['value']
            else:
                value = val
            if key == 'target':
                target = value
            else:
                params[key] = value
        return target, command, params
    else:
        return cmd.get('target'), cmd.get('command'), cmd.get('params', {}) or {}


def handle(params=None, bot=None, task=None, **kwargs):
    params = params or {}
    text = params.get('text', '')
    if not text:
        return {'status': 'error', 'info': '缺少 text 参数'}
    if bot is None:
        return {'status': 'error', 'info': '缺少 bot 上下文'}
    if task is not None and not getattr(task, 'original_text', None):
        task.original_text = text

    # 单例 LLM 上下文：获取历史（排除 tool 记录，仅保留 user/assistant 对话）
    history = None
    if bot.history_manager is not None:
        all_msgs = bot.history_manager.get_messages()
        history = [m for m in all_msgs if m.get('role') in ('user', 'assistant')]

    # 未启用 LLM 时直接返回错误（由 executor 自动回传）
    if bot.llm_client is None or not bot.llm_client.is_ready:
        return {'status': 'error', 'info': '该机器人未启用 LLM，无法进行自然语言对话。'}

    # 可联系机器人列表由 LLMClient 在 reload 时动态构建
    plan = bot.llm_client.plan_task(text, history=history)
    if not isinstance(plan, dict):
        plan = {'reply': str(plan), 'commands': []}

    # 保存本次对话到历史（单例上下文）
    if bot.history_manager is not None:
        bot.history_manager.add_message('user', text)
        reply_text = plan.get('reply', '') if isinstance(plan, dict) else str(plan)
        if reply_text:
            bot.history_manager.add_message('assistant', reply_text)

    commands = plan.get('commands', [])
    reply_text = plan.get('reply', '') if isinstance(plan, dict) else str(plan)
    if task is not None:
        task.output = reply_text
        task.result = reply_text

    if not commands:
        # 无委托，直接返回 reply（executor 自动将结果回传给调用方）
        return {'status': 'success', 'info': reply_text}

    # 标记对外委托过（委托方需要复核）
    task.delegated = True

    # 单命令委托：返回 continue，复用同一 task 继续转发
    if len(commands) == 1:
        target, command, cmd_params = _normalize_command(commands[0])
        if not target or not command:
            return {'status': 'success', 'info': 'invalid command in plan'}
        if command == 'chat' and not cmd_params.get('text'):
            cmd_params['text'] = text
        task.last_target = target
        return {'status': 'continue', 'next': target, 'command': command, 'params': cmd_params}

    # 多命令委托：将第一个命令作为 continue 立即转发，其余命令存入 task.command_queue
    # 队列推进由 executor 的回传前逻辑处理，使用同一 task_id 串行执行
    first_target, first_command, first_params = _normalize_command(commands[0])
    if not first_target or not first_command:
        return {'status': 'success', 'info': 'invalid command in plan'}

    # 将剩余命令存入发起者的 pending_queues，由 bot 档案负责推进
    if len(commands) > 1:
        pending = []
        for cmd in commands[1:]:
            target, command, cmd_params = _normalize_command(cmd)
            if target and command:
                if command == 'chat' and not cmd_params.get('text'):
                    cmd_params['text'] = text
                pending.append({'target': target, 'command': command, 'params': cmd_params})
        if pending:
            bot.pending_queues[task.id] = pending
    task.last_target = first_target

    # 返回 continue 转发第一个命令
    if first_command == 'chat' and not first_params.get('text'):
        first_params['text'] = text
    return {'status': 'continue', 'next': first_target, 'command': first_command, 'params': first_params}
