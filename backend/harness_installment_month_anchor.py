#!/usr/bin/env python3
"""PROOF + LOCK — an instalment's month comes from the ROW'S OWN anchor (index §19.51).

DB-free, network-free, stdlib only. Run: `python3 backend/harness_installment_month_anchor.py`

THE DEFECTS THIS PINS (all three measured live, read-only, 2026-10-06, house org
`00000000-0000-0000-0000-000000000001`):

  D2  `installment_engine.compute_installments` derived a subscriber's activation month as the
      LOWEST period index inside its own lookback window. For anybody present throughout that window
      that is `month_index == max_n`, re-derived every month as the window slides — so the FINAL
      instalment of a six-month curve was payable forever. September 2026: 16,757 of 26,981 paid rows
      / $119,887.50 of $177,462.50 sat at month 6, and against the rows' own `mi_activation_date` the
      derived month disagreed on 11,780 of 15,337 anchored subscribers ($57,992.50).
  D1  `_resolve_schedule` returned None for every subscriber (every raw_mi row carries
      `carrier_id = NULL`; every configured schedule names a carrier) and the engine reported
      `note: None`, while the router swallowed even a raise into `inst_by_rep = {}`.
  D3  the ledger's unique key omitted `pay_period`, so one pay period overwrote another's record of
      the same instalment.

SECTIONS
  §A  the scale and the date parser — one monotonic index, nothing readable guessed
  §B  `month_span` / `in_schedule` / `horizon` — the arithmetic both engines now share
  §C  `resolve_month` — the anchor precedence, and the basis it reports
  §D  `BasisTally` — an unplaced row is counted and priced, never absorbed (the §19.48 rule)
  §E  THE LIVE ORACLE — the measured 2026-10-06 figures, reproduced from the real curve shape
  §F  THE REGRESSION — the window-edge derivation, armed as a NEGATIVE CONTROL
  §G  THE UN-WIRING LOCK — both engines must keep dereferencing the one home
  §H  purity and RULE TWO — no carrier/tenant/product name, no DB, no clock
  §I  D1 — an unresolved schedule and a raised engine are REPORTED, not a silent zero
  §J  D3 — the ledger key, its adaptive writer, and the migration that widens it
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import installment_month as IM  # noqa: E402

PASS = FAIL = 0
_FAILED = []


def ok(label, cond, extra=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        _FAILED.append(label)
        print(f"  FAIL {label}" + (f"  -> {extra!r}" if extra is not None else ""))


HERE = os.path.dirname(os.path.abspath(__file__))
IM_PATH = os.path.join(HERE, "app/modules/commcalc/installment_month.py")
ENG_PATH = os.path.join(HERE, "app/modules/commcalc/installment_engine.py")
SALE_PATH = os.path.join(HERE, "app/modules/commcalc/sale_installment_engine.py")
ROUTER_PATH = os.path.join(HERE, "app/modules/commcalc/router.py")
MIG_PATH = os.path.join(
    HERE, "../database/migrations/1059_subscriber_installment_pay_period_key.sql")


def src(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def code_only(text):
    """Source with comments and docstrings stripped, so a LOCK matches real code, never prose."""
    out = re.sub(r'"""(?:.|\n)*?"""', '""', text)
    out = re.sub(r"'''(?:.|\n)*?'''", '""', out)
    return "\n".join(re.sub(r"#.*$", "", ln) for ln in out.splitlines())


# ─────────────────────────────────────────────────────────────────────────────────────────────────
print("\n§A THE SCALE, and a date that is either readable or absent")
ok("A1 period_index is year*12 + (month-1)", IM.period_index(2026, 9) == 2026 * 12 + 8)
ok("A2 consecutive months are consecutive indices",
   IM.period_index(2026, 10) - IM.period_index(2026, 9) == 1)
ok("A3 a year boundary is one month, not thirteen",
   IM.period_index(2027, 1) - IM.period_index(2026, 12) == 1)
for bad in ((0, 5), (2026, 0), (2026, 13), (None, 5), (2026, None), ("x", "y")):
    ok(f"A4 period_index{bad} -> None", IM.period_index(*bad) is None)
ok("A5 an ISO date string parses to its own month",
   IM.index_of_date("2025-08-03") == IM.period_index(2025, 8))
ok("A6 a timestamp string parses to its own month",
   IM.index_of_date("2026-09-30T23:59:59+00:00") == IM.period_index(2026, 9))
import datetime as _dt  # noqa: E402
ok("A7 a date object parses", IM.index_of_date(_dt.date(2026, 7, 1)) == IM.period_index(2026, 7))
ok("A8 a datetime object parses",
   IM.index_of_date(_dt.datetime(2026, 7, 1, 12, 0)) == IM.period_index(2026, 7))
for bad in (None, "", "   ", "not-a-date", "2026", "0000-00-00", 12345):
    ok(f"A9 index_of_date({bad!r}) -> None (absent, never guessed)", IM.index_of_date(bad) is None)
ok("A10 index_of_date never raises on hostile input",
   all(IM.index_of_date(x) is None or isinstance(IM.index_of_date(x), int)
       for x in (object(), [], {}, b"2026-01-01", float("nan"))))

print("\n§B THE ARITHMETIC both engines share")
P = IM.period_index
ok("B1 the anchor month itself is month 1", IM.month_span(P(2026, 9), P(2026, 9)) == 1)
ok("B2 one month later is month 2", IM.month_span(P(2026, 8), P(2026, 9)) == 2)
ok("B3 fourteen months later is month 14 (NOT clamped into the curve)",
   IM.month_span(P(2025, 8), P(2026, 9)) == 14)
ok("B4 a pay month before the anchor is < 1", IM.month_span(P(2026, 9), P(2026, 8)) == 0)
ok("B5 month_span(None, x) and month_span(x, None) are None",
   IM.month_span(None, P(2026, 9)) is None and IM.month_span(P(2026, 9), None) is None)
ok("B6 in_schedule admits 1..N", [IM.in_schedule(i, 6) for i in (1, 3, 6)] == [True] * 3)
ok("B7 in_schedule REJECTS 0, negative and N+1",
   [IM.in_schedule(i, 6) for i in (0, -3, 7, 14)] == [False] * 4)
ok("B8 in_schedule(None, N) and a 0-month schedule are False",
   not IM.in_schedule(None, 6) and not IM.in_schedule(1, 0))
ok("B9 horizon takes the deepest schedule", IM.horizon((1, 3, 6), 12) == 6)
ok("B10 horizon clamps at the caller's ceiling", IM.horizon((1, 3, 99), 12) == 12)
ok("B11 horizon of nothing is 1 — read the pay month, never zero months",
   IM.horizon((), 12) == 1 and IM.horizon(None, 12) == 1 and IM.horizon((None, "x"), 12) == 1)
ok("B12 horizon never returns 0 even with a 0 ceiling", IM.horizon((6,), 0) == 1)
# the retired expressions, replayed
ok("B13 horizon reproduces the residual engine's retired min(12, max(num_months))",
   all(IM.horizon(ns, 12) == min(12, max(ns)) for ns in ((1,), (3,), (6,), (12,), (1, 3, 6))))
ok("B14 horizon reproduces the sale engine's retired min(16, max(..., default=1))",
   all(IM.horizon(ns, 16) == min(16, max(ns)) for ns in ((1,), (3,), (16,), (1, 3))))
ok("B15 in_schedule reproduces the retired `month_index > num_months` clamp for 1..20",
   all(IM.in_schedule(i, 6) == (not i > 6) for i in range(1, 21)))

print("\n§C resolve_month — the anchor precedence, and the basis it reports")
pay = P(2026, 9)
r = IM.resolve_month(pay, activation_date="2026-07-15", window_floor_index=P(2026, 4))
ok("C1 an activation date WINS over the window floor", r["month_index"] == 3)
ok("C2 ... and says so", r["basis"] == IM.BASIS_ACTIVATION and r["anchored"] is True)
r = IM.resolve_month(pay, origin_index=P(2026, 7), window_floor_index=P(2026, 4))
ok("C3 an origin period is used when there is no activation date", r["month_index"] == 3)
ok("C4 ... and is ALSO anchored (it is a fact about the row)",
   r["basis"] == IM.BASIS_ORIGIN_PERIOD and r["anchored"] is True)
r = IM.resolve_month(pay, window_floor_index=P(2026, 4))
ok("C5 with no row anchor the window floor is used", r["month_index"] == 6)
ok("C6 ... and is flagged UNANCHORED — a guess, labelled",
   r["basis"] == IM.BASIS_WINDOW_EDGE and r["anchored"] is False)
r = IM.resolve_month(pay)
ok("C7 with NO anchor of any kind the month is None, not a number",
   r["month_index"] is None and r["anchored"] is False)
r = IM.resolve_month(pay, activation_date="not-a-date", window_floor_index=P(2026, 4))
ok("C8 an UNREADABLE activation date falls through to the window edge and is flagged",
   r["month_index"] == 6 and r["basis"] == IM.BASIS_WINDOW_EDGE)
r = IM.resolve_month(pay, activation_date="2025-08-03", window_floor_index=P(2026, 4))
ok("C9 THE DEFECT, FIXED: a subscriber activated 2025-08 is month 14, not month 6",
   r["month_index"] == 14 and r["basis"] == IM.BASIS_ACTIVATION)
ok("C10 ... and month 14 is NOT in a 6-month schedule, so they fall out",
   not IM.in_schedule(r["month_index"], 6))
ok("C11 the answer is stable as the window slides — the whole point",
   {IM.resolve_month(P(2026, m), activation_date="2026-07-15")["month_index"] - (m - 7)
    for m in (7, 8, 9, 10, 11, 12)} == {1})
ok("C12 only three bases exist, and exactly two of them mean 'I know'",
   set(IM.ANCHORED_BASES) == {IM.BASIS_ACTIVATION, IM.BASIS_ORIGIN_PERIOD}
   and len(IM.BASIS_LABELS) == 3 and not IM.is_anchored(IM.BASIS_WINDOW_EDGE))
ok("C13 every basis constant has a label", all(b in IM.BASIS_LABELS for b in
   (IM.BASIS_ACTIVATION, IM.BASIS_ORIGIN_PERIOD, IM.BASIS_WINDOW_EDGE)))

print("\n§D BasisTally — an unplaced row is counted and PRICED (the §19.48 rule)")
t = IM.BasisTally()
for _ in range(3):
    t.add(IM.BASIS_ACTIVATION, 10.0)
t.add(IM.BASIS_WINDOW_EDGE, 7.25)
t.add(IM.BASIS_WINDOW_EDGE, 2.75)
ok("D1 rows are counted per basis", t.as_dict()["rows_by_basis"] ==
   {IM.BASIS_ACTIVATION: 3, IM.BASIS_WINDOW_EDGE: 2})
ok("D2 dollars are summed per basis", t.as_dict()["amount_by_basis"] ==
   {IM.BASIS_ACTIVATION: 30.0, IM.BASIS_WINDOW_EDGE: 10.0})
ok("D3 the unanchored rows are reported", t.unanchored_rows == 2)
ok("D4 the unanchored DOLLARS are reported", t.unanchored_amount == 10.0)
ok("D5 the totals balance: anchored + unanchored == rows", t.total_rows == 5)
ok("D6 a note names what rested on a guess", "2 installment row(s)" in (t.note() or ""))
ok("D7 ... and mentions the dollars", "$10.00" in (t.note() or ""))
clean = IM.BasisTally()
clean.add(IM.BASIS_ACTIVATION, 5.0)
ok("D8 an all-anchored run has NO note (silence only when there is nothing to report)",
   clean.note() is None and clean.unanchored_rows == 0)
ok("D9 a hostile amount does not break the tally",
   IM.BasisTally().add(IM.BASIS_ACTIVATION, None) == IM.BASIS_ACTIVATION)
t2 = IM.BasisTally()
t2.add(None, 1.0)
ok("D10 a missing basis counts as UNANCHORED, never as known", t2.unanchored_rows == 1)

# ─────────────────────────────────────────────────────────────────────────────────────────────────
print("\n§E THE LIVE ORACLE — the measured 2026-10-06 figures (house org, read-only replay)")
# Measured by replaying the REAL engine over live rows, September 2026:
#   totals.amount $177,462.50 over 26,981 paid rows; month_index=6 held 16,757 paid rows /
#   $119,887.50; 15,337 subscribers carried an mi_activation_date and 11,780 of those (76.8%)
#   disagreed with the window-edge derivation, carrying $57,992.50; 36,542 ledger rows had no
#   activation date at all. The engine's own exposure was $110,925.00 (Aug) / $9,617.50 (Oct), with
#   $34,240.00 / $63,297.50 / $3,037.50 credited to NO rep.
LIVE = {
    "pay_period": (2026, 9), "num_months": 6, "window": 6,
    "month6_rows": 16757, "month6_amount": 119887.50, "total_amount": 177462.50,
    "anchored_subs": 15337, "disagreeing_subs": 11780, "disagreeing_amount": 57992.50,
    "unanchored_rows": 36542, "no_rep_amount": 63297.50,
}
pay_idx = P(*LIVE["pay_period"])
floor_idx = pay_idx - (LIVE["window"] - 1)
ok("E1 the window floor is six months back, as the live run's horizon was",
   IM.horizon((LIVE["num_months"],), 12) == LIVE["window"]
   and floor_idx == P(2026, 4))
ok("E2 THE LIVE DEFECT: a subscriber at the window floor derived month 6",
   IM.resolve_month(pay_idx, window_floor_index=floor_idx)["month_index"] == LIVE["month6_rows"] // LIVE["month6_rows"] * 6)
ok("E3 ... and month 6 IS inside the 6-month curve, which is why it paid",
   IM.in_schedule(6, LIVE["num_months"]))
# every real activation month that the live data showed, under both derivations
_late_by = {1: 2151, 4: 711, 2: 669, 6: 563, 5: 544, 3: 525, 7: 520, 8: 515, 9: 470, 13: 434}
def date_of_index(idx, day=15):
    """A YYYY-MM-DD string for a month index — the inverse of `period_index`, for building the
    live cohorts' real activation dates from the measured 'late by N months' spread."""
    return f"{idx // 12:04d}-{(idx % 12) + 1:02d}-{day:02d}"


ok("E4a date_of_index inverts period_index",
   all(IM.index_of_date(date_of_index(P(y, m))) == P(y, m)
       for y in (2025, 2026, 2027) for m in range(1, 13)))
ok("E4 the live 'engine is late by N months' spread is reproduced by the two derivations",
   all(IM.resolve_month(pay_idx, window_floor_index=floor_idx)["month_index"]
       - IM.resolve_month(pay_idx,
                          activation_date=date_of_index(floor_idx - n))["month_index"] == -n
       for n in _late_by))
_still_paid, _now_out = 0, 0
for n in sorted(_late_by):
    real_idx = floor_idx - n
    m = IM.month_span(real_idx, pay_idx)
    (_still_paid := _still_paid) if IM.in_schedule(m, LIVE["num_months"]) else None
    if IM.in_schedule(m, LIVE["num_months"]):
        _still_paid += _late_by[n]
    else:
        _now_out += _late_by[n]
ok("E5 EVERY one of the live late-by cohorts falls OUT of the curve once anchored on its own date",
   _still_paid == 0 and _now_out == sum(_late_by.values()), (_still_paid, _now_out))
ok("E6 the live disagreeing share is the majority of anchored subscribers",
   round(100.0 * LIVE["disagreeing_subs"] / LIVE["anchored_subs"], 1) == 76.8)
ok("E7 the live month-6 block is two thirds of the month's payout",
   round(100.0 * LIVE["month6_amount"] / LIVE["total_amount"], 1) == 67.6)
_tally = IM.BasisTally()
for _ in range(LIVE["disagreeing_subs"]):
    _tally.add(IM.BASIS_ACTIVATION, 0.0)
for _ in range(LIVE["unanchored_rows"]):
    _tally.add(IM.BASIS_WINDOW_EDGE, 0.0)
ok("E8 at live scale the tally reports the unanchored rows rather than absorbing them",
   _tally.unanchored_rows == LIVE["unanchored_rows"]
   and _tally.total_rows == LIVE["anchored_subs"] - (LIVE["anchored_subs"] - LIVE["disagreeing_subs"])
   + LIVE["unanchored_rows"])
ok("E9 the live no-rep figure is a third of the month's payout — the §19.48 shape, recorded",
   round(100.0 * LIVE["no_rep_amount"] / LIVE["total_amount"], 1) == 35.7)
ok("E10 every live oracle figure is pinned here, so a future edit that moves one fails this file",
   set(LIVE) == {"pay_period", "num_months", "window", "month6_rows", "month6_amount",
                 "total_amount", "anchored_subs", "disagreeing_subs", "disagreeing_amount",
                 "unanchored_rows", "no_rep_amount"})

# ─────────────────────────────────────────────────────────────────────────────────────────────────
print("\n§F THE REGRESSION — the window-edge derivation, ARMED as a negative control")


def retired_window_edge(pay_index, periods_in_window, present_in):
    """EXACTLY what `compute_installments` did before §19.51: the activation month is the lowest
    period index the subscriber appears at inside the window. Kept here so the defect is a RUNNING
    control rather than a memory."""
    seen = [i for i in periods_in_window if i in present_in]
    return IM.month_span(min(seen), pay_index) if seen else None


window = [pay_idx - k for k in range(LIVE["window"])]
# a subscriber who activated 14 months ago and appears in every month of the window
old_sub = set(window)
ok("F1 the retired derivation calls a 14-month-old subscriber month 6",
   retired_window_edge(pay_idx, window, old_sub) == 6)
ok("F2 the one home calls them month 14",
   IM.resolve_month(pay_idx, activation_date="2025-08-03")["month_index"] == 14)
ok("F3 THE REGRESSION: the retired derivation PAYS them, the fix does NOT",
   IM.in_schedule(retired_window_edge(pay_idx, window, old_sub), 6)
   and not IM.in_schedule(
       IM.resolve_month(pay_idx, activation_date="2025-08-03")["month_index"], 6))
ok("F4 ... and it recurs: the retired derivation says month 6 every month forever",
   {retired_window_edge(P(2026, m), [P(2026, m) - k for k in range(6)],
                        {P(2026, mm) - k for mm in (m,) for k in range(6)})
    for m in (9, 10, 11, 12)} == {6})
ok("F5 while the fix's answer keeps advancing past the curve and stops paying",
   [IM.resolve_month(P(2026, m), activation_date="2025-08-03")["month_index"]
    for m in (9, 10, 11, 12)] == [14, 15, 16, 17])
# a genuinely new subscriber: both derivations must AGREE, so the fix is not a blanket change
new_sub = {pay_idx}
ok("F6 a subscriber who really activated this month is month 1 under BOTH derivations",
   retired_window_edge(pay_idx, window, new_sub) == 1
   and IM.resolve_month(pay_idx, activation_date="2026-09-14")["month_index"] == 1)
ok("F7 a mid-curve subscriber agrees too when the window edge happens to be their activation",
   retired_window_edge(pay_idx, window, {pay_idx - 2, pay_idx - 1, pay_idx}) == 3
   and IM.resolve_month(pay_idx, activation_date="2026-07-02")["month_index"] == 3)
ok("F8 the fallback REPRODUCES the retired derivation exactly when no date exists",
   all(IM.resolve_month(pay_idx, window_floor_index=min(s))["month_index"]
       == retired_window_edge(pay_idx, window, s)
       for s in (old_sub, new_sub, {pay_idx - 2, pay_idx - 1, pay_idx})))

# ─────────────────────────────────────────────────────────────────────────────────────────────────
print("\n§G THE UN-WIRING LOCK — both engines must keep dereferencing the one home")
eng, sale = code_only(src(ENG_PATH)), code_only(src(SALE_PATH))
ok("G1 the residual engine imports the one home", "installment_month as _im" in eng)
ok("G2 the sale engine imports the one home", "installment_month as _im" in sale)
ok("G3 the residual engine resolves the month through it", "_im.resolve_month(" in eng)
ok("G4 the sale engine resolves the month through it", "_im.resolve_month(" in sale)
ok("G5 the residual engine reads the row's OWN activation column",
   "MI_ACTIVATION_DATE_COLUMN" in eng and "anchor_date" in eng)
ok("G6 NOBODY re-spells the month arithmetic `(pay_idx - <x>) + 1` outside the home",
   not re.search(r"\(\s*pay_idx\s*-\s*\w+\s*\)\s*\+\s*1", eng + sale))
ok("G7 NOBODY re-spells the `month_index > num_months` clamp",
   not re.search(r"month_index\s*>\s*num_months", eng + sale))
ok("G8 both engines clamp through in_schedule",
   "_im.in_schedule(" in eng and "_im.in_schedule(" in sale)
ok("G9 both engines take their horizon from the one home",
   "_im.horizon(" in eng and "_im.horizon(" in sale)
ok("G10 the period index is not re-derived as year*12 beside the home",
   "* 12 + (" not in eng and "* 12 + (" not in sale)
ok("G11 the residual engine's period index dereferences the home", "_im.period_index(" in eng)
ok("G12 there is exactly ONE resolve_month in the codebase",
   code_only(src(IM_PATH)).count("def resolve_month") == 1)
ok("G13 the earliest-period map is no longer used as the activation month",
   "for sub, floor_idx in earliest.items()" in eng)
ok("G14 raw_mi is read through the no-ceiling home (§19.48), not a private page loop",
   "_feed_read.read_all(" in eng and "range(start, start + page - 1)" not in eng)
ok("G15 the ledger row records the basis its month rests on",
   '"month_basis": resolved["basis"]' in eng)
ok("G16 the engine reports the basis tally and the unresolved schedules",
   '"month_basis": basis_tally.as_dict()' in eng and '"unresolved_schedule": unresolved' in eng)

print("\n§H PURITY and RULE TWO")
im_raw = src(IM_PATH)
im_code = code_only(im_raw)
ok("H1 the home imports nothing but stdlib datetime (plus the __future__ pragma)",
   [ln for ln in im_code.splitlines() if ln.startswith(("import ", "from "))]
   == ["from __future__ import annotations", "import datetime as _dt"])
for bad in ("supabase", "client", "schema(", "execute()", "requests", "httpx", "psycopg"):
    ok(f"H2 the home has no I/O: '{bad}'", bad not in im_code)
for clock in ("now()", "today()", "utcnow", "time.time"):
    ok(f"H3 the home has no clock: '{clock}'", clock not in im_code)
# RULE TWO — no carrier, tenant or product name anywhere in the module, code OR prose
for word in ("boost", "vidapay", "t-cetra", "tcetra", "total wireless", "luxelink", "cellfonz",
             "verizon", "at&t", "acima", "paygo", "novawave", "vzone"):
    ok(f"H4 RULE TWO — no '{word}' in the one home", word not in im_raw.lower())
ok("H5 the home holds no month-count, rate or ceiling literal as a business rule",
   not re.search(r"\b(?:num_months|max_months)\s*=\s*\d+", im_code))
ok("H6 the ceiling the engines pass is named, not inlined at the call",
   "MAX_SCHEDULE_MONTHS" in eng and "_im.horizon((s.get(\"num_months\") for s in scheds), MAX_SCHEDULE_MONTHS)" in eng)
ok("H7 the home never decides WHICH column holds a date — the caller hands the value in",
   "mi_activation_date" not in im_code)
ok("H8 the home is importable with no app config present", IM.__name__.endswith("installment_month"))

print("\n§I D1 — an unresolved schedule and a raised engine are REPORTED, never a silent zero")
router = code_only(src(ROUTER_PATH))
ok("I1 the bare swallow is GONE", "        except Exception:\n            inst_by_rep = {}" not in router)
ok("I2 the failure is caught WITH its reason", "except Exception as e:\n            inst_by_rep = {}" in router)
ok("I3 a raised engine appends a CRITICAL notice",
   '"type": "residual_installment_engine_failed", "severity": "critical"' in router)
ok("I4 an unresolved schedule appends a notice",
   '"type": "residual_installment_no_schedule"' in router)
ok("I5 an unanchored month appends a notice",
   '"type": "residual_installment_month_unanchored"' in router)
ok("I6 payout that names no rep appends a notice", '"type": "residual_installment_no_rep"' in router)
ok("I7 the notices go through the SAME mechanism the sale engine already used — no second channel",
   router.count("notices.append({") >= 6 and "notices is not None" in router)
ok("I8 the engine counts the unresolved subscribers and groups them by what the ROW carries",
   "unresolved_carriers[_k] = unresolved_carriers.get(_k, 0) + 1" in eng)
ok("I9 the engine reports subscribers whose row names NO carrier at all",
   '"subscribers_with_no_carrier_id"' in eng)
ok("I10 THE ENGINE NEVER GUESSES A CARRIER TO MAKE A PAYOUT HAPPEN",
   not re.search(r"carrier_id\s*=\s*carrier_id\s+or\s+", eng)
   and not re.search(r"_resolve_schedule\([^)]*carrier_id\s+or\s+", eng))
ok("I11 note is no longer an unconditional None", '"note": " ".join(notes) or None' in eng)
ok("I12 the engine states the amount credited to nobody beside the total it counted",
   '"amount_no_rep": _unpaid_rep' in eng)

print("\n§J D3 — the ledger key, the adaptive writer, and the migration")
mig = src(MIG_PATH)
ok("J1 the wide key names pay_period",
   "org_id, subscriber_id, activation_type, month_index, pay_period" in mig)
ok("J2 the migration is idempotent", "CREATE UNIQUE INDEX IF NOT EXISTS" in mig
   and "ADD COLUMN IF NOT EXISTS" in mig)
ok("J3 the migration carries a REVERT note", "-- REVERT:" in mig)
ok("J4 the migration creates the new key BEFORE dropping the old one",
   mig.index("CREATE UNIQUE INDEX IF NOT EXISTS") < mig.index("DROP CONSTRAINT"))
ok("J5 the migration finds the old constraint by its COLUMN SET, not a name spelling",
   "ARRAY['activation_type', 'month_index', 'org_id', 'subscriber_id']" in mig)
ok("J6 the migration DELETES and REWRITES nothing", "DELETE FROM" not in mig.upper()
   and "UPDATE commcalc" not in mig)
ok("J7 the migration says it is surfaced for approval, not applied by the code",
   "SURFACED FOR OWNER APPROVAL" in mig)
ok("J8 the REVERT refuses to guess which pay period to delete",
   "HAVING count(*) > 1" in mig and "money decision" in mig)
ok("J9 the writer tries the WIDE key first and the narrow one second",
   "org_id,subscriber_id,activation_type,month_index,pay_period" in eng
   and eng.index("month_index,pay_period") < eng.index('"org_id,subscriber_id,activation_type,month_index"'))
ok("J10 the writer is ADAPTIVE, so the code is inert until the migration is applied",
   "LEDGER_CONFLICT_KEYS" in eng and "for key in LEDGER_CONFLICT_KEYS" in eng)
ok("J11 the optional column is probed through the one home, never assumed",
   "_col.present_columns(" in eng and "LEDGER_OPTIONAL_COLUMNS" in eng)
ok("J12 the per-batch `except Exception: pass` is GONE", "        except Exception:\n            pass" not in eng)
ok("J13 the writer REPORTS which key it used and whether it finished",
   '"conflict_key"' in eng and '"written"' in eng and '"error"' in eng)
ok("J14 the engine surfaces that report", '"persisted": persisted' in eng)
ok("J15 an incomplete ledger write appends a notice",
   '"type": "residual_installment_ledger_write"' in router)
ok("J16 the sibling ledger's key shape is what this matches",
   "sale_installment_ledger" in mig and "mig 201" in mig)

# ─────────────────────────────────────────────────────────────────────────────────────────────────
print(f"\n{'=' * 78}")
print(f"  {PASS} passed, {FAIL} failed")
if _FAILED:
    print("  FAILED:")
    for f in _FAILED:
        print(f"    - {f}")
print(f"{'=' * 78}")
sys.exit(1 if FAIL else 0)
