"""Ranking and transfer report. Ranking metric: net expectancy per dollar at risk after costs, with a bootstrap interval, plus max drawdown, worst trade and trade count.
Win rate is shown and never ranks. Every strategy is shown with and without catalyst-tagged trades (none dropped) and against its random-entry control.
Owner rule of 2026-11-22: paper checks implementation and costs; ranking comes from real-quote history with Holm correction and deflated Sharpe (the Stage 1 file);
paper trades confirm. A strategy without a passing Stage 1 result is labelled 'unranked, not evidence' however many paper trades it has. 30 closed paper trades can
kill a strategy under its kill criteria but never rank it."""
from __future__ import annotations

import random
import statistics

from . import policy as P


def boot_ci(xs, n_boot: int = 4000, seed: int = 20261121, alpha: float = 0.05):
    if len(xs) < 5:
        return None
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(n_boot))
    return (round(means[int(alpha / 2 * n_boot)], 4), round(means[int((1 - alpha / 2) * n_boot) - 1], 4))


def diff_ci(a, b, n_boot: int = 4000, seed: int = 20261122, alpha: float = 0.05):
    if len(a) < 5 or len(b) < 5:
        return None
    rng = random.Random(seed)
    d = sorted(statistics.fmean(rng.choices(a, k=len(a))) - statistics.fmean(rng.choices(b, k=len(b))) for _ in range(n_boot))
    return (round(d[int(alpha / 2 * n_boot)], 4), round(d[int((1 - alpha / 2) * n_boot) - 1], 4))


def max_drawdown(pnls) -> float:
    peak = cum = worst = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        worst = min(worst, cum - peak)
    return round(worst, 2)


def returns(items, key="pnl_model_usd"):
    return [i["exit"][key] / i["entry"]["max_loss_usd"] for i in items]


def block(items) -> dict:
    if not items:
        return {"n": 0}
    r = returns(items)
    pnls = [i["exit"]["pnl_model_usd"] for i in items]
    nat = returns(items, "pnl_natural_usd")
    return {"n": len(items), "expectancy_per_dollar_at_risk": round(statistics.fmean(r), 4), "interval": boot_ci(r),
            "expectancy_if_natural_fills": round(statistics.fmean(nat), 4), "max_drawdown_usd": max_drawdown(pnls), "worst_trade_usd": round(min(pnls), 2),
            "worst_trade_per_dollar": round(min(r), 4), "win_rate_shown_not_ranked": round(sum(1 for p in pnls if p > 0) / len(pnls), 3), "total_result_usd": round(sum(pnls), 2)}


def transfer(items) -> dict:
    """Scale-free result (percent of dollars at risk), plus an $800 account at $50 to $80 risk per trade. Flags a strategy whose smallest version costs more than that."""
    if not items:
        return {}
    lo, hi = P.SMALL_RISK_RANGE_USD
    r = returns(items)
    exp = statistics.fmean(r)
    smalls = sorted(i["entry"]["smallest_version_risk_usd"] for i in items)
    med = smalls[len(smalls) // 2]
    fits = sum(1 for s in smalls if s <= hi) / len(smalls)
    return {"percent_of_dollars_at_risk": round(100 * exp, 2), "scaled_800_per_trade_usd_at_50": round(exp * lo, 2), "scaled_800_per_trade_usd_at_80": round(exp * hi, 2),
            "median_smallest_version_risk_usd": round(med, 2), "share_of_trades_tradeable_at_80": round(fits, 2),
            "flag": None if fits >= 0.5 else f"CANNOT_TRADE_AT_800 (median smallest version risks {med:.0f} USD, above {hi:.0f})"}


def trades_needed(r, t: float = 2.0):
    """Trades for a t-statistic of t at the observed mean and spread per trade (mean / sd x square root of n). None when the mean is not positive or n < 5."""
    if len(r) < 5:
        return None
    m, sd = statistics.fmean(r), statistics.stdev(r)
    if m <= 0 or sd == 0:
        return None
    return int(round((t * sd / m) ** 2))


def strategy_report(spec_id: str, state: dict, stage1: dict | None = None) -> dict:
    real = [c for c in state["closed"] if c["entry"]["spec_id"] == spec_id]
    ctrl = [c for c in state["closed_control"] if c["entry"]["spec_id"] == spec_id]
    tagged = [c for c in real if c["entry"].get("catalyst_tagged")]
    clean = [c for c in real if not c["entry"].get("catalyst_tagged")]
    rr, cr = returns(real), returns(ctrl)
    d = diff_ci(rr, cr)
    s1 = (stage1 or {}).get(spec_id, {})
    history_pass = bool(s1.get("holm_pass")) and bool(s1.get("dsr_pass"))
    beats = bool(d and d[0] > 0)
    contradicts = bool(d and d[1] < 0)       # paper interval entirely below its control
    if not history_pass:
        label = P.UNRANKED_LABEL
    elif contradicts:
        label = "not ranked: paper contradicts the history result (below its random-entry control)"
    elif len(real) >= P.MIN_TRADES_FOR_RANKING and beats:
        label = "ranked: history passed, paper confirms"
    else:
        label = "ranked on history, paper confirmation pending"
    return {"spec_id": spec_id, "label": label, "trades_needed_t2": trades_needed(rr), "all": block(real), "with_catalyst_tag": block(tagged), "without_catalyst_tag": block(clean), "control": block(ctrl),
            "difference_vs_control_per_dollar": None if not d else {"interval": d, "beats_control": beats}, "transfer": transfer(real), "trades_open_now": None}


def build(state: dict, stage1: dict | None = None, account_label: str | None = None) -> dict:
    ids = sorted({c["entry"]["spec_id"] for c in state["closed"]} | set(state["specs"]) | {r["spec_id"] for r in state["open"].values()})
    rep = [strategy_report(i, state, stage1) for i in ids]
    for r in rep:
        r["trades_open_now"] = sum(1 for x in state["open"].values() if x["spec_id"] == r["spec_id"])
    return {"account": account_label or P.ACCOUNT_LABEL, "never_pooled_with_other_accounts": True, "strategies": rep, "no_fill_count": state["no_fill"],
            "halts": {f"{k[0]}:{k[1] or 'all'}": v for k, v in state["halts"].items() if v}}


def text(rep: dict) -> str:
    out = [f"Options program report, account {rep['account']} (never pooled). Counts and percentages only."]
    for s in rep["strategies"]:
        a = s["all"]
        line = f"{s['spec_id']}: {s['label']}. closed {a.get('n', 0)}"
        if a.get("n"):
            line += f", expectancy {a['expectancy_per_dollar_at_risk']:+.3f} per dollar at risk {a['interval']}, drawdown {a['max_drawdown_usd']}, worst {a['worst_trade_usd']}, win rate {a['win_rate_shown_not_ranked']}"
        out.append(line)
    return "\n".join(out)
