"""ToolReport 测试：从 bot.yaml 的 tool_list 生成上报清单。"""

import os
import tempfile
import unittest
from pathlib import Path

from lis_harness.report import ToolReport


class ToolReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lis_harness_report_"))
        # 构造一个最小 bot.yaml + tools.yaml
        self.tools_yaml = self.tmp / "tools.yaml"
        self.tools_yaml.write_text("""
public:
  file_read:
    description: "读取文件"
    params: { path: "string" }
    access: { allow: ["*"] }
private:
  _secret:
    description: "内部"
    params: {}
    access: {}
""", encoding="utf-8")
        self.bot_yaml = self.tmp / "bot.yaml"
        self.bot_yaml.write_text(f"""
name: test_bot
id: agent/test_bot
tool_list: {self.tools_yaml.name}
""", encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tool_list_relative_to_bot_yaml(self):
        r = ToolReport(self.bot_yaml)
        self.assertEqual(r.tool_list_path(), self.tools_yaml)

    def test_build_includes_public_and_forced_tools(self):
        r = ToolReport(self.bot_yaml)
        rep = r.build()
        # public 工具
        self.assertIn("file_read", rep["tools"])
        self.assertIn("file_read", rep["skills"])
        # 强加载工具被合并
        self.assertIn("ping", rep["tools"])
        self.assertIn("LISreport", rep["tools"])
        # private 不出现
        self.assertNotIn("_secret", rep["tools"])

    def test_skills_structure_matches_build_registration_info(self):
        r = ToolReport(self.bot_yaml)
        rep = r.build()
        skill = rep["skills"]["file_read"]
        self.assertEqual(set(skill.keys()), {"name", "description", "access", "params"})
        self.assertEqual(skill["name"], "file_read")
        self.assertEqual(skill["description"], "读取文件")
        self.assertEqual(skill["access"], {"allow": ["*"]})
        self.assertIn("path", skill["params"])  # 上报带工具参数

    def test_missing_tool_list_returns_empty(self):
        bot = self.tmp / "empty_bot.yaml"
        bot.write_text("name: x\nid: agent/x\n", encoding="utf-8")
        r = ToolReport(bot)
        rep = r.build()
        # 没有 tool_list → 只有强加载工具
        self.assertEqual(rep["tools"], ["LISreport", "ping"])
        self.assertIn("ping", rep["skills"])

    def test_missing_tool_manifest_file(self):
        bot = self.tmp / "bad_bot.yaml"
        bot.write_text("name: x\nid: agent/x\ntool_list: nonexistent.yaml\n", encoding="utf-8")
        r = ToolReport(bot)
        rep = r.build()
        self.assertEqual(rep["tools"], ["LISreport", "ping"])


if __name__ == "__main__":
    unittest.main()
