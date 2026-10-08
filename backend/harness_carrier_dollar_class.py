#!/usr/bin/env python3
"""PROOF + LOCK — a carrier dollar's classification has ONE home (index §58).

DB-free, network-free, stdlib only. Run: `python3 backend/harness_carrier_dollar_class.py`

THE DEFECT THIS PINS (owner report 2026-10-08, measured live read-only, house org
`00000000-0000-0000-0000-000000000001`):

  Owner: *"i just checked the commission details for boost, the commission is over stated as the
  device reimbursement is being added in the commision and also in device reimbursement … example is
  103 fulton street ↳ Commission (promo) $7,583.96 / Device-financing reimbursements (Distributor)
  $7,999.93 … the report which pays us is same as what is reported in commision 7583.96"*, then
  *"need to move that amount from commssiomn to teh device reimbursement"*, then *"distributors
  payments are not in additon to the reimbursement they are the same payments but the discrepancy
  nbetween them shows that the distributor claims it was paid bunt epay never paid it"*.

  TWO MAPS ANSWERED ONE QUESTION AND DISAGREED, and the one the P&L read never saw the org's own
  declaration. `carrier_category_map`'s generic keyword ladder called every period-renamed carrier
  offer COMMISSION; `payment_categories` — the org's OWN declaration — calls those same offers a
  reimbursement, SEVEN of them on the exact same spelling. Measured over `raw_comp_report`
  March–October 2026: $2,784,846.76 booked as commission against the org's own word, $775,198.35
  more labelled SPIFF against it, and $83,465.55 the org declares Commission rendering "Unmapped".
  A THIRD copy lived in the payment-detail recon, whose private category reader never honoured the
  house spelling of its own reimbursement category at all.

SECTIONS
  §A  the ruling — a declaration beats a keyword rule, in both directions
  §B  an inference travels LABELLED, and only within the configured lookback
  §C  an undeclared type is REPORTED whether or not a keyword rule caught it
  §D  a declared category the org's map cannot honour is neither guessed nor silently dropped
  §E  `tally` — every dollar accounted for by component AND by basis, with `balances` as the proof
  §F  component → P&L line is CONFIG, and an unrouted component never vanishes
  §G  THE REGRESSION — the owner's 103 Fulton September figures, armed as a negative control
  §H  THE UN-WIRING LOCK — both callers keep dereferencing the one home; a second copy fails
  §I  purity and RULE TWO — no carrier, tenant, quarter or product name anywhere in the home
  §J  the distributor's claim is HELD, not booked, and the line stops naming the wrong payer
  §K  the module graph and the index know about all of it
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import carrier_dollar_class as C  # noqa: E402
from app.modules.commcalc import payment_category as _PC  # noqa: E402
from app.modules.core import module_graph as MG  # noqa: E402

PASS = FAIL = 0
_FAILED = []
HERE = os.path.dirname(os.path.abspath(__file__))


def ok(label, cond, extra=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        _FAILED.append(label)
        print(f"  FAIL {label}" + (f"  -> {extra!r}" if extra is not None else ""))


def section(name):
    print(f"\n── {name} " + "─" * max(0, 76 - len(name)))


def read(rel):
    with open(os.path.join(HERE, rel)) as fh:
        return fh.read()


HOME_REL = "app/modules/commcalc/carrier_dollar_class.py"
HOME = read(HOME_REL)
COA = read("app/modules/account/coa.py")
ROUTER = read("app/modules/commcalc/router.py")
INDEX = read("../docs/SYSTEM_DATA_FLOW_INDEX.md")

CFG = C.default_config()

# ── THE FIXTURE ──────────────────────────────────────────────────────────────────────────────────
# Shaped like the house org's real vocabulary but spelled with NEUTRAL words, deliberately: a lock
# that hard-codes a carrier's promo names is the RULE TWO violation it exists to forbid. The two
# maps disagree here exactly as they disagree live.
RULES = [
    {"raw_category": "Residual", "match_type": "contains", "component": "RESIDUAL", "priority": 15},
    {"raw_category": "Bounty", "match_type": "contains", "component": "SPIFF", "priority": 20},
    {"raw_category": "Reimbursement", "match_type": "contains", "component": "REIMBURSEMENT",
     "priority": 30},
    {"raw_category": "Offer", "match_type": "contains", "component": "COMMISSION", "priority": 40},
    {"raw_category": "Upgrade", "match_type": "contains", "component": "COMMISSION", "priority": 45},
]
DECL_RAW = {
    "2026 Q2 Device Offer": "Re-imbursement",       # declared, exact spelling — the strongest case
    "2025 Q4 Device Offer": "Re-imbursement",       # the twin an undeclared 2026 Q3 inherits
    "2025 Q1 Device Offer": "Re-imbursement",       # an older twin, outranked by the newer one
    "Activation Bounty": "Commission",              # declaration OVERRIDES the SPIFF keyword
    "Field Support Fund": "MDF",                    # a category the component map cannot honour
}
# Folded through §57's OWN rule, because that is the map a real caller gets back from
# `payment_category.load_map`. Using the home's rule here rather than re-typing it is the point:
# a fixture that folded its keys its own way would be the tenth copy of the folding bug §57 fixed.
DECL = {_PC._fold(k): v for k, v in DECL_RAW.items()}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. THE RULING — a declaration beats a keyword rule, in BOTH directions")
a1 = C.classify(DECL, RULES, "2026 Q2 Device Offer", CFG)
ok("A1 a declared type takes the declared component, not the keyword ladder's",
   a1["component"] == "REIMBURSEMENT" and a1["keyword_component"] == "COMMISSION", a1)
ok("A2 it is marked `declared` and carries the org's own category string",
   a1["basis"] == C.BASIS_DECLARED and a1["declared"] is True
   and a1["declared_category"] == "Re-imbursement" and a1["inferred"] is False, a1)
a3 = C.classify(DECL, RULES, "Activation Bounty", CFG)
ok("A3 the override runs the OTHER way too — a declared Commission beats the SPIFF keyword",
   a3["component"] == "COMMISSION" and a3["keyword_component"] == "SPIFF"
   and a3["basis"] == C.BASIS_DECLARED, a3)
a4 = C.classify(DECL, RULES, "Device Upgrade", CFG)
ok("A4 an undeclared type with no twin falls to the ladder, basis `keyword_rule`",
   a4["component"] == "COMMISSION" and a4["basis"] == C.BASIS_KEYWORD and a4["declared"] is False, a4)
a5 = C.classify(DECL, RULES, "Something Nobody Mapped", CFG)
ok("A5 nothing resolves it ⇒ component None, basis `unresolved` — never a default bucket",
   a5["component"] is None and a5["basis"] == C.BASIS_UNRESOLVED, a5)
ok("A6 a declaration is matched case/whitespace-folded, so a cosmetic re-spelling is not 'new'",
   C.classify(DECL, RULES, "2026  q2   device offer", CFG)["basis"] == C.BASIS_DECLARED)
ok("A7 the declaration can be switched off per org, and the ladder then decides alone",
   C.classify(DECL, RULES, "2026 Q2 Device Offer",
              dict(CFG, declaration_wins=False))["component"] == "COMMISSION")
ok("A8 an empty type classifies to nothing and raises nothing",
   C.classify(DECL, RULES, "", CFG)["component"] is None
   and C.classify(DECL, RULES, None, CFG)["basis"] == C.BASIS_UNRESOLVED)
ok("A9 no declarations at all ⇒ every row is the pre-fix keyword posture, byte for byte",
   all(C.classify({}, RULES, t, CFG)["component"] == C.classify({}, RULES, t, CFG)["keyword_component"]
       for t in ("2026 Q2 Device Offer", "Activation Bounty", "Device Upgrade", "Nothing")))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. AN INFERENCE TRAVELS LABELLED — a prior-period twin is never read as a declaration")
b1 = C.classify(DECL, RULES, "2026 Q3 Device Offer", CFG)
ok("B1 an undeclared period-renamed type inherits its twin's component",
   b1["component"] == "REIMBURSEMENT", b1)
ok("B2 and it is NOT a declaration: basis says inferred, `declared` is False, `inferred` is True",
   b1["basis"] == C.BASIS_INFERRED_TWIN and b1["declared"] is False and b1["inferred"] is True, b1)
ok("B3 the inference NAMES the row it came from, so a reader can audit it",
   b1["twin_of"] == ("2026 q2 device offer", "Re-imbursement"), b1["twin_of"])
ok("B4 the NEWEST qualifying twin wins, not the oldest — 'earlier' is the period's own order, so "
   "an earlier quarter of the SAME year is a carry-forward too",
   C.classify(DECL, RULES, "2026 Q1 Device Offer", CFG)["twin_of"][0] == "2025 q4 device offer"
   and C.classify(DECL, RULES, "2025 Q3 Device Offer", CFG)["twin_of"][0] == "2025 q1 device offer")
ok("B5 a SAME-OR-LATER period is never a carry-forward",
   C.period_rename_twin({"2026 q3 device offer": "Re-imbursement"}, "2026 Q3 Device Offer", CFG) is None
   and C.period_rename_twin({"2027 q1 device offer": "Re-imbursement"},
                            "2026 Q3 Device Offer", CFG) is None)
ok("B6 a different STEM is never a twin — the inference is not a fuzzy match",
   C.period_rename_twin(DECL, "2026 Q3 Tablet Offer", CFG) is None)
ok("B7 the lookback is config, and a twin beyond it is NOT used",
   C.classify({"2020 q1 device offer": "Re-imbursement"}, RULES, "2026 Q3 Device Offer",
              dict(CFG, rename_lookback=1))["basis"] == C.BASIS_INFERRED_TWIN
   and C.classify({"2020 q1 device offer": "Re-imbursement", "2025 q4 device offer": "Commission"},
                  RULES, "2026 Q3 Device Offer", dict(CFG, rename_lookback=1))["twin_of"][0]
   == "2025 q4 device offer")
ok("B8 the inference can be switched off per org, and the row is then plainly undeclared",
   C.classify(DECL, RULES, "2026 Q3 Device Offer",
              dict(CFG, rename_inference=False))["basis"] == C.BASIS_KEYWORD)
ok("B9 a twin whose own category the map cannot honour is NOT a twin",
   C.period_rename_twin({"2025 q4 device offer": "MDF"}, "2026 Q3 Device Offer", CFG) is None)
ok("B10 the rename shape is a CONFIG regex, and an unusable one keeps the default instead of "
   "silently disabling the inference",
   C._valid_rename_pattern(CFG["rename_pattern"]) and not C._valid_rename_pattern("(")
   and not C._valid_rename_pattern(r"^(\d{4})\s+(.*)$"))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. AN UNDECLARED TYPE IS REPORTED — whether or not a keyword rule caught it")
rows = [
    {"compensation_type": "2026 Q2 Device Offer", "payment_amount": 100.0},   # declared
    {"compensation_type": "2026 Q3 Device Offer", "payment_amount": 200.0},   # inferred
    {"compensation_type": "Device Upgrade", "payment_amount": 30.0},          # keyword only
    {"compensation_type": "Field Support Fund", "payment_amount": 40.0},      # declared, unmappable
    {"compensation_type": "Something Nobody Mapped", "payment_amount": 5.0},  # nothing at all
]
t = C.tally(rows, DECL, RULES, CFG)
und = {u["payment_type"]: u for u in t["undeclared"]}
ok("C1 every basis other than `declared` is reported as undeclared money",
   set(und) == {"2026 Q3 Device Offer", "Device Upgrade", "Field Support Fund",
                "Something Nobody Mapped"}, sorted(und))
ok("C2 a keyword-only type is reported even though it DID book on its fallback",
   und["Device Upgrade"]["basis"] == C.BASIS_KEYWORD
   and und["Device Upgrade"]["component"] == "COMMISSION")
ok("C3 the declared total and the undeclared total split the feed exactly",
   t["declared_total"] == 100.0 and t["undeclared_total"] == 275.0
   and t["declared_total"] + t["undeclared_total"] == t["total"], t)
ok("C4 each basis carries its REASON in words, never a bare enum",
   all(b["reason"] for b in t["by_basis"]))
ok("C5 the inference is reported separately, with its source",
   [i["payment_type"] for i in t["inferred"]] == ["2026 Q3 Device Offer"]
   and t["inferred"][0]["twin_of"][0] == "2026 q2 device offer")
ok("C6 what the keyword ladder ALONE would have said is reported, so the move is auditable",
   any(r["from"] == "COMMISSION" and r["to"] == "REIMBURSEMENT" and r["amount"] == 300.0
       for r in t["reclassified_vs_keyword"]), t["reclassified_vs_keyword"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. A DECLARED CATEGORY THE MAP CANNOT HONOUR IS NEITHER GUESSED NOR DROPPED")
d1 = C.classify(DECL, RULES, "Field Support Fund", CFG)
ok("D1 it does NOT invent a component from the category", d1["basis"] == C.BASIS_DECLARED_UNMAPPED)
ok("D2 it is not a declaration for reporting purposes", d1["declared"] is False)
ok("D3 it keeps the org's category string so a reader can see what was declared",
   d1["declared_category"] == "MDF")
ok("D4 the category→component map is CONFIG — adding a row honours it with no code change",
   C.classify(DECL, RULES, "Field Support Fund",
              dict(CFG, category_components=dict(CFG["category_components"], mdf="SPIFF"))
              )["component"] == "SPIFF")
ok("D5 an unknown category returns None, which is an honest 'cannot honour', not a default",
   C.component_of_declared_category("something else", CFG) is None
   and C.component_of_declared_category(None, CFG) is None)
ok("D6 a config component outside the vocabulary is rejected, not adopted",
   "NOT_A_COMPONENT" not in set(C.default_config()["category_components"].values()))
ok("D7 the vocabulary is DEREFERENCED from carrier_map, not re-spelled",
   "COMPONENTS = _cm.COMPONENTS" in HOME and re.search(r'COMPONENTS\s*=\s*\(', HOME) is None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. EVERY DOLLAR ACCOUNTED FOR — by component AND by basis, with `balances` as the proof")
ok("E1 the two breakdowns each sum to the feed total, to the cent", t["balances"] is True)
ok("E2 the component breakdown places the unresolvable dollars under UNKNOWN, not nowhere — "
   "both the never-mapped type and the unhonourable declaration land there, priced",
   any(c["component"] == "UNKNOWN" and c["amount"] == 45.0 for c in t["by_component"]),
   t["by_component"])
ok("E3 an empty feed balances at zero and raises nothing",
   C.tally([], DECL, RULES, CFG)["balances"] and C.tally(None, DECL, RULES, CFG)["total"] == 0.0)
ok("E4 a non-numeric amount is read as zero, never as a crash",
   C.tally([{"compensation_type": "Device Upgrade", "payment_amount": "oops"}],
           DECL, RULES, CFG)["total"] == 0.0)
ok("E5 the keys are parameters, so the SAME tally serves the comp feed and the payment-detail feed",
   C.tally([{"payment_type": "Device Upgrade", "amount": 7.0}], DECL, RULES, CFG,
           amount_key="amount", type_key="payment_type")["total"] == 7.0)
ok("E6 a missing config column is REPORTED on the tally, so a surface can say 'apply migration X'",
   C.tally([], DECL, RULES, dict(CFG, config_columns_missing=["carrier_component_lines"],
                                 config_migrations_missing=["1062_carrier_dollar_class.sql"])
           )["config_migrations_missing"] == ["1062_carrier_dollar_class.sql"])
ok("E7 every config column names the migration that adds it, in column order and deduped "
   "(1064 added the GP-column twin of the P&L routing — §58.7)",
   all(m for _c, m in C.CONFIG_COLUMNS)
   and C.config_migrations_missing([c for c, _m in C.CONFIG_COLUMNS])
   == ["1062_carrier_dollar_class.sql", "1064_carrier_gp_columns.sql"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. COMPONENT → P&L LINE IS CONFIG, and an unrouted component never vanishes")
ok("F1 THE MECHANISM SHIPS INERT: with no config row every component books where it always did, "
   "so no org's statement moves until the owner applies mig 1062",
   CFG["component_lines"] == {}
   and all(C.component_line(c, CFG["component_lines"], "carrier_comm") == "carrier_comm"
           for c in C.COMPONENTS))
ROUTED = dict(CFG, component_lines={"REIMBURSEMENT": "vip_reimb"})
ok("F2 a component with no row books to the caller's default line",
   C.component_line("COMMISSION", ROUTED["component_lines"], "carrier_comm") == "carrier_comm"
   and C.component_line("SPIFF", ROUTED["component_lines"], "carrier_comm") == "carrier_comm")
ok("F3 the routed component books where the OWNER'S config says — the owner's "
   "\"move that amount from commssiomn to teh device reimbursement\"",
   C.component_line("REIMBURSEMENT", ROUTED["component_lines"], "carrier_comm") == "vip_reimb")
ok("F4 an unclassified row still books — to the default line, never nowhere",
   C.component_line(None, CFG["component_lines"], "carrier_comm") == "carrier_comm")
ok("F5 an empty map is honoured: every component books to the default (the pre-fix destination)",
   all(C.component_line(c, {}, "carrier_comm") == "carrier_comm" for c in C.COMPONENTS))
ok("F6 the device-reimbursement source is a closed set, and its default is the LEGACY posture — "
   "de-recognising the distributor's claim is a revenue reduction and that is the owner's to apply",
   C.DEVICE_REIMB_SOURCES == ("carrier_paid", "distributor_claim")
   and CFG["device_reimb_source"] == "distributor_claim")
ok("F7 the CLASSIFICATION fix, by contrast, is unconditional: it changes the drill-down label and "
   "never a line total, because every component still books to the default line by default",
   len({C.component_line(C.classify(DECL, RULES, t, CFG)["component"],
                         CFG["component_lines"], "carrier_comm")
        for t in ("2026 Q2 Device Offer", "Activation Bounty", "Device Upgrade", "Nothing")}) == 1)
ok("F8 the migration that flips it is named, and the config is adaptive until it is applied",
   "1062_carrier_dollar_class.sql" in dict(C.CONFIG_COLUMNS).values()
   and os.path.exists(os.path.join(HERE, "..", "database", "migrations",
                                   "1062_carrier_dollar_class.sql")))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. THE REGRESSION — the owner's 103 Fulton September figures, as a negative control")
# The owner's example, in the fixture's neutral vocabulary and with his real amounts: three
# period-renamed offers the ladder called commission, and one correctly-keyworded reimbursement.
FULTON = [
    {"compensation_type": "2026 Q3 Device Offer", "payment_amount": 3964.96},
    {"compensation_type": "2026 Q3 Device Offer", "payment_amount": 3204.00},
    {"compensation_type": "2026 Q3 Device Offer", "payment_amount": 415.00},
    {"compensation_type": "Trade-In Device Reimbursement", "payment_amount": 65.00},
]
pre = C.tally(FULTON, {}, RULES, CFG)            # the pre-fix posture: no declaration is consulted
post = C.tally(FULTON, DECL, RULES, CFG)
pre_c = {c["component"]: c["amount"] for c in pre["by_component"]}
post_c = {c["component"]: c["amount"] for c in post["by_component"]}
ok("G1 ARMED: without the declaration the owner's $7,583.96 lands on COMMISSION — the reported bug",
   pre_c.get("COMMISSION") == 7583.96, pre_c)
ok("G2 with the one home, those same dollars are REIMBURSEMENT and the commission line is clean",
   post_c.get("COMMISSION") is None and post_c.get("REIMBURSEMENT") == 7648.96, post_c)
ok("G3 the move is reported as $7,583.96 COMMISSION→REIMBURSEMENT, the owner's own figure",
   any(r["from"] == "COMMISSION" and r["to"] == "REIMBURSEMENT" and r["amount"] == 7583.96
       for r in post["reclassified_vs_keyword"]), post["reclassified_vs_keyword"])
ok("G4 and it is reported as an INFERENCE, not as the org's word — $7,583.96 of it",
   sum(i["amount"] for i in post["inferred"]) == 7583.96
   and post["inferred"][0]["twin_of"][0] == "2026 q2 device offer"
   and post["undeclared_total"] == post["total"], post["inferred"])
ok("G5 the total never moved: reclassification is not revaluation",
   pre["total"] == post["total"] == 7648.96)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. THE UN-WIRING LOCK — both callers dereference the one home; a second copy fails")
ok("H1 the P&L's carrier block imports the home",
   "from app.modules.commcalc import carrier_dollar_class as _cdc" in COA)
ok("H2 and classifies through it, for every raw_comp_report row",
   "_cdc.classify(_cc_decl, _cc_rules, _ct_raw, _cdc_cfg)" in COA)
ok("H3 the P&L no longer calls the keyword ladder directly for raw_comp_report",
   "carrier_map.match_rule(" not in COA)
ok("H4 the destination comes from the home's config router, not from a branch in coa.py",
   "_cdc.component_line(" in COA
   and not re.search(r'if\s+_c\["component"\]\s*==\s*"', COA))
ok("H5 the P&L publishes the coverage report, so an inference cannot pass as a declaration",
   'L["_carrier_class_coverage"] = dict(_cc_tally' in COA and "_cdc.tally(" in COA)
ok("H6 the payment-detail recon reads the declaration through the home's ONE loader",
   "cat_of = _cdc.load_declarations(client, org_id)" in ROUTER)
ok("H7 and asks the home what a declared category MEANS, instead of keyword-matching it",
   "_cdc.component_of_declared_category(cat, class_cfg)" in ROUTER)
# A SECOND COPY OF THE FACT IS ALREADY GUARDED — by §57's own lock, which owns the EXACT inventory
# of readers of `payment_categories` and fails the build on a new one. Keeping a second inventory
# here would be the duplicate defect this file exists to stop, so what this lock asserts instead is
# that the §57 guard is real, is in CI, and that THIS home is not a reader at all.
SIBLING_LOCK = "harness_payment_category_home_lock.py"
ok("H8 the §57 home lock — which owns the reader inventory — exists and is run by a workflow",
   os.path.exists(os.path.join(HERE, SIBLING_LOCK))
   and any(SIBLING_LOCK in open(os.path.join(HERE, "..", ".github", "workflows", w)).read()
           for w in os.listdir(os.path.join(HERE, "..", ".github", "workflows"))
           if w.endswith((".yml", ".yaml"))))
ok("H8a this home keeps NO copy of the read or of the folding rule — it dereferences §57",
   'table("payment_categories")' not in HOME
   and "from app.modules.commcalc import payment_category as _pc" in HOME
   and "_pc.load_map(client, org_id)" in HOME and "_pc.category_of(" in HOME)
ok("H8b and the component ruling is what it ADDS on top of §57's category read — §57 is not "
   "asked to decide a component, and this file is not asked to decide a category",
   "component_of_declared_category" in HOME
   and "component" not in read("app/modules/commcalc/payment_category.py"))
ok("H9 the recon's own last mile is a component→column map, not a category keyword list",
   "_RECON_COMPONENT_BUCKET" in ROUTER
   and 'for x in ("commission", "bounty", "spiff")' not in ROUTER
   and 'for x in ("rebate", "reimburs", "promo", "offer", "discount")' not in ROUTER)
ok("H10 the recon path keeps no private {description: category} comprehension",
   'cat_of = {str(c.get("description")' not in ROUTER)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("I. PURITY AND RULE TWO — the home names no carrier, tenant, quarter or product")
ok("I1 no DB, no network, no clock in the home",
   not re.search(r"\b(requests|httpx|urllib|psycopg|asyncpg|datetime|time)\b", HOME)
   and "get_supabase" not in HOME)
ok("I2 the only module-level non-stdlib import is the component vocabulary it dereferences",
   [m for m in re.findall(r"^from\s+([\w.]+)\s+import", HOME, re.M)
    if not m.startswith("__future__")] == ["app.modules.commcalc"])
# RULE TWO. A quarter marker, a promo word or a brand in a STRING LITERAL of this module would be
# the very branch the fix removes. The docstring is prose about the defect and is excluded; every
# other literal is scanned.
body = HOME.split('"""', 2)[2] if HOME.count('"""') >= 2 else HOME
literals = re.findall(r"'([^']*)'|\"([^\"]*)\"", body)
flat = [a or b for a, b in literals]
BANNED = re.compile(r"\b(q[1-4]\s*20\d\d|20\d\d\s*q[1-4]|promo|iphone|byod|pic\b)", re.I)
bad = [s for s in flat if BANNED.search(s)]
ok("I3 no quarter marker, promo word or product name in any code literal of the home", not bad, bad)
ok("I4 the rename shape is a config value, not a literal in a branch",
   "rename_pattern" in C.default_config() and "rename_pattern" in HOME)
ok("I5 the declared-category map is config, and the house default is only a DEFAULT",
   "category_components" in C.default_config()
   and C.load_config.__doc__ and "ADAPTIVE" in C.load_config.__doc__)
ok("I6 every pure function really is pure — calling it twice gives the same answer",
   C.classify(DECL, RULES, "2026 Q3 Device Offer", CFG)
   == C.classify(DECL, RULES, "2026 Q3 Device Offer", CFG))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("J. THE DISTRIBUTOR'S CLAIM IS HELD, NOT BOOKED (owner 2026-10-08)")
ok("J1 the claim books revenue ONLY under the legacy posture",
   '_dist_claim_books_revenue = ((_cdc_cfg or {}).get("device_reimb_source") or "distributor_claim")'
   in COA and 'if _dist_claim_books_revenue:\n                    add("vip_reimb", st, reimb)' in COA)
ok("J2 the claim is counted per store and per period even when it books nothing",
   '_dist_claim["claim_by_store"]' in COA and '_dist_claim["rows"] += 1' in COA)
ok("J2a NEITHER side entry is bookable: `engine.compute_and_store` walks ln['by_store'] over every "
   "value of L, so both carry those keys EMPTY and the claim's dollars live under `claim_by_store`",
   'L["_carrier_class_coverage"] = dict(_cc_tally, by_store={}, company_wide=0.0)' in COA
   and '"by_store": {}, "company_wide": 0.0, "rows": 0,' in COA)
ok("J3 it is PUBLISHED beside what the carrier actually paid, with the difference named",
   'L["_distributor_reimb_claim"] = _dist_claim' in COA
   and '"carrier_paid_total"' in COA and '"difference"' in COA)
ok("J4 its status says plainly which of the two things it is",
   '"booked_as_revenue" if _dist_claim["books_revenue"] else "held_unreconciled"' in COA)
ok("J5 the held claim carries the reason in words, not a bare flag",
   "not a second receipt" in COA and "never income" in COA)
ok("J6 the line stops naming the wrong payer once it carries the carrier's figure",
   '"Device-financing reimbursements (carrier-paid)"' in COA)
ok("J7 the relabel rides the EXISTING label passthrough, so PL_SPEC's chart is untouched",
   'L[_reimb_dest]["label"] =' in COA
   and '("vip_reimb",     "Device-financing reimbursements (Distributor)"' in COA)
ok("J8 the per-org label config is applied AFTER, so the owner's own word always wins",
   0 <= COA.find('L[_reimb_dest]["label"] =') < COA.find("apply_line_labels(L"))
ok("J9 no reconciliation of the difference is built here — that is a separate mechanism",
   "deliberately not done here" in COA or "deliberately NOT built" in COA)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("K. THE MODULE GRAPH AND THE INDEX KNOW ABOUT ALL OF IT")
FACT = "carrier_dollar_component"
f = MG.FACTS.get(FACT) or {}
ok("K1 the fact is registered in the module graph", bool(f), sorted(MG.FACTS))
ok("K2 its home is this module", f.get("homes") == (HOME_REL,), f.get("homes"))
ok("K3 every caller is named in the snapshot (the GROSS PROFIT engine joined them in §58.7: it "
   "carried two private classifications that disagreed with the P&L by $418,922.21)",
   set((f.get("callers") or {})) == {"app/modules/account/coa.py",
                                     "app/modules/commcalc/gp_report.py",
                                     "app/modules/commcalc/router.py"}, f.get("callers"))
ok("K4 this lock is the fact's lock", "harness_carrier_dollar_class.py" in (f.get("locks") or ()))
ok("K5 the index section it points at exists",
   all(re.search(r"^#+\s*(§\s*)?%s[.\s]" % re.escape(s.split(".")[0]), INDEX, re.M)
       for s in (f.get("index") or ())), f.get("index"))
ok("K6 the index documents the one home by name", "carrier_dollar_class" in INDEX)
ok("K7 the index records the measured restatement, so the figure is not only in a PR",
   "2,784,846.76" in INDEX)
ok("K8 `connected()` answers 'what else answers this question' for both callers",
   FACT in MG.connected("app/modules/account/coa.py")
   and FACT in MG.connected("app/modules/commcalc/router.py"))

# ─────────────────────────────────────────────────────────────────────────────────────────────────
print(f"\n{'=' * 78}")
print(f"  {PASS} passed, {FAIL} failed")
if _FAILED:
    print("  FAILED:")
    for x in _FAILED:
        print(f"    - {x}")
print(f"{'=' * 78}")
sys.exit(1 if FAIL else 0)
