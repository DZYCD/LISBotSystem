"""SkillLoader 测试：从 skills 目录动态注册工具（单一来源）。"""

import sys
import tempfile
import unittest
from pathlib import Path

from lis_harness.registry import Registry
from lis_harness.skill_loader import SkillLoader, SkillSpec


def _make_skill(dir_: Path, name: str, desc: str, params: dict | None = None):
    skill_dir = dir_ / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "tool.yaml").write_text(
        f"name: {name}\ndescription: {desc}\n",
        encoding="utf-8",
    )
    # 无依赖的 handle
    (skill_dir / "tool.py").write_text(
        "def handle(params=None, task=None):\n"
        "    params = params or {}\n"
        "    return {'status': 'success', 'info': f'ran {params.get(\"x\", \"\")}'}\n",
        encoding="utf-8",
    )


class SkillLoaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lis_harness_skill_")
        self.skills_dir = Path(self.tmp)
        _make_skill(self.skills_dir, "greet", "Greet a user", {"type": "object"})
        _make_skill(self.skills_dir, "sum", "Add numbers")
        # 一个坏的 skill（tool.py 语法错误）应被跳过
        bad = self.skills_dir / "bad"
        bad.mkdir()
        (bad / "tool.yaml").write_text("name: bad\ndescription: broken\n", encoding="utf-8")
        (bad / "tool.py").write_text("this is not valid python {", encoding="utf-8")
        self.registry = Registry()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scan_discovers_valid_skills(self):
        loader = SkillLoader(self.skills_dir)
        specs = loader.scan()
        names = [s.name for s in specs]
        self.assertIn("greet", names)
        self.assertIn("sum", names)
        self.assertIn("bad", names)  # 扫描阶段只看 tool.yaml 存在，不判语法

    def test_load_skips_broken_skills(self):
        loader = SkillLoader(self.skills_dir)
        disposers = loader.load_into(self.registry)
        loaded = [t.name for t in self.registry.list_tools()]
        self.assertIn("greet", loaded)
        self.assertIn("sum", loaded)
        self.assertNotIn("bad", loaded)  # tool.py 语法错误被跳过
        self.assertEqual(len(disposers), 4)  # 每个 skill 注册后端+工具两对

    def test_disposers_unload_all(self):
        loader = SkillLoader(self.skills_dir)
        disposers = loader.load_into(self.registry)
        self.assertEqual(len(self.registry.list_tools()), 2)
        for d in disposers:
            d()
        self.assertEqual(len(self.registry.list_tools()), 0)

    def test_loose_schema_when_no_parameters_declared(self):
        loader = SkillLoader(self.skills_dir)
        specs = {s.name: s for s in loader.scan()}
        self.assertEqual(specs["sum"].parameters.get("type"), "object")
        self.assertTrue(specs["sum"].parameters.get("additionalProperties", False))


if __name__ == "__main__":
    unittest.main()
