import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _office_tools import edit_docx

def handle(params=None, task=None):
    if params is None: params = {}
    return edit_docx(
        params.get('path', ''),
        params.get('action', ''),
        params.get('old_text', ''),
        params.get('new_text', ''),
        params.get('case_sensitive', False),
        params.get('text', '')
    )
