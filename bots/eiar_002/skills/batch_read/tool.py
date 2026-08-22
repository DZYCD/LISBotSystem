import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _office_tools import batch_read

def handle(params=None, task=None):
    if params is None: params = {}
    return batch_read(params.get('directory', ''), params.get('recursive', False))
