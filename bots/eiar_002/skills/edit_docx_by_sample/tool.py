import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _office_tools import edit_docx_by_sample

def handle(params=None, task=None):
    if params is None: params = {}
    return edit_docx_by_sample(params.get('path', ''), params.get('sample', ''), params.get('replacement', ''))
