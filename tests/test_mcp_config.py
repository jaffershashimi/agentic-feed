import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class McpConfigTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / ".mcp.json").read_text())

    def test_alpaca_server_is_paper_only_and_has_no_trading_toolset(self):
        env = self.cfg["mcpServers"]["alpaca-readonly"]["env"]
        self.assertEqual(env["ALPACA_PAPER_TRADE"], "true")
        toolsets = set(env["ALPACA_TOOLSETS"].split(","))
        self.assertNotIn("trading", toolsets)
        self.assertNotIn("watchlists", toolsets)
        self.assertLessEqual(toolsets, {"account", "assets", "stock-data", "options-data", "corporate-actions", "news", "crypto-data", "fixed-income-data"})

    def test_credentials_are_placeholders_never_values(self):
        raw = (ROOT / ".mcp.json").read_text()
        for key in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
            self.assertEqual(self.cfg["mcpServers"]["alpaca-readonly"]["env"][key], "${" + key + "}")
        self.assertIsNone(re.search(r"\b(PK|AK)[A-Z0-9]{16,}\b", raw))

    def test_connectors_doc_states_the_unverified_runtime(self):
        text = (ROOT / "docs" / "CONNECTORS.md").read_text()
        self.assertIn("not been run", text.lower())
        self.assertIn("trading", text)


if __name__ == "__main__":
    unittest.main()
