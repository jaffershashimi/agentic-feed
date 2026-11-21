"""python3 -m consumer.opt_program <command>. Commands: account-check, run [--submit] [--ignore-window], report, checkpoint, verify.
Counts only on stdout (no symbols, strikes, prices or balances)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from consumer import alpaca_paper as ap

from . import broker as B, engine as E, report as R, specs as SP
from .ledger import Ledger, LedgerError, reduce
from .market import Market


def main(argv=None, env=None, call_new=None, call_old=None, now=None, out=print) -> int:
    env = os.environ if env is None else env
    a = argparse.ArgumentParser(prog="opt_program")
    a.add_argument("cmd", choices=["account-check", "run", "report", "checkpoint", "verify"])
    a.add_argument("--specs", default="system/13-options-program/specs")
    a.add_argument("--approvals", default="system/13-options-program/approvals.json")
    a.add_argument("--ledger", default="ledger")
    a.add_argument("--stage1", default="system/13-options-program/stage1.json")
    a.add_argument("--submit", action="store_true")
    a.add_argument("--ignore-window", action="store_true")
    args = a.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    led = Ledger(args.ledger)
    try:
        if args.cmd == "verify":
            out(json.dumps(led.verify()))
            return 0
        if args.cmd == "checkpoint":
            out(json.dumps(led.write_checkpoint(now.astimezone(E.ET).date().isoformat())))
            return 0
        if args.cmd == "report":
            st = json.loads(Path(args.stage1).read_text()) if Path(args.stage1).exists() else {}
            rep = R.build(reduce(led.rows()), st)
            out(R.text(rep))
            out(json.dumps(rep))
            return 0
        if call_new is None:
            call_new = ap.make_client(B.new_env(env))
        if call_old is None and env.get("ALPACA_API_KEY") and env.get("ALPACA_SECRET_KEY"):
            call_old = B.read_only(ap.make_client({"ALPACA_API_KEY": B._first_line(env["ALPACA_API_KEY"]), "ALPACA_SECRET_KEY": B._first_line(env["ALPACA_SECRET_KEY"]),
                                                   "ALPACA_PAPER_TRADE": "true"}))
        chk = B.account_check(call_new, call_old)
        if args.cmd == "account-check":
            out("account check: " + json.dumps(chk))
            out("MULTI-LEG OPTIONS APPROVAL: " + ("YES" if chk["multi_leg_ok"] else "NO"))
            return 0 if chk["multi_leg_ok"] else 1
        if args.submit and not (chk["multi_leg_ok"] and chk.get("distinct_from_existing_account") is True):
            out("REFUSED: orders need multi-leg approval on the new account AND proof that it is a different account from the existing desk's")
            return 1
        specs = SP.load_dir(args.specs)
        approvals = json.loads(Path(args.approvals).read_text()) if Path(args.approvals).exists() else {}
        eng = E.Engine(Market(call_new), B.Broker(call_new), led, specs, approvals, now, submit=args.submit, ignore_window=args.ignore_window)
        out("options run: " + json.dumps(eng.run(), sort_keys=True))
        return 0
    except (ap.GuardError, LedgerError, SP.SpecError) as exc:
        out("REFUSED: " + str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
