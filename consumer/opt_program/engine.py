"""One daily pass: guard the ledger, reconcile with the broker, evaluate halts, manage exits, take entries, run shadow random-entry controls, write the checkpoint.

submit=False is a dry run: it reads and plans, sends nothing, and writes nothing. Orders are sent only when submit is true, which the workflow allows only when the owner's
OPTIONS_SUBMIT variable is true and the spec is approved. The log carries counts only (no symbols, strikes or prices)."""
from __future__ import annotations

import math
import random
from datetime import date, datetime
from zoneinfo import ZoneInfo

from consumer import alpaca_options as ao
from feed_pipeline import calendar as cal

from . import catalysts, market as M, policy as P, select as sel, structures as S
from .ledger import Ledger, reduce
from .specs import approved as approved_specs, has_short_legs, sha

ET = ZoneInfo("America/New_York")


def leg_dict(lg) -> dict:
    return {"symbol": lg.symbol, "side": lg.side, "ratio": lg.ratio, "cp": lg.cp, "strike": lg.strike, "expiry": lg.expiry.isoformat(), "bid": lg.bid, "ask": lg.ask}


def legs_of(row: dict, quotes: dict | None = None):
    out = []
    for d in row["legs"]:
        q = (quotes or {}).get(d["symbol"]) or {}
        out.append(S.Leg(symbol=d["symbol"], side=d["side"], ratio=d["ratio"], cp=d["cp"], strike=d["strike"], expiry=date.fromisoformat(d["expiry"]),
                         bid=q.get("bid", 0.0), ask=q.get("ask", 0.0)))
    return out


def ceil_cent(x: float) -> float:
    return math.ceil(round(x * 100, 6)) / 100


def worse_of(model_net: float, broker_net) -> float:
    """Worse for us is the larger signed net (pay more or receive less)."""
    return model_net if broker_net is None else max(model_net, broker_net)


def in_entry_window(now: datetime) -> bool:
    t = now.astimezone(ET)
    if not cal.is_session(t.date()):
        return False
    end = (11, 30) if t.date() in cal.EARLY_CLOSES else P.ENTRY_WINDOW_ET[1]
    return P.ENTRY_WINDOW_ET[0] <= (t.hour, t.minute) <= end


def sessions_held(start: date, day: date) -> int:
    return sum(1 for i in range((day - start).days) if cal.is_session(date.fromordinal(start.toordinal() + i + 1)))


def mark(row: dict, quotes: dict):
    legs = legs_of(row, quotes)
    if any(not (lg.bid > 0 and lg.ask >= lg.bid) for lg in legs):
        return None
    close = S.close_net(legs, "model")
    entry_net = row["entry_net_model"]
    return {"close_net_model": round(close, 4), "close_net_natural": round(S.close_net(legs, "natural"), 4),
            "pnl_model": S.pnl_usd(entry_net, close, row["qty"], legs), "pnl_natural": S.pnl_usd(row["entry_net_natural"], S.close_net(legs, "natural"), row["qty"], legs), "legs": legs}


def decide_exit(row: dict, mk: dict, spec: dict, day: date):
    ex, loss, profit = spec["exit"], row["max_loss_usd"], row.get("max_profit_usd")
    basis = profit if profit else loss
    if ex.get("managed", True) is not False:   # an unmanaged twin is held to its time exit: no target, no stop
        if mk["pnl_model"] >= ex["profit_target_pct"] * basis:
            return "PROFIT_TARGET"
        if mk["pnl_model"] <= -ex["loss_stop_pct"] * loss:
            return "LOSS_STOP"
    first_exp = min(date.fromisoformat(d["expiry"]) for d in row["legs"])
    if (first_exp - day).days <= ex["time_exit_dte"]:
        return "TIME_EXIT_DTE"
    if sessions_held(date.fromisoformat(row["day"]), day) >= ex["max_hold_sessions"]:
        return "TIME_EXIT_HOLD"
    if has_short_legs(row["kind"]) and day >= sel.monday_of_week(first_exp):
        return "EXPIRATION_WEEK_GUARD"
    return None


class Engine:
    def __init__(self, market, broker, ledger: Ledger, specs: dict, approvals: dict, now: datetime, submit: bool, ignore_window: bool = False, account=None):
        self.m, self.b, self.led, self.now, self.submit = market, broker, ledger, now, submit
        self.acct = account or P.ACCOUNTS["OPT-PAPER-1"]
        self.day = now.astimezone(ET).date()
        self.ignore_window = ignore_window
        self.approved = approved_specs(specs, approvals)
        self.specs_all = specs
        self.traded_today = set()   # (spec_id, underlying) already entered or exited today: no same-day re-entry (stops churn, and a backup cron run cannot double up)
        self.c = {"approved_specs": len(self.approved), "opened": 0, "exited": 0, "no_fill": 0, "would_open": 0, "would_exit": 0, "controls_opened": 0, "controls_closed": 0,
                  "skipped": {}, "halts_new": 0}

    def skip(self, why: str):
        self.c["skipped"][why] = self.c["skipped"].get(why, 0) + 1

    def write(self, rtype: str, **f):
        return self.led.append(rtype, now=self.now, **f) if self.submit else None

    # ---------- planning
    def plan(self, spec: dict, root: str, control: bool):
        """Return (plan, reason). Conditions are checked for real entries only: a control enters at random, with the same structure and exits."""
        spot = self.m.spot(root)
        closes = self.m.daily_closes(root, self.day)
        kind = spec["structure"]
        cps = {"C"} if kind.endswith("call") else {"P"} if kind.endswith("put") else {"C", "P"}
        lo, hi = date.fromordinal(self.day.toordinal() + spec["dte_min"]), date.fromordinal(self.day.toordinal() + spec["dte_max"])
        contracts = []
        for cp in sorted(cps):
            contracts += self.m.contracts(root, cp, lo, hi, spot, band=0.12)
        expiry = sel.choose_expiry([date.fromisoformat(c["expiration_date"]) for c in contracts], self.day, spec)
        if expiry is None:
            return None, "NO_ALLOWED_EXPIRY"
        near = [c for c in contracts if date.fromisoformat(c["expiration_date"]) == expiry and c.get("tradable") is not False]
        quotes = self.m.quotes([c["symbol"] for c in near])
        iv = _atm_iv(near, quotes, spot)
        if not control:
            ok, why = M.conditions_met(spec["entry"]["conditions"], M.signals(closes, iv)) if "conditions" in spec["entry"] else (True, "")
            if not ok:
                return None, why
        cands = sel.candidates(spec, spot, expiry, near, quotes)
        if not cands:
            return None, "NO_CANDIDATE"
        k, legs, econ, info = sel.pick(cands, self.day)
        if k is None:
            return None, info
        last_exit = sel.planned_exit(expiry, self.day, spec["exit"]["time_exit_dte"], spec["exit"]["max_hold_sessions"])
        tags = catalysts.tag(spec["underlying_type"], self.day, last_exit)
        net_model, net_nat, net_mid = S.net(legs, "model"), S.net(legs, "natural"), S.net(legs, "mid")
        return {"kind": k, "legs": legs, "econ": econ, "info": info, "tags": tags, "expiry": expiry, "net_model": net_model, "net_natural": net_nat, "net_mid": net_mid,
                "smallest": info["smallest_version_risk_usd"]}, ""

    # ---------- one entry
    def open_one(self, spec: dict, root: str, state: dict, control: bool):
        plan, why = self.plan(spec, root, control)
        if plan is None:
            self.skip(why)
            return
        e = plan["econ"]
        if not control:
            why = self.cap_check(spec, root, e["max_loss_usd"], state)
            if why:
                self.skip(why)
                return
        # Fixed id (owner rule 2026-11-22): spec, underlying, day and real-or-control. It is also the broker's client_order_id, and the broker refuses a repeated
        # client_order_id, so a retried or re-run workflow cannot send the same entry twice. One entry per spec, underlying and day is already the rule.
        sid = f"{spec['id']}-{root}-{self.day.strftime('%y%m%d')}-{'c' if control else 'r'}"
        rec = {"id": sid, "spec_id": spec["id"], "spec_sha256": sha(spec), "underlying": root, "day": self.day.isoformat(), "kind": plan["kind"], "qty": 1,
               "legs": [leg_dict(lg) for lg in plan["legs"]], "entry_net_model": round(plan["net_model"], 4), "entry_net_natural": round(plan["net_natural"], 4),
               "entry_net_mid": round(plan["net_mid"], 4), "max_loss_usd": e["max_loss_usd"], "max_profit_usd": e["max_profit_usd"], "width": e["width"],
               "fees_round_trip_usd": e["fees_round_trip_usd"], "smallest_version_risk_usd": plan["smallest"], "used_fallback_cap": plan["info"]["used_fallback_cap"],
               "catalyst_tagged": plan["tags"]["catalyst_tagged"], "catalyst_events": plan["tags"]["events"], "account": self.acct.label}
        if control:
            if self.submit:
                self.write("CONTROL_ENTRY", **rec)
                state["open_control"][sid] = rec
            self.c["controls_opened"] += 1
            return
        if not self.submit:
            self.c["would_open"] += 1
            return
        if plan["kind"] not in S.ENGINE_SENDS:
            self.skip("STRUCTURE_NOT_ENABLED")
            return
        limit = ceil_cent(plan["net_model"])
        res = self.b.send_and_wait(plan["kind"], plan["legs"], 1, limit, False, sid[:48])
        if not res["filled"]:
            self.write("NO_FILL", id=sid, spec_id=spec["id"], underlying=root, status=res["status"], order_id=res["order_id"], limit_net=limit)
            self.c["no_fill"] += 1
            return
        rec["entry_net_model"] = round(worse_of(plan["net_model"], res["broker_net"]), 4)
        rec.update(order_id=res["order_id"], broker_net=res["broker_net"], limit_net=limit)
        row = self.write("ENTRY", **rec)
        state["open"][sid] = row
        self.c["opened"] += 1

    def cap_check(self, spec: dict, root: str, new_risk: float, state: dict) -> str:
        """Every cap that applies to this account and spec, in one place. Returns a skip reason or an empty string. Controls are outside the caps."""
        a, opened = self.acct, list(state["open"].values())
        if "max_premium_usd" in spec and new_risk > spec["max_premium_usd"] + 1e-9:
            return "OVER_MAX_PREMIUM"
        if sum(r["max_loss_usd"] for r in opened) + new_risk > a.open_risk_cap_usd:
            return "OPEN_RISK_CAP"
        if a.group_cap_usd is not None and root in a.group_members:
            if sum(r["max_loss_usd"] for r in opened if r["underlying"] in a.group_members) + new_risk > a.group_cap_usd:
                return "GROUP_CAP"
        if a.directional_cap_usd is not None and spec.get("directional_group"):
            dir_ids = {sid for sid, sp in self.specs_all.items() if sp.get("directional_group")}
            if sum(r["max_loss_usd"] for r in opened if r["spec_id"] in dir_ids) + new_risk > a.directional_cap_usd:
                return "DIRECTIONAL_CAP"
        return ""

    # ---------- exits
    def manage(self, state: dict, quotes_all: dict):
        for sid, row in list(state["open"].items()) + list(state["open_control"].items()):
            control = sid in state["open_control"]
            spec = self.specs_all.get(row["spec_id"])
            if spec is None:
                self.skip("EXIT_SPEC_MISSING")
                continue
            mk = mark(row, quotes_all)
            if mk is None:
                self.skip("EXIT_NO_QUOTE")
                continue
            reason = decide_exit(row, mk, spec, self.day)
            if not reason:
                continue
            if not self.submit:
                self.c["would_exit"] += 1
                continue
            exit_net = mk["close_net_model"]
            broker_net = None
            if not control:
                res = self.b.send_and_wait(row["kind"], mk["legs"], row["qty"], ceil_cent(mk["close_net_natural"]), True, ("x" + sid)[:48])
                if not res["filled"]:
                    self.skip("EXIT_NO_FILL")
                    continue
                broker_net = res["broker_net"]
                exit_net = worse_of(exit_net, broker_net)
            pnl = S.pnl_usd(row["entry_net_model"], exit_net, row["qty"], mk["legs"])
            f = dict(id=sid, spec_id=row["spec_id"], reason=reason, exit_net_model=round(exit_net, 4), exit_net_natural=mk["close_net_natural"], broker_net=broker_net,
                     pnl_model_usd=pnl, pnl_natural_usd=mk["pnl_natural"], account=self.acct.label, day=self.day.isoformat())
            self.traded_today.add((row["spec_id"], row["underlying"]))
            if control:
                self.write("CONTROL_EXIT", **f)
                state["open_control"].pop(sid, None)
                self.c["controls_closed"] += 1
            else:
                self.write("EXIT", **f)
                state["open"].pop(sid, None)
                self.c["exited"] += 1
        return state

    # ---------- results and halts
    def results(self, state: dict, quotes_all: dict) -> dict:
        res = {}
        for c in state["closed"]:
            res[c["entry"]["spec_id"]] = res.get(c["entry"]["spec_id"], 0.0) + c["exit"]["pnl_model_usd"]
        for row in state["open"].values():
            mk = mark(row, quotes_all)
            if mk:
                res[row["spec_id"]] = res.get(row["spec_id"], 0.0) + mk["pnl_model"]
        return res

    def month_result(self, state: dict, quotes_all: dict) -> float:
        """Month to date: closed results with an exit this month plus the open marks."""
        month = self.day.isoformat()[:7]
        closed = sum(c["exit"]["pnl_model_usd"] for c in state["closed"] if str(c["exit"].get("day") or c["exit"].get("ts", ""))[:7] == month)
        opened = sum((mark(r, quotes_all) or {}).get("pnl_model", 0.0) for r in state["open"].values())
        return closed + opened

    def evaluate_halts(self, state: dict, results: dict, quotes_all: dict | None = None):
        if self.acct.monthly_halt_usd is not None:
            month = self.day.isoformat()[:7]
            mr = self.month_result(state, quotes_all or {})
            self.c["month_review_line_reached"] = bool(self.acct.monthly_review_usd is not None and mr <= self.acct.monthly_review_usd)
            if mr <= self.acct.monthly_halt_usd and not state["halts"].get(("month", month)):
                self.write("HALT", scope="month", spec_id=month, result_usd=round(mr, 2))
                state["halts"][("month", month)] = True
                self.c["halts_new"] += 1
        total = sum(results.values())
        if total <= self.acct.account_halt_usd and not state["halts"].get(("account", None)):
            self.write("HALT", scope="account", result_usd=round(total, 2))
            state["halts"][("account", None)] = True
            self.c["halts_new"] += 1
        for sid, v in results.items():
            if v <= self.acct.strategy_halt_usd and not state["halts"].get(("strategy", sid)):
                self.write("HALT", scope="strategy", spec_id=sid, result_usd=round(v, 2))
                state["halts"][("strategy", sid)] = True
                self.c["halts_new"] += 1

    def reconcile(self, state: dict) -> bool:
        if self.b is None or not self.submit:
            return True
        held = {p["symbol"] for p in self.b.positions() if ao.is_option(p.get("symbol"))}
        ledger_syms = {lg["symbol"] for r in state["open"].values() for lg in r["legs"]}
        return held == ledger_syms

    # ---------- the pass
    def run(self) -> dict:
        c = self.c
        if not cal.is_session(self.day):
            c["note"] = "not a market session"
            return c
        if self.day < self.acct.start:
            c["note"] = f"program starts {self.acct.start.isoformat()}"
            return c
        self.led.guard()
        rows = self.led.rows()
        state = reduce(rows)
        if self.submit:
            for sid, spec in self.approved.items():
                frozen = state["specs"].get(sid)
                if frozen is None:
                    self.write("SPEC_FROZEN", spec_id=sid, spec_sha256=sha(spec), spec=spec)
                elif frozen["spec_sha256"] != sha(spec):
                    self.approved = {k: v for k, v in self.approved.items() if k != sid}
                    self.skip("SPEC_CHANGED_AFTER_FREEZE")
            state = reduce(self.led.rows())
        today_s = self.day.isoformat()
        for r in self.led.rows():
            if r["type"] in ("ENTRY", "CONTROL_ENTRY") and r.get("day") == today_s:
                self.traded_today.add((r["spec_id"], r["underlying"]))
        reconciled = self.reconcile(state)
        c["reconciled"] = reconciled
        syms = sorted({lg["symbol"] for r in list(state["open"].values()) + list(state["open_control"].values()) for lg in r["legs"]})
        quotes_all = self.m.quotes(syms) if syms else {}
        results = self.results(state, quotes_all)
        if self.submit:
            self.evaluate_halts(state, results, quotes_all)
        self.manage(state, quotes_all)
        entries_ok = ((in_entry_window(self.now) or self.ignore_window) and reconciled and not state["halts"].get(("account", None))
                      and not state["halts"].get(("month", self.day.isoformat()[:7])))
        if not reconciled:
            c["note"] = "broker positions differ from the ledger: no new entries until the owner looks (a reset or a manual trade)"
        if entries_ok:
            for sid, spec in sorted(self.approved.items()):
                if state["halts"].get(("strategy", sid)):
                    self.skip("STRATEGY_HALTED")
                    continue
                for root in spec["underlyings"]:
                    held = [r for r in state["open"].values() if r["spec_id"] == sid]
                    if len(held) >= P.MAX_OPEN_PER_STRATEGY:
                        self.skip("AT_MAX_OPEN_PER_STRATEGY")
                    elif any(r["underlying"] == root for r in held):
                        self.skip("ALREADY_OPEN_ON_UNDERLYING")
                    elif (sid, root) in self.traded_today:
                        self.skip("TRADED_TODAY")
                    else:
                        try:
                            self.open_one(spec, root, state, control=False)
                        except Exception as exc:  # one bad underlying never stops the rest
                            self.skip("ERROR_" + type(exc).__name__)
                    if self.submit:
                        heldc = [r for r in state["open_control"].values() if r["spec_id"] == sid]
                        rng = random.Random(f"{sid}|{root}|{self.day.isoformat()}")
                        if (len(heldc) < P.MAX_OPEN_PER_STRATEGY and not any(r["underlying"] == root for r in heldc) and (sid, root) not in self.traded_today
                                and rng.random() < P.CONTROL_DAILY_PROB):
                            try:
                                self.open_one(spec, root, state, control=True)
                            except Exception as exc:
                                self.skip("CONTROL_ERROR_" + type(exc).__name__)
        c["open_now"] = len(state["open"])
        c["open_controls"] = len(state["open_control"])
        if self.submit:
            self.write("RUN", day=self.day.isoformat(), counts={k: v for k, v in c.items() if k != "note"})
            self.led.write_checkpoint(self.day.isoformat())
        return c


def _atm_iv(contracts, quotes, spot):
    best = None
    for c in contracts:
        q = quotes.get(c["symbol"])
        if q and q.get("iv"):
            gap = abs(float(c["strike_price"]) - spot)
            if best is None or gap < best[0]:
                best = (gap, float(q["iv"]))
    return best[1] if best else None
