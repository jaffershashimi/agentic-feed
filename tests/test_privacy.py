import tempfile
import unittest
from pathlib import Path

from scripts.privacy_check import scan


class PrivacyTest(unittest.TestCase):
    def test_repository_is_clean(self):
        self.assertEqual(scan(), [])

    def test_detects_account_data_and_secrets(self):
        samples = {
            "a.md": "Agentic ••••2711",
            "b.json": '{"total_value": 1}',
            "c.py": "account 123456789",
            "d.yml": "token: ghp_" + "a" * 36,
            "e.md": "see https://example-city.demo.chatgpt.site/sync",
            ".env": "KEY=1",
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in samples.items():
                Path(tmp, name).write_text(text)
            problems = scan(Path(tmp))
        self.assertEqual(len(problems), len(samples), problems)


if __name__ == "__main__":
    unittest.main()
