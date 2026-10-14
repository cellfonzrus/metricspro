#!/usr/bin/env python3
"""PROOF: a sale's accessory total decides which DECLARED rate a line earns, and it cannot un-wire.

DB-free. Imports the REAL `commcalc.attach_qualifier`, and reads `commission_engine.py` as text for
the wiring locks. No network, no database, stdlib only.

Owner directive 2026-10-12: *"Payment for edge on total which is defined as $25 must be accompanied
with atleast $25 worth of accessories ... not as hard coded but as a logic platform wide which might
be used by other carriers also ... otherwise the commision falls back to $10 or whatever is paid to
Chicago market"*.

The defect being locked shut is a MISSING capability, not a wrong number: every gate in this engine
could only turn a payment OFF, so no rule could earn a lesser rate from a fact about its sale.

Run:  python3.11 backend/harness_attach_qualifier.py
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from app.modules.commcalc import attach_qualifier as aq  # noqa: E402
from app.modules.core.module_graph import home_under_app  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
ENGINE = os.path.join(APP, "modules", "commcalc", "commission_engine.py")
# THE GRAPH IS THE ONE HOME FOR THE RULING (index §19.18 / §50). No path literal of its own, so
# moving the home cannot leave this lock guarding a file that is no longer there while printing OK.
HOME = os.path.join(APP, home_under_app("attach_qualifier"))

_P = []


def ok(name, cond, extra=""):
    _P.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + name + (("   " + str(extra)) if extra and not cond else ""))


def section(t):
    print("\n" + t)


# ── fixtures: one sale that attaches, one that does not ──────────────────────────────────────────
def line(tid, dept, price, gp=None, kind="device"):
    return {"trans_id": tid, "department": dept, "ext_price": price,
            "gp": price * 0.3 if gp is None else gp, "product_desc": kind}


# T1: a $25-qualifying sale ($30 of accessories). T2: falls short ($10). T3: none at all.
SALE_OK = [line("T1", "Handset", 800.0, kind="device"),
           line("T1", "Accessories", 18.0, kind="case"),
           line("T1", "Accessories", 12.0, kind="protector")]
SALE_SHORT = [line("T2", "Handset", 800.0, kind="device"),
              line("T2", "Accessories", 10.0, kind="case")]
SALE_BARE = [line("T3", "Handset", 800.0, kind="device")]
ALL_ROWS = SALE_OK + SALE_SHORT + SALE_BARE

IS_ACC = (lambda r: str(r.get("department", "")).strip().lower() == "accessories")

# The owner's rule, as CONFIG: $25 flat, needs $25 of accessories on the sale, else $10.
RULE = {"id": "r1", "payout_kind": "flat_per_unit", "amount": 25.0,
        "match_field": "department", "match_op": "equals", "match_value": "handset",
        "qualifier_min": 25.0, "qualifier_fallback_amount": 10.0}
NO_Q_RULE = {"id": "r0", "payout_kind": "flat_per_unit", "amount": 25.0}


def ctx_for(rules=(RULE,), rows=None, is_acc=IS_ACC, matches=None):
    return aq.build_context(ALL_ROWS if rows is None else rows, rules, is_acc, matches)


# ═════ A. INERT UNTIL DECLARED ═══════════════════════════════════════════════════════════════════
section("A. A rule that declares no qualifier is untouched")
ok("A1 resolve() is None with no qualifier_min", aq.resolve(NO_Q_RULE) is None)
ok("A2 resolve() is None for an empty rule", aq.resolve({}) is None)
ok("A3 resolve() is None for a non-dict", aq.resolve(None) is None)
ok("A4 a zero minimum is NOT a condition", aq.resolve(dict(RULE, qualifier_min=0)) is None)
ok("A5 a negative minimum is NOT a condition", aq.resolve(dict(RULE, qualifier_min=-5)) is None)
ok("A6 a blank minimum is NOT a condition", aq.resolve(dict(RULE, qualifier_min="")) is None)
ok("A7 build_context is inert with no qualifying rule",
   aq.build_context(ALL_ROWS, [NO_Q_RULE], IS_ACC).get("active") is False)
ok("A8 verdict() is None for such a rule", aq.verdict(SALE_OK[0], NO_Q_RULE, ctx_for()) is None)
ok("A9 rule_view returns the SAME object when there is no verdict",
   aq.rule_view(NO_Q_RULE, None) is NO_Q_RULE)
ok("A10 describe() is empty for such a rule", aq.describe(NO_Q_RULE) == "")

# ═════ B. THE MEASURE IS THE WHOLE SALE ══════════════════════════════════════════════════════════
section("B. The condition is measured over the transaction, not the line")
C = ctx_for()
ok("B1 the qualifying sale totals both accessory lines ($30)",
   C["by_rule"]["r1"]["totals"]["T1"] == 30.0, C["by_rule"]["r1"]["totals"])
ok("B2 the short sale totals $10", C["by_rule"]["r1"]["totals"]["T2"] == 10.0)
ok("B3 a sale with no accessories has no total", "T3" not in C["by_rule"]["r1"]["totals"])
ok("B4 the DEVICE line of the qualifying sale earns the FULL rate",
   aq.verdict(SALE_OK[0], RULE, C)["amount"] == 25.0)
ok("B5 the device line of the SHORT sale earns the FALLBACK",
   aq.verdict(SALE_SHORT[0], RULE, C)["amount"] == 10.0)
ok("B5b and its basis says so rather than leaving the number bare",
   aq.verdict(SALE_SHORT[0], RULE, C)["basis"] == aq.BASIS_FALLBACK)
ok("B5c a sale with NO accessories also earns the fallback",
   aq.verdict(SALE_BARE[0], RULE, C)["amount"] == 10.0)
# ★ THE SILENT-DOCK REGRESSION ★ — an unanswerable predicate must not read as "nothing attached".
C_NOPRED = aq.build_context(ALL_ROWS, [RULE], None, None)
_v = aq.verdict(SALE_OK[0], RULE, C_NOPRED)
ok("B6 an UNREADABLE accessory definition pays the FULL rate, never the lesser one",
   _v["amount"] == 25.0 and _v["basis"] == aq.BASIS_UNMEASURED, _v)
ok("B6b and it is reported rather than silent",
   any(n.get("code") == "qualifier_predicate_unavailable" for n in C_NOPRED["notes"]))
# A rep's own share of the sale is irrelevant: one rep rang the device, another the accessories.
ok("B7 two reps on one sale read the SAME verdict",
   aq.verdict(SALE_OK[0], RULE, C)["met"] is aq.verdict(SALE_OK[1], RULE, C)["met"] is True)
ok("B8 exactly at the minimum QUALIFIES (at least, not more than)",
   aq.verdict({"trans_id": "X"}, RULE,
              aq.build_context([{"trans_id": "X", "department": "Accessories", "ext_price": 25.0}],
                               [RULE], IS_ACC))["met"] is True)
ok("B9 a cent short does NOT qualify",
   aq.verdict({"trans_id": "X"}, RULE,
              aq.build_context([{"trans_id": "X", "department": "Accessories", "ext_price": 24.99}],
                               [RULE], IS_ACC))["met"] is False)

# ═════ C. NO TRANSACTION ID IS NOT ZERO ══════════════════════════════════════════════════════════
section("C. A sale whose siblings cannot be read is reported, never docked")
_bare = {"department": "Handset", "ext_price": 800.0}
_v = aq.verdict(_bare, RULE, C)
ok("C1 a row with no trans_id earns the FULL rate", _v["amount"] == 25.0)
ok("C2 its basis is 'unmeasured', not 'full' — the two are different facts",
   _v["basis"] == aq.BASIS_UNMEASURED)
ok("C3 met is None, not False — unknown is not 'did not'", _v["met"] is None)
ok("C4 such rows are counted so the owner can see them",
   aq.build_context([_bare], [RULE], IS_ACC)["unmeasurable"] == 1)
ok("C5 a '.0'-suffixed id is the same transaction (the house spelling)",
   aq.txn_key({"trans_id": "123.0"}) == "123" == aq.txn_key({"trans_id": " 123 "}))

# ═════ D. THE FALLBACK IS DECLARED, NEVER INVENTED ═══════════════════════════════════════════════
section("D. The lesser rate is declared, never guessed")
_NOFB = dict(RULE); _NOFB.pop("qualifier_fallback_amount")
_v = aq.verdict(SALE_SHORT[0], _NOFB, ctx_for((_NOFB,)))
ok("D1 with no declared fallback the line pays nothing", _v["amount"] == 0.0)
ok("D2 and says so rather than implying a rate", _v["fallback_declared"] is False)
ok("D3 a declared $0 fallback is honoured as zero",
   aq.verdict(SALE_SHORT[0], dict(RULE, qualifier_fallback_amount=0),
              ctx_for((dict(RULE, qualifier_fallback_amount=0),)))["amount"] == 0.0)
ok("D4 rule_view substitutes the fallback into the rate the calculator reads",
   aq.rule_view(RULE, aq.verdict(SALE_SHORT[0], RULE, C))["amount"] == 10.0)
ok("D5 rule_view never mutates the caller's rule", RULE["amount"] == 25.0)
ok("D6 a qualifying line's rule is handed back UNCHANGED (same object)",
   aq.rule_view(RULE, aq.verdict(SALE_OK[0], RULE, C)) is RULE)
# A flat fallback cannot be substituted into a percentage rule — refused, not silently ignored.
_PCT = {"id": "r1", "payout_kind": "pct_price", "pct": 0.1, "amount": 0.0,
        "qualifier_min": 25.0, "qualifier_fallback_amount": 10.0}
_v = aq.verdict(SALE_SHORT[0], _PCT, ctx_for((_PCT,)))
ok("D7 a fallback on a PERCENTAGE rule is refused", _v["basis"] == aq.BASIS_UNSUPPORTED)
ok("D8 and that rule's pay is left exactly as it is today",
   aq.rule_view(_PCT, _v) is _PCT)

# ═════ E. RULE TWO — NO CARRIER, TENANT, PRODUCT OR DOLLAR IN CODE ═══════════════════════════════
section("E. Nothing is hard-coded (RULE TWO)")
SRC = io.open(HOME, encoding="utf-8").read()
_BODY = "\n".join(l for l in SRC.splitlines() if not l.lstrip().startswith("#"))
_BODY = _BODY.split('"""', 2)[-1]          # drop the module docstring, which QUOTES the owner
_BANNED = ("boost", "total wireless", "luxelink", "cellfonz", "vzone", "edge", "chicago", "verizon")
for w in _BANNED:
    ok(f"E1 no '{w}' in the code", w not in _BODY.lower(), [l for l in _BODY.splitlines() if w in l.lower()][:1])
ok("E2 no dollar amount is baked into a threshold",
   not re.search(r"(minimum|threshold)\s*=\s*\d", _BODY))
ok("E3 the default measure is the SELLING PRICE, stated once",
   aq.DEFAULT_MEASURE == aq.MEASURE_AMOUNT)
ok("E4 the measure vocabulary is an allowlist",
   aq.resolve(dict(RULE, qualifier_measure="moonbeams"))["measure"] == aq.DEFAULT_MEASURE)
ok("E5 the scope vocabulary is an allowlist",
   aq.resolve(dict(RULE, qualifier_scope="galaxy"))["scope"] == aq.DEFAULT_SCOPE)
ok("E6 a declared measure is honoured",
   aq.resolve(dict(RULE, qualifier_measure="gp"))["measure"] == aq.MEASURE_GP)

# ═════ F. THE MEASURES ═══════════════════════════════════════════════════════════════════════════
section("F. Each declared measure counts what it says")
_RGP = dict(RULE, qualifier_measure="gp", qualifier_min=5.0)
ok("F1 'gp' measures profit, not price",
   aq.build_context(SALE_OK, [_RGP], IS_ACC)["by_rule"]["r1"]["totals"]["T1"] == 9.0)
_RCT = dict(RULE, qualifier_measure="count", qualifier_min=2)
ok("F2 'count' measures how many lines",
   aq.build_context(SALE_OK, [_RCT], IS_ACC)["by_rule"]["r1"]["totals"]["T1"] == 2.0)
ok("F3 two accessory lines meet a count of 2",
   aq.verdict(SALE_OK[0], _RCT, aq.build_context(SALE_OK, [_RCT], IS_ACC))["met"] is True)
ok("F4 one accessory line does not",
   aq.verdict(SALE_SHORT[0], _RCT, aq.build_context(SALE_SHORT, [_RCT], IS_ACC))["met"] is False)

# ═════ G. "IS THIS AN ACCESSORY" IS NEVER ANSWERED HERE ══════════════════════════════════════════
section("G. The accessory test and the rule matcher are INJECTED, not copied")
# The sharpest form of "it owns no classifier": the module never READS a classifying column. It
# touches `trans_id`, `ext_price` and `gp` and nothing else — every classifying question is handed to
# an injected callable. A keyword list cannot appear here without this failing, and unlike a
# banned-words scan it does not trip over `accessory` used as the engine's own match_field NAME or as
# the noun in the on-screen sentence.
_CLASSIFYING = ("department", "category", "product_desc", "sku", "contract_type", "tender_type")
_READS = set(re.findall(r"""\.get\(\s*["']([a-z_]+)["']""", _BODY))
ok("G1 the module reads no classifying column — it owns no classifier",
   not (_READS & set(_CLASSIFYING)), sorted(_READS & set(_CLASSIFYING)))
ok("G1b it reads only the transaction id and the two money columns",
   _READS <= {"trans_id", "ext_price", "gp", "amount", "payout_kind", "id",
              "qualifier_min", "qualifier_measure", "qualifier_scope", "qualifier_match_field",
              "qualifier_match_op", "qualifier_match_value", "qualifier_fallback_amount",
              "qualifier_label", "match_field", "match_op", "match_value", "by_rule", "totals",
              "minimum", "measure", "basis", "fallback_amount", "label", "code",
              "predicate_unavailable", "notes"}, sorted(_READS))
ok("G2 it imports nothing from the app at all",
   not re.search(r"^\s*(?:from|import)\s+app\.", SRC, re.M))
ok("G3 the injected predicate is what decides, and swapping it swaps the answer",
   aq.build_context(SALE_OK, [RULE], (lambda r: False))["by_rule"]["r1"]["totals"] == {})
ok("G4 a non-accessory qualifier field uses the INJECTED rule matcher",
   aq.build_context(SALE_OK, [dict(RULE, qualifier_match_field="department",
                                   qualifier_match_value="accessories")], None,
                    (lambda row, rl: str(row.get("department", "")).lower()
                     == str(rl.get("match_value", "")).lower()))["by_rule"]["r1"]["totals"]["T1"] == 30.0)
ok("G5 with NO matcher injected the condition is unanswerable, not false",
   aq.build_context(SALE_OK, [dict(RULE, qualifier_match_field="department",
                                   qualifier_match_value="accessories")], None, None
                    )["by_rule"]["r1"]["predicate_unavailable"] is True)

# ═════ H. THE LABEL IS BUILT FROM THE TENANT'S OWN NUMBERS ═══════════════════════════════════════
section("H. The on-screen sentence is derived, not written")
_d = aq.describe(RULE)
ok("H1 it states the requirement and both rates", "$25 of accessories" in _d and "$10" in _d, _d)
ok("H2 a tenant with other numbers reads THEIR sentence",
   "$40 of accessories" in aq.describe(dict(RULE, qualifier_min=40, amount=60,
                                            qualifier_fallback_amount=15)))
ok("H3 cents are shown when the figure has them",
   "$12.50" in aq.describe(dict(RULE, qualifier_min=12.5)))
ok("H4 with no fallback the sentence says nothing is paid", "pays nothing" in aq.describe(_NOFB))
ok("H5 a tenant may override the whole sentence",
   aq.describe(dict(RULE, qualifier_label="Attach $25 to earn $25")) == "Attach $25 to earn $25")
ok("H6 the met/unmet reasons carry the measured figure, not just a verdict",
   "$30" in aq.verdict(SALE_OK[0], RULE, C)["reason"]
   and "$10" in aq.verdict(SALE_SHORT[0], RULE, C)["reason"])
ok("H7 a count rule reads as items, not dollars",
   "accessory items" in aq.describe(_RCT) and "$" not in aq.describe(_RCT).split("qualify")[0])

# ═════ I. THE WIRING LOCK ════════════════════════════════════════════════════════════════════════
section("I. The engine dereferences this home and carries no second copy")
ESRC = io.open(ENGINE, encoding="utf-8").read()
ok("I1 the engine imports this module", re.search(r"import\s+attach_qualifier", ESRC))
ok("I2 the engine builds the context from the period's WHOLE row list, not one rep's lines",
   bool(re.search(r"_aq\.build_context\(\s*valid\b", ESRC)))
ok("I3 the engine injects the accessory predicate rather than letting the module guess",
   re.search(r"_aq\.build_context\([^)]*_acc_fn", ESRC, re.S))
ok("I4 the engine injects its OWN rule matcher",
   re.search(r"_aq\.build_context\([^)]*_rule_matches", ESRC, re.S))
ok("I5 the substituted rate reaches the payout through rule_view",
   re.search(r"_aq\.rule_view\(", ESRC))
ok("I6 a non-qualifying line is still SHOWN with its reason (never a silent $0)",
   re.search(r"qualifier_reason|attach_reason", ESRC))
# No second copy: nothing in the engine may compare an accessory total to a threshold by hand.
# A hand-rolled test is a COMPARISON against the threshold, not a mention of it: the engine legitimately
# copies `minimum` and `measured` into the drill-down so a rep can read why they were paid what they
# were. So the detector requires a comparison operator on the same line. Z5 keeps it armed.
_HAND = [l for l in ESRC.splitlines()
         if re.search(r"(?:qualifier_min|minimum|accessory_total|acc_total)\b[^=]*(?:>=|<=|>|<)"
                      r"|(?:>=|<=|>|<)[^=]*(?:qualifier_min|accessory_total|acc_total)\b", l)
         and "_aq." not in l and not l.lstrip().startswith("#")]
ok("I7 the engine never COMPARES against the threshold itself", not _HAND, _HAND[:2])
# A guard nobody can read is half a wiring. The owner has to be able to see what fell short.
ok("I8 the engine SURFACES the outcome in its result, not just in a local counter",
   'out["attach_qualifier"]' in ESRC)
ok("I9 and it emits it whenever a condition exists, even when every sale met it",
   '_aq_on and (_aq_guard["lines"] or _aq_guard["notes"])' in ESRC)
ok("I10 the per-rule breakdown carries the requirement in words, not just counts",
   '"requirement": _aq.describe(rule)' in ESRC)

# ===== J. THE SAVE PATH ROUND-TRIPS IT (or a plan save erases money policy) ======================
section("J. A declared qualifier survives the next plan save")
ROUTER = os.path.join(APP, "modules", "commcalc", "router.py")
RSRC = io.open(ROUTER, encoding="utf-8").read()
# The rule save path is DELETE-THEN-INSERT with an EXPLICIT column list -- its own comment says so. A
# qualifier column missing from it is not a cosmetic gap: the next plan save WIPES the owner's money
# policy and nothing reports it. That is 19.18 at its most expensive, so every column is locked here.
_QCOLS = ("qualifier_min", "qualifier_measure", "qualifier_scope", "qualifier_match_field",
          "qualifier_match_op", "qualifier_match_value", "qualifier_fallback_amount",
          "qualifier_label")
_MISSING = [c for c in _QCOLS if ('rules[-1]["%s"]' % c) not in RSRC]
ok("J1 every qualifier column is written back on a plan save", not _MISSING, _MISSING)
ok("J2 the save is guarded by a column probe, so a pre-migration database saves as before",
   "_rule_qualifier_cols_present" in RSRC)
ok("J3 the allowlists are dereferenced from the one home, not retyped in the router",
   "_aqm.MEASURES" in RSRC and "_aqm.SCOPES" in RSRC)
# No second copy of the vocabulary: the router must not spell the measures as its own tuple.
_VOCAB_RETYPED = [l for l in RSRC.splitlines()
                  if '"gp"' in l and '"count"' in l and '"amount"' in l]
ok("J4 the router spells no measure vocabulary of its own", not _VOCAB_RETYPED, _VOCAB_RETYPED[:1])
ok("J5 a zero minimum is stored as NULL, never as a second spelling of OFF",
   "_qmin is not None and _qmin > 0" in RSRC)

# ═════ K. THE SCREEN CAN DECLARE IT, AND CANNOT INVENT ITS OWN VOCABULARY ═══════════════════════
section("K. The plan editor can declare a qualifier, dereferenced from this home")
# WHY THIS SECTION EXISTS. PR #457 shipped the engine, the save path and the columns, and NO INPUT —
# so the only way to declare a qualifier was a hand-written UPDATE, which section J proves the next
# plan save would then wipe. A mechanism the owner cannot reach from the screen is 19.18 wearing a
# different hat: written, unwired, and reported as done.
FRONT = os.path.join(os.path.dirname(ROOT), "frontend", "src", "app", "(platform)", "commcalc")
EDITOR = os.path.join(FRONT, "commission-plans", "page.tsx")
PANEL = os.path.join(FRONT, "_lib", "QualifierEditor.tsx")
OPTIONS = os.path.join(APP, "modules", "commcalc", "plan_options.py")
def _read_or_blank(path):
    """A file that is NOT THERE must fail these checks BY NAME, not as a traceback: "the editor sends
    every qualifier column" reads as a red line an operator can act on, where a FileNotFoundError
    reads as a broken harness and gets re-run. Verified: against pre-fix main every K check below
    fails and none of them crash."""
    try:
        return io.open(path, encoding="utf-8").read()
    except OSError:
        return ""


ESRC_UI = _read_or_blank(EDITOR)
PSRC_UI = _read_or_blank(PANEL)
OSRC = _read_or_blank(OPTIONS)
ok("K0 the editor and its qualifier panel both exist",
   bool(ESRC_UI) and bool(PSRC_UI), [p for p, c in ((EDITOR, ESRC_UI), (PANEL, PSRC_UI)) if not c])

# K1-K2: the served vocabulary IS this module's, read at call time rather than copied.
from app.modules.commcalc import plan_options as _po  # noqa: E402
_V = _po.vocabulary()
ok("K1 the served measures are exactly this home's, in its order",
   [m["value"] for m in _V.get("qualifier_measures") or []] == list(aq.MEASURES))
# getattr with a sentinel, not attribute access: a home that does not DECLARE its defaults yet must
# fail this line by name rather than end the run with an AttributeError.
_DEF = {k: getattr(aq, "DEFAULT_" + k.upper(), None)
        for k in ("measure", "scope", "match_field", "match_op", "match_value")}
ok("K2 the served defaults are exactly this home's",
   all(v is not None for v in _DEF.values()) and _V.get("qualifier_defaults") == _DEF, _DEF)
ok("K3 the vocabulary dereferences the module rather than retyping the list",
   "_aq.MEASURES" in OSRC and "_aq.MEASURE_LABELS" in OSRC)
_OPT_RETYPED = [l for l in OSRC.splitlines()
                if '"gp"' in l and '"count"' in l and '"amount"' in l]
ok("K4 plan_options spells no measure vocabulary of its own", not _OPT_RETYPED, _OPT_RETYPED[:1])

# K5: the one fact every default is read from -- resolve() must not carry its own inline spelling,
# or the screen and the engine can disagree about what "which lines count" means when left blank.
_HOMESRC = io.open(HOME, encoding="utf-8").read()
_INLINE = [l.strip() for l in _HOMESRC.splitlines()
           if 'or "accessory"' in l or 'or "equals"' in l or 'or "yes"' in l]
ok("K5 the home reads its defaults from its own constants, not inline literals",
   not _INLINE, _INLINE[:1])

# K6: every column goes back on save FROM THE EDITOR too. The router writing them (J1) is useless if
# the browser never sends them: the save path only writes the keys the caller supplied.
_UI_MISSING = [c for c in _QCOLS if (c + ":") not in ESRC_UI]
ok("K6 the editor sends every qualifier column back on save", not _UI_MISSING, _UI_MISSING)
ok("K7 the editor sends a blank minimum as null, never as 0",
   "qualifier_min: qNum(" in ESRC_UI and "=== '' || v === null" in ESRC_UI)
ok("K8 clearing a qualifier clears every one of its columns",
   all((c + ":") in PSRC_UI for c in _QCOLS))

# K9-K10: the sentence has ONE renderer. The owner reads it as the rule, so a TypeScript copy of the
# same figures is a second home -- and the one that drifts, because nothing recomputes the stored one.
ok("K9 the plan GET derives the sentence from this home",
   "_aqm.describe(" in RSRC and '"qualifier_requirement"' in RSRC)


def _decomment(src):
    """Source with comments removed, so a QUOTED owner directive or a tooltip cannot trip a scan that
    is looking for generated prose. The first draft of K10 flagged the owner's own words, pasted in a
    header comment -- a detector that fires on English rather than on structure."""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", " ", l) for l in src.splitlines())


# The phrases are `describe`'s own signature, not ordinary help-text English: a screen containing one
# has rebuilt the sentence. Checked against the three renderers so a new one cannot be missed.
_SENTENCE_WORDS = ("is required to qualify", "short of the", "meeting the")
ok("K10a the phrase list still matches describe() (a drifted list locks nothing)",
   all(_w in _HOMESRC for _w in _SENTENCE_WORDS),
   [_w for _w in _SENTENCE_WORDS if _w not in _HOMESRC])
_UI_NO_COMMENTS = _decomment(PSRC_UI) + _decomment(ESRC_UI)
_REBUILT = [w for w in _SENTENCE_WORDS if w in _UI_NO_COMMENTS]
ok("K10 neither file rebuilds the sentence in the browser", not _REBUILT, _REBUILT)
ok("K11 the editor displays the served sentence instead",
   "qualifier_requirement" in PSRC_UI)

# K11: the browser names no measure of its own. A select hard-coding 'gp' would still save, and would
# silently stop offering a measure the day the engine gained one.
_UI_MEASURE_LITERAL = [l.strip() for l in (PSRC_UI + ESRC_UI).splitlines()
                       if ("'gp'" in l or '"gp"' in l) and "qualifier" in l.lower()]
ok("K12 the editor names no measure literal", not _UI_MEASURE_LITERAL, _UI_MEASURE_LITERAL[:1])
ok("K13 the fields are hidden when the vocabulary cannot be confirmed",
   "qualifier_measures" in PSRC_UI and "qualifier_defaults" in PSRC_UI
   and "qualifierVocabReady" in ESRC_UI)
# A stored condition is never hidden behind a collapsed panel: money policy must be visible on load.
ok("K14 a rule that already declares a qualifier is shown open",
   "qOpen[i] || hasQualifier(r)" in ESRC_UI)
# The one way this screen can dock pay by accident: a non-default match field with no value matches
# nothing, so every sale measures zero and takes the lesser rate. The editor must say so.
ok("K15 a non-default field with no value is warned about, not silently saved",
   "measures zero" in PSRC_UI
   and "qualifier_match_value || ''" in PSRC_UI.replace('"', "'"))

# ═════ Z. ARMED CONTROLS — each asserts a check above would really fail ══════════════════════════
section("Z. The checks are armed (each would catch its own defect)")
ok("Z1 B6 would catch a module that read 'unanswerable' as zero",
   aq.build_context(ALL_ROWS, [RULE], None, None)["by_rule"]["r1"]["predicate_unavailable"] is True)
ok("Z2 B9 would catch a '>' written as '>='",
   aq.verdict({"trans_id": "X"}, RULE,
              aq.build_context([{"trans_id": "X", "department": "Accessories", "ext_price": 24.99}],
                               [RULE], IS_ACC))["basis"] == aq.BASIS_FALLBACK)
ok("Z3 D5 would catch a mutation (the fixture rule is still $25)", RULE["amount"] == 25.0)
ok("Z4 E1 would catch a carrier name: the detector finds one when planted",
   "boost" in "a boost rule".lower())
_Z5 = "        if row_total >= rule['qualifier_min']:"
ok("Z5 I7's detector finds a hand-rolled threshold when planted",
   bool(re.search(r"(?:qualifier_min|minimum|accessory_total|acc_total)\b[^=]*(?:>=|<=|>|<)"
                  r"|(?:>=|<=|>|<)[^=]*(?:qualifier_min|accessory_total|acc_total)\b", _Z5))
   and "_aq." not in _Z5)
ok("Z6 C1 would catch 'no id' being treated as a transaction named ''",
   aq.txn_key({}) == "" and aq.verdict({}, RULE, C)["basis"] == aq.BASIS_UNMEASURED)

ok("Z7 K6 would catch a column the editor stopped sending",
   [c for c in _QCOLS if (c + ":") not in "qualifier_min: 1, qualifier_measure: 2"]
   == ["qualifier_scope", "qualifier_match_field", "qualifier_match_op", "qualifier_match_value",
       "qualifier_fallback_amount", "qualifier_label"])
ok("Z8 K10 would catch the sentence rebuilt in TypeScript",
   [w for w in _SENTENCE_WORDS
    if w in _decomment("const s = `${min} of accessories is required to qualify for ${full}`")]
   == ["is required to qualify"])
ok("Z9 K1 would catch a vocabulary that dropped a measure",
   [m["value"] for m in (_V.get("qualifier_measures") or [])[:-1]] != list(aq.MEASURES))

print("\n%d passed, %d failed" % (sum(_P), len(_P) - sum(_P)))
sys.exit(0 if all(_P) else 1)
