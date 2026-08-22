import os, json, time

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MEM_DIR = os.path.join(BASE, 'skills', '_memory_storage')

os.makedirs(MEM_DIR, exist_ok=True)
WORLD_FILE = os.path.join(MEM_DIR, 'worldsmemory.json')
MEMORY_FILE = os.path.join(MEM_DIR, 'memory.json')


def _load_json(path):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def _save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def memory_add(data):
    wm = _load_json(WORLD_FILE)
    data['记录时间'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    wm.append(data)
    _save_json(WORLD_FILE, wm)
    return f"MEMORY_ADD 执行成功: {data.get('知识点', '')}"


def memory_query(data):
    wm = _load_json(WORLD_FILE)
    keyword = data.get('关键词', '')
    results = [m for m in wm if keyword in m.get('知识点', '')]
    return results if results else f"未找到与「{keyword}」相关的记忆"


def memory_delete(data):
    wm = _load_json(WORLD_FILE)
    keyword = data.get('关键词', '')
    before = len(wm)
    wm = [m for m in wm if keyword not in m.get('知识点', '')]
    _save_json(WORLD_FILE, wm)
    return f"已删除 {before - len(wm)} 条包含「{keyword}」的记忆"


def memory_compress():
    hist = _load_json(MEMORY_FILE)
    if len(hist) < 40:
        return "当前记忆条数不足，无需压缩"
    to_compress = hist[:40]
    hist = hist[40:]
    _save_json(MEMORY_FILE, hist)
    return f"已压缩前40条记忆，当前剩余 {len(hist)} 条"


def log_read(lines=50):
    log_path = os.path.join(BASE, 'logs', f'{os.path.basename(BASE)}.log')
    if not os.path.exists(log_path):
        return "日志文件不存在"
    with open(log_path, 'r', encoding='utf-8') as f:
        all_lines = f.readlines()
    return ''.join(all_lines[-lines:])


def log_clear():
    log_path = os.path.join(BASE, 'logs', f'{os.path.basename(BASE)}.log')
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, 'w', encoding='utf-8'):
        pass
    return "日志已清空"
