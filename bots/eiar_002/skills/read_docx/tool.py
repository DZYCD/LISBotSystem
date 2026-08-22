import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _office_tools import read_docx

def handle(params=None, task=None):
    if params is None: params = {}
    return read_docx(params.get('path', ''), params.get('detail_level', 'paragraph'))
