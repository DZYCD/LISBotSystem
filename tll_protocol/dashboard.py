#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轻量级数据大屏模块 - LIS v2

读取外部 dashboard.html，提供 API 和静态图片访问。
支持 SSE 推送注册更新，避免前端轮询。
"""

import json
import os
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE_DIR, 'dashboard.html')

# SSE 客户端队列集合
_sse_clients = []
_sse_lock = threading.Lock()


def _broadcast_register_update():
    """广播注册更新事件"""
    msg = json.dumps({'type': 'register_updated'}, ensure_ascii=False)
    with _sse_lock:
        for q in _sse_clients:
            q.put(msg)


class DashboardHandler(BaseHTTPRequestHandler):
    hook_manager = None
    bots_root = None
    ping_callback = None

    def do_GET(self):
        path = self.path.split('?')[0]
        if path in ('/', '/index.html'):
            self._send_html()
        elif path == '/api/stats':
            self._send_json(self.hook_manager.get_stats())
        elif path == '/api/events':
            limit = 50
            if 'limit=' in self.path:
                try:
                    limit = int(self.path.split('limit=')[-1].split('&')[0])
                except ValueError:
                    pass
            self._send_json(self.hook_manager.get_recent_events(limit))
        elif path == '/api/register':
            self._send_json(self._get_register_data())
        elif path == '/api/register/stream':
            self._send_sse()
        elif path.startswith('/api/ping'):
            import urllib.parse
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            bot_id = query.get('bot_id', [''])[0]
            if not bot_id:
                self._send_json({'status': 'error', 'info': '缺少 bot_id'})
                return
            try:
                if self.ping_callback:
                    self.ping_callback(bot_id)
                    self._send_json({'status': 'sent', 'bot_id': bot_id})
                else:
                    self._send_json({'status': 'error', 'info': 'ping_callback 未设置'})
            except Exception as e:
                self._send_json({'status': 'error', 'info': str(e)})
        elif path.startswith('/api/static/'):
            self._send_static(path)
        else:
            self.send_error(404)

    def _get_register_data(self):
        try:
            sys_path = os.path.join(BASE_DIR, '..', 'bots', 'skaye_sv', 'skills')
            if sys_path not in sys.path:
                sys.path.insert(0, sys_path)
            from record_lis.tool import get_registered_bots
            return get_registered_bots()
        except Exception as e:
            return {'error': str(e)}

    def _send_sse(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()

        q = queue.Queue()
        with _sse_lock:
            _sse_clients.append(q)

        try:
            self.wfile.write(b': connected\n\n')
            self.wfile.flush()
            while True:
                try:
                    data = q.get(timeout=15)
                    self.wfile.write(f'data: {data}\n\n'.encode('utf-8'))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b': heartbeat\n\n')
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            with _sse_lock:
                _sse_clients.remove(q)

    def _send_static(self, path):
        parts = path.split('/')
        if len(parts) < 5:
            self.send_error(400)
            return
        bot_id = parts[3] + '/' + parts[4]
        filename = '/'.join(parts[5:])
        dir_map = {
            'agent/sayi_996': os.path.join('bots', 'sayi_996', 'static'),
            'agent/skaye_996': os.path.join('bots', 'skaye_996', 'static'),
            'agent/sky_001': os.path.join('bots', 'skaye_001', 'static'),
            'agent/eiar_001': os.path.join('bots', 'eiar_001', 'static'),
            'agent/eiar_002': os.path.join('bots', 'eiar_002', 'static'),
        }
        if bot_id not in dir_map:
            self.send_error(404)
            return
        file_path = os.path.join(self.bots_root or os.getcwd(), dir_map[bot_id], filename)
        if not os.path.isfile(file_path):
            self.send_error(404)
            return
        ext = os.path.splitext(filename)[1].lower()
        ctype = 'image/png' if ext == '.png' else 'image/jpeg'
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.end_headers()
        with open(file_path, 'rb') as f:
            self.wfile.write(f.read())

    def _send_html(self):
        try:
            with open(HTML_PATH, 'r', encoding='utf-8') as f:
                html = f.read()
        except Exception:
            self.send_error(500)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def _send_json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode('utf-8'))

    def log_message(self, format, *args):
        pass


def start_dashboard(port=8080, hook_manager=None, ping_callback=None):
    if hook_manager is None:
        from .hook_manager import hook_manager as global_hook_manager
        hook_manager = global_hook_manager

    # 注册 record_lis 的回调，实现注册时主动推送
    try:
        sys_path = os.path.join(os.path.dirname(BASE_DIR), 'bots', 'skaye_sv', 'skills')
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        from record_lis.tool import add_register_callback
        add_register_callback(_broadcast_register_update)
    except Exception as e:
        print(f'[Dashboard] 注册回调失败: {e}')

    handler = DashboardHandler
    handler.hook_manager = hook_manager
    handler.bots_root = os.path.dirname(BASE_DIR)
    handler.ping_callback = ping_callback

    server = ThreadingHTTPServer(('0.0.0.0', port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f'[Dashboard] 数据大屏已启动: http://127.0.0.1:{port}')
    return server, thread