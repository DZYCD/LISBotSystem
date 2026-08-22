'''
Bot 启动模板

封装 create_transport_from_options、ensure_logger、on_message 等固定逻辑，
供各机器人的 start.py 调用。
'''

import os
import json
from datetime import datetime

from tll_protocol.mqtt_transport import MQTTTransport
from tll_protocol.core import TaskStatus, create_logger, HIGHLIGHT, RESET


# 关联机器人映射，用于大屏展示社交关系
def get_related_bots(bot_id):
    mapping = {
        'agent/sayi_996': ['agent/sky_001', 'agent/sky_002', 'agent/eiar_001', 'agent/eiar_002'],
        'agent/sky_001': ['agent/sayi_996'],
        'agent/skaye_sv': [],
        'agent/eiar_001': ['agent/sayi_996', 'agent/skaye_sv'],
        'agent/eiar_002': ['agent/sayi_996', 'agent/skaye_sv'],
    }
    return mapping.get(bot_id, [])


def create_transport_from_options(config, additional_topics=None):
    options = config.get_network_options()
    if not options:
        raise RuntimeError('未配置任何网络选项')

    for idx, opt in enumerate(options):
        network_type = opt.get('network', 'mqtt')
        host = opt.get('url', opt.get('host', ''))
        port = opt.get('port', 1883)
        topic = opt.get('topic', f'tll/{config.id}')

        if network_type != 'mqtt':
            continue

        transport = MQTTTransport(
            host=host or '127.0.0.1',
            port=port,
            topic=topic,
            client_id=config.id,
            additional_topics=additional_topics or []
        )
        try:
            transport.connect()
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] [传输] 已连接: {host}:{port} (选项 {idx+1}/{len(options)})")
            return transport
        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [ERROR] [传输] 连接失败 ({host}:{port}): {e}")

    raise RuntimeError('所有网络选项均连接失败')


def create_ensure_logger(bot, sender):
    '''返回 ensure_logger 闭包，为任务注入 logger 上下文。'''
    def ensure_logger(task):
        if task.logger is None:
            create_logger(task, hook_manager=bot.hook_manager)
        task.logger.hook_manager = bot.hook_manager
        task.logger.context['sender'] = sender
        task.logger.context['bot_id'] = bot.config.id
        task.logger.context['hook_manager'] = bot.hook_manager
        if hasattr(bot, 'base_dir'):
            task.logger.context['archive_dir'] = os.path.join(bot.base_dir, 'tasks')
        return task.logger
    return ensure_logger


def create_on_message(bot, receiver, executor, sender):
    '''返回 on_message 回调，处理接收到的所有 TASK 消息。'''
    ensure_logger = create_ensure_logger(bot, sender)

    def on_message(payload: bytes):
        task = receiver.receive(payload)
        if task is None:
            return
        # TASK 落地后立即将 current_agent 更新为当前机器人，确保 logger 显示正确 bot_id
        task.current_agent = bot.config.id

        if task.id in bot.outgoing_tasks:
            # 自己委托出去的任务，收到回传后直接回退交给复核逻辑
            bot.complete_task(task.id, task.status, result=task.result, output=task.output)
            logger = ensure_logger(task)
            if task.status == TaskStatus.SUCCESS or task.status == TaskStatus.FAILED or task.status == TaskStatus.RETURNING:
                logger.info(f'received return with status={task.status.value}')
                executor.process_return(task)
            else:
                logger = ensure_logger(task)
                logger.warning(f"状态异常: {task.status}")
        else:
            if task.status == TaskStatus.RETURNING:
                # 执行方回传过来的任务，进入复核/继续回退
                logger = ensure_logger(task)
                logger.info(f'received RETURNING from {task.current_agent}')
                executor.process_return(task)
            elif task.status == TaskStatus.SUCCESS or task.status == TaskStatus.FAILED:
                logger = ensure_logger(task)
                if task.status == TaskStatus.SUCCESS:
                    logger.success(f'received with SUCCESS')
                else:
                    logger.error(f'received with FAILED: {task.error}')
                executor.process_return(task)
            else:
                logger = ensure_logger(task)
                _cmd = task.tlljson.command if task.tlljson else ''
                logger.info(f"收到新任务 {HIGHLIGHT}{_cmd}{RESET}")
                executed = executor.execute(task)
                executed.logger.info(f"执行完成，状态={executed.status}")
                # 执行完成后直接进入回退处理（process_return 内部会跳过 DELEGATED/CHECK_REVIEW）
                executor.process_return(task)
    return on_message


def setup_event_publisher(bot, sender):
    """设置 hook 事件发布器，统一通过 bot 的日志分发器上报（不再禁用）"""
    bot.hook_manager.node_id = bot.config.id
    if hasattr(bot, 'log_dispatcher'):
        bot.hook_manager.set_event_publisher(bot.log_dispatcher.enqueue)


def register_to_sv(bot):
    """自动注册已由 Bot.set_sender 完成，此函数保留以兼容旧调用"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] [{bot.config.name}] 自动注册由框架完成，忽略手动调用 register_to_sv")


def run_bot(bot_path, extra_setup=None):
    """通用机器人启动流程（供各族模板调用）"""
    import time
    from tll_protocol.bot_factory import request_bot_create
    from tll_protocol.receiver import TaskReceiver
    from tll_protocol.executor import TaskExecutor
    from tll_protocol.task_sender import TaskSender

    here = bot_path
    print()
    print(f"=== 启动 {here} ===")
    bot = request_bot_create(here)
    print(f"✅ Bot 创建成功: {bot.config.name} ({bot.config.id})")
    print(f"   已加载 skills: {list(bot.skills.keys())}")

    transport = create_transport_from_options(bot.config)
    sender = TaskSender(transport=transport, bot_id=bot.config.id, peers=bot.config.peers, group=bot.config.group)
    bot.set_sender(sender)
    setup_event_publisher(bot, sender)

    if extra_setup is not None:
        extra_setup(bot)

    receiver = TaskReceiver(bot_id=bot.config.id, auth_key=bot.config.auth_key)
    executor = TaskExecutor(
        handler_map=bot.handler_map,
        sender=sender,
        bot_context=bot
    )

    transport.on_message = create_on_message(bot, receiver, executor, sender)
    print(f"✅ MQTT 已连接: {transport.host}:{transport.port}, 订阅 {transport.topic}")

    register_to_sv(bot)

    print()
    print('等待消息... (Ctrl+C 退出)')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        transport.close()
