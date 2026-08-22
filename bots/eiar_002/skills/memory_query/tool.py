import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _memory_tools import memory_query

def handle(params=None, task=None):
    if params is None: params = {}
    return memory_query(params)
