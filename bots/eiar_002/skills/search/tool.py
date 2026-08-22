import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _web_tools import search

def handle(params=None, task=None):
    if params is None: params = {}
    return search(params.get('query', ''), params.get('count', 5))
