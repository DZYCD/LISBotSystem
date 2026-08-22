#!/usr/bin/env python3
"""
独立数据大屏服务器 - 不再依附于任何 SV，通过 HTTP 转发调用 SV 的异步接口
"""

import os
import sys
import json
import re
import threading
import time
import queue
import urllib.request
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_FILE_DIR = os.path.dirname(os.path.abspath(__file__))
LIS_V2_ROOT = os.path.dirname(_FILE_DIR)
PARENT_DIR = os.path.dirname(LIS_V2_ROOT)
sys.path.insert(0, LIS_V2_ROOT)
sys.path.insert(0, PARENT_DIR)
# record_lis 是 Skaye_SV 的 skill，加入其 skills 目录
SKAYE_SV_DIR = os.path.join(LIS_V2_ROOT, 'bots', 'skaye_sv')
SKAYE_SV_SKILLS_DIR = os.path.join(SKAYE_SV_DIR, 'skills')
sys.path.insert(0, SKAYE_SV_SKILLS_DIR)

from record_lis.tool import get_registered_bots

DASHBOARD_HTML = os.path.join(_FILE_DIR, 'dashboard.html')
PING_SV_URL = 'http://127.0.0.1:8082/api/ping'
CHAT_SV_URL = 'http://127.0.0.1:8081/api/chat'
TASK_RESULT_URL = 'http://127.0.0.1:8081/api/task_result'


class EventStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.events = deque(maxlen=200)
        self.total_events = 0
        self.subscribers = []

    def add(self, event):
        with self.lock:
            self.events.appendleft(event)
            self.total_events += 1
            for sub in self.subscribers:
                try:
                    sub.put(event)
                except Exception:
                    pass

    def get_recent(self, limit=50):
        with self.lock:
            return list(self.events)[:limit]

    def get_stats(self):
        with self.lock:
            return {'total': self.total_events, 'recent_events': len(self.events)}

    def subscribe(self):
        q = queue.Queue(maxsize=100)
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)



class DashboardHandler(BaseHTTPRequestHandler):
    event_store = None

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/api/static':
            self._send_avatar()
            return
        if path == '/api/events/stream':
            self._handle_sse()
            return
        if path in ('/', '/index.html'):
            self._send_file(DASHBOARD_HTML)
        elif path == '/api/register':
            try:
                bots = get_registered_bots()
                self._send_json(bots)
            except Exception as e:
                self._send_json({'error': str(e)})
        elif path == '/api/stats':
            self._send_json(self.event_store.get_stats())
        elif path == '/api/events':
            limit = 50
            if 'limit=' in self.path:
                try:
                    limit = int(self.path.split('limit=')[-1].split('&')[0])
                except ValueError:
                    pass
            self._send_json(self.event_store.get_recent(limit))

        elif path == '/api/ping':
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            bot_id = query.get('bot_id', [''])[0]
            if not bot_id:
                self._send_json({'status': 'error', 'info': '缺少 bot_id'})
                return
            try:
                resp = urllib.request.urlopen(PING_SV_URL + '?bot_id=' + urllib.parse.quote(bot_id), timeout=6000)
                data = json.loads(resp.read().decode('utf-8'))
                self._send_json(data)
            except Exception as e:
                self._send_json({'status': 'error', 'info': str(e)})
        elif path == '/api/chat':
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            target = query.get('target', [''])[0]
            text = query.get('text', [''])[0]
            if not target or not text:
                self._send_json({'status': 'error', 'info': '缺少 target 或 text'})
                return
            try:
                full_url = CHAT_SV_URL + '?target=' + urllib.parse.quote(target) + '&text=' + urllib.parse.quote(text)
                resp = urllib.request.urlopen(full_url, timeout=6000)
                data = json.loads(resp.read().decode('utf-8'))
                self._send_json(data)
            except Exception as e:
                self._send_json({'status': 'error', 'info': str(e)})
        elif path == '/api/task_result':
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            task_id = query.get('task_id', [''])[0]
            if not task_id:
                self._send_json({'status': 'error', 'info': '缺少 task_id'})
                return
            try:
                full_url = TASK_RESULT_URL + '?task_id=' + urllib.parse.quote(task_id)
                resp = urllib.request.urlopen(full_url, timeout=6000)
                data = json.loads(resp.read().decode('utf-8'))
                self._send_json(data)
            except Exception as e:
                self._send_json({'status': 'error', 'info': str(e)})
        elif path.startswith('/api/static/'):
            self._send_static(path)
        else:
            self.send_error(404)

    def _send_avatar(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        bot_id = query.get('bot_id', [''])[0]
        if not bot_id:
            self.send_error(400, '缺少 bot_id')
            return
        import yaml
        bot_name = bot_id.split('/')[-1]
        yaml_path = os.path.join(LIS_V2_ROOT, 'bots', bot_name, 'bot.yaml')
        try:
            with open(yaml_path, 'r', encoding='utf-8') as f:
                cfg = yaml.safe_load(f)
            avatar_rel = cfg.get('avatar', '')
            if not avatar_rel:
                self.send_error(404, '未配置立绘路径')
                return
            file_path = os.path.join(LIS_V2_ROOT, 'bots', bot_name, avatar_rel)
            if not os.path.exists(file_path):
                self.send_error(404, '立绘文件不存在')
                return
            with open(file_path, 'rb') as f:
                content = f.read()
            content_type = 'image/png'
            if file_path.lower().endswith(('.jpg', '.jpeg')):
                content_type = 'image/jpeg'
            elif file_path.lower().endswith('.gif'):
                content_type = 'image/gif'
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
        except Exception as e:
            self.send_error(500, str(e))

    def _handle_sse(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        q = self.event_store.subscribe()
        try:
            while True:
                event = q.get()
                data = json.dumps(event, ensure_ascii=False)
                self.wfile.write('data: {}\n\n'.format(data).encode('utf-8'))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            self.event_store.unsubscribe(q)

    def _send_file(self, path):
        try:
            with open(path, 'rb') as f:
                content = f.read()
            self.send_response(200)
            if path.endswith('.html'):
                self.send_header('Content-Type', 'text/html; charset=utf-8')
            else:
                self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
        except Exception as e:
            self.send_error(500, str(e))

    def _send_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _send_static(self, path):
        from urllib.parse import unquote
        parts = [p for p in path.split('/') if p]
        # 路径格式: /api/static/.../<bot_name>/<img_name>
        if len(parts) >= 4 and parts[0] == 'api' and parts[1] == 'static':
            bot_name = parts[-2]
            img_name = unquote(parts[-1])
            bot_dir = os.path.join(LIS_V2_ROOT, 'bots', bot_name)
            candidates = [
                os.path.join(bot_dir, 'static', img_name),
                os.path.join(bot_dir, 'assets', img_name),
                os.path.join(bot_dir, img_name),
            ]
            for file_path in candidates:
                if os.path.exists(file_path):
                    content_type = 'image/png'
                    if file_path.lower().endswith(('.jpg', '.jpeg')):
                        content_type = 'image/jpeg'
                    elif file_path.lower().endswith('.gif'):
                        content_type = 'image/gif'
                    with open(file_path, 'rb') as f:
                        content = f.read()
                    self.send_response(200)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(len(content)))
                    self.send_header('Cache-Control', 'no-cache')
                    self.end_headers()
                    try:
                        self.wfile.write(content)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        pass
                    return
        self.send_error(404)

    def log_message(self, format, *args):
        pass


def mqtt_event_listener():
    """订阅 MQTT 事件并存入 EventStore"""
    import paho.mqtt.client as mqtt
    from threading import Lock

    def on_connect(client, userdata, flags, rc):
        print(f'[DashboardServer] MQTT 连接成功, rc={rc}')
        client.subscribe('tll/skaye_SV')

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode('utf-8'))
            if isinstance(payload, dict):
                event = payload.get('event', payload)
                if 'message' in event and event['message']:
                    event['message'] = re.sub(r'\x1b\[[0-9;]*m', '', event['message'])
                # print(f"[DashboardServer] 收到MQTT事件 source={event.get('source_bot')} level={event.get('level')} message={event.get('message')}")
                DashboardHandler.event_store.add(event)
        except Exception as e:
            print(f"[DashboardServer] 消息处理异常: {e}")

    client = mqtt.Client(client_id='dashboard_server')
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect('broker.emqx.io', 1883, 60)
    client.loop_forever()


class SilentThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def main():
    DashboardHandler.event_store = EventStore()


    t = threading.Thread(target=mqtt_event_listener, daemon=True)
    t.start()

    server = SilentThreadingHTTPServer(('0.0.0.0', 8080), DashboardHandler)
    print('✅ 独立数据大屏已启动: http://127.0.0.1:8080')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == '__main__':
    main()
