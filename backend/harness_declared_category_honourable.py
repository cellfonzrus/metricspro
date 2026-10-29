#!/usr/bin/env python3
"""PROOF + LOCK — THE ORG'S DECLARATION MUST BE HONOURABLE: a category the platform can place must
be expressible as a COMPONENT, so the declaration places the dollar and `basis` reads `declared`
(index §69).

DB-free, network-free, stdlib only. Run: `python3 backend/harness_declared_category_honourable.py`

OWNER RULING 2026-10-10, on a decision card and again in his own words: *"mdf is not equipment
ereimbursement- prepare and fix"* — "Its own MDF line".

THE DEFECT, measured live read-only on house org `00000000-0000-0000-0000-000000000001` with the
REAL functions and the LIVE config:

    classify('Ramp Up Subsidy')            -> component='REIMBURSEMENT',
                                              basis='declared_category_unmapped',
                                              declared=False, declared_category='MDF',
                                              subtype='subsidy'
    component_of_declared_category('MDF')  -> None
    component_line('REIMBURSEMENT', {'REIMBURSEMENT': 'vip_reimb'}, 'carrier_comm') -> 'vip_reimb'

The org DOES declare that payment type into category `MDF` (`commcalc.payment_categories`, §57) —
and the pay engine's own `pay_data_quality.PLACEABLE_CATEGORIES` has listed `MDF` and `Chargeback`
as categories the platform can place all along. The COMPONENT vocabulary could express only two of
those four, so the declaration could not be honoured, §58 fell through to the keyword ladder, and a
platform guess stood where the org's own word already existed. Live money: **$260,500.00**, 40 rows
of `commcalc.raw_comp_report`, March-September 2026 ($39,300/mo over 6 stores Mar-Jul; $32,000 over
5 in Aug and Sep; October 0.00 in that feed) — booked on the device-financing reimbursement line
instead of the MDF income line the org's own declaration names.

THE CLASS, NOT THE INSTANCE. Not "MDF books on the wrong line":

    **A DECLARATION THE PLATFORM'S VOCABULARY CANNOT EXPRESS IS A HOLE IN THE VOCABULARY, NOT A
      FACT ABOUT THE DOLLAR — and while the hole is open a keyword guess stands where the org's own
      word already exists.**

THE MECHANISM, AND THE ONE THAT WAS REJECTED. The fix is that `carrier_map.COMPONENTS` can express
every kind of carrier money the platform's own routing maps can name a destination for, so the
EXISTING path — declared category -> component (`carrier_class_category_components`) -> P&L line
(`carrier_component_lines`) — carries it and `basis` reads `declared`. The rejected shape was a
SECOND routing map keyed by category (category -> P&L line), beside `component_line`: it leaves
`basis` on a fallback, and it is two paths to one answer — which is the duplicate defect the index
rules forbid, and the pair would drift the first time one of them was edited.

SECTIONS
  §A  THE REGRESSION — the live case as a fixture, the pre-fix answer armed as a dead control
  §B  the vocabulary IS the mechanism: one path, and every placeable category expressible
  §C  a declaration can never again be overridden by a keyword — the cross-product
  §D  declared money is never LABELLED as undeclared
  §E  the device-financing side is a routing fact, not "whatever else was routed" (the §65 sibling)
  §F  the money: the measured restatement, and the other tenants byte-identical
  §G  THE LOCK — the callers, and one copy of each fact
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

from app.modules.commcalc import carrier_dollar_class as cdc              # noqa: E402
from app.modules.commcalc import carrier_map as cmap                      # noqa: E402
from app.modules.commcalc import pay_data_quality as pdq                  # noqa: E402
from app.modules.core import module_graph as MG                           # noqa: E402

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
    file here QUOTES the defect it used to carry, and an explanation must not trip a scan for it."""
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    except (tokenize.TokenError, IndentationError):          # pragma: no cover - defensive
        return text
    return "\n".join(out)


def compact(text):
    """`code_only` with the token separators removed, so a CALL spelled across tokens
    (`_cdc` `.` `component_line` `(`) can be searched as one string — the §66 lesson: a lock that
    cannot fail is worse than no lock."""
    return code_only(text).replace("\n", "")


HOME = src("app/modules/commcalc/carrier_dollar_class.py")
VOCAB = src("app/modules/commcalc/carrier_map.py")
COA = src("app/modules/account/coa.py")
ROUTER = src("app/modules/commcalc/router.py")
HOME_CODE, COA_CODE, ROUTER_CODE = code_only(HOME), code_only(COA), code_only(ROUTER)
COA_TOK, ROUTER_TOK = compact(COA), compact(ROUTER)
INDEX = open(os.path.join(HERE, "..", "docs", "SYSTEM_DATA_FLOW_INDEX.md"),
             encoding="utf-8", errors="replace").read()

# ── FIXTURES: the live SHAPE, not the live volume ────────────────────────────────────────────────
# `MDF`, `Chargeback`, `Ramp Up Subsidy` and `subsidy` are DATA — rows an org wrote and rows the
# platform seeded. They appear here because this harness reproduces a measurement, and in no branch
# of any module (RULE TWO).
DECL = {
    # THE LIVE CASE: declared into a category the vocabulary could not express, while the keyword
    # ladder matches its text and says REIMBURSEMENT. Exactly the $260,500.00 shape.
    "ramp up subsidy": "MDF",
    "4.0 $1k ramp up bonus": "MDF",
    # a declared category the map has always honoured, whose text the ladder reads differently
    "2026 q3 device offer": "Re-imbursement",
    "quarterly bounty true-up": "Commission",
    "activation clawback": "Chargeback",
}
RULES = [                      # the platform's generic keyword ladder — the FALLBACK, never a rule
    {"raw_category": "subsidy", "match_type": "contains", "component": "REIMBURSEMENT",
     "subtype": "subsidy", "priority": 40, "is_active": True},
    {"raw_category": "bounty", "match_type": "contains", "component": "SPIFF",
     "priority": 45, "is_active": True},
    {"raw_category": "clawback", "match_type": "contains", "component": "COMMISSION",
     "priority": 45, "is_active": True},
]
# THE ORG'S OWN CORRESPONDENCE between the categories the platform can place and the kinds of money
# the vocabulary knows. A config row per org; here it is the fixture of what the house org's row
# says, and §B3 fails the build if a placeable category is missing from it.
PLACEABLE_COMPONENT = {
    "commission": "COMMISSION",
    "re-imbursement": "REIMBURSEMENT",
    "mdf": "MDF",
    "chargeback": "CHARGEBACK",
}
DEFAULT_LINE = "carrier_comm"
# The house org's LIVE routing, and the routing the owner's ruling adds to it.
LIVE_LINES = {"REIMBURSEMENT": "vip_reimb"}
RULED_LINES = {"REIMBURSEMENT": "vip_reimb", "MDF": "mdf_income"}

# The live per-month money (read-only, `commcalc.raw_comp_report`, house org) — the fixture the
# arithmetic is proved over.
LIVE_MDF_BY_MONTH = {"March 2026": 39300.00, "April 2026": 39300.00, "May 2026": 39300.00,
                     "June 2026": 39300.00, "July 2026": 39300.00, "August 2026": 32000.00,
                     "September 2026": 32000.00, "October 2026": 0.00}
LIVE_VIP_REIMB_BEFORE = {"March 2026": 618846.88, "April 2026": 446491.33, "May 2026": 396008.25,
                         "June 2026": 467685.90, "July 2026": 475630.90, "August 2026": 418922.21,
                         "September 2026": 373211.61, "October 2026": 77685.42}


class _Q:
    """The smallest thing `column_tolerant.read_row` can read: a single config row."""

    def __init__(self, row):
        self._row = row

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return type("R", (), {"data": [dict(self._row)]})()


class _Client:
    def __init__(self, row):
        self._row = row

    def schema(self, _s):
        return self

    def table(self, _t):
        return _Q(self._row)


# THE TWO POSTURES, BUILT THROUGH THE REAL CONFIG PATH (`load_config` over a stub row) rather than
# by hand — because `load_config` is where the org's row is accepted or DROPPED, and a hand-built
# dict would sail straight past the very gate this change moves. PRE is the live posture: no
# category map of its own (the shipped default) and the one routing row mig 1062 seeded. POST is the
# same org's row after the surfaced SQL: the default map PLUS its declared category, and the routing
# PLUS that component's line, in one write.
PRE = cdc.load_config(_Client({"org_id": "o", "carrier_component_lines": dict(LIVE_LINES)}), "o")
POST = cdc.load_config(_Client({"org_id": "o",
                                "carrier_class_category_components": dict(PLACEABLE_COMPONENT),
                                "carrier_component_lines": dict(RULED_LINES)}), "o")


def line_of(cfg, raw_type):
    """What the P&L books this payment type on, through the real path and nothing else."""
    c = cdc.classify(DECL, RULES, raw_type, cfg)
    return cdc.component_line(c["component"], cfg.get("component_lines"), DEFAULT_LINE)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. THE REGRESSION — the live case, and the pre-fix answer armed as a dead control")

PRE_C = cdc.classify(DECL, RULES, "Ramp Up Subsidy", PRE)
POST_C = cdc.classify(DECL, RULES, "Ramp Up Subsidy", POST)

ok("A1 the fixture reproduces the measured pre-fix classification exactly — the keyword's component "
   "standing on a declaration, SAYING SO in `basis`",
   (PRE_C["component"], PRE_C["basis"], PRE_C["declared"], PRE_C["declared_category"],
    PRE_C["subtype"]) == ("REIMBURSEMENT", cdc.BASIS_DECLARED_UNMAPPED, False, "MDF", "subsidy"),
   PRE_C)
ok("A2 pre-fix the vocabulary could not express the org's category at all",
   cdc.component_of_declared_category("MDF", PRE) is None)
ok("A3 THE FIX — the org's own declaration decides, and `basis` says so",
   (POST_C["component"], POST_C["basis"], POST_C["declared"]) == ("MDF", cdc.BASIS_DECLARED, True),
   POST_C)
ok("A4 …and it is a DECLARATION, not an inference or a guess: no twin, no keyword subtype",
   POST_C["inferred"] is False and POST_C["twin_of"] is None and POST_C["subtype"] is None)
ok("A5 the keyword ladder still SAYS what it would have said, so the before/after report can name "
   "the reclassification", POST_C["keyword_component"] == "REIMBURSEMENT")
ok("A6 THE DOLLAR MOVES OFF THE DEVICE-FINANCING LINE AND ONTO THE LINE THE ORG NAMED",
   (line_of(PRE, "Ramp Up Subsidy"), line_of(POST, "Ramp Up Subsidy"))
   == ("vip_reimb", "mdf_income"))
ok("A7 DEAD CONTROL: the pre-fix placement is reproducible and is no longer what the home answers "
   "— the keyword's component would still route to the reimbursement line if it were consulted",
   cdc.component_line(PRE_C["component"], POST["component_lines"], DEFAULT_LINE) == "vip_reimb"
   and line_of(POST, "Ramp Up Subsidy") != "vip_reimb")
ok("A8 the org's SECOND declared type of the same category follows the same ruling, with no "
   "per-type anything", line_of(POST, "4.0 $1K Ramp Up Bonus") == "mdf_income")
ok("A9 and a type the org never declared is untouched — the keyword ladder still decides it, "
   "labelled as the guess it is",
   cdc.classify(DECL, RULES, "Mystery Bounty Thing", POST)["basis"] == cdc.BASIS_KEYWORD)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. THE VOCABULARY IS THE MECHANISM — one path, and every placeable category expressible")

ok("B1 the component vocabulary has ONE spelling, and the classification home dereferences it",
   cdc.COMPONENTS is cmap.COMPONENTS
   and re.search(r"^COMPONENTS\s*=\s*\(", HOME, re.M) is None
   and re.search(r"^COMPONENTS\s*=\s*\(", VOCAB, re.M) is not None)
ok("B2 THE PLACEABLE CATEGORIES ARE THE PLATFORM'S OWN LIST, and every one of them is covered by "
   "the correspondence this lock holds the vocabulary to — a NEW placeable category fails the "
   "build until somebody says which kind of money it is",
   {cdc._norm(c) for c in pdq.PLACEABLE_CATEGORIES} == set(PLACEABLE_COMPONENT),
   sorted(pdq.PLACEABLE_CATEGORIES))
ok("B3 …and every one of them IS EXPRESSIBLE: configured, each resolves to a real component",
   all(c in cdc.COMPONENTS for c in PLACEABLE_COMPONENT.values()),
   [c for c in PLACEABLE_COMPONENT.values() if c not in cdc.COMPONENTS])
ok("B4 each placeable category, once the org maps it, is HONOURED — basis `declared` for every "
   "one, never a fallback",
   all(cdc.classify({"t": cat}, RULES, "t", POST)["basis"] == cdc.BASIS_DECLARED
       for cat in ("Commission", "Re-imbursement", "MDF", "Chargeback")))
ok("B5 EVERY component has a Gross-Profit column in the house default — a component added without "
   "one would turn real money into `unmapped` by omission",
   all(c in (cdc.default_config()["gp_component_columns"] or {}) for c in cdc.COMPONENTS),
   [c for c in cdc.COMPONENTS if c not in cdc.default_config()["gp_component_columns"]])
ok("B6 EVERY Gross-Profit money column except the unclassified one is REACHABLE from some "
   "component — an unreachable column is a kind of money no org can declare into, which is what "
   "made a keyword guess necessary in the first place",
   set(cdc.default_config()["gp_component_columns"].values())
   == {c for c in cdc.GP_COLUMNS if c != cdc.GP_UNCLASSIFIED_COLUMN},
   sorted(set(cdc.GP_COLUMNS) - set(cdc.default_config()["gp_component_columns"].values())))
ok("B7 THE ORG'S ROW IS WHAT ACTIVATES IT: `load_config` keeps a category mapped to each of the "
   "kinds of money the vocabulary knows (pre-fix it DROPPED the two it did not)",
   POST["category_components"] == dict(PLACEABLE_COMPONENT), POST["category_components"])
ok("B8 …and the line routing comes back with it, so one config write moves the money and the "
   "classification together",
   POST["component_lines"] == dict(RULED_LINES), POST["component_lines"])
ok("B9 a value outside the vocabulary is still refused, so a typo cannot invent a kind of money",
   cdc.load_config(_Client({"org_id": "o",
                            "carrier_class_category_components": {"mdf": "MDFF"}}),
                   "o")["category_components"] == {})
ok("B10 THE REJECTED SHAPE IS NOT IN THE TREE: there is no second routing map keyed by CATEGORY "
   "beside `component_lines` — the P&L line of a carrier dollar has ONE path",
   not re.search(r"carrier_category_lines|category_lines|carrier_class_category_lines", HOME)
   and not re.search(r"category_lines", ROUTER_CODE + COA_CODE))
ok("B11 the default map is a DEFAULT and the org can switch declaration-mapping off wholesale",
   cdc.load_config(_Client({"org_id": "o", "carrier_class_category_components": {}}),
                   "o")["category_components"] == {}
   and cdc.classify(DECL, RULES, "Ramp Up Subsidy",
                    dict(PRE, category_components={}))["basis"] == cdc.BASIS_DECLARED_UNMAPPED)
ok("B12 the shipped default does NOT seed a kind of money an org already declares — a default row "
   "would restate a booked statement on merge, with the destination line defaulting to the "
   "caller's generic line instead of the one the owner named",
   cdc.component_of_declared_category("MDF") is None
   and cdc.component_line("MDF", {}, DEFAULT_LINE) == DEFAULT_LINE)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. A DECLARATION CAN NEVER AGAIN BE OVERRIDDEN BY A KEYWORD — the cross-product")

bad = []
for cat, comp in PLACEABLE_COMPONENT.items():
    for kw in cdc.COMPONENTS:
        if kw == comp:
            continue
        rules = [{"raw_category": "t", "match_type": "exact", "component": kw,
                  "subtype": "x", "priority": 1, "is_active": True}]
        c = cdc.classify({"t": cat}, rules, "t", POST)
        if not (c["component"] == comp and c["basis"] == cdc.BASIS_DECLARED and c["declared"]):
            bad.append((cat, kw, c["component"], c["basis"]))
ok("C1 for EVERY placeable category and EVERY contrary keyword verdict, the org's declaration "
   "wins and the basis is `declared` — the whole cross-product, not one example", not bad, bad[:4])
ok("C2 …and the line follows the declaration, never the keyword",
   all(cdc.component_line(
       cdc.classify({"t": "MDF"}, [{"raw_category": "t", "match_type": "exact", "component": kw,
                                    "priority": 1, "is_active": True}], "t", POST)["component"],
       POST["component_lines"], DEFAULT_LINE) == "mdf_income" for kw in cdc.COMPONENTS))
ok("C3 a component reached on the org's own declaration IS evidence about the dollar — §66's "
   "predicate, dereferenced, not re-spelled here",
   cdc.component_is_evidence(POST_C["basis"]) is True
   and cdc.component_is_evidence(PRE_C["basis"]) is False)
ok("C4 the GP column of the classification and of the declared category agree for every placeable "
   "category — the two feeds cannot fold one category two ways",
   all(cdc.gp_column_of_classification(cdc.classify({"t": cat}, RULES, "t", POST), POST)
       == cdc.gp_column_of_declared_category(cat, POST) for cat in PLACEABLE_COMPONENT))
ok("C5 and no placeable category lands in the unclassified column once it is mapped",
   not [cat for cat in PLACEABLE_COMPONENT
        if cdc.gp_column_of_declared_category(cat, POST) == cdc.GP_UNCLASSIFIED_COLUMN])
_t = cdc.tally([{"compensation_type": "Ramp Up Subsidy", "payment_amount": 39300.00},
                {"compensation_type": "Mystery Bounty Thing", "payment_amount": 100.00}],
               DECL, RULES, POST)
ok("C6 the coverage report counts the declared dollars as DECLARED and keeps the undeclared ones "
   "named — $39,300.00 of one month stops being reported as money resting on a guess",
   _t["declared_total"] == 39300.00 and _t["undeclared_total"] == 100.00 and _t["balances"]
   and [u["payment_type"] for u in _t["undeclared"]] == ["Mystery Bounty Thing"], _t)
ok("C7 and it is reported as a RECLASSIFICATION against the keyword ladder, so the move is visible "
   "rather than silent",
   any(r["from"] == "REIMBURSEMENT" and r["to"] == "MDF" and r["amount"] == 39300.00
       for r in _t["reclassified_vs_keyword"]), _t["reclassified_vs_keyword"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. DECLARED MONEY IS NEVER LABELLED AS UNDECLARED")

ok("D1 every kind of money the vocabulary knows has a human label",
   all(cdc.component_label(c) and cdc.component_label(c) != cdc.UNDECLARED_LABEL
       for c in cdc.COMPONENTS),
   [c for c in cdc.COMPONENTS if cdc.component_label(c) == cdc.UNDECLARED_LABEL])
ok("D2 no component's label is the word for money nobody declared",
   cdc.UNDECLARED_LABEL not in set(cdc.COMPONENT_LABELS.values()))
ok("D3 the P&L drill-down DEREFERENCES the label and keeps no private map of its own — the four "
   "entry dict it carried would have shown the owner's DECLARED MDF money as 'Undeclared'",
   "_cdc.component_label(" in COA and "_CC_LABEL" not in COA)
ok("D4 nothing at all still reads as undeclared, which is the one honest use of the word",
   cdc.component_label(None) == cdc.UNDECLARED_LABEL
   and cdc.component_label("") == cdc.UNDECLARED_LABEL)
ok("D5 and a component with no wording yet shows ITSELF, not the undeclared word",
   cdc.component_label("SOMETHINGNEW") == "SOMETHINGNEW")
ok("D6 the existing drill wording is carried over unchanged — moving a fact into its home must not "
   "restate a label the owner reads on the statement",
   cdc.component_label("COMMISSION") == "Commission (promo)"
   and cdc.component_label("SPIFF") == "SPIFF / bounty"
   and cdc.component_label("REIMBURSEMENT") == "Reimbursement"
   and cdc.component_label("RESIDUAL") == "Residual")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. THE DEVICE-FINANCING SIDE IS A ROUTING FACT, NOT 'WHATEVER ELSE WAS ROUTED' (§65)")


def pre_fix_device_components(line_map):
    """THE PRE-FIX DERIVATION, kept live as a control: 'every component routed OFF the generic
    carrier-commission line'. An inference that held only while exactly one component was routed
    anywhere."""
    return {str(k).strip().upper() for k, v in (line_map or {}).items()
            if str(v or "").strip() and str(v).strip() != DEFAULT_LINE}


ok("E1 with the live routing both readings agree — so this change moves nothing today",
   cdc.device_reimb_components(LIVE_LINES, DEFAULT_LINE)
   == pre_fix_device_components(LIVE_LINES) == {"REIMBURSEMENT"})
ok("E2 ARMED CONTROL: under the owner's ruling the pre-fix reading calls the MDF component device "
   "money — $91,000.00 a month of per-store lump sums, no device named and nothing to name one "
   "for, back inside a per-DEVICE report §65.5 had just cleared of exactly that money",
   pre_fix_device_components(RULED_LINES) == {"REIMBURSEMENT", "MDF"})
ok("E3 THE FIX: the device side is the components that book to the line the device-financing "
   "reimbursement books to, so a component routed to a line of its OWN is not device money",
   cdc.device_reimb_components(RULED_LINES, DEFAULT_LINE) == {"REIMBURSEMENT"})
ok("E4 unrouted is EMPTY, never 'everything' — with no routing declared a caller must say it "
   "measured nothing rather than guess",
   cdc.device_reimb_components({}, DEFAULT_LINE) == set()
   and cdc.device_reimb_components({"REIMBURSEMENT": DEFAULT_LINE}, DEFAULT_LINE) == set())
ok("E5 two components an org routes to ONE line are both that money — one line is one kind of "
   "money on the books, and the org said so",
   cdc.device_reimb_components({"REIMBURSEMENT": "vip_reimb", "SPIFF": "vip_reimb"}, DEFAULT_LINE)
   == {"REIMBURSEMENT", "SPIFF"})
ok("E6 a key that is not a component cannot smuggle itself in",
   cdc.device_reimb_components({"REIMBURSEMENT": "vip_reimb", "NOTACOMPONENT": "vip_reimb"},
                               DEFAULT_LINE) == {"REIMBURSEMENT"})
ok("E7 the line itself is asked, never spelled: the P&L and the per-device report cannot disagree "
   "about which line that money is on",
   cdc.device_reimb_line(RULED_LINES, DEFAULT_LINE) == "vip_reimb"
   and cdc.device_reimb_line({}, DEFAULT_LINE) == DEFAULT_LINE)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. THE MONEY — the measured restatement, and the other tenants byte-identical")

moved = {}
for m, mdf in LIVE_MDF_BY_MONTH.items():
    before = LIVE_VIP_REIMB_BEFORE[m]
    after_reimb = round(before - mdf, 2)
    after_mdf = round(0.0 + mdf, 2)
    moved[m] = (before, after_reimb, after_mdf)
ok("F1 every month's after re-sums to its before to the cent — a reclassification between two "
   "lines and nothing else",
   all(round(a + b, 2) == round(before, 2) for before, a, b in moved.values()), moved)
ok("F2 the reimbursement line falls by exactly the MDF total and the MDF line rises by it",
   round(sum(b for b, _a, _m in moved.values()), 2) == 3274482.50
   and round(sum(a for _b, a, _m in moved.values()), 2) == 3013982.50
   and round(sum(m for _b, _a, m in moved.values()), 2) == 260500.00, moved)
ok("F3 the one month with no such row moves $0.00 — the restatement is the rows' own, not a "
   "blanket", moved["October 2026"][0] == moved["October 2026"][1])
ok("F4 AN ORG THAT DECLARES NOTHING IS BYTE-IDENTICAL whatever the vocabulary can express — the "
   "other two live tenants carry no `payment_categories` row at all",
   all(cdc.classify({}, RULES, t, POST) == cdc.classify({}, RULES, t, PRE)
       for t in ("Ramp Up Subsidy", "Quarterly Bounty True-Up", "Mystery Thing")))
ok("F5 and an org that declares but has NOT mapped its category keeps today's answer exactly — "
   "nothing changes for a tenant until its own row does",
   cdc.classify(DECL, RULES, "Ramp Up Subsidy", PRE) == PRE_C)
ok("F6 the Gross-Profit columns do not move for this money: §66 already placed it by the declared "
   "category, and the declaration now reaches the same column through its component",
   cdc.gp_column_of_classification(PRE_C, PRE)
   == cdc.gp_column_of_classification(POST_C, POST) == "mdf")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. THE LOCK — the callers, and ONE copy of each fact")

# THE QUESTION MOVED, THE PROPERTY DID NOT. "Which components are the device side" was asked inside
# `router._device_line_reimb_run` until 2026-10-28, when the whole device-money question was factored
# into §COMMCALC-44 (`commcalc/carrier_device_money.py`) because a second report needed the same
# figure. So G1 now reads the home that asks, and G1b proves the router really dereferences it —
# together they are the same lock one indirection away, not a weaker one.
CDM = src("app/modules/commcalc/carrier_device_money.py")
CDM_TOK = code_only(CDM)
ok("G1 the per-device report ASKS which components are the device side and derives nothing",
   "_cdc.device_reimb_components(" in CDM)
ok("G1b …and the router reaches that answer through the one home rather than asking again, so the "
   "P&L's reimbursement line and the watchdog's chargeback amount cannot drift apart",
   "_cdm.per_device(" in ROUTER and "_cdc.device_reimb_components(" not in ROUTER)
ok("G2 …and the pre-fix comprehension is GONE from both — the inference cannot come back by edit",
   all('!= "carrier_comm"' not in t and "!= 'carrier_comm'" not in t
       for t in (ROUTER_TOK, CDM_TOK)))
ok("G3 the P&L asks for the device-financing LINE instead of spelling the component",
   "_cdc.device_reimb_line(" in COA)
ok("G4 …and names that component nowhere in its own code",
   not re.search(r'"REIMBURSEMENT"|\'REIMBURSEMENT\'',
                 "\n".join(l for l in COA.splitlines() if not l.lstrip().startswith("#"))),
   [l for l in COA.splitlines() if "REIMBURSEMENT" in l and not l.lstrip().startswith("#")][:3])
ok("G5 the P&L still routes by COMPONENT through the one path — no category→line shortcut was "
   "added behind it", '_cdc.component_line(_c["component"]' in COA)

# THE ONE-COPY CHECKS. Each fact this section adds has exactly one home, and a second copy under
# app/ fails the build — the §19.18 failure mode (a registry written and the callers left on their
# own copies) has happened three times here.
APP = os.path.join(HERE, "app")
copies = {"vocabulary": [], "labels": [], "device_side": [], "financing_component": []}
for root, _d, files in os.walk(APP):
    for f in sorted(files):
        if not f.endswith(".py"):
            continue
        rel = os.path.join("app", os.path.relpath(os.path.join(root, f), APP)).replace(os.sep, "/")
        if rel in ("app/modules/commcalc/carrier_dollar_class.py",
                   "app/modules/commcalc/carrier_map.py"):
            continue
        body = src(rel)
        bcode = code_only(body)
        names = set(re.findall(r"['\"]([A-Z]{4,})['\"]", body))
        # a second spelling of the component vocabulary: three or more of its members in one tuple
        for m in re.finditer(r"=\s*\(([^)]{0,400})\)", body):
            mem = set(re.findall(r"['\"]([A-Z]{4,})['\"]", m.group(1)))
            if len(mem & set(cdc.COMPONENTS)) >= 3 and rel not in copies["vocabulary"]:
                copies["vocabulary"].append(rel)
        # A COMPONENT -> DISPLAY-TEXT map: keys that are components, values that are wording for a
        # human. A module whose buckets merely SHARE a name with a component (an MA-side airtime
        # bucket, say) is not a copy of anything and must not be accused of being one, so the scan
        # also requires the module to touch the classification at all.
        touches = "carrier_map" in bcode or "carrier_dollar_class" in bcode
        for d in [n for n in ast.walk(ast.parse(body)) if isinstance(n, ast.Dict)]:
            ks = [k.value for k in d.keys
                  if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            vs = [v.value for v in d.values
                  if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            if (touches and len(set(ks) & set(cdc.COMPONENTS)) >= 3 and len(vs) == len(d.values)
                    and any(" " in v or "/" in v for v in vs) and rel not in copies["labels"]):
                copies["labels"].append(rel)
        if re.search(r"component_lines", bcode) and re.search(r"carrier_comm", bcode) \
                and re.search(r"for\s+\w+\s*,\s*\w+\s+in", bcode) \
                and "device_reimb_components" not in bcode and rel != "app/modules/account/coa.py":
            copies["device_side"].append(rel)
ok("G6 THE COMPONENT VOCABULARY EXISTS IN EXACTLY ONE MODULE — a second tuple of its members "
   "anywhere under app/ is the divergence this check exists to stop (dereference "
   "`carrier_map.COMPONENTS`)", not copies["vocabulary"], copies["vocabulary"])
ok("G7 the component LABELS exist in exactly one module",
   not copies["labels"], copies["labels"])
ok("G8 and nothing re-derives which components are the device-financing side",
   not copies["device_side"], copies["device_side"])
ok("G9 the home states each new fact ONCE and composes it rather than re-spelling it: the device "
   "set is derived from the device LINE, which is `component_line` of the one named component",
   "device_reimb_line(" in inspect.getsource(cdc.device_reimb_components)
   and "component_line(" in inspect.getsource(cdc.device_reimb_line)
   and "DEVICE_FINANCING_COMPONENT" in inspect.getsource(cdc.device_reimb_line))
# THE SIBLING THAT IS EXCUSED, MEASURED RATHER THAN ASSUMED. The carrier recon owns ONE fact of its
# own — which of the back office's three workbook columns a component belongs to — and its map has no
# row for the two kinds of money the vocabulary gained. A component with no row DEFERS to the comp
# type, which is exactly what an unhonourable category did before this change, so the recon's answer
# is unchanged either way. Giving those components a column is a decision about a COMPARISON report
# and deliberately not taken here; this check locks the safe behaviour so it cannot change by
# accident.
# Read from the SOURCE, not by importing the router: this lock runs in a dependency-free CI job and
# the router pulls in the web framework (`harness_ci_pipefail_lock.py` fails the build on that).
_RB = next((n for n in ast.walk(ast.parse(ROUTER))
            if isinstance(n, ast.Assign)
            and any(getattr(t, "id", "") == "_RECON_COMPONENT_BUCKET" for t in n.targets)), None)
_RB_KEYS = {k.value for k in (_RB.value.keys if isinstance(_RB and _RB.value, ast.Dict) else [])
            if isinstance(k, ast.Constant)}
ok("G11 the carrier recon's last-mile map is keyed by COMPONENT, and a component it has no column "
   "for DEFERS to the comp type (`.get(comp or \"\")` -> None) instead of falling into a column "
   "that would mean something else — so its buckets are identical before and after this change, "
   "and giving the new kinds of money a workbook column stays a separate decision",
   bool(_RB_KEYS) and _RB_KEYS <= set(cdc.COMPONENTS)
   and not (_RB_KEYS & {"MDF", "CHARGEBACK"})
   and '_RECON_COMPONENT_BUCKET.get(comp or "")' in ROUTER, sorted(_RB_KEYS))

# THE FRONTEND MIRROR, INVENTORIED AND BOUNDED. The Carrier Mapping setup screen keeps its own
# array of the components its dropdowns offer. It is a KEYWORD-LADDER editor — the fallback path, not
# the declaration — so the owner's ruling does not depend on it, and wiring it up is a visible UI
# change that waits for his eyeball under the house merge policy. What this lock does NOW is make the
# copy bounded and visible: the API serves the vocabulary (`carrier-category-options`), and the
# mirror may never offer a component the platform does not know. A value it invents fails the build.
FE = os.path.join(HERE, "..", "frontend", "src", "app", "(platform)", "commcalc",
                  "carrier-mapping", "page.tsx")
_fe = open(FE, encoding="utf-8", errors="replace").read() if os.path.exists(FE) else ""
_fe_comps = set()
_m2 = re.search(r"const\s+COMPONENTS\s*=\s*\[([^\]]*)\]", _fe)
if _m2:
    _fe_comps = {x for x in re.findall(r"['\"]([A-Z_]+)['\"]", _m2.group(1))}
ok("G12 the server SERVES the component vocabulary to the setup screen, so the page has a source to "
   "read instead of an array to maintain", '"components": list(carrier_map.COMPONENTS)' in ROUTER)
ok("G13 …and the screen's own array can never offer a component the platform does not know — it is "
   "a strict subset, pending the UI change that reads the served list",
   bool(_fe_comps) and _fe_comps <= set(cdc.COMPONENTS), sorted(_fe_comps - set(cdc.COMPONENTS)))

ok("G10 CONTROL: the vocabulary scan really does bite — a second tuple of three members is found",
   len({"RESIDUAL", "COMMISSION", "SPIFF"} & set(cdc.COMPONENTS)) >= 3)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. PURITY, RULE TWO, THE MODULE GRAPH AND THE INDEX")

tree = ast.parse(HOME)
newfns = [n for n in tree.body if isinstance(n, ast.FunctionDef)
          and n.name in ("component_label", "device_reimb_line", "device_reimb_components")]
ok("H1 all three new functions are in the home and not one of them performs a read",
   len(newfns) == 3 and not [a for n in newfns for a in ast.walk(n)
                             if isinstance(a, ast.Attribute)
                             and a.attr in ("execute", "load_map", "load_config", "load_rules",
                                            "load_declarations")])
ok("H2 they are PURE — the same arguments give the same answer, and the caller's map is not mutated",
   cdc.device_reimb_components(RULED_LINES, DEFAULT_LINE)
   == cdc.device_reimb_components(RULED_LINES, DEFAULT_LINE)
   and RULED_LINES == {"REIMBURSEMENT": "vip_reimb", "MDF": "mdf_income"})
ok("H3 RULE TWO — no carrier, tenant, quarter or payment-type name in ANY code of the home or the "
   "vocabulary",
   not re.search(r"(?i)\b(boost|luxelink|vidapay|epay|cellfonz|iphone|samsung|q[1-4]\s*20\d\d|"
                 r"ramp\s*up|subsidy)\b", HOME_CODE + code_only(VOCAB)))
ok("H4 RULE TWO — the kinds of money are platform vocabulary and the CATEGORY that means each one "
   "is config: the home maps no tenant's category wording to a component outside its default, and "
   "the default is overridable per org",
   "carrier_class_category_components" in HOME
   and all(k == cdc._norm(k) for k in cdc.default_config()["category_components"]))
ok("H5 RULE TWO — neither caller branches on a category, a payment type or a tenant",
   not re.search(r"(?i)(ramp\s*up|subsidy|payment_categories)", COA_CODE)
   and not re.search(r"(?i)(ramp\s*up|subsidy)", ROUTER_CODE))
ok("H6 this harness is DB-free and network-free by construction",
   not re.search(r"(supabase|psycopg|requests|urllib|get_supabase)",
                 code_only(src("harness_declared_category_honourable.py"))))
FACT = "carrier_dollar_component"
f = MG.FACTS.get(FACT) or {}
ok("H7 this lock is registered as one of the fact's locks",
   "harness_declared_category_honourable.py" in (f.get("locks") or ()), f.get("locks"))
ok("H8 the fact's question says a declaration must be EXPRESSIBLE, so the next reader finds this "
   "from the graph", "honour" in (f.get("question") or "").lower(), f.get("question"))
ok("H9 and the fact carries this index section", "69" in (f.get("index") or ()), f.get("index"))

_m = re.search(r"^## 69\. ", INDEX, re.M)
SEC = INDEX[_m.start():] if _m else ""
_n = re.search(r"^## 7\d\. ", SEC, re.M)
SEC = SEC[:_n.start()] if _n else SEC
ok("H10 the index documents the class in its own section", bool(SEC)
   and "honourable" in SEC.lower() and "COMPONENTS" in SEC)
ok("H11 the section carries the measured money and the months", "260,500.00" in SEC
   and "39,300.00" in SEC and "32,000.00" in SEC)
ok("H12 the section states which mechanism was chosen and which was REJECTED, with the reason",
   re.search(r"(?i)rejected", SEC) is not None and "component_line" in SEC)
ok("H13 the section records that the activation is a CONFIG write surfaced to the owner, not a "
   "code default, and which months need a recompute",
   re.search(r"(?i)(surfaced|owner)", SEC) is not None
   and re.search(r"(?i)recompute", SEC) is not None)
ok("H14 the section records the §65 sibling correction, so a per-device report that did NOT change "
   "is not later read as an oversight",
   "device_reimb_components" in SEC and "91,000.00" in SEC)
ok("H15 the per-month before/after table is in the index, not only in the PR body",
   SEC.count("39,300.00") >= 5 and "3,013,982.50" in SEC)
ok("H16 the TOC row exists (a bare TOC cell carries no section mark, which is how two earlier "
   "passes missed it)",
   re.search(r"^\|\s*69\s*\|", INDEX, re.M) is not None)

# ─────────────────────────────────────────────────────────────────────────────────────────────────
print(f"\n{'=' * 78}")
print(f"  {PASS} passed, {FAIL} failed")
if _FAILED:
    print("  FAILED:")
    for x in _FAILED:
        print(f"    - {x}")
print(f"{'=' * 78}")
sys.exit(1 if FAIL else 0)
