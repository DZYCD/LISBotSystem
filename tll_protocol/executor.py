import os
import inspect
from .core import STATUS_COLORS


# 状态转换统一由 Task._set_status 处理，此函数已废弃
from datetime import datetime
from typing import Callable, Dict, Optional
from .core import Task, TaskStatus, create_logger


class TaskExecutor:
    def __init__(self, handler_map: Dict[str, Callable], sender=None, bot_context=None, local_logger=None):
        self.handler_map = handler_map or {}
        self.sender = sender
        self.bot_context = bot_context
        self.local_logger = local_logger

    def check_task(self, task):
        return task is not None and task.tlljson and task.tlljson.command in self.handler_map

    def authorize(self, task):
        if task is None or task.tlljson is None:
            return False

        # 未加载工具，拒绝
        if not self.bot_context:
            return False

        command = task.tlljson.command
        skill_info = self.bot_context.skills.get(command)
        if skill_info is None:
            return False

        access = skill_info.get('access', {})
        if not access:
            return True  # 未配置 access，默认允许

        allow = access.get('allow', [])  # 精确 bot ID 白名单
        allow_groups = access.get('allow_groups', [])  # 组白名单
        deny = access.get('deny', [])  # 精确 bot ID 黑名单
        deny_groups = access.get('deny_groups', [])  # 组黑名单

        caller_id = task.from_bot
        caller_group = task.sender_group or ''

        # 黑名单优先，命中即拒绝
        if caller_id in deny or (caller_group and caller_group in deny_groups) or '*' in deny or '*' in deny_groups:
            return False

        # 白名单非空时，未命中则拒绝
        if allow or allow_groups:
            if caller_id in allow or (caller_group and caller_group in allow_groups) or '*' in allow or '*' in allow_groups:
                return True
            return False

        # 白名单为空，默认允许
        return True

    def _ensure_logger(self, task):
        bot_hook_manager = self.bot_context.hook_manager if self.bot_context else None
        if not getattr(task, 'logger', None):
            create_logger(task, hook_manager=bot_hook_manager)
        task.logger.hook_manager = bot_hook_manager
        task.logger.context['sender'] = self.sender
        bot_id = self.bot_context.config.id if self.bot_context else ''
        task.logger.context['bot_id'] = bot_id
        task.logger.context['hook_manager'] = bot_hook_manager
        if self.bot_context and getattr(self.bot_context, 'base_dir', None):
            task.logger.context['archive_dir'] = os.path.join(self.bot_context.base_dir, 'tasks')
        return task.logger

    def execute(self, task):
        # 执行前确保 current_agent 为当前机器人
        if self.bot_context:
            task.current_agent = self.bot_context.config.id
        if not self.check_task(task):
            logger = self._ensure_logger(task)
            task.set_failed("unknown command or missing tlljson")
            logger.error(f"failed: {task.error}")
            return task
        if not self.authorize(task):
            logger = self._ensure_logger(task)
            task.set_failed("not authorized to use tool: " + task.tlljson.command)
            logger.error(f"failed: {task.error}")
            return task

        command = task.tlljson.command
        handler = self.handler_map[command]
        params = task.tlljson.params
        logger = self._ensure_logger(task)
        bot_id = self.bot_context.config.id if self.bot_context else ''
        task._set_status(TaskStatus.RUNNING)
        logger.info("开始执行任务")
        task.local_executed = True  # 标记当前节点确实执行了 handler
        try:
            result = None
            try:
                sig = inspect.signature(handler)
                if 'bot' in sig.parameters:
                    result = handler(params, bot=self.bot_context, task=task)
                elif 'task' in sig.parameters:
                    result = handler(params, task=task)
                else:
                    result = handler(params)
            except TypeError:
                result = handler(params)

            # 解析工具返回的统一 JSON 格式
            if isinstance(result, dict) and 'status' in result:
                status = result['status']
                info = result.get('info', '')
                if status == 'success':
                    # 仅记录结果，不设 SUCCESS；最终状态由 process_return 决定
                    task.output = info
                    task.result = info
                    task.logger.context['executed'] = True
                    task.logger.success(f"executed successfully: {info}")
                    # 工具调用记录已移除，不再写入历史
                elif status == 'error':
                    task.set_failed(info)
                    task.logger.error(f"failed: {info}")
                elif status == 'continue':
                    next_bot = result.get('next')
                    if not next_bot:
                        task.set_failed("continue without next")
                        task.logger.error(f"failed: continue without next")
                    else:
                        # 允许在转发时更新命令和参数，保持同一个 task 传递
                        new_command = result.get('command')
                        new_params = result.get('params')
                        if new_command:
                            task.tlljson.command = new_command
                        if new_params is not None:
                            task.tlljson.params = new_params
                        task.tlljson.to = next_bot
                        task.current_agent = next_bot
                        task._set_status(TaskStatus.DELEGATED)
                        task.trace.add_hop(bot=bot_id, action=f"continue_to_{next_bot}")
                        logger.info(f"continue to {next_bot}")
                        # 保留原 output/result（LLM 回复），不修改为委托去向
                        if self.sender is None:
                            task.set_failed("no sender available for continue")
                            task.logger.error(f"Task {task.id} failed: no sender")
                        else:
                            # 统一使用 send_command，登记 outgoing_tasks 并委托计数+1
                            if self.bot_context is not None and hasattr(self.bot_context, 'send_command'):
                                self.bot_context.send_command(task, next_bot)
                            else:
                                task.delegate_count = getattr(task, 'delegate_count', 0) + 1
                                self.sender.send_task(task, next_bot, push_route=True)
                            logger.info(f"任务结束：【{task.output or task.result or ''}】")
                            return task
                else:
                    task.set_failed(f"unknown status: {status}")
                    task.logger.error(f"Task {task.id} failed: unknown status {status}")
            else:
                # 仅记录结果，不设 SUCCESS
                task.output = result
                task.result = result
                task.logger.context['executed'] = True
                task.logger.success(f"executed successfully (pending)")
                # 工具调用记录已移除，不再写入历史

            if self.local_logger:
                if task.status == TaskStatus.SUCCESS:
                    self.local_logger.success(f"executed successfully")
                elif task.status == TaskStatus.FAILED:
                    self.local_logger.error(f"failed: {task.error}")

        except Exception as e:
            task.set_failed(str(e))
            task.logger.error(f"failed: {e}")
            if self.local_logger:
                self.local_logger.error(f"failed: {e}")

        if task.status not in (TaskStatus.FAILED, TaskStatus.DELEGATED, TaskStatus.CHECK_REVIEW):
            task._set_status(TaskStatus.PENDING)
        _end_result = task.output or task.result or task.error or ''
        logger.info(f"任务结束：【{_end_result}】")
        task.trace.add_hop(bot=bot_id or getattr(task, 'current_agent', 'unknown'), action='execute')

        # 回传统一由 process_return 处理，这里不再自动回传
        return task

    def process_return(self, task):
        '''统一处理回传：execute 后无条件调用，仅 RETURNING 状态复核，其余正常回传'''
        bot_id = self.bot_context.config.id if self.bot_context else ''
        logger = self._ensure_logger(task)

        if task.status in (TaskStatus.RETURNING, TaskStatus.FAILED):
            if not hasattr(task, 'queue_results'):
                task.queue_results = []
            _req_text = ''
            if task.tlljson and task.tlljson.params:
                _req_text = task.tlljson.params.get('text') or task.tlljson.params.get('query') or ''
            task.queue_results.append({
                'target': getattr(task, 'last_target', ''),
                'command': task.tlljson.command if task.tlljson else '',
                'request': _req_text,
                'status': task.status.value,
                'output': task.output,
                'error': task.error,
                'result': task.result,
            })
            # 同时记录到 bot 的队列复核记录（复核时拼接并清空，保证每次都是新委托结果）
            if self.bot_context is not None and hasattr(self.bot_context, 'queue_records'):
                self.bot_context.queue_records.setdefault(task.id, []).append({
                    'target': getattr(task, 'last_target', ''),
                    'command': task.tlljson.command if task.tlljson else '',
                    'request': _req_text,
                    'status': task.status.value,
                    'output': task.output,
                    'error': task.error,
                    'result': task.result,
                })

        # 若当前节点未执行任何 handler（仅转发/复核），设置占位输出便于追踪
        if not task.local_executed and not task.output and not task.result and not task.error and task.status not in (TaskStatus.DELEGATED, TaskStatus.CHECK_REVIEW):
            bot_id = self.bot_context.config.id if self.bot_context else ''
            task.output = f"Node {bot_id} did not execute (relay/review only)"



        # 委托方收到回传状态时，若还有剩余命令队列，先串行推进下一个，等待其回传后再复核
        if task.status in (TaskStatus.RETURNING, TaskStatus.FAILED):
            if self.bot_context is not None:
                pending = getattr(self.bot_context, 'pending_queues', {}).get(task.id)
                if pending:
                    next_cmd = pending.pop(0)
                    next_bot = next_cmd['target']
                    # 关键修复：清空上一轮的所有结果状态，避免残留污染下一个子任务
                    task.output = None
                    task.result = None
                    task.error = None
                    task.queue_results = []
                    task.original_text = None
                    task.tlljson.command = next_cmd['command']
                    task.tlljson.params = next_cmd['params']
                    task.tlljson.to = next_bot
                    task.current_agent = next_bot
                    task.trace.add_hop(bot=bot_id, action='queue_continue_to_' + next_bot)
                    logger.info(f'queue continue to {next_bot}')
                    task._set_status(TaskStatus.PENDING)
                    if self.bot_context and hasattr(self.bot_context, 'send_command'):
                        self.bot_context.send_command(task, next_bot)
                    else:
                        self.sender.send_task(task, next_bot, push_route=True)
                    return
                else:
                    self.bot_context.pending_queues.pop(task.id, None)
            logger.info(f"entering review")
            original_text = getattr(task, 'original_text', '')
            if (original_text and self.bot_context and self.bot_context.llm_client
                    and self.bot_context.llm_client.is_ready and getattr(task, 'delegate_count', 0) <= 100):
                task._set_status(TaskStatus.CHECK_REVIEW)
                queue_records = []
                if self.bot_context is not None and hasattr(self.bot_context, 'queue_records'):
                    queue_records = self.bot_context.queue_records.pop(task.id, [])
                review_results = ''
                if queue_records:
                    lines = []
                    for r in queue_records:
                        req = r.get('request') or ''
                        res = r.get('result') or r.get('error') or r.get('output') or ''
                        if req:
                            lines.append(f"[请求] {req}\n[结果] {res}")
                        else:
                            lines.append(f"[{r['target']}] {res}")
                    review_results = '\n' + '\n'.join(lines)
                else:
                    review_results = task.output or task.error or ''
                review_text = f"工具执行结果：\n{review_results}\n如果结果已经满足请求，请直接输出最终回复；如果不满足或需要补充，请继续处理。"
                task.tlljson.command = 'chat'
                task.tlljson.params = {'text': review_text}
                task.current_agent = bot_id
                logger.info(f'entering review')
                self.execute(task)
                if task.status == TaskStatus.DELEGATED:
                    logger.info(f"复核后继续委托给 {task.current_agent}")
                    # 复核时继续委托，已转发，等待回传
                    return
                elif task.status == TaskStatus.CHECK_REVIEW:
                    review_output = task.output or task.result or ''
                    if isinstance(review_output, dict):
                        review_output = review_output.get('reply', review_output)
                    task.output = review_output
                    task.result = review_output
                    logger.info(f"复核输出：{review_output}")
                    # 复核完成且未继续委托，状态转为 PENDING 以便回传
                    task._set_status(TaskStatus.PENDING)

        # DELEGATED 已委托下级，等待回传；CHECK_REVIEW 复核中，均跳过回退
        if task.status in (TaskStatus.DELEGATED, TaskStatus.CHECK_REVIEW):
            return

        # 所有子任务完成或失败，构造最终结果（仅使用当前节点真实输出，防止旧 queue_results 跨节点污染）
        final_info = task.output or task.error or ''

        task.output = final_info
        if task.error:
            task.error = final_info
        else:
            task.result = final_info

        # CHECK_REVIEW 状态跳过回退（复核中）
        if task.status == TaskStatus.CHECK_REVIEW:
            return

        # 回传：如果还有上一级则设置 RETURNING 并发送；否则到达最终节点，设置最终状态
        return_to = None
        if task.route:
            return_to = task.route.pop()
            if return_to == bot_id:
                return_to = task.from_bot
        elif task.from_bot and task.from_bot != bot_id:
            return_to = task.from_bot

        if return_to and self.sender:
            pass
            task.trace.add_hop(bot=bot_id, action='return_to_' + return_to)
            if task.tlljson:
                task.tlljson.to = return_to
            task.current_agent = return_to
            task._set_status(TaskStatus.RETURNING)
            self.sender.send_task(task, return_to, push_route=False)
            pass
        else:
            # 最终节点：根据 error 是否为空决定成败
            if task.error:
                task._set_status(TaskStatus.FAILED)
                task.logger.error(f"委托完成，结果：{task.error}")
            else:
                task._set_status(TaskStatus.SUCCESS)
                task.logger.success(f"委托完成，结果：{task.output}")
            task.trace.add_hop(bot=bot_id, action='finalize')
            logger.info(f'finalized with status={task.status.value}')
            # 强制归档，确保任务被销毁
            # 归档前回调：允许创建任务的bot在任务结束前执行自定义逻辑（如保存信息更新状态）
            if self.bot_context is not None:
                archive_cb = getattr(self.bot_context, '_archive_callbacks', {}).pop(task.id, None)
                if archive_cb is not None:
                    try:
                        if not hasattr(task, 'bot'):
                            task.bot = self.bot_context
                        archive_cb(task)
                    except Exception as e:
                        logger.error(f"归档前回调执行失败: {e}")
            try:
                logger.archive(task)
            except Exception as e:
                logger.error(f"归档失败: {e}")
