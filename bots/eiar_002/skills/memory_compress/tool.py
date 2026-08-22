import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _memory_tools import memory_compress

def handle(params=None, task=None):
    return memory_compress()
