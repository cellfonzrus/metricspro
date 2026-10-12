"""
PROOF — SEVERAL HANDSETS TO ONE BUYER IN ONE DAY (owner ask 2026-10-12, index §COMMCALC-05).

DB-free. Drives the real `commcalc/multi_handset_day.py` over fixtures shaped like the live
`commcalc.daily_sales_feed`, and locks the design decisions that measurement forced:

 A. A HANDSET IS A BOX-DEPARTMENT LINE, by the caller's own per-org set — never a serial number.
    The regression: a plan line carries a serial too, so the first version of this detector counted
    one phone plus two plans as three devices.

 B. THE BUYER KEY. The dirty live values ('No Customer', 'walk  in', 'BOOST MOBILE', 'Back Order')
    are NOT grouped; casing and spacing ARE folded; nothing else is, because folding initials or
    misspellings would invent an identity.

 C. GROUPING IS ACROSS RECEIPTS. The regression reproduces the live case a per-receipt check cannot
    see: 16 handsets split over 8 separate receipts, every line $0.00.

 D. THE THRESHOLD IS CONFIG, NOT CODE — read through `flag_registry.rule_params`, exclusive, and the
    tenant's switch is honoured.

 E. EVIDENCE, NOT A VERDICT. No function returns a judgement, and no user-facing string in the module
    calls anything fraud, abuse or a scheme.

 F. THE GAPS ARE STATED. Lines with no buyer are counted and reported; an undeclared box-department
    set yields no findings AND says so, rather than reading as "all clear".

 G. ONE HOME, DEREFERENCED. The module spells no severity, area or label of its own, and the box
    departments are injected — an AST scan asserts it never reads the config or defaults the set.
"""
import ast
import os
import sys

sys.path.insert(0, ".")

from harnesslib import py_code_only                             # noqa: E402
from app.modules.core.module_graph import home_under_app        # noqa: E402
from app.modules.commcalc import multi_handset_day as M         # noqa: E402
from app.modules.commcalc import flag_registry as REG           # noqa: E402

HOME = home_under_app("multi_handset_one_buyer")
# `home_under_app` returns the path RELATIVE TO app/ (the same shape
# harness_prospect_eval reads), so the absolute path is built the same way rather than
# re-typing the module path here and letting the two drift.
HOME_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", HOME)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"   [{detail}]" if detail and not cond else ""))


def section(t):
    print(f"\n── {t} " + "─" * max(0, 92 - len(t)))


BOX = {"Android - XP", "IPHONE - XP", "TABLET - XP", "BrandedHandset", "Handset"}


def row(**kw):
    base = {"store": "3248 W Lawrence Ave", "trans_date": "2026-09-18", "customer": "Leonardo Cornelio",
            "department": "BrandedHandset", "product_desc": "Samsung Galaxy A16", "trans_id": "1",
            "serial_1": "", "ext_price": 0, "salesperson": "rep1"}
    base.update(kw)
    return base


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. a handset is a BOX-DEPARTMENT line, never a serial number")

PLANS = [
    row(department="", product_desc="Total MAX 5G Plan $55", serial_1="S1"),
    row(department="", product_desc="Total Wireless Home Internet", serial_1="S2"),
    row(department="", product_desc="Total Wireless Base Unlimited Tablet Plan $50", serial_1="S3"),
]
ONE_PHONE = [row(department="BrandedHandset", product_desc="Motorola Moto G", serial_1="S4")]

check("A1 plan lines carrying a serial are NOT handsets",
      M.handset_rows(PLANS, BOX) == [])
check("A2 the box-department line IS a handset",
      len(M.handset_rows(ONE_PHONE, BOX)) == 1)
# THE REGRESSION: the defect this harness exists for.
check("A3 REGRESSION one phone + three serial-bearing plans is ONE handset, not four",
      len(M.handset_rows(ONE_PHONE + PLANS, BOX)) == 1)
check("A4 a serial is never required — a box line with no serial still counts",
      len(M.handset_rows([row(serial_1="")], BOX)) == 1)
check("A5 a department the tenant has NOT declared is not a handset",
      M.handset_rows([row(department="Accessory")], BOX) == [])
check("A6 the tenant's own set decides: 'Handset' counts here, not under the house set",
      len(M.handset_rows([row(department="Handset")], BOX)) == 1
      and M.handset_rows([row(department="Handset")],
                         {"Android - XP", "IPHONE - XP", "TABLET - XP"}) == [])
check("A7 an EMPTY declared set yields nothing — it never falls back to someone else's labels",
      M.handset_rows([row()], set()) == [] and M.handset_rows([row()], None) == [])
check("A8 whitespace in a declared department name does not stop the match",
      len(M.handset_rows([row(department=" BrandedHandset ")], BOX)) == 1)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. the buyer key: fold casing and spacing, group no placeholder")

check("B1 casing and double spaces fold to one buyer",
      M.normalise_buyer("Rene  HERNANDEZ") == M.normalise_buyer("rene hernandez") == "rene hernandez")
check("B2 punctuation and digits are stripped",
      M.normalise_buyer("Frank  . Jose 2") == "frank jose")
for junk in ("No Customer", "walk  in", "BOOST MOBILE", "Back Order", "", "   ", "n/a"):
    check(f"B3 placeholder not a buyer: {junk!r}", M.is_junk_buyer(junk))
check("B4 a real name is NOT a placeholder", not M.is_junk_buyer("Rene Hernandez"))
check("B5 initials are NOT folded into a longer name (no invented identity)",
      M.normalise_buyer("R Hernandez") != M.normalise_buyer("Rene Hernandez"))
check("B6 a misspelling is NOT folded either",
      M.normalise_buyer("Rene Hernandes") != M.normalise_buyer("Rene Hernandez"))

g = M.group_by_buyer_day([row(customer="No Customer"), row(customer="walk in"),
                          row(customer="Ana Gomez"), row(customer="ANA  GOMEZ")], BOX)
check("B7 two spellings of one buyer are ONE group", len(g["groups"]) == 1)
check("B8 placeholders are reported as unattributable, never grouped",
      len(g["unattributable"]) == 2 and g["handset_lines"] == 4)
check("B9 the same buyer at a DIFFERENT store is a different case",
      len(M.group_by_buyer_day([row(), row(store="other")], BOX)["groups"]) == 2)
check("B10 the same buyer on a DIFFERENT day is a different case",
      len(M.group_by_buyer_day([row(), row(trans_date="2026-09-19")], BOX)["groups"]) == 2)
check("B11 a timestamped date folds to its day",
      len(M.group_by_buyer_day([row(trans_date="2026-09-18T09:00:00Z"),
                                row(trans_date="2026-09-18")], BOX)["groups"]) == 1)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. grouping is ACROSS receipts — the live case a per-receipt check cannot see")

# The real shape, 2026-09-18, 3248 W Lawrence Ave: 16 handsets over EIGHT receipts, all $0.00.
SPLIT = [row(trans_id=str(100 + i // 2), product_desc=f"Phone {i}", ext_price=0)
         for i in range(16)]
cs = M.cases(M.group_by_buyer_day(SPLIT, BOX), 2)
check("C1 the split case is ONE case, not eight", len(cs) == 1)
check("C2 REGRESSION all 16 handsets are counted", cs[0]["handsets"] == 16)
check("C3 it records that it was split", cs[0]["split_across_receipts"] is True)
check("C4 and over how many receipts", cs[0]["receipt_count"] == 8)
check("C5 with the per-receipt breakdown a human can check",
      sum(cs[0]["handsets_per_receipt"].values()) == 16
      and len(cs[0]["handsets_per_receipt"]) == 8)
check("C6 every line at $0.00 is recorded as such", cs[0]["all_zero_dollars"] is True)
check("C7 dollars total to 0.00", cs[0]["dollars"] == 0.0)

ONE_RECEIPT = [row(trans_id="9", product_desc=f"P{i}", ext_price=19.99) for i in range(3)]
c1 = M.cases(M.group_by_buyer_day(ONE_RECEIPT, BOX), 2)
check("C8 a single-receipt case is NOT marked split", c1[0]["split_across_receipts"] is False)
check("C9 dollars are summed and rounded to cents", c1[0]["dollars"] == 59.97)
check("C10 a paid case is not reported as all-zero", c1[0]["all_zero_dollars"] is False)
check("C11 the devices carry their model and line count",
      sum(d["lines"] for d in c1[0]["devices"]) == 3)
check("C12 a row with no model says so rather than showing blank",
      any(d["model"] == "(no model on the row)"
          for d in M.cases(M.group_by_buyer_day(
              [row(product_desc=""), row(product_desc=""), row(product_desc="")], BOX), 2)[0]["devices"]))
check("C13 cases are sorted by handset count, descending",
      [c["handsets"] for c in M.cases(M.group_by_buyer_day(
          SPLIT + [row(customer="Ana Gomez", trans_id="7", product_desc=f"Q{i}") for i in range(3)],
          BOX), 2)] == [16, 3])
check("C14 the reps on the case are listed",
      M.cases(M.group_by_buyer_day(
          [row(salesperson="a"), row(salesperson="b"), row(salesperson="a")], BOX), 2)[0]["reps"]
      == ["a", "b"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. the threshold is CONFIG, exclusive, and the tenant's switch is honoured")

check("D1 the house threshold is 2, read from the registry",
      REG.default_params(M.FLAG_TYPE).get(M.THRESHOLD_KEY) == 2)
TWO = [row(trans_id="1", product_desc="a"), row(trans_id="1", product_desc="b")]
THREE = TWO + [row(trans_id="1", product_desc="c")]
check("D2 EXCLUSIVE: exactly 2 handsets does not flag",
      M.cases(M.group_by_buyer_day(TWO, BOX), 2) == [])
check("D3 3 handsets flags", len(M.cases(M.group_by_buyer_day(THREE, BOX), 2)) == 1)
check("D4 a tenant raising it to 3 stops the 3-handset case",
      M.cases(M.group_by_buyer_day(THREE, BOX), 3) == [])
check("D5 threshold 0 flags any buyer-day with a handset",
      len(M.cases(M.group_by_buyer_day([row()], BOX), 0)) == 1)

f, s = M.findings(THREE, BOX, org_id="o1", period="September 2026")
check("D6 findings come back stamped and registered",
      len(f) == 1 and f[0]["flag_type"] == M.FLAG_TYPE)
check("D7 the finding carries the registry's severity, not its own",
      f[0].get("severity_canon") == REG.severity_for(M.FLAG_TYPE)
      or f[0].get("severity") == REG.severity_for(M.FLAG_TYPE))
check("D8 the threshold in force is reported back", s["threshold"] == 2)
OFF = [{"flag_type": M.FLAG_TYPE, "enabled": False}]
f_off, s_off = M.findings(THREE, BOX, rule_rows=OFF)
check("D9 a tenant can switch it off", f_off == [] and s_off["enabled"] is False)
check("D10 ...and switching it off still reports what it saw",
      s_off["handset_lines"] == 3)
RAISED = [{"flag_type": M.FLAG_TYPE, "params": {"max_handsets": 5}}]
check("D11 a tenant's params row moves the threshold",
      M.findings(THREE, BOX, rule_rows=RAISED)[0] == [])
check("D12 an unknown param key is ignored, never trusted",
      M.findings(THREE, BOX, rule_rows=[{"flag_type": M.FLAG_TYPE,
                                         "params": {"nonsense": 99}}])[1]["threshold"] == 2)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. evidence, not a verdict")

SRC = open(HOME_PATH).read()
# LOCKS READ CODE, not comments. The docstring above deliberately QUOTES the evidence —
# the plan names, the carrier words, the config this module must NOT read — because that is
# what explains the design. Scanning raw source matched its own explanation and failed on a
# correct file, exactly the trap harnesslib.py documents, so the shared stripper is
# dereferenced rather than copied an eleventh time.
CODE = py_code_only(SRC)
check("E1 no case carries a verdict field",
      not ({"verdict", "is_fraud", "fraudulent", "suspicious", "guilty"} & set(cs[0])))
_words = ("fraud", "abuse", "scheme", "stealing", "theft", "criminal")
# Every string literal that is NOT a docstring — ast.get_docstring only reaches the module's own,
# so function and class docstrings are excluded by walking the tree and skipping them explicitly.
_T0 = ast.parse(SRC)
_docstrings = set()
for _n in ast.walk(_T0):
    if isinstance(_n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        _d = ast.get_docstring(_n, clean=False)
        if _d:
            _docstrings.add(_d)
_user_facing = [n.value for n in ast.walk(_T0)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value not in _docstrings and len(n.value) > 12]
check("E2 no user-facing string in the module calls anything fraud",
      not [v for v in _user_facing if any(w in v.lower() for w in _words)],
      str([v[:40] for v in _user_facing if any(w in v.lower() for w in _words)]))
check("E3 the sentence a manager reads is descriptive and names the split",
      "8 separate receipts" in M.describe(cs[0]) and "16 handsets" in M.describe(cs[0]))
check("E4 a one-receipt case does not claim a split",
      "separate receipts" not in M.describe(c1[0]))
check("E5 the note asks for a CHECK, it does not assert wrongdoing",
      "Confirm" in M.coaching_note(cs[0])
      and not any(w in M.coaching_note(cs[0]).lower() for w in _words))
check("E6 the split case's note names the per-sale-limit question",
      "split" in M.coaching_note(cs[0]).lower())
check("E7 the buyer is shown AS RECORDED, not only normalised",
      M.cases(M.group_by_buyer_day([row(customer="Ana  GOMEZ")] * 3, BOX), 2)[0]
      ["buyer_as_recorded"] == ["Ana  GOMEZ"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. the gaps are stated, so nothing reads as a false all-clear")

_, s_none = M.findings([row(), row(), row()], set())
check("F1 no declared box departments → no findings AND it says it saw no handsets",
      s_none["cases"] == 0 and s_none["handset_lines"] == 0)
_, s_junk = M.findings([row(customer="No Customer")] * 4, BOX)
check("F2 unattributable lines are counted, not silently dropped",
      s_junk["unattributable_lines"] == 4 and s_junk["cases"] == 0)
check("F3 the summary says what the key cannot answer",
      len(s_junk["cannot_answer"]) >= 3)
check("F4 ...including the different-day / different-store limit",
      any("different days" in c for c in s_junk["cannot_answer"]))
check("F5 ...and that a placeholder buyer is not grouped",
      any("No Customer" in c for c in s_junk["cannot_answer"]))
_, s_split = M.findings(SPLIT, BOX)
check("F6 the summary counts the split cases separately",
      s_split["split_cases"] == 1)
check("F7 and the all-zero-dollar cases", s_split["zero_dollar_cases"] == 1)
check("F8 buyer-days seen is reported alongside cases found",
      s_split["buyer_days"] == 1 and s_split["cases"] == 1)
check("F9 an empty feed is zero cases, not an error",
      M.findings([], BOX)[1]["cases"] == 0)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. one home, dereferenced — the module owns no vocabulary of its own")

T = ast.parse(SRC)
_assigned = {t.id for n in ast.walk(T) if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name)}
check("G1 the module declares no severity of its own",
      not ({"SEVERITIES", "SEVERITY", "HIGH", "MEDIUM", "LOW", "CRITICAL"} & _assigned))
check("G2 nor its own area or label map",
      not ({"AREAS", "TYPES", "LABELS"} & _assigned))
check("G3 severity / threshold / on-off all come from flag_registry",
      all(f"_reg.{fn}" in CODE for fn in ("is_enabled", "rule_params", "stamp")))
_calls = {ast.unparse(n.func) for n in ast.walk(T) if isinstance(n, ast.Call)}
check("G4 the module does NO I/O — no client, no table, no select",
      not any(k in CODE for k in (".table(", ".execute(", "schema(", "requests.", "sb()")))
check("G5 it never reads the box departments itself — they are a parameter",
      "accessory_config" not in CODE and "box_departments" in
      {a.arg for n in ast.walk(T) if isinstance(n, ast.FunctionDef) for a in n.args.args})
check("G6 and never defaults them to the house labels",
      not any(lbl in CODE for lbl in ("Android - XP", "IPHONE - XP", "TABLET - XP")))
check("G7 no tenant, carrier or store name is branched on (RULE TWO)",
      not any(w in CODE for w in ("Luxelink", "Cellfonz", "Total Wireless")),
      "a carrier/tenant word survives in CODE (not a comment)")
check("G8 the registry agrees this writer owns this flag",
      REG.TYPES[M.FLAG_TYPE]["writer"].endswith(os.path.basename(HOME_PATH)))
check("G9 the flag's grain is one the board already renders",
      REG.grain_of(M.FLAG_TYPE) in ("transaction", "store_day", "rep_period", "store_period"))
check("G10 the threshold key the module reads is the one the registry declares",
      M.THRESHOLD_KEY in REG.default_params(M.FLAG_TYPE))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. the finding is a row the flags table can actually hold, written under its OWN source")

import importlib.util as _ilu
_fp_spec = _ilu.spec_from_file_location(
    "fp_probe", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "app", "modules", "commcalc", "flag_persist.py"))
FP = _ilu.module_from_spec(_fp_spec)
_fp_spec.loader.exec_module(FP)

_HS = [{"department": "BOX", "store": "1 Main St", "trans_date": "2026-08-01",
        "customer": "Rene Hernandez", "trans_id": f"R{i}", "product_desc": "Phone A",
        "serial_1": f"S{i}", "ext_price": 120.0, "salesperson": "alice"} for i in range(4)]
_rows_h, _sum_h = M.findings(_HS, {"BOX"}, org_id="o", period="August 2026",
                             period_month=8, period_year=2026)

# THE DEFECT THIS SECTION EXISTS FOR, found 2026-10-12 while wiring the detector up.
# `flag_persist.sync` RETIRES every open flag in the period carrying one of the caller's `sources`
# that the run did not produce. `router._CALC_FLAG_SOURCES` claims 'sales' for the commission
# recalculation's own flag pass, so writing these findings under 'sales' would have had the next
# recalculation of the same month silently resolve every one of them away. The detector would have
# looked correct and emptied itself.
check("H1 the writer owns its own `source` string",
      M.SOURCE and M.SOURCE != "sales")
_CALC_SOURCES = ('payment_detail', 'sales', 'dlar_store', 'mi_report')
check("H2 and it collides with no source the commission flag pass retires",
      M.SOURCE not in _CALC_SOURCES,
      f"{M.SOURCE!r} is retired by router._CALC_FLAG_SOURCES")
check("H3 every finding carries it", _rows_h and all(r["source"] == M.SOURCE for r in _rows_h))

_persisted = set(FP._ROW_FIELDS)
_own = {"org_id", "evidence"}
check("H4 every key of a finding is either a flags column or deliberately display-only",
      _rows_h and all(set(r) - _persisted <= _own for r in _rows_h),
      f"unknown keys: {sorted(set(_rows_h[0]) - _persisted - _own) if _rows_h else []}")
check("H5 the POS store spelling goes in store_address, never guessed into store_code",
      all(r.get("store_address") and not r.get("store_code") for r in _rows_h))
check("H6 the period identity flag_persist keys on is present",
      all(r.get("period_month") and r.get("period_year") for r in _rows_h))
check("H7 flag_persist can key the row without falling back to the raw spelling",
      FP.period_canon(_rows_h[0]) == "202608")
_clean = FP._clean(_rows_h[0])
check("H8 and the cleaned payload keeps the sentence a manager reads",
      _clean["description"] == _rows_h[0]["description"]
      and _clean["coaching_note"] == _rows_h[0]["coaching_note"])
check("H9 a single rep / model / serial is named, several are not misreported as one",
      _clean["epay_salesperson"] == "alice" and _clean["phone_model"] == "Phone A"
      and _rows_h[0]["imei"] is None)
check("H10 the amount is the dollars on the lines", float(_clean["amount"]) == 480.0)

# The sweep must hand the detector the per-org box set and the void filter, not decide either.
WIRE = py_code_only(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "app", "modules", "commcalc",
                                      "watchdog_router.py")).read())
check("H11 the sweep calls the detector", "_handsets.findings(" in WIRE)
check("H12 it injects the per-org box departments", "_accessory_config(client, org_id)" in WIRE
      and 'get("box_departments")' in WIRE)
check("H13 it excludes voided lines through the shared token set", "is_voided(" in WIRE)
check("H14 it writes under the detector's own source", "_handsets.SOURCE" in WIRE)
check("H15 it names no sales table literally — the one home picks it",
      "_open_month_source(" in WIRE
      and '"raw_sales"' not in WIRE and "'raw_sales'" not in WIRE
      and '"daily_sales_feed"' not in WIRE and "'daily_sales_feed'" not in WIRE,
      "a literal sales table name is a second opinion about which feed is live")
check("H16 and it resolves the store_code through the one home (mig 285)",
      "flag_store_resolver.stamp_flags" in WIRE)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 98)
print(f"{len(PASS)} passed / {len(FAIL)} failed")
if FAIL:
    for f_ in FAIL:
        print("  FAILED:", f_)
    sys.exit(1)
print("Several handsets to one buyer in one day — counted across receipts, from the live feed, "
      "with the gaps said out loud and no verdict.")
