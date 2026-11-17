import gzip
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from consumer import day_trade_kit as kit

FIXTURE = Path(__file__).resolve().parent.parent / "third_party" / "day_trader_skill" / "tests" / "fixtures" / "trader-timemachine" / "TSLA-60d5m.json.gz"
NOW = datetime(2026, 10, 19, 14, 15, tzinfo=timezone.utc)  # 10:15 New York time


def alpaca_like():
    """The upstream TSLA fixture rewritten as Alpaca-style bar dicts, to prove the adapter loses nothing."""
    r = json.loads(gzip.open(FIXTURE, "rt").read())["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(r["timestamp"]):
        if None in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]):
            continue
        out.append({"t": datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z"), "o": q["open"][i], "h": q["high"][i], "l": q["low"][i], "c": q["close"][i], "v": q["volume"][i]})
    return out


class AdapterTests(unittest.TestCase):
    def test_every_skill_loads_without_editing_upstream(self):
        for name in ("scanner", "news", "entry", "exit", "timemachine", "risk", "budget", "broker", "alerts", "analyst", "pelosi"):
            self.assertTrue(hasattr(kit.load_skill(name), "main"), name)
        with self.assertRaises(ValueError):
            kit.load_skill("nope")

    def test_exit_plan_matches_the_pack_reel(self):
        p = kit.exit_plan(364.18, 362.18, 354.47, "or_mid", 1.0)
        self.assertEqual((p["stop"], p["target"], p["risk"]), (358.32, 370.04, 5.86))

    def test_exit_plan_refuses_what_upstream_refuses(self):
        ex = kit.load_skill("exit")
        with self.assertRaises(ex.PlanError):
            kit.exit_plan(100.0, 100.0, 99.99, "or_mid", 1.0)  # risk far under 0.05 percent of the price

    def test_time_machine_on_alpaca_shaped_bars_reproduces_the_reel_numbers(self):
        plan = {"date": "2026-10-19", "entry": 364.18, "entry_time": "10:15 AM", "stop": 358.32, "target": 370.04, "opening_range": {"minutes": 15}}
        doc = kit.time_machine("TSLA", alpaca_like(), plan, NOW, replays=10000, seed=19, until="16:00")
        mc = doc["mc"]
        self.assertEqual((mc["p_target"], mc["p_stop"], mc["p_neither"]), (0.2938, 0.2548, 0.4514))
        self.assertEqual(doc["history"]["triggered"], 33)
        self.assertTrue(doc["verdict"].startswith("Close to a coin flip"))

    def test_chart_text_is_valid_json_with_the_expected_meta(self):
        d = json.loads(kit.alpaca_to_chart("tsla", alpaca_like()[:6]))
        meta = d["chart"]["result"][0]["meta"]
        self.assertEqual((meta["symbol"], meta["dataGranularity"]), ("TSLA", "5m"))


if __name__ == "__main__":
    unittest.main()
