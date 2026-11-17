import re
import subprocess
import sys
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parent.parent / "third_party" / "day_trader_skill"


class VendoredPackTests(unittest.TestCase):
    def test_upstream_tests_pass_on_the_pinned_copy(self):
        """The vendored pack must stay byte-for-byte working: its own 315 offline tests run in a subprocess."""
        r = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"], cwd=PACK, capture_output=True, text=True, timeout=240)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertIn("OK", r.stderr)

    def test_license_and_notice_are_kept(self):
        self.assertIn("MIT License", (PACK / "LICENSE").read_text())
        self.assertIn("3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd", (PACK.parent / "NOTICE-day-trader-skill.md").read_text())

    def test_no_keys_or_real_account_values_in_the_vendored_pack(self):
        key = re.compile(r"\b(PK|AK|CK)[A-Z0-9]{16,}\b|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}")
        for f in PACK.rglob("*"):
            if f.is_file() and f.suffix in (".py", ".md", ".json", ".txt"):
                self.assertIsNone(key.search(f.read_text(errors="ignore")), str(f))

    def test_the_pack_cannot_place_an_order(self):
        # the only broker-facing script prints tickets; no module in the pack opens a connection to an order endpoint
        for f in PACK.glob("trader-*/*.py"):
            text = f.read_text()
            self.assertNotIn("paper-api.alpaca.markets/v2/orders", text, f.name)
            self.assertNotIn("api.alpaca.markets/v2/orders", text, f.name)


if __name__ == "__main__":
    unittest.main()
