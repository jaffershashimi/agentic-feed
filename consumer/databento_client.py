"""Databento historical data client with a hard budget guard. Standard library only. UNTESTED AGAINST THE LIVE API (no key yet): confirm with a free cost estimate first.

Databento sells historical market data (stock and option quotes and trades, to the nanosecond). We want it for one reason: bid and ask history, which Alpaca does not give us. That
lets us measure real execution costs (S-042) and backtest option spreads (S-052). Rules: the key comes only from the environment variable DATABENTO_API_KEY (a GitHub secret in
Actions), is never printed, and a download is refused unless its estimated cost is under the budget cap (DATABENTO_BUDGET_USD, default 5 USD). Raw data is licensed and private:
it never goes into GitHub; only aggregate results do.

  python3 -m consumer.databento_client cost DATASET SCHEMA SYMBOLS START END      # the estimate in USD; downloads nothing
Example: python3 -m consumer.databento_client cost XNAS.ITCH mbp-1 AAPL 2026-11-17T14:30 2026-11-17T15:30
"""
import base64
import json
import os
import sys
import urllib.parse
import urllib.request

BASE = "https://hist.databento.com/v0"
DEFAULT_BUDGET_USD = 5.0


class BudgetError(Exception):
    pass


class KeyMissing(Exception):
    pass


def _key(env=os.environ):
    k = (env.get("DATABENTO_API_KEY") or "").strip()
    if not k:
        raise KeyMissing("DATABENTO_API_KEY is not set")
    return k


def _request(path, params, key, opener=urllib.request.urlopen, method="GET"):
    url = f"{BASE}/{path}"
    data = None
    q = urllib.parse.urlencode(params)
    if method == "GET":
        url += "?" + q
    else:
        data = q.encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{key}:".encode()).decode())   # the key is the user name, the password is empty
    return opener(req, timeout=120).read()


def estimate_cost(dataset, schema, symbols, start, end, stype_in="raw_symbol", key=None, opener=urllib.request.urlopen):
    """Estimated price in USD of a pull. Free to call; downloads nothing."""
    key = key or _key()
    raw = _request("metadata.get_cost", {"dataset": dataset, "schema": schema, "symbols": ",".join(symbols) if isinstance(symbols, (list, tuple)) else symbols,
                                         "start": start, "end": end, "stype_in": stype_in}, key, opener)
    return float(json.loads(raw))


def budget(env=os.environ):
    try:
        return float(env.get("DATABENTO_BUDGET_USD") or DEFAULT_BUDGET_USD)
    except ValueError:
        return DEFAULT_BUDGET_USD


def guard(cost_usd, env=os.environ):
    """Raise unless the cost fits the cap. The only place a download is allowed to start."""
    cap = budget(env)
    if cost_usd > cap:
        raise BudgetError(f"estimated cost {cost_usd:.2f} USD is over the cap of {cap:.2f} USD: ask the owner before raising DATABENTO_BUDGET_USD")
    return cap


def download(dataset, schema, symbols, start, end, out_path, stype_in="raw_symbol", key=None, opener=urllib.request.urlopen, env=os.environ):
    """Estimate, check the cap, then download to a private file. Returns the estimated cost."""
    key = key or _key(env)
    cost = estimate_cost(dataset, schema, symbols, start, end, stype_in, key, opener)
    guard(cost, env)
    body = _request("timeseries.get_range", {"dataset": dataset, "schema": schema, "symbols": ",".join(symbols) if isinstance(symbols, (list, tuple)) else symbols,
                                             "start": start, "end": end, "stype_in": stype_in, "encoding": "csv", "compression": "none"}, key, opener, method="POST")
    with open(out_path, "wb") as fh:
        fh.write(body)
    return cost


def main(argv=None):
    a = list(argv if argv is not None else sys.argv[1:])
    if len(a) != 6 or a[0] != "cost":
        print(__doc__)
        return 2
    try:
        c = estimate_cost(a[1], a[2], a[3].split(","), a[4], a[5])
    except KeyMissing:
        print(json.dumps({"skipped": "DATABENTO_API_KEY is not set"}))
        return 0
    print(json.dumps({"estimated_cost_usd": round(c, 4), "budget_cap_usd": budget(), "within_cap": c <= budget()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
