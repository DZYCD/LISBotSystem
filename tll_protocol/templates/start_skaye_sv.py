#!/usr/bin/env python3
'''
Skaye_SV 专属启动模板
'''

import os
import sys
import json
import threading
import time
from collections import deque
from datetime import datetime

_FILE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIS_V2_ROOT = os.path.dirname(os.path.dirname(_FILE_DIR))
PARENT_DIR = os.path.dirname(LIS_V2_ROOT)
sys.path.insert(0, LIS_V2_ROOT)
sys.path.insert(0, PARENT_DIR)
sys.path.insert(0, _FILE_DIR)
sys.path.insert(0, os.path.join(_FILE_DIR, 'skills'))

from tll_protocol.bot_factory import request_bot_create
from tll_protocol.receiver import TaskReceiver
from tll_protocol.executor import TaskExecutor
from tll_protocol.task_sender import TaskSender
from tll_protocol.templates import (
    create_transport_from_options,
    create_on_message,
    setup_event_publisher,
    register_to_sv,
)
from ping_scheduler import start_ping_scheduler
from tll_protocol.trigger import create_ping_handler


class EventStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.events = deque(maxlen=200)
        self.total_events = 0

    def add(self, event):
        with self.lock:
            self.events.appendleft(event)
            self.total_events += 1

    def get_recent_events(self, limit=50):
        with self.lock:
            return list(self.events)[:limit]

    def get_stats(self):
        with self.lock:
            return {'total_events': self.total_events, 'recent_events': len(self.events)}


def start_ping_http(bot, port=8082):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import urllib.parse
    ping_handler = create_ping_handler(bot)

    class PingHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != '/api/ping':
                self.send_error(404)
                return
            query = urllib.parse.parse_qs(parsed.query)
            bot_id = query.get('bot_id', [''])[0]
            if not bot_id:
                self.send_json({'status': 'error', 'info': '缺少 bot_id'})
                return
            try:
                result = ping_handler(bot_id)
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

    server = ThreadingHTTPServer(('0.0.0.0', port), PingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f'✅ Skaye_SV ping HTTP 接口已启动: http://127.0.0.1:{port}/api/ping')


def run_skaye_sv(bot_path=None):
    if bot_path is None:
        bot_path = _FILE_DIR
    print()
    print(f'=== 启动 {bot_path} ===')
    bot = request_bot_create(bot_path)
    print(f'✅ Bot 创建成功: {bot.config.name} ({bot.config.id})')
    print(f'   已加载 skills: {list(bot.skills.keys())}')

    transport = create_transport_from_options(bot.config)
    sender = TaskSender(transport=transport, bot_id=bot.config.id, peers=bot.config.peers, group=bot.config.group)
    bot.set_sender(sender)
    setup_event_publisher(bot, sender)

    receiver = TaskReceiver(bot_id=bot.config.id, auth_key=bot.config.auth_key)
    executor = TaskExecutor(handler_map=bot.handler_map, sender=sender, bot_context=bot)

    event_store = EventStore()
    original_on_message = create_on_message(bot, receiver, executor, sender)

    def on_message(payload):
        original_on_message(payload)
        try:
            data = json.loads(payload.decode('utf-8'))
            if isinstance(data, dict) and data.get('type') == 'TLL_EVENT':
                event = data.get('event', {})
                if event.get('level') == 'debug':
                    return
                print(f"[Skaye_SV] 转发 TLL_EVENT task_id={event.get('task_id')} level={event.get('level')} msg={event.get('message')}")
                event_store.add(event)
        except Exception:
            pass

    transport.on_message = on_message
    print(f'✅ MQTT 已连接: {transport.host}:{transport.port}, 订阅 {transport.topic} + tll/Skaye_SV')

    register_to_sv(bot)
    start_ping_scheduler(bot)
    start_ping_http(bot, port=8082)

    print()
    print('等待消息... (Ctrl+C 退出)')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        transport.close()


if __name__ == '__main__':
    run_skaye_sv()
