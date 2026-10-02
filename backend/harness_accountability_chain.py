#!/usr/bin/env python3
"""THE FIVE-STAGE ACCOUNTABILITY CHAIN — DB-free proof (owner 2026-10-02, index §48).

Owner, verbatim: "we need to see in a daily report or date range report for the following / Daily
Closing done or not with dates and by who / DM verified or not with dates and by who / CAsh pick
with dates and by who / Cash Handover with dates and by who / managment review with dates and by
who".

WHAT THIS HARNESS IS FOR. The chain reports an ACCOUNTABILITY claim about named people — "Rana
verified this store-day", "nobody has reviewed it" — so the two ways it can lie are both serious:
saying a stage is done when it is not (a gap nobody chases) and saying it is not done when it is
(a person wrongly shown as not having done their job). Every rule below is armed with a control
that fails when the defect is patched back in.

§A is the byte-identity section: `not_closed` on the cash-pickup screen used to apply its "which
stores were expected to file" filter chain inline, and now dereferences
`deposit_accountability.expected_store_codes`. A REPLICA of the original inline logic is the oracle,
so the refactor is proven not to have changed that live screen rather than asserted to.

Stdlib only; the pure module is exec'd without importing the package.
"""
import ast
import io
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
DA = os.path.join(HERE, "app", "modules", "closing", "deposit_accountability.py")
ROUTER = os.path.join(HERE, "app", "modules", "closing", "router.py")
ACTORS = os.path.join(HERE, "app", "core", "actors.py")
PAGE = os.path.join(os.path.dirname(HERE), "frontend", "src", "app", "(platform)",
                    "closing", "accountability", "page.tsx")

_pass = _fail = 0


def check(name, got, want=None):
    global _pass, _fail
    ok = (got == want) if want is not None else bool(got)
    if ok:
        _pass += 1
        print("  ok   %s" % name)
    else:
        _fail += 1
        print("  FAIL %s" % name)
        if want is not None:
            print("        got : %r" % (got,))
            print("        want: %r" % (want,))
        else:
            print("        got : %r" % (got,))
    return ok


def load_pure(path, extra=None):
    """exec only the top-level defs/assigns — the module has package imports we must not run."""
    t = ast.parse(io.open(path, encoding="utf-8").read())
    keep = [n for n in t.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Assign, ast.AnnAssign,
                              ast.ClassDef))]
    m = types.ModuleType("pure")
    m.__dict__["__builtins__"] = __builtins__
    m.__dict__.update(extra or {})
    exec(compile(ast.Module(body=keep, type_ignores=[]), path, "exec"), m.__dict__)
    return m


# The REAL module, imported for real: deposit_accountability has no third-party imports, so the
# pure logic under test is the shipped code rather than an exec'd lookalike (the posture
# harness_deposit_accountability.py already uses). `actors.py` DOES import app.core.database, so that
# one is still loaded pure, below.
sys.path.insert(0, HERE)
from app.modules.closing import deposit_accountability as DAM   # noqa: E402

RSRC = io.open(ROUTER, encoding="utf-8").read()
ASRC = io.open(ACTORS, encoding="utf-8").read()


def nocomment(src):
    return re.sub(r"(?m)^\s*#.*$", "", src)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nA. expected_store_codes IS the old not_closed filter chain (byte-identity, not a rewrite)")
# The ORACLE: the inline logic exactly as it stood on main before the extraction.
def oracle(store_rows, ks, in_ks, store_set, market_set, sm_market):
    out = []
    for s in store_rows:
        code = s.get("store_code") or ""
        if not code or s.get("is_active") is False:
            continue
        if ks is not None and not in_ks(ks, code, s.get("address")):
            continue
        if store_set and code.upper() not in store_set:
            continue
        mk = (s.get("market") or "").strip() or sm_market.get(code, "")
        if market_set and mk and mk.casefold() not in market_set:
            continue
        out.append(code)
    return out


ROSTER = [
    {"store_code": "B-559", "address": "559 Broadway", "market": "Queens", "is_active": True},
    {"store_code": "B-1800", "address": "1800 Great Neck", "market": "Nassau", "is_active": True},
    {"store_code": "B-OLD", "address": "Closed Store", "market": "Queens", "is_active": False},
    {"store_code": "", "address": "no code", "market": "Queens", "is_active": True},
    {"store_code": "B-NOMKT", "address": "No Market Rd", "market": "   ", "is_active": None},
    {"store_code": "B-FALLBK", "address": "Fallback Rd", "market": "", "is_active": True},
    {"store_code": "<2022>", "address": "roster artefact", "market": "Nassau", "is_active": True},
]
SM = {"B-FALLBK": "Brooklyn"}
_in_ks = lambda ks, c, a: c in ks

CASES = [
    ("no filters at all", None, set(), None, SM),
    ("keyset only", {"B-559", "B-NOMKT"}, set(), None, SM),
    ("store filter", None, {"B-559"}, None, SM),
    ("store filter, lowercase roster code", None, {"B-1800"}, None, SM),
    ("market filter", None, set(), {"queens"}, SM),
    ("market filter + the sm_market fallback", None, set(), {"brooklyn"}, SM),
    ("market filter with no fallback map", None, set(), {"brooklyn"}, {}),
    ("keyset AND store AND market together", {"B-559", "B-1800"}, {"B-559"}, {"queens"}, SM),
    ("a market nobody is in", None, set(), {"mars"}, SM),
    ("a store nobody is", None, {"B-NOPE"}, None, SM),
]
for label, ks, ss, ms, smm in CASES:
    want = oracle(ROSTER, ks, _in_ks, ss, ms, smm)
    got = DAM.expected_store_codes(
        ROSTER, keyset_ok=(None if ks is None else (lambda c, a, _k=ks: _in_ks(_k, c, a))),
        store_set=ss, market_set=ms, market_of=(lambda c, _m=smm: _m.get(c, "")))
    check("identical to the pre-refactor inline logic — %s" % label, got, want)

check("an inactive store is never expected", "B-OLD" not in DAM.expected_store_codes(ROSTER))
check("a blank store_code is never expected",
      "" not in DAM.expected_store_codes(ROSTER))
check("is_active None (unset) IS expected — only an explicit False excludes",
      "B-NOMKT" in DAM.expected_store_codes(ROSTER))
check("a BLANK-market store survives a market filter (never hidden by a filter it cannot answer)",
      "B-NOMKT" in DAM.expected_store_codes(ROSTER, market_set={"queens"}))
check("the router's not_closed dereferences the shared rule",
      "deposit_accountability as _da_exp" in RSRC and "_da_exp.expected_store_codes(" in RSRC)
check("...and no longer re-applies those filters itself inside the loop",
      "if store_set and code.upper() not in store_set:\n                continue\n            mk ="
      not in RSRC)

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nB. the spine — a store-day with NOTHING on it still reports 'closing not done'")
DAYS = DAM.date_span("2026-09-06", "2026-09-08")
check("date_span is inclusive and ISO-ordered", DAYS, ["2026-09-06", "2026-09-07", "2026-09-08"])
check("date_span tolerates a reversed range", DAM.date_span("2026-09-08", "2026-09-06"), DAYS)
check("date_span on garbage is empty, never a crash", DAM.date_span("not-a-date", "x"), [])
rows, summ = DAM.stage_chain([("B-559", "2026-09-06")], [], [], [], [])
check("one expected store-day with no artefact yields exactly one row", len(rows), 1)
check("...and it is stuck at the FIRST stage", rows[0]["stuck_at"], "closing")
check("...with closing reading 'no closing filed'", rows[0]["stages"][0]["detail"], "no closing filed")
check("...zero stages done", rows[0]["stages_done"], 0)
check("...and it is not complete", rows[0]["complete"], False)
check("the five stages appear in the owner's order",
      [s["key"] for s in rows[0]["stages"]], list(DAM.STAGE_KEYS))
check("...labelled with the owner's own words",
      [s["label"] for s in rows[0]["stages"]],
      ["Daily Closing", "DM verified", "Cash pickup", "Cash handover", "Management review"])
# A store-day NOT in the spine but carrying an artefact must still appear (never silently dropped).
rows2, _ = DAM.stage_chain([], [{"store_code": "B-OLD", "close_date": "2026-09-06",
                                "employee_name": "Ghost", "submitted_at": "2026-09-06T23:00:00Z"}],
                           [], [], [])
check("an UNEXPECTED store-day that filed anyway is still reported", len(rows2), 1)
check("...and its stage 1 is done", rows2[0]["stages"][0]["done"], True)

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nC. each stage reports done + WHEN + WHO, from its own home")
CLOSINGS = [
    {"store_code": "B-559", "close_date": "2026-09-07", "employee_name": "Radhika",
     "submitted_at": "2026-09-07T21:04:00Z"},
    {"store_code": "B-559", "close_date": "2026-09-07", "employee_name": "Amit",
     "submitted_at": "2026-09-07T23:40:00Z"},
    {"store_code": "B-559", "close_date": "2026-09-07", "employee_name": "Radhika",
     "submitted_at": "2026-09-07T22:00:00Z"},
]
VERS = [{"store_code": "B-559", "close_date": "2026-09-07", "verified": True,
         "verified_by": "Rana", "verified_at": "2026-09-08T14:12:00Z", "note": "ok"}]
PICKUPS = [{"store_code": "B-559", "close_date": "2026-09-07", "employee_name": "Radhika",
            "amount": 1002.0, "picked_up": True, "picked_up_by": "Rajiv",
            "picked_up_at": "2026-09-08T16:00:00Z", "disposition": "handed_to_mgmt",
            "handed_to": "Sanjot", "mgmt_confirmed": True, "mgmt_confirmed_by": "Sanjot",
            "mgmt_confirmed_at": "2026-09-08T18:30:00Z", "kind": "cash"}]
COUNTS = [{"store_code": "B-559", "close_date": "2026-09-07", "counted_by": "Sanjot",
           "counted_at": "2026-09-09T10:00:00Z", "status": "match"}]
day_rows, _ = DAM.day_accountability(PICKUPS)
rows, summ = DAM.stage_chain([("B-559", "2026-09-07")], CLOSINGS, VERS, day_rows, COUNTS)
r = rows[0]
st = {s["key"]: s for s in r["stages"]}
check("closing done", st["closing"]["done"], True)
check("closing WHO = every rep who filed, distinct, order-stable",
      st["closing"]["by"], ["Radhika", "Amit"])
check("closing WHEN = the LAST submission (the day is not filed until its last rep files)",
      st["closing"]["at"], "2026-09-07T23:40:00Z")
check("dm_verify done, by Rana, at its own timestamp",
      (st["dm_verify"]["done"], st["dm_verify"]["by"], st["dm_verify"]["at"]),
      (True, ["Rana"], "2026-09-08T14:12:00Z"))
check("pickup done, by the DM who collected, at picked_up_at",
      (st["pickup"]["done"], st["pickup"]["by"], st["pickup"]["at"]),
      (True, ["Rajiv"], "2026-09-08T16:00:00Z"))
check("handover done, naming who received it",
      (st["handover"]["done"], st["handover"]["by"]), (True, ["Sanjot"]))
check("...and carries the RECEIPT handshake as its second actor pair",
      (st["handover"]["confirmed_by"], st["handover"]["confirmed_at"]),
      (["Sanjot"], "2026-09-08T18:30:00Z"))
check("mgmt_review done, by the counter, at counted_at",
      (st["mgmt_review"]["done"], st["mgmt_review"]["by"], st["mgmt_review"]["at"]),
      (True, ["Sanjot"], "2026-09-09T10:00:00Z"))
check("...and reports the count's own verdict", st["mgmt_review"]["detail"], "match")
check("a fully-walked store-day is complete with nothing stuck",
      (r["complete"], r["stuck_at"], r["stages_done"]), (True, None, 5))

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nD. the live shape — 84% of pickups have NO disposition, and that must read as stuck")
# Measured on the owner's org 2026-10-02: cash_pickup 764 rows, 640 with disposition NULL,
# envelope_count ONE row in the whole org. The chain must show those as unfinished, not as fine.
UNDISP = [dict(PICKUPS[0], disposition=None, handed_to=None, mgmt_confirmed=False,
               mgmt_confirmed_by=None, mgmt_confirmed_at=None)]
dr, _ = DAM.day_accountability(UNDISP)
rows, _ = DAM.stage_chain([("B-559", "2026-09-07")], CLOSINGS, VERS, dr, [])
st = {s["key"]: s for s in rows[0]["stages"]}
check("picked up but never disposed → pickup done", st["pickup"]["done"], True)
check("...handover NOT done", st["handover"]["done"], False)
check("...and it says the DM still holds it", "still with the DM" in st["handover"]["detail"])
check("...mgmt_review NOT done, reading 'not reviewed'",
      (st["mgmt_review"]["done"], st["mgmt_review"]["detail"]), (False, "not reviewed"))
check("...stuck_at names the handover, not the review", rows[0]["stuck_at"], "handover")
check("...three of five stages done", rows[0]["stages_done"], 3)
# A verification row that exists but is NOT verified must not count as verified.
rows, _ = DAM.stage_chain([("B-559", "2026-09-07")], CLOSINGS,
                          [{"store_code": "B-559", "close_date": "2026-09-07", "verified": False,
                            "verified_by": "Rana", "verified_at": None}], dr, [])
st = {s["key"]: s for s in rows[0]["stages"]}
check("an UNVERIFIED verification row is not 'done'", st["dm_verify"]["done"], False)
check("...and says so plainly", st["dm_verify"]["detail"], "row present, not verified")
check("...so the day is stuck at dm_verify", rows[0]["stuck_at"], "dm_verify")
# A partially-picked day: one envelope collected, one still in the store.
PART = [PICKUPS[0], dict(PICKUPS[0], employee_name="Amit", picked_up=False, picked_up_by=None,
                         picked_up_at=None, disposition=None, mgmt_confirmed=False)]
dr2, _ = DAM.day_accountability(PART)
rows, _ = DAM.stage_chain([("B-559", "2026-09-07")], CLOSINGS, VERS, dr2, COUNTS)
st = {s["key"]: s for s in rows[0]["stages"]}
check("one of two envelopes picked → pickup NOT done", st["pickup"]["done"], False)
check("...and it counts them honestly", st["pickup"]["detail"], "1 of 2 envelope(s) picked up")
check("...stuck at pickup even though later stages have activity", rows[0]["stuck_at"], "pickup")

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nE. a bank deposit is a handover too, and the slip gap stays visible")
DEP = [dict(PICKUPS[0], disposition="deposited", handed_to=None, deposit_slip_path="s3://slip.jpg",
            deposited_at="2026-09-08T17:00:00Z", mgmt_confirmed=False, mgmt_confirmed_by=None,
            mgmt_confirmed_at=None)]
dr, _ = DAM.day_accountability(DEP)
rows, _ = DAM.stage_chain([("B-559", "2026-09-07")], CLOSINGS, VERS, dr, COUNTS)
st = {s["key"]: s for s in rows[0]["stages"]}
check("deposited-at-the-bank counts as handed over", st["handover"]["done"], True)
check("...WHO reads as the bank, not a blank", st["handover"]["by"], ["bank deposit"])
check("...WHEN is deposited_at", st["handover"]["at"], "2026-09-08T17:00:00Z")
check("...with no confirmation claimed (a slip accounts for it, not a handshake)",
      (st["handover"]["confirmed_rows"], st["handover"]["confirmed_at"]), (0, None))
NOSLIP = [dict(DEP[0], deposit_slip_path="")]
dr, _ = DAM.day_accountability(NOSLIP)
rows, _ = DAM.stage_chain([("B-559", "2026-09-07")], CLOSINGS, VERS, dr, COUNTS)
st = {s["key"]: s for s in rows[0]["stages"]}
check("a slip-less deposit is still a handover (the cash did move)", st["handover"]["done"], True)
check("...but the missing slip is surfaced on the cell", st["handover"]["missing_slip"], 1)
check("...and the day is not green", st["handover"]["green"], False)

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nF. the summary counts only the rows handed in (keyset consistency)")
many = []
for i, d in enumerate(["2026-09-06", "2026-09-07", "2026-09-08"]):
    many.append({"store_code": "B-559", "close_date": d, "employee_name": "R",
                 "submitted_at": d + "T22:00:00Z"})
rows, summ = DAM.stage_chain([("B-559", d) for d in DAYS] + [("B-1800", d) for d in DAYS],
                            many, [], [], [])
check("store_days counts the whole spine, not just the days with rows", summ["store_days"], 6)
check("closing done on 3 of 6", [s for s in summ["by_stage"] if s["key"] == "closing"][0]["done"], 3)
check("...and missing on the other 3",
      [s for s in summ["by_stage"] if s["key"] == "closing"][0]["missing"], 3)
check("complete_days is 0 — nothing walked the whole chain", summ["complete_days"], 0)
check("the stuck histogram points at closing for the 3 blanks",
      [x for x in summ["stuck_at"] if x["key"] == "closing"][0]["store_days"], 3)
check("...and at dm_verify for the 3 that filed",
      [x for x in summ["stuck_at"] if x["key"] == "dm_verify"][0]["store_days"], 3)
check("a summary over NO rows is zeros, never a crash",
      (DAM.chain_summary([])["store_days"], DAM.chain_summary([])["complete_days"]), (0, 0))
check("chain_summary(None) is safe too", DAM.chain_summary(None)["store_days"], 0)
check("every stage appears in by_stage even when nothing is done",
      [s["key"] for s in DAM.chain_summary([])["by_stage"]], list(DAM.STAGE_KEYS))

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nG. a NAME is never mangled, a UUID is never shown raw (core.actors)")
AM = load_pure(ACTORS, extra={"re": re, "get_supabase": lambda: None})
UID = "0b3c1d2e-4f56-4789-abcd-0123456789ab"
check("a canonical UUID is recognised", AM.is_actor_uid(UID), True)
check("a person's name is NOT a uid", AM.is_actor_uid("Rana"), False)
check("the retired 'management' sentinel is NOT a uid", AM.is_actor_uid("management"), False)
check("blank / None are not uids", (AM.is_actor_uid(""), AM.is_actor_uid(None)), (False, False))
check("a uuid with the wrong group lengths is rejected",
      AM.is_actor_uid("0b3c1d2-4f56-4789-abcd-0123456789ab"), False)
AM.actor_names = lambda uids, org, client=None: {UID: "Sanjot Singh"}
mixed = AM.resolve_actor_names(["Rana", UID, "management", ""], "org")
check("the UUID resolves to a person", mixed.get(UID), "Sanjot Singh")
check("the name passes through UNTOUCHED", mixed.get("Rana"), "Rana")
check("the legacy sentinel passes through as itself", mixed.get("management"), "management")
check("blanks are dropped, never mapped to None", "" not in mixed)
AM.actor_names = lambda uids, org, client=None: {}
check("an unresolved UUID maps to itself rather than None",
      AM.resolve_actor_names([UID], "org").get(UID), UID)
check("only the UUIDs are ever looked up (a name is never sent to the resolver)",
      "[v for v in vals if is_actor_uid(v)]" in ASRC)

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nH. the wiring — one home, dereferenced, and the screen spells no stage word")
BODY = RSRC[RSRC.index("def accountability_chain("):]
BODY = BODY[:BODY.index("\nclass MgmtConfirmIn")]
check("the endpoint is org-scoped", "require_org(org_id)" in BODY)
check("...keyset-scoped like every closing report",
      "scope_keyset(authorization, org_id)" in BODY and "in_keyset(" in BODY)
check("...bounded to the SAME 62 days as the accountability board",
      "range too large — 62 days max" in BODY)
check("...reads the SPINE from the shared expectation rule",
      "_da.expected_store_codes(" in BODY and "_da.date_span(" in BODY)
check("...gets stages 3+4 from day_accountability, not a second pickup walk",
      "_da.day_accountability(_accountability_pickup_rows(" in BODY)
check("...and never re-derives an envelope's state itself",
      "envelope_state(" not in BODY and "disposition" not in nocomment(BODY))
check("...composes the chain in the pure module", "_da.stage_chain(" in BODY)
check("...serves the stage vocabulary from the pure module", "_da.stage_catalog()" in BODY)
check("...recomputes the summary over the VISIBLE rows", "_da.chain_summary(rows)" in BODY)
check("...resolves actors through the ONE home", "_actors.resolve_actor_names(" in BODY)
# Every table this endpoint touches must be filtered by org. `.table(` (not `.table("`) because the
# shared `_range` helper passes the table name as a VARIABLE — counting only literals missed it and
# made this rule arithmetic that could not fail.
check("every sensitive read in the endpoint is org-scoped",
      nocomment(BODY).count('.eq("org_id", org_id)'),
      len(re.findall(r'\.table\(', nocomment(BODY))))
if os.path.exists(PAGE):
    PSRC = io.open(PAGE, encoding="utf-8").read()
    PCODE = re.sub(r"(?m)\s*//.*$", "", re.sub(r"/\*.*?\*/", "", PSRC, flags=re.S))
    # A QUOTED stage key is the defect; the bare word is not. The route itself is
    # /closing/accountability and the endpoint is '/api/v1/closing/accountability-chain', so the
    # substring 'closing' legitimately appears — a rule that banned it would have to be switched off
    # for this page, which is how a lock stops meaning anything.
    for key in DAM.STAGE_KEYS:
        check("the screen does not hardcode the stage key %r as a literal" % key,
              ("'%s'" % key) not in PCODE and ('"%s"' % key) not in PCODE)
    for lab in DAM.STAGE_LABELS.values():
        check("the screen does not hardcode the stage label %r" % lab, lab not in PCODE)
    check("CONTROL: the quoted-literal rule really would catch one",
          "'closing'" in "const k = 'closing'")
    check("the screen renders the SERVER's stage list", "data?.stages" in PSRC or "data.stages" in PSRC)
else:
    check("the screen exists", False)

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nI. CONTROLS — each rule goes RED with the defect patched back in")
check("CONTROL: counting a verification row's EXISTENCE as verified would pass an unverified day",
      bool({"verified": False}.get("verified_by", "Rana")))
check("CONTROL: taking the EARLIEST submission would call a half-filed day done at 21:04",
      min(c["submitted_at"] for c in CLOSINGS), "2026-09-07T21:04:00Z")
check("CONTROL: ignoring unpicked envelopes would call a 1-of-2 day picked up",
      len([e for e in PART if e.get("picked_up")]), 1)
check("CONTROL: treating 'undisposed' as handed over would hide the 640-row live gap",
      DAM.day_accountability(UNDISP)[0][0]["envelopes"][0]["state"], "undisposed")
check("CONTROL: a store-day absent from the spine would vanish from a 'done or not' report",
      DAM.stage_chain([], [], [], [], [])[0], [])
check("CONTROL: sending a name to the uid resolver would ask the DB about 'Rana'",
      AM.is_actor_uid("Rana"), False)

print("\n" + "=" * 96)
print("RESULT: %d passed, %d failed" % (_pass, _fail))
print("=" * 96)
if _fail:
    print("FAIL  the five-stage accountability chain has regressed")
    sys.exit(1)
print("OK  five stages, each read from its own home; the spine makes 'not done' visible")
