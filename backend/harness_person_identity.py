#!/usr/bin/env python3
"""WHO IS THIS PERSON — DB-free proof of the one home (owner report 2026-10-14, index §CORE-08).

Owner, verbatim: *"why does it show 2 reps who worked at the store, it should only show the actual
person who worked for all reporting not the one with the home store"*.

THE DEFECT, measured on live production data 2026-10-14. B-1750 on 2026-10-10 had ONE man in it.
`storeops.timelog` holds his punch as `Simarjyot Singh` (11.77h); the B2B sales feed holds the same
day's sales under `salesperson = "Singh, Simarjyot"`. `_who_worked_by_store` unioned the two NAME
STRINGS, so the report listed "Simarjyot Singh, Singh, Simarjyot" — two reps, one human.

THE CLASS. A name string is not an identity, and this repo answered "is this the same human" in SIX
places. §F below is the lock: it fails the build if a seventh appears, or if any of the six stops
dereferencing the home.

WHAT THIS HARNESS IS FOR. The home now decides who gets paid (`commission_engine._canon_person` is
read ~30 times inside commcalc) as well as who shows on a report, so the two ways it can lie are both
serious: merging two real people (one person's money or absence attributed to another) and splitting
one person in two (the defect above). §A is therefore a BYTE-IDENTITY section — replicas of the five
rules exactly as they stood on main are the oracle, so the move is PROVEN not to have changed any
caller's answer rather than asserted to. Every behavioural rule carries a control that fails when the
defect is patched back in (§G).

Stdlib only; the pure module is imported for real (it imports nothing of ours).
"""
import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PI = os.path.join(HERE, "app", "modules", "core", "person_identity.py")
ROUTER = os.path.join(HERE, "app", "modules", "closing", "router.py")
FRONT = os.path.join(os.path.dirname(HERE), "frontend", "src", "app", "(platform)", "closing")
WIRED = {
    "commission_engine": os.path.join(HERE, "app", "modules", "commcalc", "commission_engine.py"),
    "management_incentive": os.path.join(HERE, "app", "modules", "commcalc", "management_incentive.py"),
    "targets_engine": os.path.join(HERE, "app", "modules", "commcalc", "targets_engine.py"),
    "closing_router": ROUTER,
    "ops_chargebacks": os.path.join(HERE, "app", "modules", "closing", "ops_chargebacks.py"),
    "commission_drilldown": os.path.join(HERE, "app", "modules", "commcalc", "commission_drilldown.py"),
}

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
        print("        got : %r" % (got,))
        if want is not None:
            print("        want: %r" % (want,))
    return ok


sys.path.insert(0, HERE)
from app.modules.core import person_identity as PI_MOD   # noqa: E402

RSRC = io.open(ROUTER, encoding="utf-8").read()


def src(path):
    return io.open(path, encoding="utf-8").read()


def nocomment(s):
    return re.sub(r"(?m)^\s*#.*$", "", s)


def fn_src(source, name):
    """The source of one top-level function, docstring stripped — what the body actually DOES."""
    t = ast.parse(source)
    for n in ast.walk(t):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            body = [b for b in n.body
                    if not (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant)
                            and isinstance(b.value.value, str))]
            return "\n".join(ast.unparse(b) for b in body)
    return ""


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nA. THE MOVE CHANGED NOTHING — byte-identity against the five rules as they stood on main")

# ORACLES: the bodies exactly as main ran them, before this PR.
def oracle_canon_person(s):                                     # commission_engine._canon_person
    folded = re.sub(r"\s+", " ", ("" if s is None else str(s)).strip().casefold())
    if folded.count(",") == 1:
        last, first = (x.strip() for x in folded.split(","))
        if last and first:
            return f"{first} {last}"
    return folded


def oracle_mgmt_canon_person(name):                             # management_incentive._canon_person
    raw = str(name or "").strip()
    if not raw:
        return ""
    if "," in raw:
        last, _, first = raw.partition(",")
        raw = f"{first.strip()} {last.strip()}"
    return " ".join(raw.lower().split())


def oracle_name_key(name):                                      # targets_engine.name_key
    toks = re.sub(r"[^A-Z0-9 ]+", " ", str(name or "").upper()).split()
    return " ".join(sorted(toks))


def oracle_name_match(a, b):                                    # closing/router._name_match
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return a.split()[0] == b.split()[0]


def oracle_tok(s):                                              # commission_drilldown._tok
    return set(re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).split())


def oracle_fold_person(v):                                      # payroll_identity._fold_person
    from app.modules.account.store_identity import fold_accents
    return "".join(ch for ch in fold_accents(v).lower() if ch.isalnum())


def oracle_name_tokens(v):                                      # payroll_identity.name_tokens
    return {t for t in (oracle_fold_person(x) for x in re.split(r"[,\s]+", str(v or ""))) if t}


def oracle_folded_token_key(v):                                 # payroll_identity.person_key
    return "|".join(sorted(oracle_name_tokens(v)))


# The corpus: every live spelling shape this platform actually holds (house + LuxeLink feeds,
# storeops.timelog, storeops.shifts, storeops.employees and the B2B salesperson column, read
# 2026-10-14), plus the degenerate inputs every one of these functions is reachable with.
CORPUS = [
    None, "", "   ", ",", ",,", " , ", "a", "A",
    "Simarjyot Singh", "Singh, Simarjyot", "simarjyot  singh", " SINGH ,  SIMARJYOT ",
    "Angie Ramos", "Ramos, Angie", "Angelica Escobar", "Abdul K", "Abdul Kakar",
    "Akber Awais", "Amir Khan", "Amir", "Ariful Islam Khan", "Islam Khan, Ariful",
    "Alejandro Galarza", "Alondra Navarro", "Angelica Chacon", "Anthony Castellanos",
    "Antonio Chavez", "Brendaliz Aviles", "Carolina Espinoza", "Cartos Vega",
    "Christopher Romero", "Daniel Udhai", "David", "David Caba", "Diana Antunez",
    "Antunez, Diana", "KELLIE MARK", "Kellie, Mark", "Natasha Cabrera",
    "Natasha Nicole Cabrera", "Khan, Ariful, Jr", "O'Brien, Sean", "Jean-Luc Picard",
    "  Mary   Jane  Watson ", "josé álvarez", "JOSÉ ÁLVAREZ", "R2D2", "rep 12",
]

check("person_key is byte-identical to commission_engine._canon_person over the live corpus",
      [PI_MOD.person_key(n) for n in CORPUS], [oracle_canon_person(n) for n in CORPUS])
check("token_key is byte-identical to targets_engine.name_key over the live corpus",
      [PI_MOD.token_key(n) for n in CORPUS], [oracle_name_key(n) for n in CORPUS])
check("tokens is byte-identical to commission_drilldown._tok over the live corpus",
      [PI_MOD.tokens(n) for n in CORPUS], [oracle_tok(n) for n in CORPUS])
PAIRS = [(a, b) for a in CORPUS[:28] for b in CORPUS[:28]]
check("fold_person is byte-identical to payroll_identity._fold_person over the live corpus",
      [PI_MOD.fold_person(n) for n in CORPUS], [oracle_fold_person(n) for n in CORPUS])
check("name_tokens is byte-identical to payroll_identity.name_tokens over the live corpus",
      [PI_MOD.name_tokens(n) for n in CORPUS], [oracle_name_tokens(n) for n in CORPUS])
check("folded_token_key is byte-identical to payroll_identity.person_key over the live corpus",
      [PI_MOD.folded_token_key(n) for n in CORPUS], [oracle_folded_token_key(n) for n in CORPUS])
check("loosely_same_person is byte-identical to _name_match over %d live pairs" % len(PAIRS),
      [PI_MOD.loosely_same_person(a, b) for a, b in PAIRS],
      [oracle_name_match(a, b) for a, b in PAIRS])

# management_incentive's copy was NOT the engine's rule, and the PR says so. The ONLY divergence is
# the multi-comma shape, which is what dereferencing settles — asserted here rather than hand-waved,
# so a future reader can see exactly what moved.
_div = [n for n in CORPUS if oracle_mgmt_canon_person(n) != oracle_canon_person(n)]
check("management_incentive's old copy diverged from the engine only where a name is punctuation "
      "or holds TWO commas — never on a real name",
      _div, [",", ",,", " , ", "Khan, Ariful, Jr"])
check("...and every one-or-zero-comma name — i.e. every live name — keys identically either way",
      [PI_MOD.person_key(n) for n in CORPUS if n not in _div],
      [oracle_mgmt_canon_person(n) for n in CORPUS if n not in _div])
check("...with the engine's rule now deciding the multi-comma shape too",
      PI_MOD.person_key("Khan, Ariful, Jr"), "khan, ariful, jr")


print("\nA2. TWO MODULES CLAIMED THIS QUESTION — the clock feed's and the commission engine's")
# `storeops/payroll_identity.person_key` DECLARED ITSELF the one home for a person spelling (the
# clock feed's own docstring said so) while `commission_engine._canon_person` answered the same
# question a different way. Neither could be read from `closing` without a module cycle, which is
# exactly why the who-worked signal grew a name union of its own. Both now dereference.
PAYROLL = os.path.join(HERE, "app", "modules", "storeops", "payroll_identity.py")
for fname, home_fn in (("_fold_person", "fold_person"), ("name_tokens", "name_tokens"),
                       ("person_key", "folded_token_key")):
    body = re.sub(r"\s+", " ", fn_src(src(PAYROLL), fname)).strip()
    check("payroll_identity.%s is ONE return of person_identity.%s" % (fname, home_fn),
          body, "return _person_identity.%s(v)" % home_fn)
check("...and holds no second copy of the accent fold",
      "fold_accents" in nocomment(src(PAYROLL)), False)
check("the clock feed's docstring now names the home, not the module that dereferences it",
      "core/person_identity` is the one home" in src(
          os.path.join(HERE, "app", "modules", "storeops", "clock_feed.py")), True)
check("the strongest key forgives the accent the who-worked signal could not reach",
      PI_MOD.folded_token_key("Martínez, LESLIEE"), PI_MOD.folded_token_key("LESLIEE Martinez"))
check("...and person_key, which money keys on, deliberately still does not",
      PI_MOD.person_key("Martínez, LESLIEE") == PI_MOD.person_key("LESLIEE Martinez"), False)
check("a middle initial is still not dropped by the strongest key either",
      PI_MOD.folded_token_key("Caba, David, A") == PI_MOD.folded_token_key("Caba, David"), False)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nB. THE REPORTED DEFECT — one man, two feeds, two spellings, ONE entry")
SIMARJYOT_PUNCH = "Simarjyot Singh"       # storeops.timelog, B-1750, 2026-10-10
SIMARJYOT_SALE = "Singh, Simarjyot"       # B2B salesperson, store '1750 5th Ave', same day
check("the punch spelling and the sales spelling are ONE person",
      PI_MOD.folded_token_key(SIMARJYOT_PUNCH), PI_MOD.folded_token_key(SIMARJYOT_SALE))
KEY = PI_MOD.folded_token_key          # the key the who-worked signal actually joins on
coll = PI_MOD.collapse([SIMARJYOT_PUNCH, SIMARJYOT_SALE], None, KEY)
check("...so collapsing the day's names yields ONE person, not two", len(coll), 1)
check("...shown in the spelling a human reads, not the feed's comma form",
      list(coll.values())[0]["name"], "Simarjyot Singh")
check("...while both spellings stay visible for the recon",
      list(coll.values())[0]["variants"], ["Simarjyot Singh", "Singh, Simarjyot"])
check("the whole reported cell collapses to the one man",
      sorted(p["name"] for p in PI_MOD.collapse(
          ["Angie Ramos", SIMARJYOT_PUNCH, SIMARJYOT_SALE], None, KEY).values()),
      ["Angie Ramos", "Simarjyot Singh"])

# The ROSTER spelling is what an administrator typed, so it outranks both feeds.
ROSTER = {KEY("Simarjyot Singh"): "Simarjyot S. Singh"}
check("the roster's spelling wins outright when it holds the person",
      PI_MOD.best_spelling([SIMARJYOT_SALE], ROSTER, KEY), "Simarjyot S. Singh")
check("...and is found through the KEY, so a comma-form feed still matches the roster row",
      PI_MOD.best_spelling([SIMARJYOT_SALE, "SINGH, SIMARJYOT"], ROSTER, KEY), "Simarjyot S. Singh")
check("a person the roster does not hold still gets a readable spelling, never blank",
      PI_MOD.best_spelling(["Vega, Cartos"], ROSTER, KEY), "Vega, Cartos")


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nC. best_spelling — the chosen name does not depend on which feed loaded first")
check("comma-free beats 'Last, First'", PI_MOD.best_spelling(["Ramos, Angie", "Angie Ramos"]),
      "Angie Ramos")
check("...in either input order", PI_MOD.best_spelling(["Angie Ramos", "Ramos, Angie"]),
      "Angie Ramos")
check("more words beats fewer (a full name beats an abbreviated one)",
      PI_MOD.best_spelling(["Ariful Islam Khan", "Islam Khan, Ariful"]), "Ariful Islam Khan")
check("a tie is broken alphabetically, so the answer is deterministic",
      PI_MOD.best_spelling(["b b", "a a"]), "a a")
check("whitespace-only variants are not a name", PI_MOD.best_spelling(["  ", "", None]), "")
check("no variants at all is the empty string, never an exception",
      PI_MOD.best_spelling(None), "")
check("collapse ignores blanks rather than minting an empty person",
      len(PI_MOD.collapse(["", None, "   ", "Angie Ramos"])), 1)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nD. DELIBERATELY NOT FUZZY — the home must never merge two real people")
check("'Abdul K' and 'Abdul Kakar' stay TWO people (both live house punch spellings)",
      PI_MOD.person_key("Abdul K") == PI_MOD.person_key("Abdul Kakar"), False)
check("...so the day's cell reports both, honestly, instead of guessing",
      len(PI_MOD.collapse(["Abdul K", "Abdul Kakar"])), 2)
check("a middle name is not dropped ('natasha cabrera' != 'natasha nicole cabrera')",
      PI_MOD.person_key("Natasha Cabrera") == PI_MOD.person_key("Natasha Nicole Cabrera"), False)
check("person_key forgives word order ONLY at a comma: 'Ramos, Angie' == 'Angie Ramos'",
      PI_MOD.person_key("Ramos, Angie") == PI_MOD.person_key("Angie Ramos"), True)
check("...so 'KELLIE MARK' and 'Kellie, Mark' are NOT one key — the live 2026-09-02 shape "
      "person_key cannot reach, and the whole reason token_key exists beside it",
      PI_MOD.person_key("KELLIE MARK") == PI_MOD.person_key("Kellie, Mark"), False)
check("token_key DOES forgive bare word order — which is why it is a second, named function",
      PI_MOD.token_key("KELLIE MARK") == PI_MOD.token_key("Kellie, Mark"), True)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nE. THE LOOSE RULE IS NOT AN IDENTITY — proven, which is why it is a separate function")
check("'Amir' loosely matches 'Amir Khan'", PI_MOD.loosely_same_person("Amir", "Amir Khan"), True)
check("'Amir' loosely matches 'Amir Hassan'", PI_MOD.loosely_same_person("Amir", "Amir Hassan"), True)
check("...but those two are NOT one person — so the rule is not transitive and cannot be a key",
      PI_MOD.loosely_same_person("Amir Khan", "Amir Hassan"), True)
check("...whereas the grouping key keeps them apart",
      PI_MOD.person_key("Amir Khan") == PI_MOD.person_key("Amir Hassan"), False)
check("an empty side never loosely matches anything", PI_MOD.loosely_same_person("", "Amir"), False)
check("...in either position", PI_MOD.loosely_same_person("Amir", None), False)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nF. THE LOCK — one home, dereferenced, and it cannot un-wire")
for mod, path in WIRED.items():
    s = src(path)
    check("%s imports the one home" % mod, "person_identity" in s, True)

# Each of the six call sites must be a DEREFERENCE — a body that calls the home and does nothing
# else. A body that grows a rule of its own fails here, which is the whole point.
DEREF = [
    ("commission_engine", "_canon_person", "person_key"),
    ("management_incentive", "_canon_person", "person_key"),
    ("targets_engine", "name_key", "token_key"),
    ("closing_router", "_name_match", "loosely_same_person"),
    ("ops_chargebacks", "_name_match", "loosely_same_person"),
]
for mod, fname, home_fn in DEREF:
    body = fn_src(src(WIRED[mod]), fname)
    check("%s.%s is ONE return of person_identity.%s" % (mod, fname, home_fn),
          re.sub(r"\s+", " ", body).strip(),
          "return _person_identity.%s(%s)" % (home_fn, "s" if (mod, fname) == ("commission_engine", "_canon_person")
                                              else "name" if fname in ("name_key", "_canon_person") else "a, b"))

check("commission_drilldown._tok dereferences the home's word-set",
      "person_identity.tokens" in fn_src(src(WIRED["commission_drilldown"]), "_tok"), True)

# NO SEVENTH COPY. The giveaway of the canon rule is the single-comma reorder; of the loose rule, the
# first-token fallback. Either appearing outside the home is the patchwork growing back.
HOME_SRC = src(PI)
for mod, path in WIRED.items():
    body = nocomment(src(path))
    check("%s holds no second copy of the comma reorder" % mod,
          'count(",") == 1' in body or 'partition(",")' in body, False)
    check("%s holds no second copy of the first-token fallback" % mod,
          "a.split()[0] == b.split()[0]" in body, False)
check("the home itself holds the comma reorder (it is the one place it may live)",
      'count(",") == 1' in HOME_SRC, True)
check("the home is import-time PURE — its only TOP-LEVEL import is stdlib, so nothing can cycle on it",
      re.findall(r"(?m)^(?:from|import)\s+(\S+)", HOME_SRC), ["re"])
check("...and its one app import is DEFERRED to call time, inside the accent fold's caller",
      re.findall(r"(?m)^\s+from (app\.\S+) import", HOME_SRC), ["app.modules.account.store_identity"])

# WHO WORKED, in the router: the union must be over PEOPLE and the bucket over THE ONE store key.
WW = fn_src(RSRC, "_who_worked_by_store")
check("who-worked keys people through the one home's STRONGEST key",
      "_person_identity.folded_token_key" in WW, True)
check("...and not through the weaker money key, which an accent would split",
      "_person_identity.person_key" in WW, False)
check("...and displays them through the one spelling rule",
      "_person_identity.best_spelling" in WW, True)
check("...and buckets BOTH signals on the one store key, so 'b-418' cannot split from 'B-418'",
      WW.count("_store_identity.store_key"), 2)
check("...reading the roster for the authoritative spelling",
      "_roster_spellings" in WW, True)
check("no raw punch name is added to a bucket any more (the defect's own line is gone)",
      '_bucket(code)["clocked_in"].add(nm)' in nocomment(RSRC), False)
check("...nor a raw sales name", 'b["sold"].add(sp)' in nocomment(RSRC), False)

# WHO WORKED vs WHO WAS EXPECTED: two facts, two fields, on every caller.
def raw_fn(source, name):
    """The function's source as WRITTEN (not unparsed), so an assertion can quote its real text."""
    lines = source.splitlines(True)
    t = ast.parse(source)
    for n in t.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return "".join(lines[n.lineno - 1:n.end_lineno])
    return ""


WWD = raw_fn(RSRC, "_who_worked_display_by_store")
check("the display home returns the roster guess in its OWN field",
      '"scheduled": sched_out' in WWD, True)
check("...and `worked` carries only the actual signal (the scheduled branch returns reps = [])",
      'reps = []\n            source = "scheduled"' in WWD, True)
check("the cash-pickup screen reads the guess as scheduled_only",
      '"scheduled_only": wd.get("scheduled")' in RSRC, True)
check("the accountability chain reads the guess as scheduled_only",
      '"scheduled_only": _w.get("scheduled")' in RSRC, True)

# THE SCREENS. A column headed "Who worked" must not be able to print the roster's guess.
for page, label in ((os.path.join(FRONT, "_lib", "ClosingPickupStatus.tsx"), "Closing & Cash Pickup Status"),
                    (os.path.join(FRONT, "accountability", "page.tsx"), "the accountability chain"),
                    (os.path.join(FRONT, "pickup", "page.tsx"), "the cash-pickup screen")):
    s = src(page)
    check("%s renders scheduled_only as its own thing" % label, "scheduled_only" in s, True)
check("the status report exports the guess in a column that NAMES it a guess",
      "Scheduled (nobody worked)" in src(os.path.join(FRONT, "_lib", "ClosingPickupStatus.tsx")), True)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nG. CONTROLS — each fails if the defect is patched back in")
def union_of_strings(punch, sale):
    """The OLD mechanism, kept here as the control: a union of name strings."""
    return sorted(set(punch) | set(sale))


check("CONTROL the old string union really did report two reps for one man",
      union_of_strings([SIMARJYOT_PUNCH], [SIMARJYOT_SALE]),
      ["Simarjyot Singh", "Singh, Simarjyot"])
check("CONTROL a case-only difference would have split him a third way",
      len(union_of_strings([SIMARJYOT_PUNCH], ["SIMARJYOT SINGH"])), 2)
check("...where the key does not", len(PI_MOD.collapse([SIMARJYOT_PUNCH, "SIMARJYOT SINGH"])), 1)
check("CONTROL a fuzzy key would have merged two real people (this one does not)",
      PI_MOD.token_key("Abdul K") == PI_MOD.token_key("Abdul Kakar"), False)
check("CONTROL the home has no TOP-LEVEL app import (the thing that would make it cyclable)",
      bool(re.search(r"(?m)^from app\.", HOME_SRC)), False)
check("CONTROL best_spelling with an empty roster still answers",
      PI_MOD.best_spelling([SIMARJYOT_SALE], {}, KEY), "Singh, Simarjyot")
check("CONTROL the weaker key WOULD have split the live accent case (so the choice matters)",
      len(PI_MOD.collapse(["Martínez, LESLIEE", "LESLIEE Martinez"], None, PI_MOD.person_key)), 2)
check("...where the key who-worked uses does not",
      len(PI_MOD.collapse(["Martínez, LESLIEE", "LESLIEE Martinez"], None, KEY)), 1)


print("\n%d passed, %d failed" % (_pass, _fail))
sys.exit(1 if _fail else 0)
