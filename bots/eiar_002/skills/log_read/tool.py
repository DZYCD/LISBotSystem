import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _memory_tools import log_read

def handle(params=None, task=None):
    if params is None: params = {}
    return log_read(params.get('lines', 50))
