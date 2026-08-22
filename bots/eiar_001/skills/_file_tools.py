import os, json, shutil

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _safe(path):
    if not path: return BASE
    if not os.path.isabs(path): path = os.path.join(BASE, path)
    return path

def file_tree(path='.'):
    path = _safe(path)
    if not os.path.isdir(path): return f'目录不存在: {path}'
    out = [os.path.basename(path.rstrip(os.sep)) + '/']
    try: entries = sorted(os.listdir(path))
    except PermissionError: return '权限不足'
    for e in entries:
        if e.startswith('.') or e == '__pycache__': continue
        out.append(('  '+e+'/') if os.path.isdir(os.path.join(path,e)) else ('  '+e))
    return '\n'.join(out)

def file_read(path, start=None, end=None):
    path = _safe(path)
    if not os.path.isfile(path): return f'文件不存在: {path}'
    with open(path, 'r', encoding='utf-8') as f: lines = f.readlines()
    if start is None and end is None:
        return ''.join(lines)
    s = (start or 1) - 1
    e = end or len(lines)
    content = ''.join(lines[s:e])
    return f'# meta: total_lines={len(lines)}, file={os.path.basename(path)}, range={s+1}-{e}\n' + content

def file_write(path, mode='overwrite', content='', start=None, end=None, line=None):
    path = _safe(path)
    d = os.path.dirname(path)
    if d: os.makedirs(d, exist_ok=True)
    if mode == 'overwrite':
        with open(path, 'w', encoding='utf-8') as f: f.write(content)
    elif mode == 'insert':
        lines = []
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f: lines = f.readlines()
        ins = [l+'\n' for l in content.rstrip('\n').split('\n')]
        if line is None: line = len(lines)+1
        if line < 1: line = 1
        if line > len(lines)+1: line = len(lines)+1
        lines[line-1:line-1] = ins
        with open(path, 'w', encoding='utf-8') as f: f.writelines(lines)
    elif mode == 'replace':
        if start is None or end is None: return 'replace模式需要start和end'
        lines = []
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f: lines = f.readlines()
        if start < 1 or end > len(lines)+1: return f'行号越界: {start}-{end}, 共{len(lines)}行'
        rep = [l+'\n' for l in content.rstrip('\n').split('\n')]
        lines[start-1:end] = rep
        with open(path, 'w', encoding='utf-8') as f: f.writelines(lines)
    elif mode == 'delete':
        if start is None or end is None: return 'delete模式需要start和end'
        lines = []
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f: lines = f.readlines()
        if start < 1 or end > len(lines)+1: return f'行号越界: {start}-{end}, 共{len(lines)}行'
        del lines[start-1:end]
        with open(path, 'w', encoding='utf-8') as f: f.writelines(lines)
    elif mode == 'diff':
        try: diff = json.loads(content)
        except: return 'diff模式content必须为JSON'
        search, replace = diff.get('search',''), diff.get('replace','')
        if not search: return '缺少search'
        with open(path, 'r', encoding='utf-8') as f: text = f.read()
        if search not in text: return f'未找到search: {search[:50]}'
        with open(path, 'w', encoding='utf-8') as f: f.write(text.replace(search, replace))
    else:
        return f'未知模式: {mode}'
    return f'已写入: {path} (模式:{mode})'

def file_create(path, type='file'):
    path = _safe(path)
    if type == 'dir': os.makedirs(path, exist_ok=True)
    else:
        d = os.path.dirname(path)
        if d: os.makedirs(d, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f: f.write('')
    return f'已创建{type}: {path}'

def file_delete(path):
    path = _safe(path)
    if os.path.isdir(path): shutil.rmtree(path)
    elif os.path.exists(path): os.remove(path)
    else: return f'路径不存在: {path}'
    return f'已删除: {path}'

def file_copy(src, dst):
    src, dst = _safe(src), _safe(dst)
    d = os.path.dirname(dst)
    if d: os.makedirs(d, exist_ok=True)
    if os.path.isdir(src): shutil.copytree(src, dst)
    else: shutil.copy2(src, dst)
    return f'已复制: {src} → {dst}'

def file_move(src, dst):
    src, dst = _safe(src), _safe(dst)
    d = os.path.dirname(dst)
    if d: os.makedirs(d, exist_ok=True)
    shutil.move(src, dst)
    return f'已移动: {src} → {dst}'

def search_text(path, keyword, case_sensitive=False):
    path = _safe(path)
    if not os.path.isfile(path): return f'文件不存在: {path}'
    with open(path, 'r', encoding='utf-8') as f: lines = f.readlines()
    total = len(lines)
    matches = []
    for i, l in enumerate(lines, 1):
        if (case_sensitive and keyword in l) or (not case_sensitive and keyword.lower() in l.lower()):
            matches.append((i, l.rstrip()))
    if not matches: return f'# meta: total_lines={total}, keyword="{keyword}", matches=0'
    return f'# meta: total_lines={total}, keyword="{keyword}", matches={len(matches)}\n' + '\n'.join(f'{i}| {t}' for i,t in matches)
