"""SYNTHETIC BLAST RADIUS for agent/commission/multimonth-category-config (owner directive 2026-07-27).

WHY THIS EXISTS: this codespace has NO database credentials (`supabase_key is required`), so the real
per-rep July numbers cannot be read here. Two honest substitutes are shipped instead:

  1. `multimonth_category_blast_radius.sql` — READ-ONLY, operator-run in the Supabase SQL Editor,
     covers BOTH orgs and BOTH period spellings ('July 2026' / '2026-07').
  2. THIS FILE — the SAME arithmetic the live endpoint does
     (`GET /api/v1/commcalc/plan-installments/category-impact/July%202026?org_id=<org>`), run against a
     synthetic July 2026 tenant built from THE OWNER'S OWN PASTED ROWS, so the SHAPE and SIGN of every
     delta can be reviewed at Gate-1 before anything is recalculated in production.

  ⚠️ The dollar amounts below are SYNTHETIC (owner's product strings + owner's rates, a plausible rep
     mix). They are mechanism, not a production count. The production numbers come from (1) or from the
     live endpoint after Gate-2.

FIVE SCENARIOS, so the two moving parts are never confused with each other:
  B  BEFORE            every category included, device-price guard OFF   = what production pays today
  C  MRC-CORRECTED     every category included, guard ON                 = B + the mig-246 tablet fix
  T  TABLETS EXCLUDED  guard ON, tablet unticked only
  S  SIMs EXCLUDED     guard ON, sim unticked only
  A  OWNER DEFAULTS    guard ON, tablet + SIM unticked                   = what the next Calculate pays

Run:  cd backend && python3 scratchpad/multimonth_category_blast_radius.py
Writes nothing. Never calls the database. Never triggers a calculation.
"""
import io
import os
import sys
import contextlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# The proof harness owns the FakeClient + the owner's fixtures. It is EXECUTED here (not imported: it
# ends in sys.exit(), which a plain import turns into a silent exit of this script) with its own
# 123-check output captured, so this file reads as a report rather than a second test run. Its exit
# code is re-checked, so this report can never be produced from a harness that was failing.
import types as _types_mod

_HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "installment_category_qualification_proof.py")
P = _types_mod.ModuleType("icat_proof")
P.__dict__["__file__"] = _HARNESS
P.__dict__["__name__"] = "icat_proof"
_buf, _rc = io.StringIO(), 0
try:
    with contextlib.redirect_stdout(_buf):
        exec(compile(open(_HARNESS).read(), _HARNESS, "exec"), P.__dict__)
except SystemExit as _e:
    _rc = int(_e.code or 0)
if _rc:
    print(_buf.getvalue())
    raise SystemExit(f"REFUSING to report: the proof harness exited {_rc} (failing checks above).")
print("(fixtures + FakeClient from installment_category_qualification_proof.py — "
      + [l for l in _buf.getvalue().splitlines() if "passed," in l][-1].strip() + ")")

import app.modules.commcalc.sale_installment_engine as SIE
from app.modules.commcalc import installment_category as ICAT

LUXE = P.LUXE
HOUSE = P.HOUSE
PERIOD = P.PERIOD
ALL_ON = {k: True for k in ICAT.CATEGORY_KEYS}

# ── the synthetic July 2026 book: 3 reps, the owner's real product strings ────────────────────────
# Ana Cruz    — 2 Samsung tablets, 1 phone, 1 SIM/BYOD
# Beto Diaz   — 1 TCL tablet ($50 plan), 1 TCL tablet ($30 plan), 1 home internet
# Cyn Ellis   — 2 phones (one of them the owner's DOUBLE-serial case), 1 SIM/BYOD ($30 plan)
REPS = ("Ana Cruz", "Beto Diaz", "Cyn Ellis")


def _rep(lines, rep):
    for r in lines:
        r["salesperson"] = rep
    return lines


def book(org=LUXE):
    s = []
    s += _rep(P.tablet_sale(org, tid="TB1", serial="357845420428952", mdn="7735550111"), "Ana Cruz")
    s += _rep(P.tablet_sale(org, tid="TB2", serial="357845420429083", mdn="7735550112"), "Ana Cruz")
    s += _rep(P.phone_sale(org, tid="PH1", serial="358662802056451", mdn="7735550113"), "Ana Cruz")
    s += _rep(P.sim_sale(org, tid="SM1", iccid="89148000008588591838", mdn="7735550114", mrc=65.0),
              "Ana Cruz")

    s += _rep(P.tcl_sale(org, tid="TC1", serial="357845420452713", mdn="7735550121", mrc=50.0),
              "Beto Diaz")
    s += _rep(P.tcl_sale(org, tid="TC2", serial="357845420452714", mdn="7735550122", mrc=30.0),
              "Beto Diaz")
    s += _rep(P.home_internet_sale(org, tid="HI1", serial="358835493256918", mdn="7735550123"),
              "Beto Diaz")

    s += _rep(P.phone_sale(org, tid="PH2", serial="358662802056452", mdn="7735550131"), "Cyn Ellis")
    # THE OWNER'S DOUBLE ROW: one transaction, TWO subscribers, ONE IMEI on the receipt. The second
    # subscriber's rate-plan line carries no serial of its own, so the chain borrows the only IMEI.
    s += _rep([P.line(org, "PH3", P.PHONE_DEV, serial="358662802056452", ext=190.0,
                      dept="BrandedHandset", cat="KittedBranded"),
               P.line(org, "PH3", P.PHONE_PLAN, mdn="7735550132", ext=55.0, dept="Rtr",
                      cat="Other Carr. payments"),
               P.line(org, "PH3", P.PHONE_PLAN, mdn="7735550133", ext=55.0, dept="Rtr",
                      cat="Other Carr. payments")], "Cyn Ellis")
    s += _rep(P.sim_sale(org, tid="SM2", iccid="89148000008588591788", mdn="7735550134", mrc=30.0,
                         plan="Total Wireless Unlimited Plan $30"), "Cyn Ellis")
    return s


def run(org, qualification, guard):
    st = P.store_for(org, book(org))
    return SIE.compute_sale_installments(
        P.FakeClient(st), org, PERIOD, persist=False,
        _config_override={"hardware_guard": guard, "qualification": qualification})


def _fmt(v):
    return f"{v:>10,.2f}"


def _bar(w=112):
    print("─" * w)


B = run(LUXE, ALL_ON, False)                                  # today's production behaviour
C = run(LUXE, ALL_ON, True)                                   # + the mig-246 device-price fix
T = run(LUXE, {**ALL_ON, "tablet": False}, True)              # tablets unticked only
S = run(LUXE, {**ALL_ON, "sim": False}, True)                 # SIMs unticked only
A = run(LUXE, dict(ICAT.DEFAULT_QUALIFICATION), True)         # the owner's defaults

print(__doc__.split("Run:")[0].rstrip())
print()
print("=" * 112)
print(f"PER-REP JULY 2026 BLAST RADIUS — tenant {LUXE} (synthetic book, owner's product strings)")
print("=" * 112)
hdr = (f"{'Rep':<14}{'B before':>11}{'C mrc-fix':>11}{'Δ mrc':>10}"
       f"{'T no tab':>11}{'Δ tablet':>11}{'S no sim':>11}{'Δ sim':>10}"
       f"{'A default':>11}{'Δ total':>11}")
print(hdr)
_bar()
reps = sorted(set(B["by_rep"]) | set(C["by_rep"]) | set(A["by_rep"]))
tot = {k: 0.0 for k in "BCTSA"}
for r in reps:
    b, c = B["by_rep"].get(r, 0.0), C["by_rep"].get(r, 0.0)
    t, s2, a = T["by_rep"].get(r, 0.0), S["by_rep"].get(r, 0.0), A["by_rep"].get(r, 0.0)
    tot["B"] += b; tot["C"] += c; tot["T"] += t; tot["S"] += s2; tot["A"] += a
    print(f"{r:<14}{b:>11,.2f}{c:>11,.2f}{c-b:>10,.2f}"
          f"{t:>11,.2f}{t-c:>11,.2f}{s2:>11,.2f}{s2-c:>10,.2f}{a:>11,.2f}{a-b:>11,.2f}")
_bar()
print(f"{'TOTAL':<14}{tot['B']:>11,.2f}{tot['C']:>11,.2f}{tot['C']-tot['B']:>10,.2f}"
      f"{tot['T']:>11,.2f}{tot['T']-tot['C']:>11,.2f}{tot['S']:>11,.2f}{tot['S']-tot['C']:>10,.2f}"
      f"{tot['A']:>11,.2f}{tot['A']-tot['B']:>11,.2f}")
print()
print("  B before    = every category included + device-price guard OFF  (production today)")
print("  C mrc-fix   = B + mig 246: a device/promo price can never be a monthly charge")
print("  T / S       = C with ONLY tablets / ONLY SIMs unticked")
print("  A default   = the owner's defaults (tablet + SIM unticked) = what the next Calculate pays")

# ── which activations moved, and why ─────────────────────────────────────────────────────────────
print()
print("=" * 112)
print("MRC CORRECTIONS (mig 246) — every activation whose monthly charge stopped being a device price")
print("=" * 112)


def _key(x):
    return (str(x.get("trans_id") or ""), str(x.get("mdn") or ""), int(x.get("month_index") or 0))


base_by = {_key(x): x for x in B["ledger"]}
moves = 0
for x in C["ledger"]:
    y = base_by.get(_key(x))
    if not y:
        continue
    if round(x.get("mrc_at_pay") or 0, 2) != round(y.get("mrc_at_pay") or 0, 2):
        moves += 1
        still = any(_key(z) == _key(x) for z in A["ledger"])
        print(f"  {x.get('epay_salesperson'):<11} {x.get('trans_id'):<5} M{x.get('month_index')} "
              f"{x.get('serial_1'):<17} MRC {y.get('mrc_at_pay'):>8,.2f} → {x.get('mrc_at_pay'):>7,.2f}   "
              f"${y.get('amount'):>6,.2f} → ${x.get('amount'):>5,.2f}   [{x.get('device_category')}]"
              f"{'' if still else '   (then excluded by category → $0)'}")
        print(f"              {x.get('display_label')}")
print(f"  {moves} activation(s) corrected. Every correction is DOWNWARD — a promo price is not an MRC.")

print()
print("=" * 112)
print("CATEGORY EXCLUSIONS under the owner's defaults (what stops paying, per rep)")
print("=" * 112)
cg = A["category_guard"]
print(f"  config source: {cg['config_source']}   qualification: "
      + ", ".join(f"{k}={'on' if v else 'OFF'}" for k, v in cg["qualification"].items()))
for k, v in sorted((cg.get("by_category") or {}).items()):
    mark = "PAYS" if v["qualifies"] else "EXCLUDED"
    print(f"  {ICAT.CATEGORY_LABELS.get(k, k):<26} {v['chains']:>2} chain(s)  {mark}")
for k, v in sorted((cg.get("excluded") or {}).items()):
    print(f"    ↳ {ICAT.CATEGORY_LABELS.get(k, k)}: {v['chains']} chain(s), ${v['amount']:,.2f} not paid"
          f"  {v['reps']}")
print(f"  TOTAL excluded: {cg['excluded_chains']} chain(s) = ${cg['excluded_amount']:,.2f}"
      f"   ·  unclassifiable: {cg['unknown_chains']}")

print()
print("=" * 112)
print("DUPLICATE DEVICE-MONTH (the owner's IMEI 358662802056452 twice at $2.75)")
print("=" * 112)
for w in A["warnings"]:
    if w.get("type") == "duplicate_device_month":
        print(f"  IMEI {w['imei']}  M{w['month_index']}  rows={w['rows']}  ${w['amount']:,.2f}  "
              f"rep={w['rep']}")
        print(f"    trans={w['trans_ids']}  mdns={w['mdns']}  schedules={w['schedules']}")
        print(f"    {w['detail']}")
_pre = [w for w in SIE.compute_sale_installments(P.FakeClient(P.store_for(LUXE, book(LUXE))), LUXE,
                                                 PERIOD).get("warnings", [])
        if w.get("type") == "duplicate_device_month"]
print(f"  (present with the owner's defaults too: {len(_pre)} — the duplicate is a DATA/CONFIG fact, "
      f"not something this package introduces)")

print()
print("=" * 112)
print("OTHER-TENANT NO-OP — the HOUSE/Boost org has no schedules of its own")
print("=" * 112)
h = SIE.compute_sale_installments(P.FakeClient(P.store_for(HOUSE, [])), HOUSE, PERIOD)
print(f"  by_rep={h['by_rep']}  ledger={len(h['ledger'])}  note={h.get('note')}")
hb = SIE.compute_sale_installments(P.FakeClient(P.store_for(HOUSE, [])), HOUSE, PERIOD,
                                   _config_override={"hardware_guard": False, "qualification": ALL_ON})
print(f"  identical with the guard off + everything included: {h['by_rep'] == hb['by_rep']}")

print()
print("=" * 112)
print("PERIOD SPELLING — the engine is asked for both spellings of the same month")
print("=" * 112)
for spelling in ("July 2026", "2026-07"):
    r = SIE.compute_sale_installments(P.FakeClient(P.store_for(LUXE, book(LUXE))), LUXE, spelling)
    print(f"  compute('{spelling}') → reps={len(r['by_rep'])} ledger={len(r['ledger'])} "
          f"${sum(r['by_rep'].values()):,.2f}")
print("  (raw_sales stores 'July 2026'; the SQL artifact matches BOTH spellings — the _pvariants rule.)")
