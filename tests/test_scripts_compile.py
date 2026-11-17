import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ScriptsParse(unittest.TestCase):
    def test_research_scripts_at_least_parse(self):
        for name in ("pull_panel.py", "exec_timing_study.py"):
            ast.parse((ROOT / "scripts" / name).read_text(), filename=name)


if __name__ == "__main__":
    unittest.main()
