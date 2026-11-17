import py_compile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ScriptsCompile(unittest.TestCase):
    def test_research_scripts_at_least_compile(self):
        for name in ("pull_panel.py", "exec_timing_study.py"):
            py_compile.compile(str(ROOT / "scripts" / name), doraise=True, cfile="/dev/null")


if __name__ == "__main__":
    unittest.main()
