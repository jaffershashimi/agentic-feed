"""Pre-trade gate: an independent check of the day's order plan before anything is sent (the "checker" in a maker/checker split).

Institutions run automated pre-trade controls that hard-block an order (a soft warning gets clicked through). This gate reads the DRY-RUN plan produced by
`consumer.oversold_forward run` and decides, from the limits register and the health checks, whether the plan may go out:

  PASS            the plan goes out as written
  BLOCK_ENTRIES   exits still go out (getting out must always be possible), new entries are not sent
  BLOCK_ALL       nothing is sent (the kill switch is set)

Fail closed: a plan that cannot be read, or health that is missing, blocks entries. The gate never sends an order and never changes the plan; the workflow decides
what to run next from its answer. Messages carry counts, never symbols, so the log holds no account data.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

from consumer import alpaca_paper as ap
from consumer import oversold_forward as of
from engine import limits

PASS, BLOCK_ENTRIES, BLOCK_ALL = "PASS", "BLOCK_ENTRIES", "BLOCK_ALL"
# Health checks whose FAIL means "do not add risk". A news-feed problem is information only, so it is not in this set.
BLOCKING_HEALTH = {"stops present", "data fresh"}
NOTIONAL_TOLERANCE = 1.02   # the stop price is rounded to cents, so the notional derived from it can differ from the true one by a fraction of a percent
SYMBOL = re.compile(r"^[A-Z]{1,5}$")


def profiles():
    """Per-strategy numbers the gate checks against: tag, position cap, size per entry, stop fraction. Read from the code that trades each one."""
    from consumer import breakout_forward as bf
    return {of.TAG: {"cap": limits.get("S042_POSITION_CAP"), "usd": limits.get("S042_NOTIONAL_USD"), "stop": limits.get("S042_STOP_FRACTION")},
            bf.TAG: {"cap": bf.POSITION_CAP, "usd": bf.NOTIONAL_USD, "stop": bf.STOP_FRACTION}}


def entry_problems(entries, tag=of.TAG, usd=None, stop_fraction=None):
    """Counts of what is wrong with the planned entry bodies. Each key is a short reason; the value is how many entries have it."""
    bad = {}

    def hit(reason):
        bad[reason] = bad.get(reason, 0) + 1

    cap = usd if usd is not None else limits.get("S042_NOTIONAL_USD")
    stop_fraction = stop_fraction if stop_fraction is not None else limits.get("S042_STOP_FRACTION")
    ident = re.compile(r"^" + re.escape(tag) + r"-[A-Z]{1,5}-\d{6}$")
    for b in entries:
        try:
            qty = int(b["qty"])
            stop = float(b["stop_loss"]["stop_price"])
        except (KeyError, TypeError, ValueError):
            hit("entry body is missing a quantity or a stop")
            continue
        if b.get("side") != "buy":
            hit("entry is not a buy")
        if b.get("order_class") != "oto" or stop <= 0:
            hit("entry has no broker-side stop attached")
        if qty < 1:
            hit("entry quantity is below one share")
        elif qty * (stop / stop_fraction) > cap * NOTIONAL_TOLERANCE:
            hit("entry is larger than the per-entry cap")
        if not SYMBOL.match(str(b.get("symbol", ""))):
            hit("symbol has an unexpected shape")
        if not ident.match(str(b.get("client_order_id", ""))):
            hit("client order id does not carry the strategy tag")
    return bad


def decide(plan, health_rows, halted=False, now=None, tag=of.TAG):
    """Return {"decision", "checks": [{"id", "ok", "blocks", "detail"}], "n_entries", "n_exits"}. `plan` is the dry-run dict (or None if it could not be produced)."""
    now = now or datetime.now(timezone.utc)
    prof = profiles()[tag]
    checks = []

    def add(cid, ok, blocks, detail):
        checks.append({"id": cid, "ok": bool(ok), "blocks": blocks, "detail": detail})

    add("kill_switch_clear", not halted, BLOCK_ALL, "the HALT file is set" if halted else "no HALT file")
    if not isinstance(plan, dict) or "entries" not in plan:
        add("plan_readable", False, BLOCK_ENTRIES, "the dry-run plan is missing or unreadable")
        entries, exits = [], []
    else:
        add("plan_readable", True, BLOCK_ENTRIES, "dry-run plan read")
        entries = plan.get("entries") or []
        exits = [e for e in (plan.get("exits") or []) if e.get("action") == "EXIT"]
        in_window = of.in_morning_window(now)
        add("in_execution_window", bool(plan.get("market_open")) and in_window, BLOCK_ENTRIES,
            "market open and inside the 09:50 to 11:30 ET window" if plan.get("market_open") and in_window else "the market is closed or the time is outside the 09:50 to 11:30 ET window")
        cap = prof["cap"]
        slots = int(plan.get("slots") or 0)
        add("entries_fit_free_slots", len(entries) <= min(slots, cap), BLOCK_ENTRIES, f"{len(entries)} planned entries, {slots} free slots, cap {cap}")
        problems = entry_problems(entries, tag, prof["usd"], prof["stop"])
        add("entry_orders_well_formed", not problems, BLOCK_ENTRIES, "; ".join(f"{n} x {r}" for r, n in sorted(problems.items())) if problems else "every entry has a stop, a size inside the cap and our tag")
        add("no_duplicate_entries", len({b.get("symbol") for b in entries}) == len(entries) and len({b.get("client_order_id") for b in entries}) == len(entries), BLOCK_ENTRIES, "no repeated symbol or order id")
        add("corporate_action_data_ok", not plan.get("corporate_actions_error"), BLOCK_ENTRIES, "corporate-action lookup worked" if not plan.get("corporate_actions_error") else "corporate-action lookup failed, so a split could hide in the plan")
        add("exits_not_more_than_cap", len(exits) <= cap, BLOCK_ENTRIES, f"{len(exits)} planned exits")
    if health_rows is None:
        add("health_available", False, BLOCK_ENTRIES, "health checks could not be run")
    else:
        failed = sorted(r["check"] for r in health_rows if r["status"] == "FAIL" and r["check"] in BLOCKING_HEALTH)
        add("health_clear_for_new_risk", not failed, BLOCK_ENTRIES, "no blocking health failure" if not failed else f"health FAIL: {', '.join(failed)}")
    rank = {PASS: 0, BLOCK_ENTRIES: 1, BLOCK_ALL: 2}
    decision = max((c["blocks"] for c in checks if not c["ok"]), key=lambda b: rank[b], default=PASS)
    return {"decision": decision, "checks": checks, "n_entries": len(entries), "n_exits": len(exits)}


def render(result):
    lines = [f"GATE DECISION: {result['decision']}  ({result['n_entries']} planned entries, {result['n_exits']} planned exits)"]
    for c in result["checks"]:
        lines.append(f"{'PASS ' if c['ok'] else 'BLOCK'} {c['id']:<28} {c['detail']}")
    return "\n".join(lines)


def halted(state_dir):
    return ap.halt_file(Path(state_dir)).exists()
