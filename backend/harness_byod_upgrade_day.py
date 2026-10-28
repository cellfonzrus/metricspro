"""
PROOF — A BYOD ACTIVATION AND AN UPGRADE ON THE SAME NUMBER, SAME STORE, SAME DAY
(owner ask 2026-10-28, index §COMMCALC-43).

Owner, verbatim: *"Need to create a new flag report to check if a byod and an upgrade is done on the
same day for the same phone number at the same store"*.

DB-free. Drives the REAL `commcalc/byod_upgrade_day.py` over fixtures shaped like the live
`commcalc.daily_sales_feed`, and locks the design decisions that measurement forced:

 A. THE EVENT IS THE ONE HOME, AND ONE LINE IS NOT A PAIR. `line_class.activation_events` already
    owns "what is one activation, what phone line is it on, and what class is it"; its house
    precedence is byod > upgrade, so a single line whose contract type reads 'BYOD Upgrade' resolves
    to ONE byod event. THE REGRESSION: a naive token test would see both words on that one line and
    report a pair where there is one sale.

 B. THE PAIR IS A PAIR OF EVENTS on one (store, day, number). Two byod events are not a pair; a
    different store, a different day or a different number is not a pair.

 C. THE PHONE KEY IS NOT OURS. The number comes from `line_class.line_event_keys` →
    `pos/customer_identity.norm_phone`, so a dialled 1, dashes and brackets are one number and this
    module never writes a second normaliser. An event the one home keyed on a DEVICE carries no
    number and cannot be paired — counted and reported, never assumed clean.

 D. CONFIG, NOT CODE (RULE TWO). Which classes make a pair, and whether the two must be on separate
    receipts, come through `flag_registry.rule_params`; the tenant's `enabled` switch is honoured.

 E. EVIDENCE, NOT A VERDICT. No function returns a judgement, and no user-facing string in the module
    calls anything fraud, abuse or a scheme. A number legitimately activated on a brought device and
    then upgraded the same day reads identically to the thing the owner is worried about.

 F. THE GAPS ARE STATED. A tenant whose events are device-keyed reads "not measured", never "none
    found" — the §19.26 rule. An undeclared box-department set leaves the handset signals None.

 G. ONE HOME, DEREFERENCED. An AST scan asserts the module spells no severity, area or label of its
    own, writes no phone normaliser, carries no class tokens, and never defaults the box set.

 H. THE LIVE SHAPE, as the 95 measured Cellfonz cases carry it: a BYOD activation with no handset
    line, then a SEPARATE receipt the same day upgrading the same number onto a promo device.

Run: `cd backend && python3 harness_byod_upgrade_day.py`
"""
import ast
import os
import sys

sys.path.insert(0, ".")

from harnesslib import py_code_only                             # noqa: E402
from app.modules.commcalc import byod_upgrade_day as M          # noqa: E402
from app.modules.commcalc import flag_registry as REG           # noqa: E402
from app.modules.commcalc import line_class as LC               # noqa: E402

HOME_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "app", "modules", "commcalc", "byod_upgrade_day.py")
SRC = open(HOME_PATH).read()
# Locks read CODE, not comments: this module's docstring quotes the vocabulary it must NOT carry
# (the contract-type words), so scanning raw source would fail on a correct file. Same trap
# harnesslib.py documents and harness_multi_handset_day dereferences.
CODE = py_code_only(SRC)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"   [{detail}]" if detail and not cond else ""))


def section(t):
    print(f"\n── {t} " + "─" * max(0, 92 - len(t)))


BOX = {"Android - XP", "IPHONE - XP", "TABLET - XP"}
RULES = LC.HOUSE_RULES
PARAMS = REG.default_params(M.FLAG_TYPE)


def row(**kw):
    """One sales line shaped like `commcalc.daily_sales_feed`."""
    base = {"store": "1 S 60th street", "trans_date": "2026-10-02", "trans_id": "215344",
            "mdn": "2674832829", "contract_type": "BYOD", "product_desc": "Autopay",
            "department": "", "category": "", "serial_1": "", "ext_price": 0.0,
            "salesperson": "Jaladhi, Akhila", "voided": "", "trans_type": "Sale"}
    base.update(kw)
    return base


def byod_side(**kw):
    """The BYOD activation as the live feed carries it: plan + autopay, no handset line."""
    k = {"trans_id": "215344", "contract_type": "BYOD", **kw}
    return [row(product_desc="Unlimited Data, Talk & Text + 30GB", **k),
            row(product_desc="Autopay", **k)]


def upgrade_side(**kw):
    """The upgrade on a SECOND receipt the same day: plan lines plus a promo handset and a fee."""
    k = {"trans_id": "215350", "contract_type": "Upgrade", **kw}
    return [row(product_desc="Unlimited+ $50 Unlimited Data, Talk", **k),
            row(product_desc="Autopay", **k),
            row(product_desc="moto g play - 2026 Q4 Promo Upgrade", department="Android - XP",
                ext_price=0.99, **k),
            row(product_desc="Device Setup Charge Upgrade (non-refundable)", ext_price=35.0, **k)]


def found(rows, params=None, **kw):
    """(grouped, cases) over the REAL module. `is_box` rides to `cases`, which is where the
    handset predicate is injected — `number_days` only folds events."""
    g = M.number_days(rows, RULES)
    return g, M.cases(g, params or PARAMS, **kw)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. the EVENT is the one home, and one line is not a pair")

check("A1 the detector reads the one home for an activation event",
      "activation_events" in CODE)
check("A2 a BYOD activation and an upgrade on one number-day resolve to TWO events",
      len(M.number_days(byod_side() + upgrade_side(), RULES)["groups"]
          [("1 S 60th street", "2026-10-02", "2674832829")]) == 2)

# THE REGRESSION. 46 live lines read 'BYOD Upgrade'. `activation_class` precedence is byod > upgrade,
# so that line is ONE byod event — a token test seeing both words would invent a pair.
ONE_LINE = [row(contract_type="BYOD Upgrade", trans_id="900",
                product_desc="moto g play", department="Android - XP", ext_price=0.99)]
check("A3 REGRESSION a single 'BYOD Upgrade' line is ONE byod event, not a byod + an upgrade",
      LC.activation_class(ONE_LINE[0], RULES) == "byod"
      and found(ONE_LINE)[1] == [])
check("A4 REGRESSION a whole receipt of 'BYOD Upgrade' lines is still not a pair",
      found([row(contract_type="BYOD Upgrade", trans_id="900", product_desc=p)
             for p in ("Autopay", "Unlimited+ $50", "moto g play")])[1] == [])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. the pair is a pair of EVENTS on ONE (store, day, number)")

_, CASES = found(byod_side() + upgrade_side())
check("B1 a byod event and an upgrade event on one number-day IS a case", len(CASES) == 1)
check("B2 the case names the store, the day and the number",
      CASES and CASES[0]["store"] == "1 S 60th street"
      and CASES[0]["day"] == "2026-10-02" and CASES[0]["number"] == "2674832829")
check("B3 two BYOD events on one number-day are NOT a case",
      found(byod_side() + byod_side(trans_id="215399"))[1] == [])
check("B4 two upgrade events on one number-day are NOT a case",
      found(upgrade_side() + upgrade_side(trans_id="215399"))[1] == [])
check("B5 a different STORE is not a case",
      found(byod_side() + upgrade_side(store="6149 Woodland Ave"))[1] == [])
check("B6 a different DAY is not a case",
      found(byod_side() + upgrade_side(trans_date="2026-10-03"))[1] == [])
check("B7 a different NUMBER is not a case",
      found(byod_side() + upgrade_side(mdn="2674832830"))[1] == [])
check("B8 a voided line is not a sale — a voided upgrade leaves no pair",
      found(byod_side() + [dict(r, voided="true") for r in upgrade_side()])[1] == [])
check("B9 a RETURN is not a sale either",
      found(byod_side() + [dict(r, trans_type="Return") for r in upgrade_side()])[1] == [])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. the phone key is NOT ours")

check("C1 a dialled 1, dashes and brackets are ONE number — the shared normaliser, not a new one",
      len(found(byod_side(mdn="(267) 483-2829") + upgrade_side(mdn="1-267-483-2829"))[1]) == 1)
NO_NUM = byod_side(mdn="") + upgrade_side(mdn="")
g_nonum, c_nonum = found(NO_NUM)
check("C2 an event the one home could not key on a number cannot be paired",
      c_nonum == [])
check("C3 and it is COUNTED, so the screen can say what it could not look at",
      M.summary(g_nonum, c_nonum)["events_without_number"] > 0)
# A device-shaped tracking number is the live LuxeLink shape: 93.9% of that tenant's events key on
# the device, so this detector can measure almost nothing there and must say so.
DEV = (byod_side(mdn="", serial_1="356938035643809")
       + upgrade_side(mdn="", serial_1="356938035643810"))
g_dev, c_dev = found(DEV)
check("C4 a device-keyed event is not a number and is not paired",
      c_dev == [])
check("C5 measurable coverage is reported as a SHARE, so 'nothing found' can be told from "
      "'nothing looked at'",
      M.summary(g_dev, c_dev)["numbered_share"] == 0.0)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. config, not code (RULE TWO)")

check("D1 the type is registered, with its area, label and severity owned by the registry",
      REG.area_of(M.FLAG_TYPE) == "fraud" and REG.label_of(M.FLAG_TYPE)
      and REG.severity_for(M.FLAG_TYPE))
check("D2 the house pair is byod + upgrade, declared in the registry and not in this module",
      sorted(PARAMS["classes"]) == ["byod", "upgrade"])
check("D3 a tenant can watch a DIFFERENT pair with a config row and no code change",
      len(M.cases(M.number_days(byod_side() + [dict(r, contract_type="Port-In")
                                               for r in upgrade_side()], RULES),
                  REG.rule_params(M.FLAG_TYPE, [{"flag_type": M.FLAG_TYPE,
                                                 "params": {"classes": ["byod", "port"]}}]))) == 1)
check("D4 a config row with fewer than two classes is read as 'we do not watch for this', not as "
      "'flag everything'",
      M.cases(M.number_days(byod_side() + upgrade_side(), RULES), {"classes": ["byod"]}) == []
      and M.cases(M.number_days(byod_side() + upgrade_side(), RULES), {"classes": []}) == []
      and M.cases(M.number_days(byod_side() + upgrade_side(), RULES), {}) == [])
check("D5 a THREE-class row needs all three present, not any two",
      M.cases(M.number_days(byod_side() + upgrade_side(), RULES),
              {"classes": ["byod", "upgrade", "port"]}) == []
      and len(M.cases(M.number_days(
          byod_side() + upgrade_side()
          + [dict(r, contract_type="Port-In", trans_id="99") for r in upgrade_side()], RULES),
          {"classes": ["byod", "upgrade", "port"]})) == 1)
check("D6 the tenant's switch is honoured — off means no findings AND says so",
      M.findings(byod_side() + upgrade_side(), RULES,
                 rule_rows=[{"flag_type": M.FLAG_TYPE, "enabled": False}])[0] == []
      and M.findings(byod_side() + upgrade_side(), RULES,
                     rule_rows=[{"flag_type": M.FLAG_TYPE,
                                 "enabled": False}])[1]["enabled"] is False)
check("D7 NO class word is typed in the module's own code — every field name is derived from the "
      "config, so an edited row cannot be silently ignored",
      not any(t in CODE for t in ('"byod"', "'byod'", '"upgrade"', "'upgrade'")))
# THE KNOB THAT CANNOT EXIST, locked so it is not added back. A "must be on separate receipts"
# setting was written and removed on 2026-10-28: mutation-testing this file showed it could never
# change an outcome, because `activation_events` keys an event on (invoice, phone line), so two
# activations of one number on one receipt are ONE event and can never be a pair.
check("D8 two activations of ONE number on ONE receipt are ONE event, so a same-receipt pair is "
      "structurally impossible and no config key may pretend to control it",
      found(byod_side(trans_id="777") + upgrade_side(trans_id="777"))[1] == []
      and "separate_receipts_only" not in CODE
      and "separate_receipts_only" not in REG.default_params(M.FLAG_TYPE))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. evidence, never a verdict")

FORBIDDEN = ("fraud", "fraudulent", "abuse", "scheme", "stealing", "theft", "cheat", "scam",
             "guilty", "gaming")
# USER-FACING strings only: every string literal that is not a docstring and is long enough to be a
# sentence. The module docstring QUOTES its own measurement and names the registry's `fraud` area,
# neither of which can reach a screen, so scanning it would fail a correct file — the same exclusion
# harness_multi_handset_day §E makes, dereferenced rather than re-reasoned.
_T0 = ast.parse(SRC)
_docs = {d for n in ast.walk(_T0)
         if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
         for d in [ast.get_docstring(n, clean=False)] if d}
_user_facing = [n.value for n in ast.walk(_T0)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value not in _docs and len(n.value) > 12]
_bad = [v[:48] for v in _user_facing if any(w in v.lower() for w in FORBIDDEN)]
check("E1 no user-facing string in the module calls anything fraud", _bad == [], str(_bad))
check("E2 no function is named like a verdict",
      not any(n.name.startswith(("is_fraud", "verdict", "judge"))
              for n in ast.walk(ast.parse(SRC)) if isinstance(n, ast.FunctionDef)))
check("E3 the case carries no verdict field",
      CASES and not any(k in CASES[0] for k in ("fraud", "verdict", "guilty", "conclusion")))
check("E4 the sentence a manager reads describes the sale and asks for a check",
      CASES and "2674832829" in M.describe(CASES[0])
      and all(w not in M.coaching_note(CASES[0]).lower() for w in FORBIDDEN))
# DECLARED, never bounded. `len(...) >= 3` was the first draft and `harness_subject_set_lock` was
# right to refuse it: a lower bound lets a stated limit be DELETED in silence, and these four
# sentences are the promises the screen makes to a manager about what it did not measure. Asserted as
# the exact set, so a limit that leaves names itself in the failure.
EXPECTED_CANNOT_ANSWER = {
    "Groups the activations on one phone line at one store on one day, so it cannot see the same "
    "line activated across different days, at different stores, or recorded under a different "
    "number.",
    "A sale whose activation lines carry no phone number of their own cannot be grouped to a line "
    "at all. Those are counted separately and reported, and a company whose feed never carries the "
    "number has nothing measured here rather than nothing to find.",
    "Which kind of activation a line is comes from the company's own declared vocabulary. A sale "
    "the company has not taught the platform to recognise is not classified, so it joins no pair.",
    "It says what was rung on one day, not whether it was right. A line activated on a phone the "
    "customer brought and upgraded onto a new handset the same day is an ordinary sale and looks "
    "exactly like this.",
    "The chargeback amount is the carrier money already recorded against that handset, not a "
    "charge anybody has made. A rebate usually arrives months after the sale, so a case on a recent "
    "day shows no amount because none has arrived yet, never because nothing is at stake.",
}
check("E5 the limits the check CANNOT answer are EXACTLY the four declared — stated on the module, "
      "not left to be discovered, and not removable in silence",
      set(M.KEY_CANNOT_ANSWER) == EXPECTED_CANNOT_ANSWER,
      str(sorted(set(M.KEY_CANNOT_ANSWER) ^ EXPECTED_CANNOT_ANSWER))[:200])
check("E5b and the screen is handed them, so they are not a comment nobody reads",
      list(M.summary(*found(byod_side() + upgrade_side()))["cannot_answer"])
      == list(M.KEY_CANNOT_ANSWER))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. the gaps are stated — a measured zero is not an unmeasured one")

def _s(v):
    return str(v or "").strip()


def _BOX_PRED(r):
    return _s(r.get("department")) in BOX


g_box, c_box = found(byod_side() + upgrade_side(), is_box=_BOX_PRED)
check("F1 with a box predicate the handset counts are NUMBERS",
      c_box and c_box[0]["second_handsets"] == 1 and c_box[0]["first_handsets"] == 0)
check("F1b and the same figures are reachable by the CLASS name a column header wants",
      c_box and c_box[0]["upgrade_handsets"] == c_box[0]["second_handsets"]
      and c_box[0]["byod_handsets"] == c_box[0]["first_handsets"])
check("F1c the positional names are what the module's own logic reads, so a tenant who declares a "
      "different pair keeps the signal and the sentence",
      len(M.cases(M.number_days(byod_side() + [dict(r, contract_type="Port-In")
                                               for r in upgrade_side()], RULES),
                  {**PARAMS, "classes": ["byod", "port"]},
                  is_box=lambda r: str(r.get("department") or "").strip() in BOX)[0]
          ["second_devices"]) == 1)
check("F2 WITHOUT one they are None — not measured, which is not zero",
      CASES and CASES[0]["second_handsets"] is None
      and CASES[0]["first_handsets"] is None)
check("F3 the summary withholds the derived signal rather than reporting a false 0",
      M.summary(*found(byod_side() + upgrade_side()))["cases_upgrade_device_only"] is None
      and M.summary(g_box, c_box)["cases_upgrade_device_only"] == 1)
check("F4 an empty feed reports zero cases AND zero events, so a screen can tell the difference",
      M.summary(*found([]))["events"] == 0)
# THE FAKE ZERO THAT IS REACHABLE IN PRODUCTION. `router._accessory_config` falls back to the house
# department labels when an org has declared none (mig 218, to keep the house box numbers
# byte-identical), so a tenant whose POS spells departments differently is handed a box set that
# matches nothing — and every handset column then reads a confident 0 instead of "we cannot tell".
# So the fit of the vocabulary is MEASURED, not trusted.
_FOREIGN = lambda r: _s(r.get("department")) in {"Handset", "BrandedHandset"}       # noqa: E731
_g_f, _c_f = found(byod_side() + upgrade_side(), is_box=_FOREIGN)
check("F5 a box vocabulary that matches NO line in the period is reported as unmatched, not as "
      "zero handsets sold",
      M.summary(_g_f, _c_f, is_box=_FOREIGN)["handset_lines"] == 0
      and M.summary(_g_f, _c_f, is_box=_FOREIGN)["box_vocabulary_matched"] is False)
check("F6 a vocabulary that DOES fit says so, and counts what it matched",
      M.summary(g_box, c_box, is_box=_BOX_PRED)["box_vocabulary_matched"] is True
      and M.summary(g_box, c_box, is_box=_BOX_PRED)["handset_lines"] == 1)
check("F7 with no predicate at all both are None — not measured, which is neither fit nor unfit",
      M.summary(*found(byod_side() + upgrade_side()))["box_vocabulary_matched"] is None
      and M.summary(*found(byod_side() + upgrade_side()))["handset_lines"] is None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. one home, dereferenced")

_tree = ast.parse(SRC)
_assigned = {t.id for n in ast.walk(_tree) if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name)}
check("G1 the module declares no severity, area or label of its own",
      not ({"SEVERITY", "AREA", "LABEL", "SEV"} & _assigned))
check("G2 it owns its flag SOURCE, so the commission pass cannot retire its findings",
      M.SOURCE and M.SOURCE != "sales")
check("G3 it writes no phone normaliser of its own",
      not any(n.name in ("norm_phone", "_norm_phone", "norm_mdn", "_norm_mdn", "_digits")
              for n in ast.walk(_tree) if isinstance(n, ast.FunctionDef)))
check("G4 it reads no config and does no I/O — every fact is injected",
      "supabase" not in CODE and "client" not in CODE and "accessory_config" not in CODE)
check("G5 the box-department predicate is INJECTED and never defaulted",
      "is_box" in CODE and "box_departments" not in CODE)
check("G6 the flag row shape is the persist layer's, not one of its own",
      M.findings(byod_side() + upgrade_side(), RULES, org_id="o", period="October 2026")[0][0]
      .keys() >= {"org_id", "period", "flag_type", "source", "store_address",
                  "transaction_date", "description"})

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. the live shape — the 95 measured Cellfonz cases")

g_live, c_live = found(byod_side() + upgrade_side(),
                       is_box=lambda r: str(r.get("department") or "").strip() in BOX)
C = c_live[0] if c_live else {}
check("H1 ONE case", len(c_live) == 1)
check("H2 the two events are on SEPARATE receipts — structurally, for EVERY case — and the case "
      "says so rather than leaving a reader to work out why",
      C.get("separate_receipts") is True and C.get("receipt_count") == 2
      and all(c["separate_receipts"] and c["receipt_count"] >= 2 for c in c_live))
check("H3 the BYOD side carried no handset and the upgrade side carried one — the live discriminator",
      C.get("first_handsets") == 0 and C.get("second_handsets") == 1)
check("H4 the promo device on the upgrade is named, so a manager can see what was given out",
      any("moto g play" in (d or "") for d in C.get("second_devices") or []))
check("H5 the dollars on the flagged lines are totalled", round(C.get("dollars") or 0, 2) == 35.99)
check("H6 the rep is carried", C.get("reps") == ["Jaladhi, Akhila"])
check("H7 every line is listed for the drill-down, with its receipt and class",
      len(C.get("lines") or []) == 6
      and {l["cls"] for l in C["lines"]} == {"byod", "upgrade"}
      and all(l["receipt"] for l in C["lines"]))
check("H8 cases are ordered newest day first, then store, so the list is stable between runs",
      [c["day"] for c in M.cases(M.number_days(
          byod_side() + upgrade_side()
          + byod_side(trans_date="2026-10-09", trans_id="a", mdn="2674576093")
          + upgrade_side(trans_date="2026-10-09", trans_id="b", mdn="2674576093"), RULES), PARAMS)]
      == ["2026-10-09", "2026-10-02"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n" + "═" * 100)
print(f"  {len(PASS)} ok, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print(f"    FAIL {f}")
print("═" * 100)
sys.exit(1 if FAIL else 0)
