#!/usr/bin/env python3
"""PROOF + LOCK — a component reached on a FALLBACK BASIS is a placement of last resort, not
evidence about the dollar; and an explicit declaration is never overridden by a keyword guess
(index §66).

DB-free, network-free, stdlib only. Run: `python3 backend/harness_component_basis_evidence.py`

THE DEFECT THIS PINS, measured live read-only on house org `00000000-0000-0000-0000-000000000001`
with the REAL functions and the REAL config (not read off the code):

    classify(... 'Ramp Up Subsidy' ...) ->
        component='REIMBURSEMENT', basis='declared_category_unmapped',
        declared=False, declared_category='MDF', subtype='subsidy'

  The org DOES declare that payment type — `commcalc.payment_categories` says its category is MDF —
  but the four-component vocabulary has no component for MDF, so `component_of_declared_category`
  returns None (an honest "cannot honour") and §58 falls through to the keyword ladder, SAYING SO in
  `basis`. `basis` exists for exactly that: so a caller can refuse a last-resort placement.

  TWO CALLERS READ `component` AND IGNORED `basis`, and so one report folded ONE declared category
  two ways for the SAME payments:

    · `gp_report` comp-report path:   gp_column('REIMBURSEMENT', 'MDF')      -> column `reimb`
    · `gp_report` payment-detail path: gp_column_of_declared_category('MDF') -> column `mdf`

  Live, read-only, house org: `raw_comp_report` carries 40 such rows / $260,500.00 (Mar–Sep 2026)
  and `raw_payment_detail` 44 rows / $287,500.00 (Mar–Oct; October's $27,000 has not reached the
  comp feed yet — the known month-end lag, not a defect). Store-for-store the two feeds agree
  exactly in all seven months both carry.

THE CLASS, NOT THE INSTANCE. Not "Ramp Up Subsidy lands in the wrong column":

    **A component reached on a FALLBACK BASIS is a PLACEMENT OF LAST RESORT, not evidence about the
      dollar — and an explicit declaration must never be overridden by a keyword guess.**

THE SHAPE OF THE FIX — the question in ONE home, the policy at each caller.
`carrier_dollar_class.component_is_evidence(basis)` answers ONLY "is this component reliable
evidence", from the basis alone, with no knowledge of who is asking. What a caller DOES about a
False differs by grain and is the caller's own:

  · a per-device / per-line report (finer grain than the category) DECLINES the dollar — it cannot
    name a device for a placement that was a guess (the device-reimbursement surface, §65);
  · the GP report and the P&L cannot decline anything, so their answer is a NAMED column or line —
    `gp_column_of_classification` places the dollar by the org's own DECLARED CATEGORY instead of
    the guessed component.

A CONSEQUENCE, STATED SO IT IS NOT LATER READ AS A BUG: a caller that DECLINES these dollars can
sit BELOW the P&L's `vip_reimb` line for the same store by exactly that amount, because the P&L does
not decline anything. That gap is the two policies behaving correctly over one shared question.

SECTIONS
  §A  the predicate — basis alone, caller-agnostic, and conservative about the unknown
  §B  THE REGRESSION — the live case as a fixture, with the pre-fix answer armed as a dead control
  §C  the two feeds agree, by construction, for every declared category
  §D  a declaration is never overridden by a keyword guess (the corollary, both directions)
  §E  nothing unreliable becomes commission; identity and arithmetic of the reclassification
  §F  gross profit and the P&L do not move
  §G  THE LOCK — the caller inventory, and the predicate may exist in exactly ONE module
  §H  purity, RULE TWO, the module graph and the index
"""
import ast
import inspect
import io
import os
import re
import sys
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app.modules.commcalc import carrier_dollar_class as cdc            # noqa: E402
from app.modules.commcalc import gp_report                              # noqa: E402
from app.modules.core import module_graph as MG                         # noqa: E402

PASS = FAIL = 0
_FAILED = []


def ok(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        _FAILED.append(label)
        print(f"  FAIL  {label}   {detail}")


def section(t):
    print(f"\n── {t} " + "─" * max(0, 92 - len(t)))


def src(rel):
    return open(os.path.join(HERE, rel), encoding="utf-8", errors="replace").read()


def code_only(text):
    """`text` with comments and string literals stripped. THE LOCK READS CODE, NOT PROSE: every
    file here QUOTES the rule it used to carry, and an explanation of a defect must not trip a scan
    for the defect."""
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    except (tokenize.TokenError, IndentationError):          # pragma: no cover - defensive
        return text
    return "\n".join(out)


HOME = src("app/modules/commcalc/carrier_dollar_class.py")
GP = src("app/modules/commcalc/gp_report.py")
ROUTER = src("app/modules/commcalc/router.py")
COA = src("app/modules/account/coa.py")
INDEX = open(os.path.join(HERE, "..", "docs", "SYSTEM_DATA_FLOW_INDEX.md"),
             encoding="utf-8", errors="replace").read()
HOME_CODE, GP_CODE, ROUTER_CODE = code_only(HOME), code_only(GP), code_only(ROUTER)


def compact(text):
    """`code_only`, with the token separators removed, so a CALL spelled across tokens
    (`_cdc` `.` `gp_column` `(`) can be searched as one string. `code_only` joins tokens with a
    newline, which silently makes every such search match nothing — a lock that cannot fail is
    worse than no lock, and this one was caught by arming its own negative control."""
    return code_only(text).replace("\n", "")


GP_TOK, ROUTER_TOK = compact(GP), compact(ROUTER)

# ── FIXTURES: the live SHAPE, not the live volume ────────────────────────────────────────────────
# `MDF`, `Ramp Up Subsidy` and `subsidy` are DATA — rows the org wrote. They appear here because
# this harness reproduces a measurement, and nowhere in any branch (RULE TWO).
DECL = {
    # THE LIVE CASE: declared to a category this org's component map has NO row for, while the
    # keyword ladder below matches its text and says REIMBURSEMENT. Exactly the $260,500.00 shape.
    "ramp up subsidy": "MDF",
    "4.0 $1k ramp up bonus": "MDF",
    # a declared category the component map CAN honour, whose text the ladder reads differently —
    # the corollary's fixture (§D)
    "2026 q3 promo pic offer": "Re-imbursement",
    "quarterly bounty true-up": "Commission",
    "activation clawback": "Chargeback",
}
RULES = [                      # the platform's generic keyword ladder — the FALLBACK, never a rule
    {"raw_category": "subsidy", "match_type": "contains", "component": "REIMBURSEMENT",
     "subtype": "subsidy", "priority": 40, "is_active": True},
    {"raw_category": "bounty", "match_type": "contains", "component": "SPIFF",
     "priority": 45, "is_active": True},
]
CFG = cdc.default_config()
STORE_MAP = [{"store_address": "103 Fulton Street", "store_code": "S1", "market": "M1",
              "salesforce_id": "SF1"}]


def comp(ct, amt, addr="103 Fulton Street"):
    return {"business_address": addr, "compensation_type": ct, "payment_amount": amt}


def pay(pt, amt, addr="103 Fulton Street"):
    return {"business_address": addr, "payment_type": pt, "amount": amt}


def run(**kw):
    kw.setdefault("carrier_declarations", DECL)
    kw.setdefault("carrier_rules", RULES)
    kw.setdefault("carrier_class_config", CFG)
    kw.setdefault("pay_category_map", DECL)
    sales = kw.pop("sales", [])
    return gp_report.calc_gp_report(sales, kw.pop("pay_detail", []), [], [], [], [],
                                    STORE_MAP, "August 2026", **kw)


def tot(r, k):
    return round(float(r["totals"].get(k) or 0), 2)


LIVE = cdc.classify(DECL, RULES, "Ramp Up Subsidy", CFG)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. THE PREDICATE — the basis alone decides, and it knows nothing about the caller")

ok("A1 the fixture reproduces the live classification exactly (component, basis, declared, "
   "category, subtype)",
   (LIVE["component"], LIVE["basis"], LIVE["declared"], LIVE["declared_category"], LIVE["subtype"])
   == ("REIMBURSEMENT", cdc.BASIS_DECLARED_UNMAPPED, False, "MDF", "subsidy"), LIVE)
ok("A2 a declaration IS evidence", cdc.component_is_evidence(cdc.BASIS_DECLARED) is True)
ok("A3 a LABELLED carry-forward of the org's own word is evidence",
   cdc.component_is_evidence(cdc.BASIS_INFERRED_TWIN) is True)
ok("A4 the keyword ladder ruling on a type the org declared NOTHING about is evidence",
   cdc.component_is_evidence(cdc.BASIS_KEYWORD) is True)
ok("A5 a keyword guess standing where a DECLARATION already exists is NOT evidence",
   cdc.component_is_evidence(cdc.BASIS_DECLARED_UNMAPPED) is False)
ok("A6 nothing resolved at all is NOT evidence",
   cdc.component_is_evidence(cdc.BASIS_UNRESOLVED) is False)
ok("A7 an unknown, blank or missing basis is NOT evidence — a caller that cannot say how a "
   "component was reached is who this protects",
   not any(cdc.component_is_evidence(b) for b in (None, "", "   ", "something_new")))
ok("A8 every BASIS_* value the home defines is ruled on — a new basis cannot arrive unclassified",
   all(isinstance(cdc.component_is_evidence(v), bool)
       for k, v in vars(cdc).items() if k.startswith("BASIS_") and isinstance(v, str)))
ok("A9 the non-evidential bases are a NAMED tuple in the home, not a literal at a call site",
   cdc.NON_EVIDENTIAL_BASES == (cdc.BASIS_DECLARED_UNMAPPED, cdc.BASIS_UNRESOLVED))
sig = inspect.signature(cdc.component_is_evidence)
ok("A10 the predicate takes the BASIS and nothing else — no grain, no intent, no caller identity",
   list(sig.parameters) == ["basis"], str(sig))
ok("A11 and it is pure: no client, no config, no read",
   not re.search(r"(execute|load_|client)", inspect.getsource(cdc.component_is_evidence)))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. THE REGRESSION — the live case, with the pre-fix answer armed as a dead control")

# The PRE-FIX call, kept here deliberately: this is what both call sites used to do.
PRE_FIX = cdc.gp_column(LIVE["component"], LIVE.get("declared_category"), CFG)
POST_FIX = cdc.gp_column_of_classification(LIVE, CFG)
ok("B1 the pre-fix call still answers what it always did — the control is live, not assumed",
   PRE_FIX == "reimb", PRE_FIX)
ok("B2 THE PRE-FIX ANSWER IS DEAD: the whole classification does not place it there",
   POST_FIX != PRE_FIX, (PRE_FIX, POST_FIX))
ok("B3 the dollar lands in the column the org's OWN declared category names",
   POST_FIX == cdc.gp_column(None, "MDF", CFG) == "mdf", POST_FIX)
ok("B4 and that is the column the payment-detail feed already placed it in",
   POST_FIX == cdc.gp_column_of_declared_category("MDF", CFG), POST_FIX)
r = run(comp_rows=[comp("Ramp Up Subsidy", 39300.00)],
        pay_detail=[pay("Ramp Up Subsidy", 39300.00)])
ok("B5 END TO END in the real engine: ONE payment type, one column, both feeds",
   tot(r, "comp_mdf") == 39300.00 and tot(r, "mdf") == 39300.00, r["totals"])
ok("B6 and nothing of it is left on either reimbursement column",
   tot(r, "comp_reimb") == 0.0 and tot(r, "reimb") == 0.0, r["totals"])
ok("B7 the second declared type of the same category travels with it",
   tot(run(comp_rows=[comp("4.0 $1K Ramp Up Bonus", 100.00)]), "comp_mdf") == 100.00)
ok("B8 the coverage report still names it undeclared-by-basis, with the reason in words",
   any(u["payment_type"] == "Ramp Up Subsidy" and u["basis"] == cdc.BASIS_DECLARED_UNMAPPED
       for u in r["carrier_class_coverage"]["undeclared"])
   and cdc.BASIS_REASONS[cdc.BASIS_DECLARED_UNMAPPED],
   r["carrier_class_coverage"]["undeclared"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. THE TWO FEEDS AGREE, BY CONSTRUCTION, FOR EVERY DECLARED CATEGORY")

disagree = []
for _desc, _cat in DECL.items():
    _c = cdc.classify(DECL, RULES, _desc, CFG)
    if cdc.gp_column_of_classification(_c, CFG) != cdc.gp_column_of_declared_category(_cat, CFG):
        disagree.append((_desc, _cat))
ok("C1 every declared type reaches the same column from either feed's shape", not disagree,
   disagree)
# The same, through the REAL engine, one row per feed per type.
r = run(comp_rows=[comp(d, 10.00) for d in DECL], pay_detail=[pay(d, 10.00) for d in DECL])
pairs = [(tot(r, "comp_" + k if k != "chb" else "comp_chb"), tot(r, k if k != "chb" else
                                                                 "chargeback"))
         for k in ("comm", "reimb", "mdf", "chb", "unmapped")]
ok("C2 and in the engine: the comp columns and the pay columns are equal, column by column",
   all(a == b for a, b in pairs), pairs)
ok("C3 an UNDECLARED type is still the keyword ladder's to place on the comp side (the pay feed "
   "carries no category for it, which is why the two shapes differ there and must)",
   tot(run(comp_rows=[comp("Some Undeclared Bounty", 5.00)]), "comp_comm") == 5.00)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. AN EXPLICIT DECLARATION IS NEVER OVERRIDDEN BY A KEYWORD GUESS")

c = cdc.classify(DECL, RULES, "Quarterly Bounty True-Up", CFG)
ok("D1 the ladder would have said otherwise, and the declaration wins",
   c["keyword_component"] == "SPIFF" and c["component"] == "COMMISSION"
   and c["basis"] == cdc.BASIS_DECLARED, c)
ok("D2 the honourable declaration's component IS evidence, so its own column is used",
   cdc.gp_column_of_classification(c, CFG) == cdc.GP_COMMISSION_COLUMN)
ok("D3 and when the map cannot honour the declaration, the CATEGORY still beats the guess — the "
   "guess never places the dollar",
   cdc.gp_column_of_classification(LIVE, CFG)
   != cdc.gp_column(LIVE["keyword_component"], None, CFG))
# An org that DOES map its category gets its component's column — config, never a branch.
CFG2 = dict(CFG, category_components=dict(CFG["category_components"], mdf="REIMBURSEMENT"))
c2 = cdc.classify(DECL, RULES, "Ramp Up Subsidy", CFG2)
ok("D4 an org that maps the category to a component is honoured on the declaration itself",
   c2["basis"] == cdc.BASIS_DECLARED and cdc.gp_column_of_classification(c2, CFG2) == "reimb", c2)
CFG3 = dict(CFG, gp_category_columns=dict(CFG["gp_category_columns"], mdf="comm"))
ok("D5 and an org that routes the CATEGORY elsewhere is honoured too (config, never code)",
   cdc.gp_column_of_classification(LIVE, CFG3) == "comm")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. NOTHING UNRELIABLE BECOMES COMMISSION; THE RECLASSIFICATION IS ARITHMETIC-NEUTRAL")

u = cdc.classify({}, [], "Nothing Declared, Nothing Matched", CFG)
ok("E1 unresolved money lands in the unclassified column, never commission",
   u["basis"] == cdc.BASIS_UNRESOLVED
   and cdc.gp_column_of_classification(u, CFG) == cdc.GP_UNCLASSIFIED_COLUMN)
ok("E2 an unreliable component can NEVER reach the commission column through this entry point",
   not any(cdc.gp_column_of_classification(
       {"component": comp_, "basis": b, "declared_category": None}, CFG)
       == cdc.GP_COMMISSION_COLUMN
       for b in cdc.NON_EVIDENTIAL_BASES for comp_ in cdc.COMPONENTS), cdc.COMPONENTS)
ALL = [comp("Ramp Up Subsidy", 39300.00), comp("2026 Q3 Promo PIC Offer", 1000.00),
       comp("Quarterly Bounty True-Up", 500.00), comp("Activation Clawback", -40.00),
       comp("Nothing Declared", 7.00)]
r = run(comp_rows=ALL)
cols = ("comp_comm", "comp_reimb", "comp_mdf", "comp_chb", "comp_unmapped")
ok("E3 one dollar, exactly one column: the comp columns re-sum to the feed",
   round(sum(tot(r, c_) for c_ in cols), 2) == 40767.00, [tot(r, c_) for c_ in cols])
ok("E4 the money that moved is a RECLASSIFICATION — the feed total is identical to the pre-fix "
   "split of the same rows",
   round(sum(tot(r, c_) for c_ in cols), 2)
   == round(sum(float(x["payment_amount"]) for x in ALL), 2))
ok("E5 the commission leg split still re-sums to the commission column it explains",
   {s["key"]: s for s in r["commission_legs"]["sources"]}["comp_comm"]["identity_ok"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. GROSS PROFIT AND THE P&L DO NOT MOVE")

SALES = [{"store": "103 Fulton Street", "department": sorted(gp_report.DEVICE_DEPTS)[0],
          "gp": 0.0, "ext_price": 1000.00, "product_desc": "d", "salesperson": "rep",
          "category": "", "sku": "1"}]
base = run(sales=SALES, comp_rows=[])
moved = run(sales=SALES, comp_rows=[comp("Ramp Up Subsidy", 260500.00)])
for k in ("total_rev", "net_profit", "net_phone_cost", "phone_sales"):
    ok(f"F1 {k} is identical with and without $260,500.00 of reclassified comp money",
       tot(base, k) == tot(moved, k), (k, tot(base, k), tot(moved, k)))
ok("F2 the comp columns are display-only: net_phone_cost reads the PAY-side reimbursement",
   "net_phone_cost = phone_sales + reimb" in GP)
ok("F3 and no comp_* term appears in the revenue or profit arithmetic",
   not re.search(r"total_rev\s*=[^\n]*comp_", GP)
   and not re.search(r"net_profit\s*=[^\n]*comp_", GP))
# THE P&L IS NOT TOUCHED. `component_line` routing is a CONFIG decision surfaced to the owner and
# unanswered (map the unhonourable category to a component, or give it its own line); moving it
# would move booked money, so this change leaves it exactly where it was and says so.
ok("F4 the P&L's own routing function is untouched by this change — it still rules on the "
   "component, which is the owner's pending config decision, not this fix's to make",
   cdc.component_line(LIVE["component"], {"REIMBURSEMENT": "vip_reimb"}, "carrier_comm")
   == "vip_reimb")
ok("F5 and the P&L caller still asks `component_line`, with no basis policy injected behind the "
   "owner's back", "_cdc.component_line(_c[\"component\"]" in COA)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. THE LOCK — the caller inventory, and ONE copy of the predicate")

ok("G1 the GP engine's comp path hands the WHOLE classification over",
   "_cdc.gp_column_of_classification(_c, _cc_cfg)" in GP)
ok("G2 the GP engine's pay path still rules on the declared category through the home",
   "_cdc.gp_column_of_declared_category(cat, _cc_cfg)" in GP)
ok("G3 neither GP path calls the component-only entry point any more",
   "_cdc.gp_column(" not in GP_TOK, "gp_column( survives in gp_report")
ok("G4 the commission-leg trend — the surface that EXPLAINS the GP column — dereferences it too",
   "_cdc_leg.gp_column_of_classification(c, cfg)" in ROUTER
   and "_cdc_leg.gp_column(" not in ROUTER_TOK)

# THE CALLER INVENTORY. Any module that calls `classify` and then PLACES the dollar from the
# component it returns is at the grain this rule governs. Each one must either dereference the
# predicate (directly, or through an entry point in the home that does) or be named here with the
# reason it does not. A caller that is NEITHER fails the build — which is how the GP comp path
# stayed broken for a PR after the field that would have caught it was introduced.
#
# EXCUSED, each for a stated reason, each a MONEY path whose change is its own surfaced decision:
EXCUSED = {
    # The P&L books every dollar somewhere and cannot decline one. Whether a dollar declared to a
    # category the component map cannot honour belongs on the reimbursement line or a line of its
    # own is a CONFIG decision surfaced to the owner and unanswered; routing it here would move
    # $260,500.00 of booked money unasked.
    "app/modules/account/coa.py": "P&L booking — component_line routing is the owner's pending config decision",
    # The same question, same answer, for the carrier drill-down that renders those P&L lines.
    "app/modules/commcalc/router.py": "P&L line routing (component_line) + the GP/trend paths, which DO dereference it",
}
DEREFERENCERS = {
    "app/modules/commcalc/gp_report.py",
    "app/modules/commcalc/router.py",
}
APP = os.path.join(HERE, "app")
unknown, copies = [], []
for root, _d, files in os.walk(APP):
    for f in sorted(files):
        if not f.endswith(".py"):
            continue
        rel = os.path.join("app", os.path.relpath(os.path.join(root, f), APP)).replace(os.sep, "/")
        if rel == "app/modules/commcalc/carrier_dollar_class.py":
            continue
        body = src(rel)
        bcode = code_only(body)
        # a second copy of the PREDICATE: the two basis names spelled together outside the home
        if "BASIS_DECLARED_UNMAPPED" in bcode and "BASIS_UNRESOLVED" in bcode:
            copies.append(rel)
        places = re.search(r"\.classify\(", bcode) and re.search(
            r"(\[['\"]component['\"]\]|get\(['\"]component['\"]\))", bcode)
        if places and rel not in DEREFERENCERS and rel not in EXCUSED:
            unknown.append(rel)
ok("G5 every module that places a dollar from a classify component either dereferences the "
   "predicate or is EXCUSED with a reason", not unknown, unknown)
ok("G6 THE PREDICATE EXISTS IN EXACTLY ONE MODULE — a second copy of its basis tuple anywhere "
   "under app/ is the divergence this check exists to stop (dereference "
   "`carrier_dollar_class.component_is_evidence` instead)", not copies, copies)
ok("G7 the home states the rule ONCE as an allowlist, and the complement is DERIVED from it "
   "rather than re-spelled",
   "EVIDENTIAL_BASES = (" in HOME and "NON_EVIDENTIAL_BASES = tuple(" in HOME
   and "in EVIDENTIAL_BASES" in inspect.getsource(cdc.component_is_evidence))
ok("G8 the GP policy is stated once too — the entry point asks the predicate rather than testing "
   "a basis itself",
   "component_is_evidence(" in inspect.getsource(cdc.gp_column_of_classification)
   and not re.search(r"BASIS_", inspect.getsource(cdc.gp_column_of_classification)))
ok("G9 the excused callers are really excused: each is named with a reason, and the inventory is "
   "not empty", all(v and len(v) > 20 for v in EXCUSED.values()) and len(EXCUSED) == 2)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. PURITY, RULE TWO, THE MODULE GRAPH AND THE INDEX")

tree = ast.parse(HOME)
newfns = [n for n in tree.body if isinstance(n, ast.FunctionDef)
          and n.name in ("component_is_evidence", "gp_column_of_classification")]
ok("H1 both new functions exist in the home and neither performs a read",
   len(newfns) == 2 and not [a for n in newfns for a in ast.walk(n)
                             if isinstance(a, ast.Attribute)
                             and a.attr in ("execute", "load_map", "load_config", "load_rules",
                                            "load_declarations")])
ok("H2 RULE TWO — no carrier, tenant, product, quarter or category name in ANY code in the home",
   not re.search(r"(?i)\b(boost|luxelink|vidapay|epay|cellfonz|iphone|samsung|q[1-4]|mdf|ramp)\b",
                 HOME_CODE))
# `mdf` and `chb` ARE platform column keys and appear in the engine as such; what may never appear
# is a payment type, a category label or a tenant's own vocabulary driving a branch.
ok("H3 RULE TWO — no payment type, category label or tenant word at either call site",
   not re.search(r"(?i)(ramp up|subsidy|'MDF'|\"MDF\")", GP_CODE)
   and not re.search(r"(?i)(ramp up|subsidy)", ROUTER_CODE))
ok("H4 this harness is DB-free and network-free by construction",
   not re.search(r"(supabase|psycopg|requests|urllib|get_supabase)",
                 code_only(src("harness_component_basis_evidence.py"))))
FACT = "carrier_dollar_component"
f = MG.FACTS.get(FACT) or {}
ok("H5 this lock is registered as one of the fact's locks",
   "harness_component_basis_evidence.py" in (f.get("locks") or ()), f.get("locks"))
ok("H6 the fact's question names the basis rule, so the next reader finds it from the graph",
   "basis" in (f.get("question") or "").lower(), f.get("question"))
# The SECTION's own body, not the whole index: a cross-reference elsewhere must not be able to
# satisfy a check about what this section says.
_m = re.search(r"^## 66\. ", INDEX, re.M)
SEC = INDEX[_m.start():] if _m else ""
_n = re.search(r"^## 6[7-9]\. ", SEC, re.M)
SEC = SEC[:_n.start()] if _n else SEC
ok("H7 the index documents the class, in its own section", bool(SEC)
   and "last resort" in SEC.lower() and "component_is_evidence" in SEC)
ok("H8 the section carries the measured figure, so it is not only in a PR", "260,500.00" in SEC)
ok("H9 the section states that gross profit and the P&L do not move",
   re.search(r"gross profit[^\n]{0,200}(0\.00|does not move|unchanged)", SEC, re.I) is not None)
ok("H10 the section records the two-policy consequence, so a lower paid side is not read as a bug",
   re.search(r"(?i)(below the P&L|declines).{0,400}(vip_reimb|not a discrepancy)", SEC) is not None)
ok("H11 the section names both the one home and the EXCUSED P&L callers, with the reason",
   "component_line" in SEC and "EXCUSED" in SEC and "unanswered" in SEC.lower())
ok("H12 the per-month before/after table is in the index, not only in the PR body",
   SEC.count("39,300.00") >= 5 and "3,013,982.50" in SEC)

# ─────────────────────────────────────────────────────────────────────────────────────────────────
print(f"\n{'=' * 78}")
print(f"  {PASS} passed, {FAIL} failed")
if _FAILED:
    print("  FAILED:")
    for x in _FAILED:
        print(f"    - {x}")
print(f"{'=' * 78}")
sys.exit(1 if FAIL else 0)
