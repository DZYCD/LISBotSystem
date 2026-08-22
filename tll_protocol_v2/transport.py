"""TLL v2 transport —— 复用 harness TLLTransport，注入真实 MQTT 发送。

继承 harness 的 TLLTransport（白名单校验 / TASK id 复用 / 同步阻塞等待 /
handle_response 桥接都复用），只把「发送 TASK」这一步从模拟改为真实 MQTT：
- 发 TASK：用 v2 Task 结构 + 加密 + MQTT publish
- 回传：MQTT 收到回传消息 → 调 handle_response(task_id, result) 填充 future
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

# 确保 harness（LIS_v2 根下的独立核心）可导入
_LIS_ROOT = Path(__file__).resolve().parents[1]
if str(_LIS_ROOT) not in sys.path:
    sys.path.insert(0, str(_LIS_ROOT))

from lis_harness.adapters import TLLTask, TLLTransport, TLLTransportConfig
from lis_harness.security.capability import ExecutionResult

from .core import Task, TaskStatus, TLLjson, Trace
from .mqtt import MQTTTransport
from .security import encrypt_payload


class V2TLLTransport(TLLTransport):
    """真实 MQTT 版 TLL transport。

    Args:
        config: harness TLLTransportConfig（peers 白名单等）。
        mqtt: 真实 MQTT 传输实例（用于发送）。
        my_id: 本机 id。
    """

    def __init__(self, config: TLLTransportConfig, mqtt: MQTTTransport,
                 my_id: str) -> None:
        super().__init__(config)
        self._mqtt = mqtt
        self._my_id = my_id
        # 当前正在处理的委托链 task_id（由 node 在处理 task 时设置）。
        # execute 委托时若调用方没显式传 task_id，则复用本值，保持委托链连贯，
        # 让被委托方按 task_id 检测回环（同一条链回到本机则拒绝）。
        self.current_task_id = ""
        # 当前委托链的 trace（谁委托谁）。node 收到 task 时把 task.trace 存这里，
        # 委托时带上并追加本 hop，让完整委托链沿任务传递、可追踪。
        self.current_trace = None  # Optional[v2 core.Trace]

    def _build_real_task(self, task: TLLTask, to_bot: str = "") -> Task:
        """把 harness 的 TLLTask 转成 v2 线协议 Task（带 trace 委托链）。"""
        t = Task(
            task_type="general",
            from_bot=task.from_bot,
            current_agent=self._my_id,
            tlljson=TLLjson(
                from_bot=task.from_bot,
                command=task.command,
                to=task.to,
                params=task.params,
            ),
            task_id=task.task_id,
        )
        # 携带委托链 trace（谁委托谁）：从 current_trace 继承，追加本 hop
        if self.current_trace is not None:
            t.trace = self.current_trace
        if to_bot:
            t.trace.add_hop(self._my_id, f"delegate_to_{to_bot}")
        t.status = TaskStatus.DELEGATED
        return t

    def _do_real_send(self, task: TLLTask, to_bot: str) -> bool:
        """真实发送 TASK 到目标（用目标 auth_key 加密）。"""
        real = self._build_real_task(task, to_bot)
        peer = self._config.peers.get(to_bot, {})
        target_key = peer.get("auth_key", "") if isinstance(peer, dict) else ""
        payload = {
            "type": "TASK",
            "target": to_bot,
            "sender": self._my_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task": real.to_dict(),
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if target_key:
            encrypted = encrypt_payload(data, target_key)
            final = json.dumps({
                "type": "ENCRYPTED_TASK",
                "target": to_bot,
                "sender": self._my_id,
                "timestamp": payload["timestamp"],
                "ciphertext": encrypted.decode("utf-8"),
            }).encode("utf-8")
        else:
            final = data
        ok = self._mqtt.send_payload(final, f"tll/{to_bot}")
        if not ok:
            print(f"[transport] {self._my_id} send_payload 到 tll/{to_bot} 失败", flush=True)
        return ok

    async def execute(self, request, policy):
        """执行委托：复用白名单/TASK id 复用，但发送改为真实 MQTT。"""
        args = request.arguments
        to_bot = args.get("to")
        command = args.get("command")
        params = args.get("params") or {}

        # 禁止自我网络委托：to==本机就是回环（如 delegate_to_自己），直接拒绝。
        # 所有 bot 一视同仁（含 sayi_sv 无 LLM 节点），从根上杜绝自我 TLL 委托。
        if to_bot == self._my_id:
            return ExecutionResult(ok=False, denied=True,
                                   error=f"[tll: denied] self-delegation to {to_bot} is not allowed")

        if to_bot not in self._config.peers:
            return ExecutionResult(ok=False, denied=True,
                                   error=f"[tll: denied] target {to_bot} is not in whitelist")
        if command is None:
            return ExecutionResult(ok=False, error="tll: requires command", denied=False)

        task_id = args.get("task_id") or self.current_task_id or uuid.uuid4().hex[:12]
        task = TLLTask(
            task_id=task_id, from_bot=self._my_id, to=to_bot,
            command=command, params=params,
        )
        self.sent_tasks.append(task)

        send_ok = self._do_real_send(task, to_bot)
        if not send_ok:
            return ExecutionResult(ok=False, denied=False, error=f"[tll] send failed to {to_bot}")

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[task_id] = fut
        try:
            result = await asyncio.wait_for(asyncio.shield(fut), timeout=self._config.timeout_s)
        except asyncio.TimeoutError:
            return ExecutionResult(ok=False, denied=False, error=f"[tll] timeout waiting {task_id}")
        finally:
            self._pending.pop(task_id, None)

        return ExecutionResult(ok=True, value={
            "task_id": task_id, "to": to_bot, "command": command, "result": result,
        })

    def handle_response(self, task_id: str, result: Any) -> bool:
        """线程安全地填充等待 future（从 MQTT 线程调用）。

        返回是否消费（task_id 有等待者）。
        """
        fut = self._pending.get(task_id)
        if fut is None or fut.done():
            return False
        loop = fut.get_loop()
        loop.call_soon_threadsafe(fut.set_result, result)
        return True
