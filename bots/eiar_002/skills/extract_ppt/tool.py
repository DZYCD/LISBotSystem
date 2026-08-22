import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _office_tools import extract_ppt

def handle(params=None, task=None):
    if params is None: params = {}
    return extract_ppt(params.get('path', ''))
