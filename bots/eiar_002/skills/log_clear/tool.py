import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _memory_tools import log_clear

def handle(params=None, task=None):
    return log_clear()
