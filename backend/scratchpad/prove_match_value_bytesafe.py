"""RULE THREE adoption — money-safety proof for commission-plans `match_value`.

Claim to prove: converting the `match_value` free-text box to <EntityPicker> changes INPUT UX only,
never what the calc pays. The engine that matches sale lines to a rule is the REAL
`commission_engine._rule_matches`; this harness drives it directly (no mocks) and shows that, for the
SAME user intent, the string the picker emits produces a BYTE-IDENTICAL match vector to the string the
old text box stored.

Why it holds (the keystone): `_rule_matches` normalizes BOTH sides with `.strip().lower()` before every
comparison (commission_engine.py:43-51 for match_value; :34 `_line_value` for the sale value). So the
stored `match_value` only affects payouts up to strip+lowercase equivalence — exactly the equivalence
class the picker preserves:
  • PICK an observed value  -> onChange emits the raw distinct string (id===value): a string the user
    would have typed to match that value.
  • CREATE a value          -> onCreate emits query.trim(): trailing/leading whitespace removed, which
    the engine ALSO strips, so the match set is unchanged; for a clean typed value it is byte-identical.

Run:  cd backend && python3 scratchpad/prove_match_value_bytesafe.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.modules.commcalc.commission_engine import _rule_matches, _line_value  # REAL engine

# Sale lines exactly as they land in raw_sales / daily_sales_feed (mixed case + real labels).
ROWS = [
    {"contract_type": "Upgrade", "department": "Accessories", "category": "Cases",
     "product_desc": "Device Setup Charge", "tender_type": "ACIMA", "trans_type": "Sale", "sku": "SET-001"},
    {"contract_type": "New Activation", "department": "Ondigo", "category": "",
     "product_desc": "Screen Protector", "tender_type": "Cash", "trans_type": "Sale", "sku": "SCR-99"},
    {"contract_type": "", "department": "", "category": "",
     "product_desc": "iPhone 15 Case", "tender_type": "Credit", "trans_type": "Return", "sku": "CAS-15"},
    {"contract_type": "BYOD", "department": "Insurance", "category": "Protection",
     "product_desc": "Monthly Insurance Plan", "tender_type": "acima", "trans_type": "Sale", "sku": "INS-1"},
    {"contract_type": "Port-In", "department": "Accessories", "category": "Chargers",
     "product_desc": "USB-C Setup Kit", "tender_type": "Cash", "trans_type": "Sale", "sku": "SET-002"},
]


def vec(field, op, value):
    """Match vector for a rule over ROWS (the exact tuple the engine would count)."""
    rule = {"match_field": field, "match_op": op, "match_value": value}
    return tuple(_rule_matches(r, rule) for r in ROWS)


N = {"pass": 0, "fail": 0}


def eq(label, a, b):
    ok = a == b
    N["pass" if ok else "fail"] += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         old-typed = {a}")
        print(f"         picker    = {b}")


print("== A. same-intent equivalence: old free-text  ==  picker emit (per field/op) ==")
# (field, op, OLD typed string, PICKER-picked observed | None, PICKER-created(trim)).
# `picked` is asserted ONLY for op=equals — there, picking the exact observed value IS the same intent as
# typing it. For op=contains the user types a SUBSTRING pattern and CREATES it (picking a whole observed
# value is a DIFFERENT, narrower intent by design); for op=in the picker CREATES the comma list. So for
# contains/in we prove the CREATE path is byte-identical (`picked`=None → not asserted).
CASES = [
    # equals on contract_type — the DEFAULT match field (blankRule)
    ("contract_type", "equals", "upgrade",         "Upgrade",        "Upgrade"),      # lowercase vs observed vs create
    ("contract_type", "equals", "Upgrade ",        "Upgrade",        "Upgrade"),      # trailing space (old) vs trimmed
    ("contract_type", "equals", " new activation", "New Activation", "New Activation"),
    ("contract_type", "equals", "BYOD",            "BYOD",           "BYOD"),
    ("department",    "equals", "accessories",     "Accessories",    "Accessories"),
    ("trans_type",    "equals", "return ",         "Return",         "Return"),
    # contains on product_desc / category — the legitimate op=contains PATTERN case (CREATE path)
    ("product_desc",  "contains", " setup ",       None, "Setup"),                    # spaces + case; both -> "setup"
    ("product_desc",  "contains", "CASE",          None, "Case"),
    ("category",      "contains", "PROTECT",        None, "Protect"),
    # in on tender_type — comma list; picker CREATES the whole list string
    ("tender_type",   "in", "ACIMA, Cash ",        None, "acima,cash"),
]
for field, op, old, picked, created in CASES:
    base = vec(field, op, old)
    if picked is not None:
        eq(f"{field}/{op}: old '{old}'  ==  picked '{picked}'", base, vec(field, op, picked))
    eq(f"{field}/{op}: old '{old}'  ==  created(trim) '{created}'", base, vec(field, op, created))

print("\n== B. normalization lemma: strip+lower-equal strings are match-identical (any field/op) ==")
# If two strings collapse to the same strip().lower(), the engine can never tell them apart. This is the
# formal reason the picker's trim (and observed-vs-typed casing) is money-safe.
PAIRS = [("Upgrade", "  upgrade  "), ("Device Setup Charge", "DEVICE SETUP CHARGE"),
         ("acima,cash", " ACIMA , CASH "), ("Return", "return")]
for field in ("contract_type", "product_desc", "tender_type", "trans_type"):
    for op in ("equals", "contains", "in"):
        for a, b in PAIRS:
            eq(f"{field}/{op}: '{a}'  ==  '{b}'", vec(field, op, a), vec(field, op, b))

print("\n== C. 'any' field ignores match_value entirely (disabled picker can't change pay) ==")
# The picker is disabled when match_field=='any'; even if a stale match_value lingers, the engine ignores
# it — so a disabled picker is provably inert.
for mv in ("", "Upgrade", "  whatever  ", "leftover-from-a-prior-field"):
    eq(f"any/equals match_value={mv!r} -> all True", vec("any", "equals", mv), tuple(True for _ in ROWS))

print("\n== D. sanity: the picker actually CHANGES nothing a real match would (regression guard) ==")
# A picked observed value must still MATCH the row it came from (proves options carry real, matchable data).
eq("picked 'Upgrade' matches exactly the 1 Upgrade line", vec("contract_type", "equals", "Upgrade"),
   (True, False, False, False, False))
eq("contains 'setup' matches the 2 setup lines", vec("product_desc", "contains", "setup"),
   (True, False, False, False, True))
eq("_line_value normalizes the sale side too", _line_value(ROWS[0], "contract_type"), "upgrade")

print(f"\n==== {N['pass']} PASS / {N['fail']} FAIL ====")
sys.exit(1 if N["fail"] else 0)
