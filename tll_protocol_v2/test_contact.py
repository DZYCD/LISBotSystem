"""牵线接口测试：Skaye 族通过 set_sayi_contact / set_eiar_contacts 牵线。

验证：
1. EiAr 侧 set_sayi_contact：清空所有 SaYi → 写当前 SaYi（不含工具）
2. SaYi 侧 set_eiar_contacts：清空所有 EiAr → 写当前 EiAr（含工具）
3. 权限：非 Skaye 族调用被拒绝
"""

import asyncio
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tll_protocol_v2.bot_config import BotConfigManager
from tll_protocol_v2.contact_tools import create_set_sayi_contact_tool, create_set_eiar_contacts_tool


def _fake_request(actor, args):
    class Req:
        pass
    r = Req()
    r.actor = actor
    r.arguments = args
    return r


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class ContactToolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _write_bot(self, bot_id, peers):
        import yaml
        safe = bot_id.replace("/", "_")
        p = os.path.join(self.tmp, f"{safe}.yaml")
        with open(p, "w", encoding="utf-8") as f:
            yaml.safe_dump({"id": bot_id, "peers": peers}, f, allow_unicode=True, sort_keys=False)
        return p

    def test_set_sayi_contact_clears_and_writes(self):
        # EiAr 的 bot.yaml 初始有旧 SaYi
        path = self._write_bot("agent/eiar_001", {
            "agent/sayi_996": {"auth_key": "old"},
            "agent/skaye_sv": {"auth_key": "sk-sv"},
        })
        tool = create_set_sayi_contact_tool(path)
        req = _fake_request("agent/skaye_996", {
            "sayi_id": "agent/sayi_996",
            "sayi_info": {"bot_id": "agent/sayi_996", "auth_key": "sk-test", "network": {"topic": "tll/agent/sayi_996"}},
        })
        r = run(tool.execute(req, None))
        self.assertTrue(r.ok)
        import yaml
        data = yaml.safe_load(open(path, encoding="utf-8"))
        peers = data["peers"]
        self.assertIn("agent/sayi_996", peers)
        # 不含 tools
        self.assertNotIn("tools", peers["agent/sayi_996"])
        # skaye_sv 保留
        self.assertIn("agent/skaye_sv", peers)

    def test_set_eiar_contacts_clears_and_writes(self):
        # SaYi 的 bot.yaml 初始有旧 EiAr
        path = self._write_bot("agent/sayi_996", {
            "agent/eiar_001": {"auth_key": "old1"},
            "agent/skaye_996": {"auth_key": "sk-sky001"},
        })
        tool = create_set_eiar_contacts_tool(path)
        req = _fake_request("agent/skaye_996", {
            "eiar_list": [
                {"bot_id": "agent/eiar_001", "auth_key": "sk-eiar001", "tools": ["file_read"]},
                {"bot_id": "agent/eiar_002", "auth_key": "sk-eiar002", "tools": ["read_docx"]},
            ]
        })
        r = run(tool.execute(req, None))
        self.assertTrue(r.ok)
        import yaml
        data = yaml.safe_load(open(path, encoding="utf-8"))
        peers = data["peers"]
        # 新 EiAr 写入，含工具
        self.assertIn("agent/eiar_001", peers)
        self.assertIn("agent/eiar_002", peers)
        self.assertEqual(peers["agent/eiar_001"]["tools"], ["file_read"])
        # 旧 eiar_001 被覆盖
        self.assertEqual(peers["agent/eiar_001"]["auth_key"], "sk-eiar001")
        # skaye_996 保留
        self.assertIn("agent/skaye_996", peers)

    def test_non_skaye_denied(self):
        path = self._write_bot("agent/eiar_001", {})
        tool = create_set_sayi_contact_tool(path)
        req = _fake_request("agent/eiar_002", {"sayi_id": "agent/sayi_996", "sayi_info": {}})
        r = run(tool.execute(req, None))
        self.assertFalse(r.ok)
        self.assertTrue(r.denied)


if __name__ == "__main__":
    unittest.main()
