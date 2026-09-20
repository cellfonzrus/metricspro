"""DB-FREE PROOF — TOTAL ACTIVATION counts one sale twice when its lines land in two buckets.

OWNER, 2026-09-20: "the total activations should still match for every month as they are coming from
b2b data … so why does the system calculate less commission".

Reconciling that question on live rows turned up three separate mechanisms, and this harness pins the
one that is a DEFECT rather than a policy:

  A. A RETURN drops out.        Canonical skip rule, correct, and it explains an exact −1.
  B. UPGRADES pay $0.           The plan's own `mtd_rates.upgrade = 0`. Deliberate config, not a miss.
  C. A MIXED-BUCKET TICKET IS COUNTED TWICE.  ← the defect this file is about.

(C): TOTAL ACTIVATION is the sum of four DISTINCT-TRANSACTION counts, but `router._sales_cell_agg`
assigns a transaction to a bucket PER LINE. One ticket carrying, say, a tablet 'Activation AAL' line
AND a 'BYOD Activation' line joins BOTH sets and is counted twice. It is one sale. On an exec-MTD-basis
plan every extra bucket membership is another paid unit, so the inflation is also an over-payment.

Live, org 854f6d7b, May–Sep 2026: 211 such tickets (NY 92, Chicago 119) — e.g. July txns 4191 / 4687 /
5323 for one rep alone. NOTHING IS FIXED HERE. Which bucket such a ticket belongs to is a money
decision and it is the owner's; this ships the MEASUREMENT so the gap against b2bsoft can be
reconciled ticket by ticket instead of guessed at.

`activation_bucketing.mixed_bucket_transactions` re-classifies nothing — it reads the sets the
classifier already produced, so it can never disagree with the thing it reports on.

Run:  cd backend && python3 harness_activation_cross_bucket.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app.modules.commcalc.activation_bucketing as AB
from app.modules.commcalc.calculator import classify_contract_type

FAILS = []
N = [0]


def ok(label, cond, detail=""):
    N[0] += 1
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"   {detail}"))
    if not cond:
        FAILS.append(label)


def eq(label, got, want):
    ok(label, got == want, f"got={got!r} want={want!r}")


def cell(rep="Kellie", store="A STORE", prem=(), byod=(), upg=()):
    return {"store": store, "salesperson": rep,
            "_prem": set(prem), "_byod": set(byod), "_upg": set(upg)}


# ══ §A  THE DEFECT, REPRODUCED ═══════════════════════════════════════════════════════════════════
print("\n§A  one ticket, two buckets, counted twice")
# txn '4191' live: an 'Activation AAL' tablet line AND a 'BYOD Activation' line on the SAME ticket.
c = {("s", "Kellie", "2026-07-06"): cell(prem=("4191", "4200"), byod=("4191",))}
r = AB.mixed_bucket_transactions(c)
eq("A1 the ticket is identified", sorted(r["transactions"]), ["4191"])
eq("A2 with both buckets named", r["transactions"]["4191"]["buckets"], ["Activation", "BYOD"])
eq("A3 it inflates TOTAL ACTIVATION by exactly one unit", r["extra_units"], 1)
eq("A4 TOTAL ACTIVATION here reads 3 for 2 real sales",
   len(c[("s", "Kellie", "2026-07-06")]["_prem"]) + len(c[("s", "Kellie", "2026-07-06")]["_byod"]), 3)
eq("A5 the rep who carries it is named", r["transactions"]["4191"]["rep"], "Kellie")
eq("A6 and the store", r["transactions"]["4191"]["store"], "A STORE")

# ══ §B  NEGATIVE CONTROLS — a clean month must report nothing ════════════════════════════════════
print("\n§B  negative controls")
eq("B1 disjoint buckets -> no finding",
   AB.mixed_bucket_transactions({("s", "R", "d"): cell(prem=("1",), byod=("2",), upg=("3",))})["count"], 0)
eq("B2 empty cells -> no finding", AB.mixed_bucket_transactions({})["count"], 0)
eq("B3 None -> no finding", AB.mixed_bucket_transactions(None)["count"], 0)
eq("B4 the SAME txn in the SAME bucket twice is not a finding (it is one set)",
   AB.mixed_bucket_transactions({("s", "R", "d1"): cell(prem=("9",)),
                                 ("s", "R", "d2"): cell(prem=("9",))})["count"], 0)
ok("B5 a blank trans_id is never reported",
   AB.mixed_bucket_transactions({("s", "R", "d"): cell(prem=("", "  "), byod=("", "  "))})["count"] == 0)

# ══ §C  ALL THREE BUCKETS, AND THE ARITHMETIC ════════════════════════════════════════════════════
print("\n§C  a ticket in all three buckets counts twice over")
r3 = AB.mixed_bucket_transactions({("s", "R", "d"): cell(prem=("7",), byod=("7",), upg=("7",))})
eq("C1 three buckets named", r3["transactions"]["7"]["buckets"], ["Activation", "BYOD", "Upgrade"])
eq("C2 extra = buckets - 1", r3["transactions"]["7"]["extra"], 2)
eq("C3 extra_units aggregates across tickets",
   AB.mixed_bucket_transactions({("s", "R", "d"): cell(prem=("7", "8"), byod=("7", "8"))})["extra_units"], 2)
eq("C4 per-rep attribution, ranked",
   AB.mixed_bucket_transactions({("s", "R1", "d"): cell(rep="R1", prem=("1",), byod=("1",)),
                                 ("s", "R2", "d"): cell(rep="R2", prem=("2", "3"), byod=("2", "3"))}
                                )["by_rep"], {"R2": 2, "R1": 1})

# ══ §D  WHY THESE TICKETS EXIST — the AAL labels ARE classified ══════════════════════════════════
print("\n§D  the 'AAL' contract types are recognised (they are NOT the missing units)")
for label, want in (("Activation AAL", "premium"), ("Activation With IDV AAL", "premium"),
                    ("Port with IDV AAL", "premium"), ("BYOD Activation AAL", "byod"),
                    ("BYOD Port AAL", "byod"), ("Activation", "premium"), ("Upgrade", "upgrade")):
    eq(f"D1 {label!r} -> {want}", classify_contract_type(label), want)
eq("D2 a blank contract type is not an activation", classify_contract_type(""), None)
ok("D3 so an AAL ticket lands in a bucket — and a ticket with BOTH an AAL activation line and a "
   "BYOD line lands in two",
   classify_contract_type("Activation AAL") == "premium"
   and classify_contract_type("BYOD Activation") == "byod")

# ══ §E  PURITY + RULE TWO ════════════════════════════════════════════════════════════════════════
print("\n§E  pure, and free of tenant vocabulary")
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "modules", "commcalc",
                         "activation_bucketing.py")).read()
_fn = _src[_src.index("def mixed_bucket_transactions("):]
_body = "\n".join(l for l in _fn.splitlines() if not l.strip().startswith("#")).lower()
eq("E1 no tenant / carrier / market name",
   [w for w in ("luxelink", "boost", "cricket", "total wireless", "chicago", '"ny"') if w in _body], [])
ok("E2 no I/O — the module imports no client, no network, no DB",
   not any(t in _src for t in ("import requests", "supabase", "client.schema", "psycopg")))
_before = {("s", "R", "d"): cell(prem=("1",), byod=("1",))}
_snapshot = {k: {kk: (set(vv) if isinstance(vv, set) else vv) for kk, vv in v.items()}
             for k, v in _before.items()}
AB.mixed_bucket_transactions(_before)
eq("E3 it does not mutate the cells it reads", _before, _snapshot)

print("\n" + "=" * 78)
print(f"{N[0]} checks, {len(FAILS)} failed" + ("" if not FAILS else f"  -> {FAILS}"))
print("=" * 78)
sys.exit(1 if FAILS else 0)
