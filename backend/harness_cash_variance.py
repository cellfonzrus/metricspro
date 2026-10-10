"""Offline proof harness for CASH VARIANCE AFTER MANAGEMENT RECON (owner ask 2026-10-10, mig 1070).

Pure stdlib, no DB and no network — it drives the REAL functions in
`app/modules/closing/cash_variance.py` and the REAL verdict truth table
(`deposit_recon.status_for`), never reimplementations of either.

WHAT IS PROVEN
  A. `leg` — a cash figure carries its basis; absence is `present: False`, never 0.00; a basis word
     the module has no behaviour for resolves to None instead of being trusted.
  B. `compare` REFUSES rather than subtracting: leg absent / basis unknown / basis mismatch each
     return `measured: False` with a reason, and NO variance number. Same-basis pairs score through
     `deposit_recon.status_for`, so negative is short on this surface like every other.
  C. THE REGRESSION, from live data measured 2026-10-10 before this shipped: a management count on
     the NET store-cash basis scored against the rep's WHOLE-DRAWER declaration must be
     `basis_mismatch`, not `short`. Six real store-days whose "shortage" was the bill-payment cash
     to the cent (574−324=250, 866−585=281, 240−200=40, 335−260=75, 1699−929=770, 1378−1083=295)
     are replayed; every one must come back unmeasured. Naive subtraction read 141 of 275 counted
     store-days as short and would have accused nine named employees of about $20,000.
  D. The leg builders state the basis from the column that records it: the `envelope_count` ladder
     wins over the pickup; a pre-mig-1039 NULL `amount_basis` is UNKNOWN, not guessed; two counted
     envelopes are promoted to the whole drawer; an UNcounted bill-pay envelope is never added as
     $0.00.
  E. Rollups and trends re-derive nothing — `by_store`/`by_employee` sum to `totals` field by field,
     a group with nothing measured sorts to the BOTTOM, and every number a trend quotes is read off
     its own group.
  F. THE BAND IS CONFIG, NEVER CODE (RULE TWO): `band`/`min_days` resolve the per-org row over the
     house default, NULL/blank/negative/garbage degrade to the house default rather than to zero,
     and — the build lock — `HOUSE_BAND` / `HOUSE_MIN_DAYS` are the ONLY numeric thresholds in the
     module, with the report endpoint spelling none of its own.
  G. Registration: the pure module, the endpoint, the page, the nav entry and the index section all
     exist, so the report can be reached and found.

Run: `cd backend && python3 harness_cash_variance.py`
"""
import re
import sys

sys.path.insert(0, ".")

from app.modules.closing import cash_variance as cv
from app.modules.closing import deposit_recon
from app.modules.closing import billpay_netting

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


BAND = 5.00

# ── A. A cash figure carries its basis. ────────────────────────────────────────────────────────
print("\n== A. leg(): a figure, its basis, and the honest absence ==")
L = cv.leg(250.00, cv.BASIS_STORE, "cash_pickup")
check("A1 a leg carries amount, basis and source",
      (L["amount"], L["basis"], L["source"]) == (250.0, "store_cash", "cash_pickup"), str(L))
check("A2 a leg with a recorded figure is present", L["present"] is True and L["basis_declared"] is True)
A = cv.leg(None, cv.BASIS_TOTAL, "x")
check("A3 ABSENCE IS NEVER ZERO: amount None stays None and present is False",
      A["amount"] is None and A["present"] is False, str(A))
check("A4 a zero that WAS recorded is present (0.00 is a measurement, not an absence)",
      cv.leg(0, cv.BASIS_TOTAL, "x")["present"] is True
      and cv.leg(0, cv.BASIS_TOTAL, "x")["amount"] == 0.0)
for bad in (None, "", "total", "net", "epay", "TOTAL_CASH_v2"):
    pass
check("A5 a basis word the module has no behaviour for is UNKNOWN, never trusted",
      all(cv.leg(1, b, "x")["basis"] is None and cv.leg(1, b, "x")["basis_declared"] is False
          for b in (None, "", "total", "net", "epay", "future_basis")))
check("A6 basis words are deposit_recon's own (mig 509), not a third vocabulary",
      cv.BASIS_TOTAL == "total_cash" and cv.BASIS_STORE == "store_cash"
      and cv.BASIS_BILLPAY == "bill_payment_cash")

# ── B. compare() refuses rather than subtracting. ──────────────────────────────────────────────
print("\n== B. compare(): the only place two cash figures may meet ==")
same_a = cv.leg(300.00, cv.BASIS_TOTAL, "a")
same_b = cv.leg(295.00, cv.BASIS_TOTAL, "b")
c_ok = cv.compare(same_a, same_b, BAND)
check("B1 same basis, inside the band ⇒ measured and 'ok'",
      c_ok["measured"] and c_ok["verdict"] == "ok" and c_ok["variance"] == 5.0, str(c_ok))
c_short = cv.compare(cv.leg(200.00, cv.BASIS_TOTAL, "a"), same_b, BAND)
check("B2 NEGATIVE IS SHORT, the same convention as every closing recon surface",
      c_short["verdict"] == "short" and c_short["variance"] == -95.0, str(c_short))
c_over = cv.compare(cv.leg(400.00, cv.BASIS_TOTAL, "a"), same_b, BAND)
check("B3 positive is over", c_over["verdict"] == "over" and c_over["variance"] == 105.0)
check("B4 the verdict IS deposit_recon.status_for, not a second truth table",
      all(cv.compare(cv.leg(295.00 + d, cv.BASIS_TOTAL, "a"), same_b, BAND)["verdict"]
          == deposit_recon.status_for(round(d, 2), BAND)
          for d in (-200.0, -5.01, -5.0, 0.0, 5.0, 5.01, 200.0)))
for name, got, exp, reason in (
        ("one leg never recorded", cv.leg(None, cv.BASIS_TOTAL, "a"), same_b, "leg_absent"),
        ("the other leg never recorded", same_a, cv.leg(None, cv.BASIS_TOTAL, "b"), "leg_absent"),
        ("a basis nobody recorded", cv.leg(100, None, "a"), same_b, "basis_unknown"),
        ("two different bases", cv.leg(100, cv.BASIS_STORE, "a"), same_b, "basis_mismatch")):
    r = cv.compare(got, exp, BAND)
    check(f"B5 REFUSES on {name} ⇒ {reason}, and NO number",
          r["measured"] is False and r["reason"] == reason
          and r["variance"] is None and r["verdict"] is None, str(r))
check("B6 every refusal carries a sentence a screen can show as-is",
      all(cv.UNMEASURED_REASONS.get(k) and "$" not in cv.UNMEASURED_REASONS[k]
          for k in ("leg_absent", "basis_unknown", "basis_mismatch")))
check("B7 there is NO escape hatch: compare takes legs, never two bare floats",
      "def compare(got, expected, dollar_band)" in open(
          "app/modules/closing/cash_variance.py", encoding="utf-8").read())

# ── C. THE REGRESSION. The six live store-days whose shortage was the bill-pay cash. ───────────
print("\n== C. REGRESSION: the bill-payment cash is not a shortage (live data 2026-10-10) ==")
#         t_cash   epay    counted(net)
LIVE = [(574.00,  324.00,  250.00),
        (866.00,  585.00,  281.00),
        (240.00,  200.00,   40.00),
        (335.00,  260.00,   75.00),
        (1699.00, 929.00,  770.00),
        (1378.00, 1083.00, 295.00)]
for t_cash, epay, counted in LIVE:
    assert round(t_cash - epay, 2) == counted, "fixture is not the measured relationship"
    row = cv.day_row(
        {"close_date": "2026-09-01", "store_code": "B-559", "employee_name": "Radhika"},
        received=cv.received_leg(None, {"actual_picked_amount": counted, "amount_basis": "pos"}, None),
        declared=cv.declared_leg({"t_cash": t_cash, "epay_on_cash": epay}),
        pos=None, dollar_band=BAND)
    head = row["pairs"]["received_vs_envelope"]
    check(f"C1 ${t_cash:,.2f} drawer / ${epay:,.2f} bill-pay / ${counted:,.2f} counted ⇒ "
          f"basis_mismatch, NOT a ${t_cash - counted:,.2f} shortage",
          head["measured"] is False and head["reason"] == "basis_mismatch"
          and head["variance"] is None and row["verdict"] == "not_measured", str(head))
check("C2 the same figures on the SAME basis DO score — the refusal is about the basis, not a "
      "blanket refusal to measure",
      cv.compare(cv.leg(250.00, cv.BASIS_STORE, "p"),
                 cv.leg(250.00, cv.BASIS_STORE, "d"), BAND)["verdict"] == "ok")
check("C3 a pre-mig-1039 pickup (NULL amount_basis) is UNKNOWN, not inferred — 181 of 275 live "
      "counted pickups, 126 of which would 'tie' on the net basis",
      cv.received_leg(None, {"actual_picked_amount": 250.0, "amount_basis": None}, None)["basis"] is None)
check("C4 nor is it inferred from the figures agreeing — same input, same answer",
      cv.received_leg(None, {"actual_picked_amount": 574.0, "amount_basis": None}, None)["basis"] is None)

# ── D. The leg builders state the basis from the column that records it. ───────────────────────
print("\n== D. The three legs ==")
check("D1 the mig-1039 pickup vocabulary is mapped in full — a word billpay_netting gains without "
      "this map gaining an entry would silently change a basis",
      set(cv.PICKUP_BASIS_TO_CASH) == set(billpay_netting.PICKUP_BASES),
      f"{sorted(cv.PICKUP_BASIS_TO_CASH)} vs {sorted(billpay_netting.PICKUP_BASES)}")
check("D2 netting OFF / no POS figure ⇒ the envelope is the WHOLE drawer",
      cv.PICKUP_BASIS_TO_CASH["off"] == cv.BASIS_TOTAL
      and cv.PICKUP_BASIS_TO_CASH["none"] == cv.BASIS_TOTAL)
check("D3 netted by POS or by the declaration ⇒ the envelope is STORE cash",
      cv.PICKUP_BASIS_TO_CASH["pos"] == cv.BASIS_STORE
      and cv.PICKUP_BASIS_TO_CASH["declared"] == cv.BASIS_STORE)
lad = cv.received_leg({"counted_amount": 500.0, "basis": "total_cash"},
                      {"actual_picked_amount": 250.0, "amount_basis": "pos"}, None)
check("D4 THE LADDER: management's own count (mig 936) wins over the DM's count at pickup",
      lad["amount"] == 500.0 and lad["source"] == "envelope_count" and lad["basis"] == "total_cash",
      str(lad))
both = cv.received_leg(None, {"actual_picked_amount": 250.0, "amount_basis": "pos"},
                       {"actual_picked_amount": 324.0})
check("D5 BOTH envelopes counted ⇒ added together and promoted to the whole drawer (which is what "
      "makes the end-to-end POS check possible at all)",
      both["amount"] == 574.0 and both["basis"] == cv.BASIS_TOTAL, str(both))
one = cv.received_leg(None, {"actual_picked_amount": 250.0, "amount_basis": "pos"},
                      {"actual_picked_amount": None})
check("D6 an UNCOUNTED bill-pay envelope is never added as $0.00 — the leg stays store cash",
      one["amount"] == 250.0 and one["basis"] == cv.BASIS_STORE, str(one))
check("D7 no management count at all ⇒ absent, never zero",
      cv.received_leg(None, None, None)["present"] is False
      and cv.received_leg(None, {"actual_picked_amount": None}, None)["amount"] is None)
check("D8 the declared leg is the WHOLE drawer and DOES NOT net (owner 2026-09-02, 'Total cash in "
      "store including Bill Payments')",
      cv.declared_leg({"t_cash": 866.0, "epay_on_cash": 585.0})["amount"] == 866.0
      and cv.declared_leg({"t_cash": 866.0, "epay_on_cash": 585.0})["basis"] == cv.BASIS_TOTAL)
check("D9 t_cash absent falls back to store_cash (the pre-mig-103 era), and both absent is absent",
      cv.declared_leg({"t_cash": None, "store_cash": 120.0})["amount"] == 120.0
      and cv.declared_leg({})["present"] is False)
check("D10 a declaration where bill-pay EXCEEDS total cash (81 live store-days) is left alone, not "
      "netted into negative cash",
      cv.declared_leg({"t_cash": 140.0, "epay_on_cash": 325.0})["amount"] == 140.0)
check("D11 the POS leg is the stored close-gate figure on the whole-drawer basis — not a feed replay",
      cv.pos_leg({"b2b_cash": 842.15})["amount"] == 842.15
      and cv.pos_leg({"b2b_cash": 842.15})["basis"] == cv.BASIS_TOTAL
      and cv.pos_leg({"b2b_cash": 842.15})["source"] == "closing_attempt"
      and cv.pos_leg({})["present"] is False)
check("D12 the declared and POS legs share a basis, so the pair the gate itself compares is "
      "measurable (36 of 166 live store-days tie inside $5)",
      cv.compare(cv.declared_leg({"t_cash": 842.15}), cv.pos_leg({"b2b_cash": 842.15}),
                 BAND)["measured"] is True)

# ── E. Rollups and trends re-derive nothing. ───────────────────────────────────────────────────
print("\n== E. Rollups and trends ==")


def _row(date, store, emp, counted, t_cash, epay=0.0, pos=None, basis="off"):
    return cv.day_row(
        {"close_date": date, "store_code": store, "store_address": store + " St",
         "employee_name": emp, "market": "M"},
        received=cv.received_leg(None, {"actual_picked_amount": counted, "amount_basis": basis}, None)
        if counted is not None else None,
        declared=cv.declared_leg({"t_cash": t_cash, "epay_on_cash": epay}),
        pos=cv.pos_leg({"b2b_cash": pos}) if pos is not None else None,
        dollar_band=BAND)


ROWS = ([_row(f"2026-09-0{d}", "B-1", "Ann", 100.0 - 20 * d, 100.0) for d in (1, 2, 3)]      # short x3
        + [_row(f"2026-09-0{d}", "B-2", "Bob", 100.0 + 30, 100.0) for d in (4, 5, 6, 7)]     # over x4
        + [_row("2026-09-08", "B-3", "Cid", 100.0, 100.0)]                                   # ok
        + [_row("2026-09-09", "B-4", "Dee", None, 100.0)])                                   # unmeasurable
T = cv.totals(ROWS)
H = T["pairs"]["received_vs_envelope"]
check("E1 totals count every state, with not_measured FIRST-CLASS beside short/over/ok",
      (T["days"], H["short"], H["over"], H["ok"], H["not_measured"]) == (9, 3, 4, 1, 1), str(H))
check("E2 short_total and over_total are gross positives; net is over MINUS short, beside them",
      (H["short_total"], H["over_total"], H["net_variance"]) == (120.0, 120.0, 0.0), str(H))
emps = cv.by_employee(ROWS)
stores = cv.by_store(ROWS)
for label, groups, key in (("by_employee", emps, "employee_name"), ("by_store", stores, "store_code")):
    agg = {}
    for g in groups:
        for f in ("short", "over", "ok", "not_measured", "short_total", "over_total"):
            agg[f] = round(agg.get(f, 0) + g["pairs"]["received_vs_envelope"][f], 2)
    check(f"E3 {label} SUMS TO totals field by field — one summariser, so a line can never "
          f"disagree with the tiles above it",
          all(agg[f] == H[f] for f in agg) and sum(g["days"] for g in groups) == T["days"],
          f"{agg} vs {H}")
check("E4 worst first: the biggest shortage is the top line",
      emps[0]["employee_name"] == "Ann" and stores[0]["store_code"] == "B-1", str(emps[0]))
check("E5 a group with NOTHING measured sorts to the BOTTOM, never to the top as a clean sheet",
      emps[-1]["employee_name"] == "Dee" and stores[-1]["store_code"] == "B-4",
      str([e["employee_name"] for e in emps]))
check("E6 store keys are UPPER-CASED, so 'b-1115' can never become a phantom store (the measured "
      "2026-10-09 defect)",
      cv.by_store([_row("2026-09-01", "b-1115", "Ann", 10.0, 10.0),
                   _row("2026-09-02", "B-1115", "Ann", 10.0, 10.0)])[0]["store_code"] == "B-1115")
TR = cv.trends(emps, "employee_name", 3)
kinds = {(t["subject"], t["kind"]) for t in TR}
check("E7 repeat_short fires at min_days and names the person", ("Ann", "repeat_short") in kinds, str(kinds))
check("E8 repeat_over fires too — a drawer repeatedly OVER is cash declared short, not a gift",
      ("Bob", "repeat_over") in kinds, str(kinds))
check("E9 one_direction fires for a run that is only ever one way",
      ("Ann", "one_direction") in kinds and ("Bob", "one_direction") in kinds, str(kinds))
check("E10 nothing_measurable fires for the group that is invisible to the control, not clean",
      ("Dee", "nothing_measurable") in kinds, str(kinds))
check("E11 a group inside the band raises NO trend", not any(t["subject"] == "Cid" for t in TR))
check("E12 every trend quotes its OWN group's numbers — no re-derivation",
      all(abs(t["dollars"]) in (0.0, 120.0) for t in TR)
      and next(t["days"] for t in TR if t["kind"] == "repeat_short") == 3, str(TR[:2]))
check("E13 below min_days nothing is highlighted (one bad day is a bad day)",
      not cv.trends(cv.by_employee([_row("2026-09-01", "B-9", "Eve", 10.0, 100.0)]),
                    "employee_name", 3))
check("E14 min_days is honoured, not ignored — raising it silences the run",
      not any(t["kind"] == "repeat_short" for t in cv.trends(emps, "employee_name", 9)))
check("E15 every trend carries a 'why' a manager can act on, and no trend invents a dollar figure "
      "for an unmeasurable group",
      all(t.get("why") for t in TR)
      and next(t["dollars"] for t in TR if t["kind"] == "nothing_measurable") == 0.0)
BL = cv.measurement_blockers(ROWS)
check("E16 blockers REPORT what cannot be answered rather than hiding it behind a shorter table",
      any(b["pair"] == "received_vs_pos" and b["days"] >= 8 for b in BL), str(BL))
check("E17 each blocker names its reasons with counts, no dollar figures",
      all(b["reasons"] and all(r.get("label") for r in b["reasons"]) for b in BL))
check("E18 EVERY pair is present on every row, measured or not — a dropped pair reads on screen "
      "as a pair that tied",
      all(set(r["pairs"]) == set(cv.PAIR_KEYS) for r in ROWS))
check("E19 verdict_filter covers the states and never silently drops on a typo",
      len(cv.verdict_filter(ROWS, "short")) == 3
      and len(cv.verdict_filter(ROWS, "discrepancy")) == 7
      and len(cv.verdict_filter(ROWS, "not_measured")) == 1
      and len(cv.verdict_filter(ROWS, "shrot")) == len(ROWS)
      and len(cv.verdict_filter(ROWS, "")) == len(ROWS))

# ── F. The band is CONFIG, never code (RULE TWO) — and the build lock. ─────────────────────────
print("\n== F. The $5 band is config, never code ==")
check("F1 the house band is the owner's declared $5 and the house trend threshold is 3 days",
      cv.HOUSE_BAND == 5.00 and cv.HOUSE_MIN_DAYS == 3)
check("F2 an org row overrides the house default",
      cv.band({"cash_variance_band": 2.5}) == 2.5 and cv.min_days({"cash_variance_min_days": 5}) == 5)
for bad in (None, "", "abc", -1, "-2.00", {}):
    row = {} if bad == {} else {"cash_variance_band": bad}
    check(f"F3 NULL/blank/negative/garbage band ({bad!r}) degrades to the house default, NEVER to "
          f"zero — zero would make every rounding difference a shortage against a named employee",
          cv.band(row) == cv.HOUSE_BAND, str(cv.band(row)))
check("F4 the same posture on min_days, and never below 1",
      all(cv.min_days(r) == cv.HOUSE_MIN_DAYS
          for r in ({}, {"cash_variance_min_days": None}, {"cash_variance_min_days": 0},
                    {"cash_variance_min_days": "x"})))
check("F5 a band of exactly 0 IS honourable when an org declares it (tie to the cent)",
      cv.band({"cash_variance_band": 0}) == 0.0)
_SRC = open("app/modules/closing/cash_variance.py", encoding="utf-8").read()
_body = _SRC.split('"""', 2)[2] if _SRC.count('"""') >= 2 else _SRC
_decl = re.findall(r"^(HOUSE_BAND|HOUSE_MIN_DAYS)\s*=", _body, re.M)
# Zero initialisers and `round(x, 2)` are not thresholds; a NON-ZERO dollar literal is.
_floats = [m for m in re.findall(r"(?<![\w.])\d+\.\d+(?![\w.])", re.sub(r"#.*", "", _body))
           if float(m) != 0.0]
check("F6 BUILD LOCK — HOUSE_BAND and HOUSE_MIN_DAYS are declared exactly once each",
      sorted(_decl) == ["HOUSE_BAND", "HOUSE_MIN_DAYS"], str(_decl))
check("F7 BUILD LOCK — the module holds NO second dollar literal: every threshold is an argument, "
      "so a future edit cannot quietly reintroduce a hardcoded band",
      _floats == ["5.00"], str(_floats))
_RSRC = open("app/modules/closing/router.py", encoding="utf-8").read()
_EP = _RSRC.split('@router.get("/cash-variance")', 1)[1].split("\nclass ", 1)[0] \
    if '@router.get("/cash-variance")' in _RSRC else ""
check("F8 BUILD LOCK — the endpoint dereferences the one home and spells no band of its own",
      "_cash_variance.band(cfg_row)" in _EP and "_cash_variance.min_days(cfg_row)" in _EP
      and not re.search(r"(?<![\w.])\d+\.\d+(?![\w.])", re.sub(r"#.*", "", _EP)), _EP[:0] or "see router")
check("F9 BUILD LOCK — the endpoint computes no variance of its own: every figure comes from the "
      "pure module",
      "cv.compare" not in _EP and "- _f(" not in _EP
      and "_cash_variance.day_row(" in _EP and "_cash_variance.totals(" in _EP)
check("F10 the per-org columns are the ones migration 1070 ships",
      all(c in open("../database/migrations/1070_cash_variance_band.sql", encoding="utf-8").read()
          for c in ("cash_variance_band", "cash_variance_min_days")))

# ── G. Registration: reachable and findable. ───────────────────────────────────────────────────
print("\n== G. Registration ==")
check("G1 the endpoint exists on the closing router", '@router.get("/cash-variance")' in _RSRC)
_NAV = open("../frontend/src/lib/rbac.ts", encoding="utf-8").read()
_RI = open("../frontend/src/lib/route-index.ts", encoding="utf-8").read()
check("G2 the screen is REGISTERED so it can be reached and found — nav entry + route index",
      "'/closing/cash-variance'" in _NAV and "'/closing/cash-variance'" in _RI)
check("G3 the page file exists",
      __import__("os").path.exists("../frontend/src/app/(platform)/closing/cash-variance/page.tsx"))
_IDX = open("../docs/SYSTEM_DATA_FLOW_INDEX.md", encoding="utf-8").read()
check("G4 registered in the index (the mandatory owner directive 2026-09-01)",
      "closing/cash_variance.py" in _IDX and "/closing/cash-variance" in _IDX)
check("G5 the pair, basis and verdict vocabularies are served from the pure module, so the screen "
      "spells no key and cannot carry a stale header",
      "pairs" in cv.PAIRS[0] or all(p.get("key") and p.get("label") for p in cv.PAIRS))
check("G6 the page reads those served vocabularies instead of hardcoding a pair key",
      all(k not in open("../frontend/src/app/(platform)/closing/cash-variance/page.tsx",
                        encoding="utf-8").read().replace("received_vs_envelope", "")
          for k in ("declared_vs_pos'", "received_vs_pos'"))
      if __import__("os").path.exists("../frontend/src/app/(platform)/closing/cash-variance/page.tsx")
      else False)


print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
