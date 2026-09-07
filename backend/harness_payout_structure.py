"""HARNESS — payout_structure.py (the employee-facing Payout Structure PDF).

The risk in this document is not that it crashes: it is that it prints a WRONG RATE on a page an employee
reads as policy. Every section below targets a specific way that could happen, and the fixtures are taken
from LUXELINK'S REAL ROWS (measured 2026-08-11) rather than invented, so a passing harness means the real
document is right.

  A. Trap ① — `amount` and `pct` are both populated; only the one the engine reads may be printed.
  B. Trap ② — `contains` is a substring test, `in` is a list. Confusing them broadens a rule.
  C. Trap ③ — "per unit" resolves through the mig-260 pay gate; a tender rule pays once per DEVICE.
  D. Zero-rate and non-qualifying rules never appear in the pay table.
  E. Honest degradation — empty plan, unassigned plan, inactive plan, missing config.
  F. Exclusions carry their own operator vocabulary (word/prefix/suffix).
  G. The PDF actually renders, escapes markup, and survives hostile text.
  H. ARMED negative control — a deliberately wrong expectation MUST fail, or this harness proves nothing.

Run: python3 harness_payout_structure.py     (no DB, no network — pure fixtures)
"""
import sys
import os

# Anchored to THIS FILE's directory so the harness runs identically from `backend/` and
# from the repo root (commit 564c171f).
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from app.modules.commcalc import payout_structure as ps   # noqa: E402
from app.modules.commcalc import plan_pay_gate as gate    # noqa: E402

PASS, FAIL = [], []

# ── reportlab: SKIP (loudly) vs PRODUCT DEFECT ───────────────────────────────────────────────────
# This file used to die on `ModuleNotFoundError: No module named 'reportlab'`. Before making it
# tolerate that, the question that actually matters was answered: does the PRODUCT need reportlab at
# runtime, and is the deployment missing it?
#
#   It needs it   — app/modules/notify/render.py::build_pdf and three other shipped modules import
#                   reportlab lazily inside the render call. Those are live code paths, not tests.
#   It declares it — backend/requirements.txt line 13, `reportlab>=4.2.0`, uncommented.
#   It installs it — backend/Dockerfile: `RUN pip install --no-cache-dir -r requirements.txt`.
#
# So the deployed image HAS reportlab and there is no production defect here. What is missing is
# this HARNESS CONTAINER's copy (it is short 7 declared deps: uvicorn, reportlab, pdfplumber, segno,
# anthropic, playwright, pywebpush). That is an environment gap, and a skip is the honest answer.
#
# But a blanket `try: import reportlab / except: pass` would ALSO stay quiet on the day someone adds
# a shipped import of a package nobody declared — the real, dangerous version of this failure. So
# the gate below distinguishes the two, and only ONE of them is a skip:
#   installed              -> RUN the PDF assertions for real.
#   missing but DECLARED   -> SKIP, counted and printed in the summary. Never a silent pass.
#   missing and UNDECLARED -> FAIL loudly: shipped code importing a package the deployment does not
#                             install is a production defect, and this harness will say so.
SKIPPED = []


def _pdf_backend():
    """('run' | 'skip' | 'defect', message) for the reportlab PDF backend."""
    import importlib.util
    installed = importlib.util.find_spec("reportlab") is not None
    req = os.path.join(_HERE, "requirements.txt")
    declared = False
    try:
        for line in open(req, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and line.split("[")[0].split("=")[0] \
                    .split(">")[0].split("<")[0].strip().lower() == "reportlab":
                declared = True
                break
    except OSError:
        pass
    if installed:
        return "run", "reportlab present"
    if declared:
        return "skip", ("reportlab is DECLARED in backend/requirements.txt and installed by the "
                        "Dockerfile, but is absent from THIS container — environment gap, not a "
                        "product defect. Install it to run these assertions: pip install reportlab")
    return "defect", ("PRODUCT DEFECT: shipped code imports reportlab but backend/requirements.txt "
                      "does not declare it — the deployed image would 500 on every PDF export")


PDF_MODE, PDF_WHY = _pdf_backend()


def skip(name, why):
    SKIPPED.append(name)
    print(f"  SKIP {name}\n       {why}")




def check(label, got, want):
    if got == want:
        PASS.append(label)
    else:
        FAIL.append(f"{label}\n      got:  {got!r}\n      want: {want!r}")


def check_in(label, needle, hay):
    if needle in hay:
        PASS.append(label)
    else:
        FAIL.append(f"{label}\n      {needle!r} not found in {hay!r}")


def check_not_in(label, needle, hay):
    if needle not in hay:
        PASS.append(label)
    else:
        FAIL.append(f"{label}\n      {needle!r} WAS found in {hay!r} (must not be)")


def section(t):
    print(f"\n── {t}")


# ── REAL LUXELINK ROWS (measured 2026-08-11 via sbsql against commcalc.commission_rule) ────────────
R_NY_ACC = {"label": "Accessories", "match_field": "accessory", "match_op": "equals",
            "match_value": "yes", "qualifies": True, "payout_kind": "pct_price",
            "amount": "10.0", "pct": "0.1", "tiered": False, "sort": 1}
R_NY_ACT = {"label": "Activations", "match_field": "category", "match_op": "equals",
            "match_value": "KittedBranded", "qualifies": True, "payout_kind": "flat_per_unit",
            "amount": "10.0", "pct": "0.0", "tiered": False, "sort": 0}
R_CHI_ACC = {"label": "accessory", "match_field": "accessory", "match_op": "equals",
             "match_value": "yes", "qualifies": True, "payout_kind": "pct_price",
             "amount": "0.175", "pct": "0.175", "tiered": False, "sort": 0}
R_CHI_EDGE = {"label": "edge", "match_field": "tender_type", "match_op": "contains",
              "match_value": "Credit Card; TW Financing Prepaid", "qualifies": True,
              "payout_kind": "flat_per_unit", "amount": "25.0", "pct": "0.0", "sort": 1}
R_CHI_UPG = {"label": "upgrade", "match_field": "contract_type", "match_op": "equals",
             "match_value": "Upgrade", "qualifies": True, "payout_kind": "flat_per_unit",
             "amount": "0.0", "pct": "0.0", "sort": 7}
R_DM_VHI = {"label": "VHI", "match_field": "product_desc", "match_op": "contains",
            "match_value": "Verizon Home Internet", "qualifies": True,
            "payout_kind": "flat_per_unit", "amount": "2.0", "pct": "0.0", "sort": 1}

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. TRAP ① — the rate printed is the rate the ENGINE reads (amount vs pct)")

# The single most dangerous row in the tenant: amount=10.0 AND pct=0.1 on a pct_price rule.
# commission_engine._line_payout reads `pct` -> 10% of price. Printing `amount` would promise $10/unit.
rate, zero = ps.describe_rate(R_NY_ACC)
check("A1 NY accessory prints the pct the engine uses", rate, "10% of the sale price")
check_not_in("A2 NY accessory NEVER prints the decoy $10.00 amount", "$10", rate)
check("A3 NY accessory is not flagged zero-pay", zero, False)

check("A4 Chicago accessory = 17.5% of sale price", ps.describe_rate(R_CHI_ACC)[0],
      "17.5% of the sale price")
check("A5 flat_per_unit reads `amount`", ps.describe_rate(R_NY_ACT)[0], "$10.00")
check("A6 flat_per_unit $25 edge", ps.describe_rate(R_CHI_EDGE)[0], "$25.00")

# Every pct_* kind must read pct, and every flat kind must read amount. A decoy is planted in the
# unread field of each so a swapped read is caught, not merely a wrong format.
for kind, basis in (("pct_gp", "of gross profit"), ("pct_price", "of the sale price"),
                    ("pct_price_over_cost", "of the margin (sale price less cost)"),
                    ("pct_mrc", "of the monthly plan charge")):
    r = {"payout_kind": kind, "pct": "0.05", "amount": "999.0"}
    check(f"A7.{kind} reads pct, ignores the $999 decoy", ps.describe_rate(r)[0], f"5% {basis}")
r_flat = {"payout_kind": "flat", "amount": "100.0", "pct": "0.99"}
check("A8 flat bonus reads amount, ignores the 99% decoy", ps.describe_rate(r_flat)[0], "$100.00 bonus")

check("A9 money() always carries cents", ps.money(25), "$25.00")
check("A10 pct() trims trailing zeros", ps.pct(0.10), "10%")
check("A11 pct() keeps real decimals", ps.pct(0.175), "17.5%")

section("B. TRAP ② — `contains` is a substring, `in` is a list")

cond = ps.describe_condition(R_CHI_EDGE)
check("B1 contains renders as a single literal string", cond,
      'Payment method contains “Credit Card; TW Financing Prepaid”')
check_not_in("B2 a contains value is NEVER split into 'any of'", "any of", cond)

r_in = dict(R_CHI_EDGE, match_op="in", match_value="Cash,Credit Card")
check("B3 `in` renders as a list", ps.describe_condition(r_in),
      'Payment method is any of: “Cash”, “Credit Card”')
r_in1 = dict(R_CHI_EDGE, match_op="in", match_value="Cash")
check("B4 a one-value `in` reads naturally", ps.describe_condition(r_in1), 'Payment method is “Cash”')
check("B5 equals is exact", ps.describe_condition(R_CHI_UPG), 'Contract type is “Upgrade”')
check("B6 accessory=yes reads as a category", ps.describe_condition(R_NY_ACC),
      "Any item classified as an accessory")
check("B7 accessory=no is negated, not dropped",
      ps.describe_condition(dict(R_NY_ACC, match_value="no")),
      "Any item NOT classified as an accessory")
check("B8 match_field=any covers everything",
      ps.describe_condition({"match_field": "any"}), "Every sale line")
check("B9 an unknown field is humanized, never dropped",
      ps.describe_condition({"match_field": "weird_new_field", "match_op": "equals", "match_value": "x"}),
      'Weird new field is “x”')
check("B10 a blank match_value is stated, not silently rendered as everything",
      ps.describe_condition({"match_field": "category", "match_op": "equals", "match_value": ""}),
      "Category (no value set)")

section("C. TRAP ③ — frequency comes from the pay gate, not from the words 'per unit'")

# Under the code defaults (which ARE the owner's 2026-08-01 ruling), a flat_per_unit rule matching on
# tender_type collapses to one payment per DEVICE. This is the 8x$25 overpayment fix.
freq, note = ps.describe_frequency(R_CHI_EDGE)
check("C1 a tender-matched flat rule pays once per device", freq, "Once per device")
check("C2 ... and carries the explanatory footnote", bool(note and "accessories" in note), True)

freq2, note2 = ps.describe_frequency(R_DM_VHI)
check("C3 a product-matched flat rule pays per item", freq2, "Each qualifying item")
check("C4 ... with no dedup footnote", note2, None)

check("C5 a pct rule is never deduped", ps.describe_frequency(R_NY_ACC)[0], "Each qualifying item")
check("C6 a flat bonus is once per period", ps.describe_frequency(r_flat)[0], "Once per pay period")
check("C7 an explicit per-rule unit_basis wins",
      ps.describe_frequency(dict(R_DM_VHI, unit_basis="per_transaction"))[0], "Once per transaction")

# The document must follow a tenant that has RECONFIGURED the gate, not the code default.
off = {"enabled": False, "auto_txn_level_fields": ["tender_type"], "default_basis": "per_device"}
check("C8 gate disabled by the tenant => per line, and the doc says so",
      ps.describe_frequency(R_CHI_EDGE, off)[0], "Each qualifying item")
custom = dict(gate.UNIT_DEFAULTS, default_basis="per_transaction")
check("C9 tenant's own default_basis is honoured",
      ps.describe_frequency(R_CHI_EDGE, custom)[0], "Once per transaction")

# Independent confirmation that C1 is the ENGINE's answer and not this module's opinion.
check("C10 resolve_unit_basis agrees (same resolver the payout uses)",
      gate.resolve_unit_basis(R_CHI_EDGE, gate.UNIT_DEFAULTS), ("per_device", "auto_txn_field"))

section("D. Zero-rate and non-qualifying rules stay OUT of the pay table")

plan_chi = {"id": "p1", "name": "Total Employee Comp Chicago", "is_active": True,
            "rules": [R_CHI_ACC, R_CHI_EDGE, R_CHI_UPG], "tiers": [],
            "assignments": [{"scope": "employee", "scope_value": f"Rep {i}"} for i in range(32)]}
doc = ps.build_doc([plan_chi], tenant_name="Luxelink Wireless LLC", generated_at="August 11, 2026")
p = doc["plans"][0]
paid = {i["what"] for i in p["pay_items"]}
unpaid = {i["what"] for i in p["no_pay_items"]}
check("D1 the $0 upgrade rule is not presented as paying", "Upgrade" in paid, False)
check("D2 ... it is disclosed as not paying", "Upgrade" in unpaid, True)
check("D3 the paying rules are listed", paid, {"Accessory", "Edge"})
check("D4 a $0 rule says why",
      p["no_pay_items"][0]["why"], "Currently set to zero — earns no incentive.")

nq = dict(R_DM_VHI, qualifies=False, label="tracked only")
doc_nq = ps.build_doc([dict(plan_chi, rules=[R_CHI_ACC, nq])])
check("D5 qualifies=false is disclosed as tracking-only",
      doc_nq["plans"][0]["no_pay_items"][0]["why"], "Tracked for reporting only — does not pay.")
check("D6 ... and never in the pay table",
      [i["what"] for i in doc_nq["plans"][0]["pay_items"]], ["Accessory"])

# Presentation of the tenant's own shorthand labels must never RENAME their products.
check("D7 an all-lowercase label is sentence-cased", ps.display_label("accessory"), "Accessory")
check("D8 an acronym is left alone (Title-case would give 'Vhi')", ps.display_label("VHI"), "VHI")
check("D9 ... and so is FIOS", ps.display_label("FIOS"), "FIOS")
check("D10 a mixed-case label is untouched", ps.display_label("edge Financing"), "edge Financing")
check("D11 an empty label is safe", ps.display_label(None), "")
check("D12 a labelless rule falls back to its condition, not a blank cell",
      ps.build_doc([dict(plan_chi, rules=[dict(R_NY_ACT, label="")])])["plans"][0]["pay_items"][0]["what"],
      "Category is “KittedBranded”")

section("E. Honest degradation")

empty = {"id": "p2", "name": "Total Comp Sales manager", "is_active": True, "rules": [], "tiers": [],
         "assignments": [{"scope": "employee", "scope_value": "Jose Utrera"}]}
d_empty = ps.build_doc([empty])
check("E1 a plan with no rules warns that it pays nothing",
      any("earns $0.00" in w for w in d_empty["plans"][0]["warnings"]), True)
check("E2 ... and shows no pay rows", d_empty["plans"][0]["pay_items"], [])

unassigned = dict(plan_chi, assignments=[])
check("E3 an unassigned plan is called out",
      any("not assigned to anyone" in w for w in ps.build_doc([unassigned])["plans"][0]["warnings"]),
      True)

inactive = dict(plan_chi, id="p3", name="Zzz Paused", is_active=False)
d_ord = ps.build_doc([inactive, plan_chi])
check("E4 inactive plans sort last, not hidden", [x["name"] for x in d_ord["plans"]],
      ["Total Employee Comp Chicago", "Zzz Paused"])
check("E5 inactive is flagged", d_ord["plans"][1]["active"], False)

tiered_no_table = dict(plan_chi, rules=[dict(R_CHI_ACC, tiered=True)])
check("E6 a tier-scaled rule with no tier table is explained",
      any("no tiers configured" in w for w in ps.build_doc([tiered_no_table])["plans"][0]["warnings"]),
      True)

check("E7 assignments summarise headcount", p["applies"]["lines"][0], "32 named employees")
check("E8 singular reads correctly", ps.describe_assignments(
    [{"scope": "employee", "scope_value": "Jose Utrera"}])["lines"][0], "1 named employee")
check("E9 a default-scope plan explains precedence", ps.describe_assignments(
    [{"scope": "default", "scope_value": None}])["lines"],
    ["Everyone not covered by a more specific plan"])
check("E10 store scope is named", ps.describe_assignments(
    [{"scope": "store", "scope_value": "957"}])["lines"], ["Store: 957"])

check("E11 plan_id filter renders one plan only",
      [x["name"] for x in ps.build_doc([plan_chi, empty], plan_id="p2")["plans"]],
      ["Total Comp Sales manager"])
check("E12 no plans at all still produces a document", ps.build_doc([])["plans"], [])

# The multi-plan precedence bullet must appear only when it is TRUE for this tenant.
check("E13 precedence explained when >1 plan",
      any("takes precedence" in b for b in ps.build_doc([plan_chi, empty])["how_it_works"]), True)
check("E14 ... and not claimed when there is one plan",
      any("takes precedence" in b for b in ps.build_doc([plan_chi])["how_it_works"]), False)

section("F. Rule scope + exclusion vocabulary")

scoped = dict(R_NY_ACT, applies_scope_kind="market", applies_scope_value="New York")
check("F1 a scoped rule discloses where it applies", ps.describe_rule_scope(scoped),
      "Market: New York")
check("F2 the RAW value is displayed, not the canonicalised match key",
      ps.describe_rule_scope(dict(R_NY_ACT, applies_scope_kind="store",
                                  applies_scope_value="957 Pennsylvania Ave")),
      "Store: 957 Pennsylvania Ave")
check("F3 an unscoped rule shows nothing", ps.describe_rule_scope(R_NY_ACT), None)

rtr = gate.DEFAULT_EXCLUSIONS[0]
check("F4 the seeded RTR exclusion is word-anchored in words", ps.describe_exclusion_condition(rtr),
      "Product name contains the word “RTR”")
check_not_in("F5 ... and never reads as a plain equality", "is “RTR”",
             ps.describe_exclusion_condition(rtr))
check("F6 prefix op", ps.describe_exclusion_condition(
    {"match_field": "sku", "match_op": "prefix", "match_value": "AC-"}),
    "SKU starts with “AC-”")
check("F7 suffix op", ps.describe_exclusion_condition(
    {"match_field": "sku", "match_op": "suffix", "match_value": "-RF"}), "SKU ends with “-RF”")

d_exc = ps.build_doc([plan_chi], exclusions=[rtr])
check("F8 exclusions reach the document", d_exc["never_pays"][0]["condition"],
      "Product name contains the word “RTR”")
check("F9 a disabled exclusion is dropped",
      ps.build_doc([plan_chi], exclusions=[dict(rtr, enabled=False)])["never_pays"], [])

section("G. Tiers")

tier_plan = dict(plan_chi, base_tier_metric="activations", tier_below_min_multiplier=0.5,
                 tiers=[{"min_count": 0, "multiplier": 1, "metric": "activations"},
                        {"min_count": 30, "multiplier": 1.25, "metric": "activations"}])
t = ps.build_doc([tier_plan])["plans"][0]["tiers"]
check("G1 tier metric surfaces", t["metric"], "activations")
check("G2 base tier reads as full rate", t["rows"][0]["effect"], "full rate")
check("G3 an uplift is expressed as a percentage", t["rows"][1]["effect"],
      "25% more than the base rate")
check("G4 multiplier formatting", t["rows"][1]["multiplier"], "1.25x")
check("G5 below-minimum multiplier disclosed", t["below"], "0.5x")
check("G6 a downgrade tier is worded as less",
      ps.describe_tiers({"tiers": [{"min_count": 5, "multiplier": 0.8}]})["rows"][0]["effect"],
      "20% less than the base rate")

section("H. The PDF renders, and hostile text cannot break it")

hostile = dict(R_DM_VHI, label="Tom & Jerry <b>bonus</b>",
               match_value="A&B <script>alert(1)</script> Ω")
h_doc = ps.build_doc([dict(plan_chi, rules=[hostile], notes="Notes with <tags> & ampersands")],
                     tenant_name="Ampersand & Co <Ltd>", exclusions=[rtr])

if PDF_MODE == "defect":
    FAIL.append("H0 reportlab is imported by shipped code but NOT declared in requirements.txt "
                f"— {PDF_WHY}")
elif PDF_MODE == "skip":
    for _n in ("H1 output is a real PDF", "H2 ... of a plausible size", "H3 ... and is terminated",
               "H4 markup in tenant/product/label text renders instead of crashing",
               "H5 an empty document still renders"):
        skip(_n, PDF_WHY)
else:
    pdf = ps.render_pdf(doc)
    check("H1 output is a real PDF", pdf[:5], b"%PDF-")
    check("H2 ... of a plausible size", len(pdf) > 3000, True)
    check("H3 ... and is terminated", pdf.rstrip()[-5:], b"%%EOF")
    h_pdf = ps.render_pdf(h_doc)
    check("H4 markup in tenant/product/label text renders instead of crashing", h_pdf[:5], b"%PDF-")
    check("H5 an empty document still renders", ps.render_pdf(ps.build_doc([]))[:5], b"%PDF-")

# Reportlab-INDEPENDENT half of the same guarantee, so a missing PDF backend can never take the
# whole hostile-text proof down with it. Note what is NOT claimed here: `build_doc` is PURE by
# design (module docstring: "everything above render_pdf is pure"), so hostile text SURVIVES it
# verbatim — escaping is `render_pdf`'s job, applied as the text reaches reportlab's Paragraph.
check("H4b (backend-independent) the hostile document builds at all — the pure layer does not choke "
      "on markup, it carries it",
      "<script>" in repr(h_doc) and bool(h_doc["plans"]), True)
_ps_src = open(os.path.join(_HERE, "app/modules/commcalc/payout_structure.py"),
               encoding="utf-8").read()
_render_body = _ps_src.split("def render_pdf")[1]
check("H4c (backend-independent) render_pdf escapes all three Paragraph metacharacters before any "
      "tenant text reaches reportlab",
      all(pat in _render_body for pat in ('.replace("&", "&amp;")',
                                          '.replace("<", "&lt;")',
                                          '.replace(">", "&gt;")')), True)
check("H4d (backend-independent) …and the tenant name specifically goes through it",
      "Paragraph(esc(tenant)" in _render_body, True)

check("H6 filename is slugged and dated", ps.filename_for(doc),
      "luxelink-wireless-llc-payout-structure-august-11-2026.pdf")
check("H7 filename survives an empty tenant", ps.filename_for({"tenant": "", "generated_at": ""}),
      "payout-structure.pdf")

# The document model must be JSON-safe: the endpoint also serves it as JSON for the on-screen preview.
import json  # noqa: E402
check("H8 the document model is JSON-serialisable", isinstance(json.dumps(doc), str), True)

section("I. ARMED negative control — these MUST fail if the checks are real")

_p_before, _f_before = len(PASS), len(FAIL)
check("I-armed rate", ps.describe_rate(R_NY_ACC)[0], "$10.00 per unit")       # the WRONG answer
check("I-armed frequency", ps.describe_frequency(R_CHI_EDGE)[0], "Each qualifying item")
armed_failed = len(FAIL) - _f_before
if armed_failed == 2:
    FAIL[:] = FAIL[:_f_before]
    PASS.append("I1 negative control fired on both wrong expectations (checks are live)")
else:
    FAIL.append(f"I1 NEGATIVE CONTROL DID NOT FIRE — {armed_failed}/2 wrong answers were accepted. "
                f"The assertions above prove nothing.")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 78}")
for f in FAIL:
    print(f"  ✗ {f}")
print(f"  PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
if SKIPPED:
    print(f"  SKIPPED {len(SKIPPED)} (NOT passed): {', '.join(SKIPPED)}")
    print(f"  reason: {PDF_WHY}")
print(f"{'=' * 78}")
sys.exit(1 if FAIL else 0)
