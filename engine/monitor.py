"""Model monitoring: is the live strategy behaving the way the backtest said it would?

A model risk framework (the Federal Reserve's SR 11-7 is the reference) asks for ongoing monitoring with pre-agreed triggers, not only a validation before launch.
These triggers compare the forward test with the benchmark in engine/benchmarks.py. A trigger raises WARN (review) or FAIL (the owner decides on a pause). Nothing
here pauses or changes a strategy: the owner owns every rule, and the pre-registered decision rules are not touched.

  evidence_progress   closed forward trades against the owner's go-live minimum, total and under the current morning-limit execution (information)
  cost_drift          measured entry cost against the modeled cost (needs 10 measured entries)
  return_drift        mean return per morning-limit trade against the backtest mean, in standard errors (needs 30 such trades); a negative mean after 50 is a FAIL
  fill_rate           share of our orders that filled (needs 10 orders); unfilled orders are an unmeasured opportunity cost
  plumbing            any FAIL from the health checks
"""
import math

from engine import benchmarks

B = benchmarks.S042


def evaluate(trades, tca, health_rows):
    rows = []
    n = len(trades)
    live = [t for t in trades if t.get("mode") == "morning_limit"]
    need = B["go_live_min_closed_trades"]
    rows.append(("evidence_progress", "OK", f"{n} closed forward trades ({len(live)} under the current morning-limit execution) toward the go-live minimum of {need}"))

    status, detail = (tca or {}).get("status"), (tca or {}).get("detail")
    rows.append(("cost_drift", status or "OK", detail or "no cost analysis available"))

    # Return drift is judged only on trades that used the execution the benchmark models. The retired market-at-open mode was expected to lose, so it is not a warning.
    bench = B["mean_ret_pct_at_10am_scenario"]
    m_n = len(live)
    if m_n >= 30:
        rets = [t["ret_pct"] for t in live]
        m = sum(rets) / m_n
        sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (m_n - 1))
        z = (m - bench) / (sd / math.sqrt(m_n)) if sd > 0 else 0.0
        if m_n >= 50 and m < 0:
            rows.append(("return_drift", "FAIL", f"mean {m:+.2f}% per trade after {m_n} morning-limit trades is negative, the backtest said {bench:+.2f}%: owner decides on a pause"))
        elif z < -2:
            rows.append(("return_drift", "WARN", f"mean {m:+.2f}% per trade is {abs(z):.1f} standard errors below the backtest's {bench:+.2f}%: review"))
        else:
            rows.append(("return_drift", "OK", f"mean {m:+.2f}% per trade, {z:+.1f} standard errors from the backtest's {bench:+.2f}%"))
    else:
        rows.append(("return_drift", "OK", f"{m_n} morning-limit trades closed; a verdict needs 30"))

    fills = (tca or {}).get("fills") or {}
    if fills.get("sent", 0) >= 10 and fills.get("fill_rate") is not None and fills["fill_rate"] < 0.7:
        rows.append(("fill_rate", "WARN", f"only {fills['fill_rate']:.0%} of {fills['sent']} orders filled; the cost of the unfilled ones is not measured"))
    else:
        rows.append(("fill_rate", "OK", f"{fills.get('filled', 0)} of {fills.get('sent', 0)} orders filled in the window" if fills.get("sent") else "no orders in the window"))

    bad = sorted(r["check"] for r in (health_rows or []) if r["status"] == "FAIL")
    rows.append(("plumbing", "FAIL" if bad else "OK", f"health FAIL on: {', '.join(bad)}" if bad else "no health failures"))
    return [{"trigger": t, "status": s, "detail": d} for t, s, d in rows]
