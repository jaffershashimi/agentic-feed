"""Execution-timing study: price at 09:35, 10:00 and 10:30 ET relative to the 09:30 open for sampled trades of an engine trade list. Usage: python3 scripts/exec_timing_study.py TRADES.json OUT.json N
(Alpaca 5-minute bars; private output.)"""
import json, random, sys, time, urllib.parse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import sys as _s, pathlib as _p
_s.path.insert(0, str(_p.Path(__file__).resolve().parent.parent))
from consumer import alpaca_paper as ap, news_events as ne
ET=ZoneInfo("America/New_York"); call=ap.make_client()
trades=json.load(open(sys.argv[1])); rnd=random.Random(5)
trades=[t for t in trades if t["entry_date"]>="2023-01-03"]
rnd.shuffle(trades); trades=trades[:int(sys.argv[3])]
def bars(sym,day):
    a=datetime.fromisoformat(day).replace(hour=9,minute=29,tzinfo=ET).astimezone(timezone.utc); b=datetime.fromisoformat(day).replace(hour=10,minute=36,tzinfo=ET).astimezone(timezone.utc)
    q={"symbols":sym,"timeframe":"5Min","start":a.strftime("%Y-%m-%dT%H:%M:%SZ"),"end":b.strftime("%Y-%m-%dT%H:%M:%SZ"),"feed":"sip","adjustment":"all","limit":"30"}
    for k in range(6):
        try:
            time.sleep(0.35); j=call("GET","/v2/stocks/bars?"+urllib.parse.urlencode(q),None,"data"); break
        except Exception as e:
            if "429" not in str(e): return None
            time.sleep(4*2**k)
    out={}
    for x in (j.get("bars") or {}).get(sym,[]):
        t=ne.parse_ts(x["t"]).astimezone(ET); out[t.strftime("%H:%M")]=x
    return out
res={"entry":[],"exit":[]}
for t in trades:
    for kind,day in (("entry",t["entry_date"]),("exit",t["exit_date"])):
        if t["how"]=="STOP" and kind=="exit": continue
        b=bars(t["symbol"],day)
        if not b or "09:30" not in b: continue
        o=b["09:30"]["o"]
        row={"sym":t["symbol"],"day":day}
        for hh in ("09:35","10:00","10:30"):
            if hh in b: row[hh]=b[hh]["o"]/o-1
        # fraction of the first 5 minutes' range: where the open sits
        res[kind].append(row)
json.dump(res,open(sys.argv[2],"w"))
import statistics
for kind in ("entry","exit"):
    for hh in ("09:35","10:00","10:30"):
        v=[r[hh] for r in res[kind] if hh in r]
        if v: print(kind,hh,"n",len(v),"mean drift vs 09:30 open bps",round(1e4*statistics.mean(v),1),"median",round(1e4*statistics.median(v),1))
