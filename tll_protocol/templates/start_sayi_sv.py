#!/usr/bin/env python3
'''
SaYi_SV 专属启动模板
'''

import os
import sys
import json
import queue
import time
import threading
from datetime import datetime

_FILE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIS_V2_ROOT = os.path.dirname(os.path.dirname(_FILE_DIR))
PARENT_DIR = os.path.dirname(LIS_V2_ROOT)
sys.path.insert(0, LIS_V2_ROOT)
sys.path.insert(0, PARENT_DIR)
sys.path.insert(0, os.path.join(_FILE_DIR, 'skills'))

from tll_protocol.sv_node import SVNodeBase
from tll_protocol.trigger import create_chat_handler


class SaYiSV(SVNodeBase):
    def __init__(self, bot_path=None):
        if bot_path is None:
            bot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'bots', 'sayi_sv')
        super().__init__(bot_path, node_name='SaYi_SV')
        self._current_queue = None
        self.task_results = {}
        self._result_lock = threading.Lock()

    def handle_task(self, task, logger):
        print(f"[SaYi_SV][archive] task_id={task.id} result={getattr(task, 'result', None)}")
        if task.tlljson and (task.tlljson.command == 'reply' or task.id in self.bot.outgoing_tasks):
            params = task.tlljson.params or {}
            if task.tlljson.command != 'reply':
                params = {'reply': task.output or task.error or '', 'task_id': task.id}
            result_info = {
                'task_id': task.id,
                'target': task.from_bot,
                'status': task.status.value if hasattr(task.status, 'value') else str(task.status),
                'output': task.output,
                'result': task.result,
                'error': task.error,
                'reply': params.get('reply', ''),
                'updated_at': datetime.now().isoformat(),
            }
            with self._result_lock:
                self.task_results[task.id] = result_info
            if self._current_queue is not None:
                self._current_queue.put(params)
            return
        if task.tlljson:
            print(f"[SaYi_SV] 收到其他消息: {task.tlljson.command}")

    def send_and_wait(self, bot_id, command, params, timeout=6000):
        self._current_queue = queue.Queue()
        task = self.bot.create_task(bot_id, command, params)
        self.bot.send_command(task)
        try:
            result = self._current_queue.get(timeout=timeout)
            return result
        finally:
            self._current_queue = None

    def process_result(self, result):
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                return result
        if not isinstance(result, dict):
            return result
        commands = result.get('commands', [])
        if commands:
            print(f"[SaYi_SV] 检测到 {len(commands)} 条委托，继续下发...")
            final_reply = result.get('reply', '')
            for cmd in commands:
                if not isinstance(cmd, dict):
                    continue
                target = cmd.get('target')
                command = cmd.get('command')
                params = cmd.get('params', {})
                if not target or not command:
                    continue
                sub_result = self.send_and_wait(target, command, params)
                sub_reply = self.process_result(sub_result)
                final_reply = f"{final_reply}\n[子结果] {sub_reply}"
            return final_reply
        else:
            return result.get('reply', '')

    def get_task_result(self, task_id):
        with self._result_lock:
            info = self.task_results.get(task_id)
        return {'status': 'found', 'task': info} if info else {'status': 'not_found', 'task_id': task_id}

    def send_chat(self, bot_id, text):
        print(f"[SaYi_SV] 异步发送给 {bot_id}: {text}")
        task = self.bot.create_task(bot_id, 'chat', {'text': text})
        self.bot.send_command(task)
        return task.id

    def converse(self, bot_id, text):
        result = self.send_and_wait(bot_id, 'chat', {'text': text})
        return self.process_result(result)

    def _start_chat_http(self, port=8081, handler=None, result_getter=None):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import urllib.parse

        class ChatHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == '/api/task_result':
                    query = urllib.parse.parse_qs(parsed.query)
                    task_id = query.get('task_id', [''])[0]
                    if not task_id:
                        self.send_json({'status': 'error', 'info': '缺少 task_id'})
                        return
                    result = result_getter(task_id) if result_getter else {'status': 'error', 'info': 'no result_getter'}
                    self.send_json(result)
                    return
                if parsed.path != '/api/chat':
                    self.send_error(404)
                    return
                query = urllib.parse.parse_qs(parsed.query)
                target = query.get('target', [''])[0]
                text = query.get('text', [''])[0]
                if not target or not text:
                    self.send_json({'status': 'error', 'info': '缺少 target 或 text'})
                    return
                try:
                    result = handler(target, text)
                    self.send_json(result)
                except Exception as e:
                    self.send_json({'status': 'error', 'info': str(e)})

            def send_json(self, data):
                body = json.dumps(data, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(('0.0.0.0', port), ChatHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"[SaYi_SV] 聊天 HTTP 接口已启动: http://127.0.0.1:{port}/api/chat")


    def start(self):
        print(f"[SaYi_SV] 已启动，订阅 {self.transport.topic}")
        print("[SaYi_SV] 可用技能: {}".format(list(self.bot.skills.keys())))
        try:
            info = self.bot.build_registration_info()
            task = self.bot.create_task('agent/skaye_sv', 'record_lis', info)
            self.bot.send_command(task)
            print("[SaYi_SV] 已自动注册到 Skaye_SV")
        except Exception as e:
            print(f"[SaYi_SV] 自动注册失败: {e}")
        chat_handler = create_chat_handler(self)
        self.chat_handler = chat_handler
        self._start_chat_http(port=8081, handler=chat_handler, result_getter=self.get_task_result)
        threading.Thread(target=self.run_cli, daemon=True).start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.transport.close()

    def run_cli(self):
        print("\n可联系的机器人:")
        for bot_id in self.bot.config.peers:
            print(f"  - {bot_id}")
        while True:
            try:
                cmd = input("\n请输入选择或指令 (exit退出): ").strip()
                if cmd.lower() == 'exit':
                    break
                if cmd in self.bot.config.peers:
                    text = input("输入对话内容: ").strip()
                    if not text:
                        continue
                    reply = self.converse(cmd, text)
                    print(f"\n[SaYi_SV] 最终回复: {reply}\n")
                elif cmd == 'list':
                    for bot_id in self.bot.config.peers:
                        print(f"  - {bot_id}")
                else:
                    print("无效输入。输入 bot ID 开始对话，输入 list 重新列出。")
            except KeyboardInterrupt:
                print("\n退出")
                break
            except Exception as e:
                print(f"[错误] {e}")


def run_sayi_sv(bot_path=None):
    if bot_path is None:
        bot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'bots', 'sayi_sv')
    bot_path = os.path.abspath(bot_path)
    if bot_path not in sys.path:
        sys.path.insert(0, bot_path)
    skills_path = os.path.join(bot_path, 'skills')
    if skills_path not in sys.path:
        sys.path.insert(0, skills_path)
    sv = SaYiSV(bot_path)
    sv.start()


if __name__ == '__main__':
    run_sayi_sv()
