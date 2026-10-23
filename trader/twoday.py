"""2-day $100 option bets on real past option prices: `python -m trader.twoday`. Picks are made on May-Jul 2026
and checked on Aug-Oct 2026 (out of sample). Uses trader/budgetbacktest.py rules and data/optionhist/."""

from trader import budgetbacktest as bb
from datetime import date
from collections import defaultdict
HOLD=2
rows=[]
for sym in ["NVDA","AMD","PLTR","QQQ"]:
    chain,closes=bb.load_options(sym),bb.load_closes(sym)
    days=sorted(closes)
    for e in sorted({e for e,_,_ in chain}):
        ed=date.fromisoformat(e)
        for k,d in enumerate(days):
            dte=(ed-date.fromisoformat(d)).days
            if not 5<=dte<=45 or k+HOLD>=len(days) or k<2: continue
            x=days[k+HOLD]
            if x>e: continue
            r1=closes[d]/closes[days[k-1]]-1; r2=closes[d]/closes[days[k-2]]-1
            for rule in ("call_nearest","call_cheapest","put_nearest","call_spread","put_spread"):
                legs=bb.pick(rule,chain,closes[d],d,e)
                if not legs: continue
                cost=sum((chain[(e,t,s)][d]+bb.SLIP) if q>0 else -(chain[(e,t,s)][d]-bb.SLIP) for t,s,q in legs)
                val=bb._value(legs,chain,e,x,closes[x],False)
                if val is None or cost<=0: continue
                rows.append(dict(sym=sym,d=d,dte=dte,rule=rule,r1=r1,r2=r2,ret=val/cost-1))
def dteb(t): return "5-14" if t<=14 else "15-27" if t<=27 else "28-45"
filters={"any day":lambda r:True,"after -3% day":lambda r:r["r1"]<=-0.03,"after +3% day":lambda r:r["r1"]>=0.03,
         "after 2-day drop -5%":lambda r:r["r2"]<=-0.05,"up day":lambda r:r["r1"]>0}
res=[]
for rule in ("call_nearest","call_cheapest","put_nearest","call_spread","put_spread"):
  for db in ("5-14","15-27","28-45"):
    for fn,f in filters.items():
      sel=[r for r in rows if r["rule"]==rule and dteb(r["dte"])==db and f(r)]
      tr=[r["ret"] for r in sel if r["d"]<"2026-08-01"]; te=[r["ret"] for r in sel if r["d"]>="2026-08-01"]
      if len(tr)>=15 and len(te)>=10:
        res.append((sum(tr)/len(tr),rule,db,fn,len(tr),sum(x>0 for x in tr)/len(tr),len(te),sum(te)/len(te),sum(x>0 for x in te)/len(te)))
res.sort(reverse=True)
print(f"{len(res)} setups tested. Top 10 by May-Jul average, then how they did Aug-Oct:")
for a,rule,db,fn,n,w,n2,a2,w2 in res[:10]:
  print(f"{rule:13} DTE {db:5} {fn:20} | May-Jul n={n:3} avg {a:+6.1%} win {w:.0%} | Aug-Oct n={n2:3} avg {a2:+6.1%} win {w2:.0%}")
print("Overall average of ALL setups Aug-Oct:", round(sum(r[7] for r in res)/len(res),3))
allc=[r["ret"] for r in rows]; print("all 2-day option bets:",len(allc),"avg",round(sum(allc)/len(allc),3),"win",round(sum(x>0 for x in allc)/len(allc),2))
