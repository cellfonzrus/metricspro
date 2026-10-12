#!/usr/bin/env python3
"""A COUNT OF THE WHOLE ENVELOPE IS NOT COMPARABLE WITH A NETTED EXPECTATION — DB-free proof.

OWNER QUESTION 2026-10-10, verbatim: *"on teh management recon for 3 pl on 10/2 it shows declard on
envelope is 552 but written on enevelp 671 actual cash is 671 , where is the 552 coming from"*.

WHAT WAS WRONG. `pickup_actual.row_variance` subtracted the envelope's stored `amount` from
`actual_picked_amount` and called the difference short or over. Sound when written — `amount` was the
whole drawer and the bag held the whole drawer. Then `billpay_netting` was switched on, making
`amount` the drawer MINUS the POS bill-payment cash, while the bag still holds whatever the rep left
and A REP NETS BY HIS OWN DECLARED FIGURE. Two different subtractions, compared with each other.

LIVE EVIDENCE (house org, read 2026-10-10 via the Supabase REST API): of 998 cash pickups, 660 carry
a DM count; 135 of those are on the 'pos' basis and 105 read short or over — 59 short ($1,365.50)
and 46 over ($3,682.30). B-3PL / 2026-10-02 is one of the 46 and is replayed below as the regression.

THE CLASS. §72 says a variance between two cash figures is meaningless unless both count the same
cash. This is the same class one level down, and the distinction §72's vocabulary CANNOT express:
both figures here count the same cash (store cash) and still disagree, because they were netted FROM
DIFFERENT SOURCES. §72's bases name WHICH CASH; a pickup basis names WHOSE FIGURE came out.

Run: cd backend && python3 harness_envelope_count_basis.py
"""
import importlib.util as _iu
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, rel):
    spec = _iu.spec_from_file_location(name, os.path.join(_HERE, rel))
    m = _iu.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


# `pickup_actual` imports `.envelope_report` and `.billpay_netting` relatively, so the real package
# is loaded — no stub, no reimplementation of the thing under test.
sys.path.insert(0, _HERE)
from app.modules.closing import billpay_netting as bn           # noqa: E402
from app.modules.closing import pickup_actual as pa             # noqa: E402

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
head("A. the basis vocabulary, and what a MISSING one resolves to")

check("A1 every comparable basis is a real pickup basis — the allowlist cannot name a word the "
      "vocabulary does not have",
      all(b in bn.PICKUP_BASES for b in bn.COUNT_COMPARABLE_BASES),
      str(bn.COUNT_COMPARABLE_BASES))
check("A2 'pos' is the ONE recorded basis that is not comparable (it is the only one netted from a "
      "source the envelope itself was not)",
      tuple(b for b in bn.PICKUP_BASES if not bn.count_comparable(b)) == ("pos",),
      str(tuple(b for b in bn.PICKUP_BASES if not bn.count_comparable(b))))
for b in ("off", "none", "declared"):
    check(f"A3 '{b}' IS comparable — the expectation and the bag were netted the same way (or not "
          f"at all)", bn.count_comparable(b) is True)
check("A4 'pos' is NOT comparable", bn.count_comparable("pos") is False)

# THE ERROR THIS HARNESS EXISTS TO PREVENT A RETURN OF. My first cut ruled an UNRECORDED basis
# "unknown" and refused it — which would have withdrawn 226 live shortage findings worth $59,321.53
# that are perfectly sound, and would have been a SECOND answer to a question
# `router.pickup_basis_label` had already settled (unrecorded reads as total cash).
check("A5 an UNRECORDED basis resolves to total cash and stays COMPARABLE — netting did not exist "
      "before the column did, so a pre-1039 amount IS the whole drawer (226 live shortage findings "
      "depend on this)",
      bn.resolved_basis(None) == "off" and bn.count_comparable(None) is True)
check("A5b ...and the same for blank and for a word the vocabulary does not have, so this and "
      "`pickup_basis_label` can never disagree about one row",
      all(bn.resolved_basis(v) == "off" and bn.count_comparable(v) for v in ("", "   ", "future")))
check("A6 but WHETHER it was recorded stays separately answerable — a screen may say 'as far as can "
      "be told' instead of claiming somebody declared it",
      bn.basis_recorded("pos") is True and bn.basis_recorded(None) is False
      and bn.basis_recorded("future") is False)
check("A7 case and padding do not change the answer (a basis round-tripped through a form)",
      bn.count_comparable("  POS  ") is False and bn.count_comparable(" OFF ") is True)
check("A8 a refused basis carries a one-sentence WHY — 'not comparable' alone sends somebody "
      "looking for missing cash",
      "POS" in (bn.count_incomparable_reason("pos") or "")
      and "declared" in (bn.count_incomparable_reason("pos") or ""))
check("A9 ...and a comparable basis carries NO reason, so a caller cannot print one beside a "
      "verdict that stands",
      all(bn.count_incomparable_reason(b) is None for b in ("off", "none", "declared", None, "")))

head("B. the sibling: the bill-payment envelope has no basis column at all")

check("B1 the cash envelope carries its OWN stored basis",
      bn.pickup_amount_basis_for("cash_pickup", "pos") == "pos"
      and bn.pickup_amount_basis_for("cash_pickup", None) is None)
check("B2 the bill-payment envelope resolves UN-NETTED without a column — netting moves bill-pay "
      "cash INTO it, never out, so the fact is settled by construction and needs no migration",
      bn.pickup_amount_basis_for("billpay_pickup", None) == bn.BASIS_NOT_NETTED
      and bn.count_comparable(bn.pickup_amount_basis_for("billpay_pickup", None)) is True)
check("B3 THE REGRESSION THAT MATTERS FOR THE SIBLING: a bill-pay count is still scored. A guard "
      "that refused every bill-pay envelope for lacking a column it was never given would be the "
      "fix breaking the sibling it was meant to protect",
      pa.row_variance({"amount": 1940.00, "actual_picked_amount": 1900.00})["status"] == "short")
check("B4 a blank table FAILS CLOSED to the row's own basis — a caller that cannot name its table "
      "has not established that its amount was never netted",
      bn.pickup_amount_basis_for("", None) is None
      and bn.pickup_amount_basis_for(None, "pos") == "pos")
check("B5 the netted table is a VALUE, not a branch spelled in a caller (RULE TWO posture)",
      bn.NETTED_PICKUP_TABLE == "cash_pickup")

head("C. THE REGRESSION — B-3PL / 2026-10-02, the owner's own store-day")

# Live figures, read from production 2026-10-10 and verified against the sales feed line by line:
#   daily_closing.t_cash          $2,646.00   the whole drawer
#   declared epay_on_cash         $1,975.00   the rep's own bill-payment cash
#   POS bill-pay cash             $2,093.50   = $2,013.50 of bills + $80.00 of service fees, CASH
#                                             tender only (26 + 20 lines, independently summed)
#   cash_pickup.amount            $  552.50   = 2,646.00 - 2,093.50, amount_basis 'pos'
#   actual_picked_amount          $  671.00   = 2,646.00 - 1,975.00, what Rajiv counted
B3PL = {"amount": 552.50, "actual_picked_amount": 671.00, "amount_basis": "pos"}

check("C1 the arithmetic that produced each figure is exactly as measured in production",
      round(2646.00 - 2093.50, 2) == 552.50 and round(2646.00 - 1975.00, 2) == 671.00
      and round(2013.50 + 80.00, 2) == 2093.50)
_old = pa.variance_fields(B3PL["amount"], B3PL["actual_picked_amount"])
check("C2 BEFORE: the naive subtraction called this '+$118.50 over' — a verdict against a rep who "
      "did nothing wrong",
      _old["status"] == "over" and _old["variance"] == 118.50)
check("C3 ...and $118.50 is EXACTLY the POS-versus-declared gap, not cash: $2,093.50 - $1,975.00",
      round(2093.50 - 1975.00, 2) == 118.50 == _old["variance"])
_new = pa.row_variance(B3PL)
check("C4 AFTER: the comparison is REFUSED rather than scored",
      _new["status"] == pa.NOT_COMPARABLE and _new["comparable"] is False)
check("C5 ...and `variance` is None — NOT 0.00 and NOT 'match', either of which would read as 'it "
      "tied out' and hide the finding",
      _new["variance"] is None)
check("C6 ...while BOTH figures are kept, so a screen can still show what was expected and counted",
      _new["declared"] == 552.50 and _new["actual"] == 671.00)
check("C7 ...and the refusal explains itself in a sentence a manager can act on",
      "POS" in (_new["reason"] or ""))

head("D. the comparisons that are SOUND are untouched (the fix withdraws 135, keeps 239)")

check("D1 an un-netted envelope genuinely short is STILL short",
      pa.row_variance({"amount": 500.0, "actual_picked_amount": 450.0,
                       "amount_basis": "off"})["status"] == "short")
check("D2 a PRE-1039 row (no basis) genuinely short is STILL short — the 226-row case",
      pa.row_variance({"amount": 500.0, "actual_picked_amount": 450.0})["status"] == "short")
check("D3 netting by the REP'S OWN declaration stays comparable — the expectation and the bag were "
      "netted by the same figure",
      pa.row_variance({"amount": 300.0, "actual_picked_amount": 300.0,
                       "amount_basis": "declared"})["status"] == "match")
check("D4 'none' (no POS figure for the day, so nothing was netted) stays comparable",
      pa.row_variance({"amount": 300.0, "actual_picked_amount": 280.0,
                       "amount_basis": "none"})["status"] == "short")
check("D5 no recorded count is still an honest ABSENCE, never a shortage and never a refusal",
      pa.row_variance({"amount": 500.0, "amount_basis": "pos"}) is None
      and pa.row_variance({"amount": 500.0, "actual_picked_amount": ""}) is None)
check("D6 a count of 0.00 on an opened envelope is a real finding, not an absence",
      (pa.row_variance({"amount": 500.0, "actual_picked_amount": 0.0,
                        "amount_basis": "off"}) or {}).get("status") == "short")
check("D7 the comparable shape is the PRE-EXISTING triple plus two keys — an older reader that "
      "only reads variance/status/declared/actual is byte-identical",
      all(k in pa.row_variance({"amount": 1.0, "actual_picked_amount": 2.0, "amount_basis": "off"})
          for k in ("variance", "status", "declared", "actual", "comparable", "reason")))

head("E. ONE GATE, dereferenced by BOTH the read path and the write path")

_w = pa.count_patch({"actual_amount": 671.00, "envelope_opened": True},
                    declared=552.50, counted_at="2026-10-10T00:00:00Z", basis="pos")
check("E1 the WRITE path (count_patch, the open-and-count save) refuses the same comparison the "
      "read path does — a figure scored on save and refused on the board is the drift this locks",
      _w["variance"]["status"] == pa.NOT_COMPARABLE and _w["variance"]["variance"] is None)
check("E2 ...and still STORES the count itself: refusing to score it is not refusing to record it",
      _w["row"]["actual_picked_amount"] == 671.00)
_w2 = pa.count_patch({"actual_amount": 450.00}, declared=500.0, counted_at="t", basis="off")
check("E3 the write path on a comparable basis scores exactly as before",
      _w2["variance"]["status"] == "short" and _w2["variance"]["variance"] == -50.0)
_w3 = pa.count_patch({"actual_amount": 450.00}, declared=500.0, counted_at="t")
check("E4 the write path with NO basis given scores it — the unrecorded-reads-as-total-cash rule, "
      "from the same home, so an older caller is unchanged",
      _w3["variance"]["status"] == "short")

head("F. BUILD LOCKS — so this cannot un-wire (§19.18 has three precedents)")

_PA = open(os.path.join(_HERE, "app/modules/closing/pickup_actual.py"), encoding="utf-8").read()
_RT = open(os.path.join(_HERE, "app/modules/closing/router.py"), encoding="utf-8").read()
_BN = open(os.path.join(_HERE, "app/modules/closing/billpay_netting.py"), encoding="utf-8").read()

check("F1 `row_variance` goes through the ONE gate and does not score anything itself",
      "gate_on_basis(" in _PA.split("def row_variance")[1].split("def ")[0])
check("F2 `count_patch` goes through the SAME gate — the write path cannot acquire its own rule",
      "gate_on_basis(" in _PA.split("def count_patch")[1].split("\ndef ")[0])
# PROSE IS NOT CODE. Both this module's docstrings legitimately quote `status='not_comparable'` when
# explaining the refusal, so the scan runs on the module with its triple-quoted blocks removed —
# otherwise the lock fails on documentation, which teaches writers to delete the documentation.
_PA_CODE = re.sub(r'"""(?:.|\n)*?"""', '""', _PA)
_nc_literals = re.findall(r"""['"]not_comparable['"]""", _PA_CODE)
check("F3 the refused status WORD is written exactly once — at the constant. Every other site "
      "reaches it by name, so a hand-typed second spelling cannot drift",
      len(_nc_literals) == 1 and 'NOT_COMPARABLE = "not_comparable"' in _PA_CODE,
      f"found {len(_nc_literals)} literal(s)")
check("F3b ...and every refusal assigns that NAME rather than a string",
      not re.search(r"""\[['"]status['"]\]\s*=\s*['"]not_comparable['"]""", _PA_CODE)
      and re.search(r"""['"]status['"]\s*:\s*NOT_COMPARABLE|\[['"]status['"]\]\s*=\s*NOT_COMPARABLE""",
                    _PA_CODE) is not None)
# The scan is on COMPARISONS and MEMBERSHIP, not on any occurrence of the word: `pickup_actual`
# legitimately uses "declared" as a variance FIELD NAME (`{"declared": ...}`, the expected figure)
# and names 'pos' in its prose. What must never appear is this module TESTING a basis itself —
# `== "pos"`, `in ("off", ...)` — because that is the second copy of the rule.
check("F4 `pickup_actual` TESTS no basis word of its own — it asks billpay_netting instead",
      not re.search(r"""(?:==|!=|\bin)\s*\(?\s*['"](?:pos|off|none|declared)['"]""", _PA)
      and not re.search(r"""['"](?:pos|off|none|declared)['"]\s*(?:==|!=)""", _PA))
check("F5 NOBODY re-derives the comparability rule: `count_comparable` is defined once and every "
      "other mention is a call",
      _BN.count("def count_comparable") == 1
      and "COUNT_COMPARABLE_BASES" not in _PA and "COUNT_COMPARABLE_BASES" not in _RT)
check("F6 the router's basis LABEL dereferences the same resolution the gate uses — a private copy "
      "there is how the screen and the gate came to disagree about one row",
      "billpay_netting.resolved_basis(" in _RT and "billpay_netting.basis_recorded(" in _RT)
check("F7 ...and no longer carries its own `if not recorded: b = 'off'` copy",
      'recorded:\n        b = "off"' not in _RT)
check("F8 every router site that hands a basis to the gate names its TABLE through the one home, "
      "so the sibling cannot be refused for lacking a column",
      _RT.count("billpay_netting.pickup_amount_basis_for(") >= 2,
      f"found {_RT.count('billpay_netting.pickup_amount_basis_for(')}")
check("F8b ...and NO site feeds the gate a raw `amount_basis` column, which is what would refuse "
      "every bill-pay count (the defect the first cut of #439 shipped with)",
      not re.search(r"""basis\s*=\s*(?:row|p|r)\.get\(["']amount_basis["']\)""", _RT)
      and not re.search(r"""basis\s*=\s*_basis\b(?!\s*\))""", _RT),
      "a gate call takes a stored column directly instead of pickup_amount_basis_for(table, ...)")
check("F9 the board never sums a refused variance into its total (it would be adding None)",
      'vf and vf.get("variance") is not None' in
      open(os.path.join(_HERE, "app/modules/closing/deposit_accountability.py"),
           encoding="utf-8").read())
_DA = open(os.path.join(_HERE, "app/modules/closing/deposit_accountability.py"),
           encoding="utf-8").read()
check("F10 ...and the by-DM shortage rollup — the surface that NAMES a DM — keeps not-comparable "
      "in its own bucket rather than as uncounted or as a match",
      '"not_comparable_rows": 0' in _DA and 'cmp_ is False' in _DA)
check("F11 the refusal REASON reaches every surface that renders a status (both pickup lists and "
      "the board)",
      _RT.count('"pickup_variance_reason"') == 2 and '"pickup_variance_reason"' in _DA)

head("G. the screens say so (reported, never hidden)")

_FE = {
    "board": "../frontend/src/app/(platform)/closing/deposit-recon/page.tsx",
    "pickup": "../frontend/src/app/(platform)/closing/pickup/page.tsx",
    "billpay": "../frontend/src/app/(platform)/closing/billpay-pickup/page.tsx",
}
_SRC = {k: open(os.path.join(_HERE, v), encoding="utf-8").read() for k, v in _FE.items()}

for k in ("pickup", "billpay"):
    check(f"G1 the {k} list renders the refused state instead of silently rendering nothing",
          "'not_comparable'" in _SRC[k])
check("G2 the board shows a NEUTRAL count of them — not red (which accuses) and not hidden",
      "pickup_not_comparable_rows" in _SRC["board"])
check("G3 THE OWNER'S ACTUAL COMPLAINT: the dialog no longer calls the expected figure 'declared "
      "on the envelope' — nobody declared $552.50",
      "Declared on the envelope" not in _SRC["board"]
      and "Expected in this envelope" in _SRC["board"])
check("G4 ...and it states WHICH CASH that figure counts, in words the SERVER supplies, so the "
      "screen spells no basis of its own",
      "openEnv.basis_label" in _SRC["board"]
      and not re.search(r"""['"](?:pos|declared|none)['"]\s*(?:===|==)""", _SRC["board"]))
check("G5 the save message cannot fall through to 'matches' on a refused comparison — the old "
      "else-branch would have said exactly that",
      "'not_comparable'" in _SRC["board"] and "matches the expected amount" in _SRC["board"])
check("G6 the board's basis words come from the ONE labeller, served per envelope",
      'env["amount_basis_label"]' in _RT and "pickup_basis_label(" in _RT)

head("H. negative controls for the re-expressed F3/F8 locks (2026-10-12)")

# Both were COUNT PINS ("exactly two mentions") until §77 added a legitimate third caller. A count
# pin fails on honest growth and passes the actual defect, so each was re-expressed to forbid the
# defect directly — which is only an improvement if it still fires. Proved here on literals.
_v3 = 'NOT_COMPARABLE = "not_comparable"\n    out["status"] = "not_comparable"\n'
check("H1 F3 DOES catch a hand-typed second spelling of the status word",
      len(re.findall(r"""['"]not_comparable['"]""", _v3)) != 1)
check("H2 F3b DOES catch the string being assigned instead of the name",
      re.search(r"""\[['"]status['"]\]\s*=\s*['"]not_comparable['"]""", _v3) is not None)
_c3 = 'NOT_COMPARABLE = "not_comparable"\n    out["status"] = NOT_COMPARABLE\n'
check("H3 ...and does NOT fire on the correct form",
      len(re.findall(r"""['"]not_comparable['"]""", _c3)) == 1
      and not re.search(r"""\[['"]status['"]\]\s*=\s*['"]not_comparable['"]""", _c3))
for _v in ('count_patch(x, basis=row.get("amount_basis"))',
           "count_patch(x, basis=p.get('amount_basis'))",
           'gate(basis=r.get("amount_basis"))'):
    check(f"H4 F8b DOES catch a gate call taking the raw column — `{_v[:38]}`",
          re.search(r"""basis\s*=\s*(?:row|p|r)\.get\(["']amount_basis["']\)""", _v) is not None)
for _c in ('count_patch(x, basis=billpay_netting.pickup_amount_basis_for(t, row.get("amount_basis")))',
           'p["amount_basis"] = billpay_netting.pickup_amount_basis_for(table, p.get("amount_basis"))'):
    check("H5 ...and does NOT fire when the one home is between them",
          re.search(r"""basis\s*=\s*(?:row|p|r)\.get\(["']amount_basis["']\)""", _c) is None)

print(f"\n{PASS}/{PASS + FAIL} checks passed")
sys.exit(1 if FAIL else 0)
