def handle(params=None, bot=None, task=None):
    """回复 PONG，返回与 record_lis 相同的注册/心跳信息"""
    if bot is None:
        return {'status': 'error', 'info': '缺少 bot 上下文'}
    return bot.build_registration_info()
