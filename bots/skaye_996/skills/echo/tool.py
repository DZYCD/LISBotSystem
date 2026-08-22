def handle(params=None, task=None):
    """回显收到的所有参数"""
    if params is None:
        params = {}
    return {"status": "success", "info": params}
