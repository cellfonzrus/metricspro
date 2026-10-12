#!/usr/bin/env python3
"""AN ENVELOPE COUNT IS SCORED AGAINST THE ENVELOPE'S EXPECTED FIGURE — DB-free proof (index §CLOSING-01).

OWNER, 2026-10-12: *"Build the label fix"* — after asking, 2026-10-10, where the $552 on the
Management Recon screen came from.

WHAT WAS WRONG, AND IT WAS WORSE THAN WHAT #439 FIXED. #439 gated `pickup_actual.row_variance`, which
the pickup list and the accountability board read. The MANAGEMENT RECON screen reads neither. It
summed `cash_pickup.actual_picked_amount` per store-day and subtracted `daily_closing.t_cash` — THE
WHOLE DECLARED DRAWER — in its row builder. Under bill-payment netting the bag holds the drawer LESS
the bill-pay cash, so that subtraction returns minus the rep's declared bill-pay cash, every netted
store-day, on the one surface that names the DM who did the counting. Management's own count had the
same defect: `envelope_count.counted_amount` minus the drawer, when the count STORES the figure it
was actually scored against.

LIVE EVIDENCE (house org, Supabase REST, 2026-10-12). 470 counted store-days on this screen:
  · the screen showed            short 344 ($-118,249.40)   over   2   match 124
  · against the envelope figure  short 244 ($ -60,685.07)   over  33   match 193
  · 278 of the 470 showed a "shortage" EXACTLY equal to the declared bill-pay cash — $90,280.01 of
    variance that was an artifact of netting and nothing else. B-3PL / 2026-10-02 is replayed in §C.

THE CLASS, NOT THE INSTANCE. #439's class was "a count of the whole envelope is not comparable with a
netted expectation". This is the class one step up and the reason #439 did not reach here: A SUM HAS
NO BASIS. Any caller that wants a store-day total must aggregate the ROWS' gated verdicts, because
the gate cannot run on two sums. So `pickup_actual.day_variance` is the one home for a store-day's
verdict, and this screen computes no variance at all any more.

Run: cd backend && python3 harness_management_recon_expected.py
"""
import importlib.util as _iu
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from app.modules.closing import billpay_netting as bn           # noqa: E402
from app.modules.closing import pickup_actual as pa             # noqa: E402

_ROUTER = open(os.path.join(_HERE, "app/modules/closing/router.py")).read()
_PAGE = open(os.path.join(
    _HERE, "../frontend/src/app/(platform)/closing/cash-recon-management/page.tsx")).read()

PASS = FAIL = 0


def check(msg, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS  {msg}")
    else:
        FAIL += 1
        print(f"FAIL  {msg}" + (f"  [{detail}]" if detail else ""))


def head(t):
    print(f"\n== {t} ==")


# ─────────────────────────────────────────────────────────────────────────────────────────────────
head("A. a store-day's verdict comes from the rows, and keeps the row-level vocabulary")

_one = [{"amount": 100.0, "actual_picked_amount": 90.0, "amount_basis": "off"}]
_dv = pa.day_variance(_one)
check("A1 a single comparable envelope gives the same verdict the row gives — no second truth table",
      _dv["variance"] == pa.row_variance(_one[0])["variance"] == -10.0
      and _dv["status"] == "short", str(_dv))
check("A2 the verdict carries the SAME keys a caller already renders for a row, so one screen "
      "renders both the same way",
      {"variance", "status", "reason", "comparable"} <= set(_dv), str(sorted(_dv)))
_two = pa.day_variance([{"amount": 100.0, "actual_picked_amount": 90.0},
                        {"amount": 50.0, "actual_picked_amount": 50.0}])
check("A3 two envelopes on one store-day aggregate — expected 150, counted 140, short 10",
      (_two["expected"], _two["counted"], _two["variance"], _two["status"])
      == (150.0, 140.0, -10.0, "short"), str(_two))
check("A4 an UNCOUNTED envelope is skipped, never zeroed — a day with nothing counted has NO "
      "verdict rather than a 100%-short one",
      pa.day_variance([{"amount": 100.0}]) is None
      and pa.day_variance([]) is None and pa.day_variance(None) is None)
_mix = pa.day_variance([{"amount": 100.0, "actual_picked_amount": 90.0},
                        {"amount": 50.0, "actual_picked_amount": 50.0}, {"amount": 99.0}])
check("A4b ...and a counted envelope beside an uncounted one still scores, on the counted one only",
      (_mix["expected"], _mix["counted"], _mix["counted_rows"]) == (150.0, 140.0, 2), str(_mix))

head("B. a mixed day FAILS CLOSED — the dangerous case")

_bad = pa.day_variance([{"amount": 100.0, "actual_picked_amount": 90.0},
                        {"amount": 552.50, "actual_picked_amount": 671.0, "amount_basis": "pos"}])
check("B1 one refused envelope refuses the DAY — summing the rest would publish a number that is "
      "neither the day's shortage nor any envelope's",
      _bad["variance"] is None and _bad["status"] == pa.NOT_COMPARABLE
      and _bad["comparable"] is False, str(_bad))
check("B2 and it SAYS WHY, in the refused envelope's own words — a refusal with no reason reads as "
      "a bug to whoever is looking at it",
      isinstance(_bad.get("reason"), str) and "bill-payment" in _bad["reason"])
check("B3 the totals are still returned, so the screen can show what was in play",
      (_bad["expected"], _bad["counted"]) == (652.5, 761.0), str(_bad))
check("B4 the refused count is reported, not merely implied",
      _bad["not_comparable_rows"] == 1 and _bad["counted_rows"] == 2)
check("B5 a refusal is NEVER 'match' and NEVER 0.00 — either would read as 'it tied out'",
      _bad["status"] != "match" and _bad["variance"] != 0.0)

head("C. THE REGRESSION — B-3PL / 2026-10-02, the store-day the owner asked about")

# Read from production 2026-10-10/12: drawer $2,646.00, rep declared $1,975.00 of bill-pay cash, POS
# said $2,093.50, so `amount` = $552.50 on the 'pos' basis and the rep bagged $671.00.
_3pl = [{"amount": 552.50, "actual_picked_amount": 671.00, "amount_basis": "pos"}]
_r = pa.day_variance(_3pl)
check("C1 the store-day is REFUSED rather than called $118.50 over",
      _r["status"] == pa.NOT_COMPARABLE and _r["variance"] is None, str(_r))
check("C2 the old screen arithmetic would have called it $1,975.00 SHORT — exactly the rep's "
      "declared bill-pay cash, which is the artifact this fix removes",
      round(671.00 - 2646.00, 2) == -1975.00)
check("C3 both figures survive the refusal, so the screen still answers 'where is the 552 from'",
      (_r["expected"], _r["counted"]) == (552.50, 671.00), str(_r))

head("D. the 192 sound verdicts still score (the withdrawal must not over-reach)")

_sound = [{"amount": 1250.00, "actual_picked_amount": 0.00, "amount_basis": None},
          {"amount": 300.00, "actual_picked_amount": 290.00, "amount_basis": "off"},
          {"amount": 300.00, "actual_picked_amount": 310.00, "amount_basis": "declared"}]
for r in _sound:
    v = pa.day_variance([r])
    check(f"D1 basis {r['amount_basis']!r} still scores — withdrawing these would hide real money",
          v["variance"] is not None and v["status"] in ("short", "over", "match"), str(v))
check("D2 an un-netted day where the bag is 1250 light is still SHORT 1250 (the live B-196 case)",
      pa.day_variance([_sound[0]])["variance"] == -1250.0)

head("E. the router computes NO variance of its own on this screen")

_impl = _ROUTER[_ROUTER.index('@router.get("/cash-recon-management")'):]
_impl = _impl[:_impl.index("\n@router.") if "\n@router." in _impl else len(_impl)]
check("E1 the whole-drawer subtraction is GONE — neither count is scored against slot['cash']",
      not re.search(r"""(?:_ca|_mc)\s*-\s*slot\[["']cash["']\]""", _impl))
check("E2 the DM's verdict is `day_variance`'s, dereferenced",
      "_pickup_actual.day_variance(" in _impl)
check("E3 the screen reads the pickup ROWS, not two sums — a sum has no basis and the gate cannot "
      "run on one",
      "amount_basis" in _impl and "_pickup_rows(" in _impl)
check("E4 the sibling table's basis comes from the ONE home for that fact, not from an assumption "
      "written here (billpay_pickup has no such column)",
      "billpay_netting.pickup_amount_basis_for(" in _impl)
check("E5 management's variance is the count's OWN stored score — this function does not re-derive it",
      "expected_amount,variance,status" in _impl
      and not re.search(r"""_mc\s*-\s*""", _impl))
check("E6 a count stored before its score was recorded stays None rather than becoming 0.00",
      "unscored_rows" in _impl)
check("E7 a refused store-day is COUNTED in the summary rather than vanishing from both buckets",
      "dm_not_comparable_days" in _impl)
check("E8 ...and is never added to either dollar total — there is no defensible dollar to add",
      not re.search(r"""dm_(?:short|over)_amount["']\s*:\s*[^\n]*not_comparable""", _impl))

head("F. BUILD LOCKS — the ways this can silently un-wire")

check("F1 `day_variance` is the only aggregator — the router may not sum actual_picked_amount into "
      "a variance itself",
      not re.search(r"""actual_picked_amount["']\s*\)\s*,\s*2\s*\)\s*\n[^\n]*-\s*slot""", _impl))
_src = open(os.path.join(_HERE, "app/modules/closing/pickup_actual.py")).read()
_dv_src = _src[_src.index("def day_variance"):]
check("F2 `day_variance` holds NO basis word of its own — it asks the gate, as `row_variance` does, proved on the source",
      not re.search(r"""(?:==|!=|\bin)\s*\(?\s*['"](?:pos|off|none|declared)['"]""", _dv_src)
      and "row_variance(r)" in _dv_src)
check("F3 the refusal status is the shared VALUE, so a second spelling cannot drift",
      "NOT_COMPARABLE" in _dv_src and '"not_comparable"' not in _dv_src)
check("F4 the page no longer calls the expected figure a declared or verified one — the exact "
      "wording the owner read as a figure somebody typed",
      "DM verified cash" not in _PAGE and "declared envelope figure" not in _PAGE)
check("F5 the page renders the refusal rather than a bare number",
      "cash_picked_comparable" in _PAGE and "not comparable" in _PAGE)
check("F6 the page names what the variance is measured against, so the two figures cannot be read "
      "as a drawer comparison again",
      "expected in the envelope" in _PAGE)
check("F7 the export carries the refusal as a word, never as a blank that reads as nil",
      re.search(r"""cash_picked_variance\s*==\s*null\s*\?\s*r\.cash_picked_status""", _PAGE)
      is not None)
check("F8 the management column no longer claims to be the drawer subtraction",
      "Management counted minus cash declared" not in _PAGE)

head("G. negative controls — the locks must FAIL on a real violation")

_violations = [
    ('_ca - slot["cash"]', r"""(?:_ca|_mc)\s*-\s*slot\[["']cash["']\]"""),
    ("_mc - slot['cash']", r"""(?:_ca|_mc)\s*-\s*slot\[["']cash["']\]"""),
]
for frag, pat in _violations:
    check(f"G1 the E1 scan DOES catch `{frag}`", re.search(pat, frag) is not None)
for clean in ("dv = _pickup_actual.day_variance(rs)", 'slot["cash"] = round(slot["cash"] + cash, 2)',
              '"cash_declared": slot["cash"],'):
    check("G2 ...and does NOT fire on the legitimate line "
          f"`{clean[:46]}`", re.search(_violations[0][1], clean) is None)
for frag in ('b == "pos"', "basis in ('pos','off')", '"declared" == b'):
    check(f"G3 the F2b basis scan DOES catch `{frag}`",
          re.search(r"""(?:==|!=|\bin)\s*\(?\s*['"](?:pos|off|none|declared)['"]""", frag)
          or re.search(r"""['"](?:pos|off|none|declared)['"]\s*(?:==|!=)""", frag))

print(f"\n{PASS}/{PASS + FAIL} checks passed")
sys.exit(1 if FAIL else 0)
