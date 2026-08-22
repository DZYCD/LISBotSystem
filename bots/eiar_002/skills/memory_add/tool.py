import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _memory_tools import memory_add

def handle(params=None, task=None):
    if params is None: params = {}
    return memory_add(params)
