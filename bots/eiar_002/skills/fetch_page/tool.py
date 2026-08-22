import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _web_tools import fetch_page

def handle(params=None, task=None):
    if params is None: params = {}
    return fetch_page(params.get('url', ''), params.get('max_length', 3000))
