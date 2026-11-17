"""Adapter that runs the vendored day-trader-skill tools (MIT, third_party/day_trader_skill) on Alpaca 5-minute bars. Research and paper only.

The upstream pack reads Yahoo chart JSON. Our data is Alpaca SIP. This module loads the upstream modules unchanged and converts Alpaca bars into the Yahoo chart
shape they expect, so the Time Machine (bar-bootstrap replay with a break-even-aware verdict), the Exit Planner arithmetic and the other tools can be used on the data
the desk already has. It places no order and calls no network by itself.

  load_skill("timemachine") -> the upstream module        alpaca_to_chart(sym, bars) -> Yahoo-shaped JSON text
  exit_plan(entry, or_high, or_low, ...) -> upstream stop and target in exact cents (or a PlanError)
  time_machine(sym, bars, plan, now_utc, replays, seed) -> the upstream timemachine.json document
"""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "third_party" / "day_trader_skill"
_CACHE = {}


def load_skill(name):
    """Import trader-<name>/<script>.py from the vendored pack without editing it. name: scanner, news, entry, exit, timemachine, risk, budget, broker, alerts, analyst, pelosi."""
    scripts = {"scanner": "scan", "news": "news", "entry": "entry", "exit": "exit", "timemachine": "timemachine", "risk": "check", "budget": "size",
               "broker": "ticket", "alerts": "alerts", "analyst": "facts", "pelosi": "ptr"}
    if name not in scripts:
        raise ValueError(f"unknown skill {name!r}")
    if name in _CACHE:
        return _CACHE[name]
    path = ROOT / f"trader-{name}" / f"{scripts[name]}.py"
    spec = importlib.util.spec_from_file_location(f"day_trader_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"day_trader_{name}"] = mod
    spec.loader.exec_module(mod)
    _CACHE[name] = mod
    return mod


def _epoch(t):
    if isinstance(t, (int, float)):
        return int(t)
    return int(datetime.fromisoformat(str(t).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())


def alpaca_to_chart(sym, bars, interval="5m"):
    """bars: [{t (ISO UTC or epoch), o, h, l, c, v}] oldest first -> Yahoo chart JSON text the upstream tools accept."""
    stamps, o, h, l, c, v = [], [], [], [], [], []
    for b in bars:
        stamps.append(_epoch(b["t"]))
        o.append(float(b["o"])); h.append(float(b["h"])); l.append(float(b["l"])); c.append(float(b["c"])); v.append(int(b.get("v") or 0))
    meta = {"symbol": sym.upper(), "dataGranularity": interval, "exchangeTimezoneName": "America/New_York", "gmtoffset": -18000, "currency": "USD",
            "regularMarketTime": stamps[-1] + 300 if stamps else 0}
    doc = {"chart": {"result": [{"meta": meta, "timestamp": stamps, "indicators": {"quote": [{"open": o, "high": h, "low": l, "close": c, "volume": v}]}}], "error": None}}
    return json.dumps(doc)


def exit_plan(entry, or_high, or_low, stop_rule="or_mid", target_r=1.0):
    """Upstream exit.compute: stop rounded away from the entry, target in R, refusals for a bad stop or a risk under 0.05 percent of the price. Returns plain floats."""
    ex = load_skill("exit")
    r = ex.compute(Decimal(str(entry)), stop_rule, None, "r", Decimal(str(target_r)), Decimal(str(or_high)), Decimal(str(or_low)))
    return {"entry": float(r["entry"]), "stop": float(r["stop"]), "target": float(r["target"]), "risk": float(r["risk"]), "reward_risk": float(r["reward_risk"])}


def time_machine(sym, bars, plan, now_utc, replays=10000, seed=19, window=2, until=None):
    """Run the upstream Time Machine on Alpaca bars. plan: symbol, date, entry, entry_time ('10:15 AM'), stop, target, optional opening_range {'minutes': 15}."""
    tm = load_skill("timemachine")
    if now_utc.tzinfo is not None:  # upstream works in naive UTC datetimes
        now_utc = now_utc.astimezone(timezone.utc).replace(tzinfo=None)
    text = alpaca_to_chart(sym, bars)
    p = tm.normalize_plan(dict(plan, symbol=sym), src="alpaca")
    return tm.time_machine(text, p, now_utc, replays=replays, seed=seed, window=window, until=until, source="alpaca")
