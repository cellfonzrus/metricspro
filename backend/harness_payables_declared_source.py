#!/usr/bin/env python3
"""DB-FREE PROOF — a declared source that cannot be read is REPORTED, never read as "nothing was
declared" (owner-facing defect 2026-10-23, index §COMMCALC-28).

WHAT WENT WRONG, so the test says what it is defending. `commcalc.payable_source_map` declared
Total's device-cost EVIDENCE feed as `raw_ma_fulfillment.sku`. That table has no `sku` column, so the
read raised, the caller's `except` returned {}, and 2,923 unsold devices were written with no price
and `owed_source = 'unconfigured'` — identical to a carrier nobody had configured. $595,125.59 of
real exposure headlined as $0.00.

THE FOUR THINGS LOCKED HERE
  A  the pure home: a failing read records its complaint and still returns the caller's empty
  B  'unconfigured' vs 'price_source_unreadable' — a declared source that produced nothing SAYS so
  C  DEGRADE, DON'T DIE: an evidence failure falls back to the price home's allowlist, and the
     SIBLING readers (`owed_link`, the sold-match feed) record too — one fixed and the others not is
     the same defect wearing a hat
  D  the ratchet: every declared (table, column) read in payables/engine.py passes a `complaints`,
     so a future reader cannot quietly go back to swallowing its failure
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.payables import declared_source as ds          # noqa: E402
from app.modules.payables import unsold_inventory as ui          # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(ROOT, "app", "modules", "payables", "engine.py")
OUT = []


def ck(name, got, want):
    OUT.append((name, got == want, got, want))


def boom(exc=KeyError("'sku'")):
    def _r():
        raise exc
    return _r


# ── A — the pure home ────────────────────────────────────────────────────────────────────────────
c = ds.Complaints("Total")
ck("A1 a failing read returns the caller's empty, not a raise",
   ds.read_declared(c, ds.KIND_PRICE_EVIDENCE, "raw_ma_fulfillment", "sku", boom(), []), [])
ck("A2 ...and records exactly one complaint", len(c), 1)
ck("A3 the complaint knows which kind", c.kinds(), [ds.KIND_PRICE_EVIDENCE])
note = c.notes()[0]
ck("A4 the note names the carrier", note.startswith("Total:"), True)
ck("A5 the note names the declared table and column", "raw_ma_fulfillment.sku" in note, True)
ck("A6 the note states what the silence COST", ds.COST[ds.KIND_PRICE_EVIDENCE] in note, True)
ck("A7 the note carries the underlying cause", "KeyError" in note, True)
ck("A8 a clean read records nothing",
   (ds.read_declared(c, ds.KIND_PRICE_LIST, "t", "f", lambda: {"a": 1.0}, {}), len(c)),
   ({"a": 1.0}, 1))
ck("A9 an empty collector is falsey, a filled one truthy",
   (bool(ds.Complaints()), bool(c)), (False, True))
ck("A10 a None collector is allowed (no caller is forced to pass one)",
   ds.read_declared(None, ds.KIND_OWED_LINK, "t", "f", boom(), {}), {})
ck("A11 every kind states a cost (a new kind cannot ship wordless)",
   sorted(ds.COST) == sorted([ds.KIND_PRICE_EVIDENCE, ds.KIND_PRICE_LIST, ds.KIND_OWED_LINK,
                              ds.KIND_SOLD_MATCH]), True)
ck("A12 merge folds the notes under the message key `warnings`",
   ds.merge({"written": 3}, c), {"written": 3, "warnings": c.notes()})
ck("A13 merge of an empty collector leaves the result alone",
   ds.merge({"written": 3}, ds.Complaints()), {"written": 3})
ck("A14 merge APPENDS rather than replacing another carrier's warnings",
   len(ds.merge({"warnings": ["x"]}, c)["warnings"]), 2)
ck("A15 a note with no table still reads as a sentence",
   "could not be read" in ds.note(ds.KIND_PRICE_LIST), True)

# ── B — 'unconfigured' keeps its meaning ─────────────────────────────────────────────────────────
ck("B1 declared + nothing built => the setup defect is named",
   ds.owed_source_label(True, False), ds.OWED_SOURCE_UNREADABLE)
ck("B2 declared + prices built => neither label (the caller stamps the real source)",
   ds.owed_source_label(True, True), ds.OWED_SOURCE_UNCONFIGURED)
ck("B3 NOT declared => 'unconfigured' still means 'this carrier declares no cost source'",
   ds.owed_source_label(False, False), ds.OWED_SOURCE_UNCONFIGURED)
ck("B4 the two labels are different strings (the whole point)",
   ds.OWED_SOURCE_UNREADABLE != ds.OWED_SOURCE_UNCONFIGURED, True)
ck("B5 a falsey-but-declared map ({}) is the live 2026-10-23 shape",
   ds.owed_source_label("unit_price_map", {}), ds.OWED_SOURCE_UNREADABLE)

# ── C — the exposure total cannot look complete when it is not ───────────────────────────────────
# The carrier's policy as `load_policies` builds it, through the one home — keyed by carrier_id,
# which is what `policy_for` looks up (a `__default__` key would silently fall back to the house
# 60-day window and prove nothing about a declared 12).
from app.modules.commcalc import device_aging_policy as _aging   # noqa: E402
POL = {"c1": _aging.resolve_policy({"sell_within_days": 12})}


def dev(imei, held_days, owed=None):
    from datetime import date, timedelta
    return {"imei": imei, "carrier_id": "c1", "sold_flag": False,
            "acquired_date": str(date(2026, 10, 23) - timedelta(days=held_days)), "owed": owed}


from datetime import date                                        # noqa: E402
TODAY = date(2026, 10, 23)
rows = [dev("1", 30, 529.0), dev("2", 30, None), dev("3", 30, None)]
summ = ui.stamp_aging(rows, POL, today=TODAY)
ck("C1 all three are out of pocket (30 days held, 12-day window)", summ["out_of_pocket"], 3)
ck("C2 the amount is only the one priced device", summ["out_of_pocket_amount"], 529.0)
ck("C3 the two unpriced devices are COUNTED, not forgotten",
   summ["out_of_pocket_unpriced"], 2)
ck("C4 and the total is flagged incomplete, so no screen presents it as a total",
   summ["amount_incomplete"], True)
summ2 = ui.stamp_aging([dev("1", 30, 529.0)], POL, today=TODAY)
ck("C5 a fully priced set is NOT flagged incomplete",
   (summ2["amount_incomplete"], summ2["out_of_pocket_unpriced"]), (False, 0))
ck("C6 the 2026-10-23 shape: everything flagged, nothing priced, $0.00 and SAYS so",
   ui.stamp_aging([dev("1", 30), dev("2", 30)], POL, today=TODAY),
   {"devices": 2, "out_of_pocket": 2, "out_of_pocket_amount": 0.0, "not_measured": 0,
    "out_of_pocket_unpriced": 2, "amount_incomplete": True})

# ── D — the ratchet: no declared read may go back to swallowing its failure ──────────────────────
SRC = open(ENGINE).read()
# Each of the three readers that takes a DECLARED table/column name must accept a collector...
for fn in ("_unit_price_map", "_owed_link_map", "_sold_imei_store_map", "_sold_imei_set"):
    m = re.search(r"^def " + fn + r"\(([^)]*)\)", SRC, re.M | re.S)
    ck(f"D1 {fn} accepts a complaints collector", bool(m) and "complaints" in m.group(1), True)
# ...and the build must actually pass one down, per carrier.
ck("D2 the build makes one collector per carrier", "_ds.Complaints(" in SRC, True)
ck("D3 the price map is built WITH the collector", "_unit_price_map(client, org_id, cfg, complaints)"
   in SRC, True)
ck("D4 the sold-match read passes it", bool(re.search(
    r"_sold_imei_store_map\([^)]*complaints\)", SRC, re.S)), True)
ck("D5 the owed_link read passes it", bool(re.search(
    r"_owed_link_map\(.{0,200}?complaints\) if link_key_field", SRC, re.S)), True)
ck("D6 the complaints reach the build RESULT (not just a log line)", "_ds.merge(result," in SRC, True)
ck("D7 per-carrier warnings are reported too", '"warnings": complaints.notes()' in SRC, True)
ck("D8 the row label is the one home's, not a literal", "_ds.owed_source_label(" in SRC, True)
ck("D9 no literal 'unconfigured' decision survives in the engine",
   re.search(r'owed_source\s*=\s*"unconfigured"', SRC) is None, True)
# The evidence read must DEGRADE to the price home's allowlist, never abandon pricing: the price
# call has to sit OUTSIDE the evidence branch. Proved structurally — the 73%-to-0% regression.
pm = SRC[SRC.index("def _unit_price_map"):SRC.index("def _reimb_types")]
ck("D10 the price list is read unconditionally, not inside the evidence branch",
   pm.index("ma_unit_price_map") > pm.index("ma_device_product_names"), True)
ck("D11 ...and with `names or None`, the documented no-evidence fallback",
   "names or None" in pm, True)
ck("D12 the evidence failure no longer returns {} from the whole function",
   "print(f\"WARN payables _unit_price_map failed" not in pm, True)
ck("D13 the docstring states which table owed_price_table names (it cost a rebuild)",
   "NOT the price list" in pm, True)

bad = [t for t in OUT if not t[1]]
print(f"\n{'='*92}\nharness_payables_declared_source — {len(OUT) - len(bad)} passed, {len(bad)} failed\n{'='*92}")
for name, ok, got, want in OUT:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
sys.exit(1 if bad else 0)
