import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _office_tools import edit_ppt

def handle(params=None, task=None):
    if params is None: params = {}
    return edit_ppt(
        params.get('path', ''),
        params.get('action', ''),
        params.get('old_text', ''),
        params.get('new_text', ''),
        params.get('case_sensitive', False),
        params.get('replacements'),
        params.get('replace_mode', 'run'),
        params.get('title_text', ''),
        params.get('body_text', '')
    )
