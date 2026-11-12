"""Proof harness — A STORE CODE IS CANONICALISED WHERE IT IS CAPTURED, NOT ONLY WHERE IT IS READ.

Owner, 2026-11-12, on the October salary defect: *"this is a mapping issue the system shoudl eb
smart enough to cpature this fromt eh get go"*.

THE INSTANCE, measured on live production 2026-11-12 before a line was changed. Against each org's
OWN declared vocabulary (`commcalc.store_mapping` + `storeops.stores` + `commcalc.store_aliases`),
the rows that carry a store code hold a spelling the org does not declare:

    storeops.timelog        2,131 rows — 2,058 of them written by `device = 'kiosk'`, the live
                            time clock, 180 of them in NOVEMBER. The kiosk writes `CICERO`,
                            `GRAND`, `LAWRENCE`, `ARMITAGE` where the org declares `Cicero`,
                            `Grand`, `lawrence`, `Armitage`.
    storeops.shifts            97 rows (82 + 15) whose code differs from the declared spelling,
                            and 123 more on codes nothing in the org recognises at all.
    commcalc.store_expenses   491 rows on codes nothing recognises.

The read side was taught to fold a case on 2026-11-11 (§ACCOUNT-01), so none of this is lost money
today. But every one of those rows was BORN wrong, and the next surface that joins on a code
without going through the one home is wrong again.

THE CLASS, stated once so nobody re-derives it.

    **A column named `store_code` is written with whatever the source happened to call the store —
      a code, a code in another case, or an ADDRESS — because no WRITE passes through the one
      place that answers "which store is this string?". Folding on read makes each reader
      correct; it does not make the stored fact true.**

THE DESIGN FIX, for every writer and not for the clock that surfaced it:

  · `commcalc/flag_store_resolver` — already THE write-side home for "which declared store_code is
    this string?", built for flags in §285 and keyed on the span keyset's own vocabulary — gains
    `capture_code` / `stamp_capture`, so a capture states no resolution rule of its own;
  · every endpoint that WRITES a store code stamps it through that home: the time clock, the
    schedule, the schedule template, and the shift a template applies;
  · a spelling the org does NOT declare is kept exactly as typed and REPORTED
    (`unrecognised_spellings`), never guessed at and never silently dropped — the owner's T-* stores
    are a mapping gap for a human, not a thing for code to invent an answer to;
  · `harness_store_code_capture.py` §D FAILS THE BUILD if a write of a store code to one of those
    tables stops dereferencing the home, or if a new one appears.

CROSS-TENANT (owner directive 2026-10-30): the capture is a property of the ORG'S OWN declared
rows. §E proves each tenant's real spellings canonicalise against its own vocabulary and that one
tenant's codes reach nothing of another's.

DB-free, pure stdlib.  Run:  cd backend && python3 harness_store_code_capture.py
"""
import ast
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harnesslib                                                          # noqa: E402
from app.modules.commcalc import flag_store_resolver as fsr                # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FAIL = 0


def check(name, cond, detail=None):
    global FAIL
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAIL += 1
        if detail is not None:
            print("        got: %r" % (detail,))


def src(rel):
    with open(os.path.join(HERE, rel), encoding="utf-8") as fh:
        return fh.read()


# ── live declared shapes, as production spells them 2026-11-12 ───────────────────────────────────
LUX = fsr.build_index(
    mapping_rows=[{"store_code": "Cicero", "store_address": "3352 W 26th St"},
                  {"store_code": "Grand", "store_address": "3966 W Grand Ave"},
                  {"store_code": "lawrence", "store_address": "3248 W Lawrence Ave"},
                  {"store_code": "Armitage", "store_address": "4801 W Armitage Ave"}],
    store_rows=[], alias_rows=[])

print("── A. the capture answer is the DECLARED spelling, in whatever case it arrives ──")
check("A1 the kiosk's CICERO captures as the declared Cicero",
      fsr.capture_code(LUX, "CICERO") == "Cicero", fsr.capture_code(LUX, "CICERO"))
check("A2 GRAND -> Grand", fsr.capture_code(LUX, "GRAND") == "Grand")
check("A3 LAWRENCE -> lawrence (the org declares it lower case)",
      fsr.capture_code(LUX, "LAWRENCE") == "lawrence")
check("A4 the declared spelling captures as itself (idempotent)",
      fsr.capture_code(LUX, "Cicero") == "Cicero")
check("A5 capturing twice is the same answer (a re-save cannot drift)",
      fsr.capture_code(LUX, fsr.capture_code(LUX, "CICERO")) == "Cicero")
check("A6 a store ADDRESS typed into the code column captures as the code",
      fsr.capture_code(LUX, "3352 W 26TH ST") == "Cicero", fsr.capture_code(LUX, "3352 W 26th St"))
check("A7 an undeclared spelling captures as NOTHING, never as a guess",
      fsr.capture_code(LUX, "T-7812") is None, fsr.capture_code(LUX, "T-7812"))
check("A8 a blank captures as nothing", fsr.capture_code(LUX, "") is None
      and fsr.capture_code(LUX, None) is None)
check("A9 capture states no rule of its own — it IS resolve_code",
      fsr.capture_code(LUX, "CICERO") == fsr.resolve_code(LUX, "CICERO"))

print("\n── B. stamping a row: the declared code, or the raw kept and reported ──")
r1 = {"store_code": "CICERO", "employee_id": "E1"}
check("B1 stamp rewrites a drifted code in place and says it was recognised",
      fsr.stamp_capture(LUX, r1) is True and r1["store_code"] == "Cicero", r1)
r2 = {"store_code": "  T-7812 "}
check("B2 an unrecognised code is KEPT, trimmed, and reported unrecognised",
      fsr.stamp_capture(LUX, r2) is False and r2["store_code"] == "T-7812", r2)
r3 = {"store_code": None}
check("B3 a blank stays blank and is not reported as a mapping gap",
      fsr.stamp_capture(LUX, r3) is False and not r3["store_code"], r3)
r4 = {"home": "GRAND"}
check("B4 the field is a parameter, not a literal — any column can be stamped",
      fsr.stamp_capture(LUX, r4, field="home") is True and r4["home"] == "Grand", r4)
r5 = {"store_code": "CICERO"}
check("B5 an empty index NEVER rewrites a row (a read failure cannot move a store)",
      fsr.stamp_capture({}, r5) is False and r5["store_code"] == "CICERO", r5)
check("B6 unrecognised_spellings counts what a human must map, and nothing else",
      fsr.unrecognised_spellings(LUX, [{"store_code": "CICERO"}, {"store_code": "T-7812"},
                                       {"store_code": "T-7812"}, {"store_code": ""}])
      == {"T-7812": 2},
      fsr.unrecognised_spellings(LUX, [{"store_code": "T-7812"}]))

print("\n── C. the regression: the live October/November kiosk spellings ──")
KIOSK_LIVE = {"CICERO": "Cicero", "GRAND": "Grand", "LAWRENCE": "lawrence", "ARMITAGE": "Armitage"}
for raw, want in KIOSK_LIVE.items():
    check("C1 kiosk %-9s is captured as %s" % (raw, want), fsr.capture_code(LUX, raw) == want,
          fsr.capture_code(LUX, raw))
check("C2 none of the live kiosk spellings captures as a store the org has not declared",
      all(fsr.capture_code(LUX, k) in set(KIOSK_LIVE.values()) for k in KIOSK_LIVE))

print("\n── D. THE LOCK: a store code is not written without the home ──")
CAPTURE_TABLES = ("shifts", "timelog", "shift_templates")
WRITE = re.compile(r'\.table\(\s*"(%s)"\s*\)\s*\.\s*(insert|upsert)\(' % "|".join(CAPTURE_TABLES))
# The two sanctioned local wrappers in storeops/router are named here, and §D4a proves each of them
# dereferences the home itself — so naming them is not a hole the next writer can walk through.
HOME_CALL = re.compile(r"(stamp_capture|capture_code|capture_store_code"
                       r"|_stamp_store_code|_captured_store_code)\s*\(")
ROUTER = "app/modules/storeops/router.py"
code = harnesslib.py_code_only(src(ROUTER))
funcs = harnesslib.function_sources(code)
offenders = []
for fname, body in funcs.items():
    if not WRITE.search(body):
        continue
    if not HOME_CALL.search(body):
        offenders.append(fname)
check("D1 every storeops write of a store code dereferences the capture home",
      not offenders, offenders)
_writers = [f for f in funcs if WRITE.search(funcs[f])]
check("D2 the writes are still there to be checked (the scan is not vacuously green)",
      len(_writers) >= 6, _writers)
# `_stamp_store_code` must reach the HOME; `_captured_store_code` is one line over it, so it is
# allowed to reach the home THROUGH it — one hop, and the hop is itself checked on the line above.
for _w, _want in (("_stamp_store_code", r"(stamp_capture|capture_code)\s*\("),
                  ("_captured_store_code", r"(stamp_capture|capture_code|_stamp_store_code)\s*\(")):
    check("D4a the sanctioned wrapper %s dereferences the home itself" % _w,
          re.search(_want, funcs.get(_w, "")) is not None, funcs.get(_w, "MISSING")[:120])
check("D3 the capture home exposes exactly one resolution chain (no second copy)",
      harnesslib.py_code_only(src("app/modules/commcalc/flag_store_resolver.py")).count(
          "def build_index") == 1)
_fsr_code = harnesslib.py_code_only(src("app/modules/commcalc/flag_store_resolver.py")).lower()
check("D4 the capture home holds NO tenant, carrier or store name in code (RULE TWO)",
      all(w not in _fsr_code for w in ("luxelink", "cellfonz", "vzone", "boost", "cicero")),
      [w for w in ("luxelink", "cellfonz", "vzone", "boost", "cicero") if w in _fsr_code])

print("\n── E. cross-tenant: an org captures against its OWN declarations only ──")
TENANTS = {
    "A": ([{"store_code": "Cicero", "store_address": "3352 W 26th St"}], "CICERO", "Cicero"),
    "B": ([{"store_code": "B-1115", "store_address": "531 Utica Ave"}], "b-1115", "B-1115"),
    "C": ([{"store_code": "Ave U", "store_address": "1 Ave U"}], "AVE U", "Ave U"),
    "D": ([{"store_code": "ny-log-1", "store_address": "1 Logistics Way"}], "NY-LOG-1", "ny-log-1"),
}
IDX = {k: fsr.build_index(mapping_rows=v[0], store_rows=[], alias_rows=[])
       for k, v in TENANTS.items()}
for name, (rows, drifted, want) in TENANTS.items():
    check("E1 tenant %s captures its own drifted spelling as %s" % (name, want),
          fsr.capture_code(IDX[name], drifted) == want, fsr.capture_code(IDX[name], drifted))
    for other, (orows, odrift, _w) in TENANTS.items():
        if other == name:
            continue
        check("E2 tenant %s reaches nothing of tenant %s" % (name, other),
              fsr.capture_code(IDX[name], odrift) is None
              and fsr.capture_code(IDX[name], orows[0]["store_code"]) is None,
              (fsr.capture_code(IDX[name], odrift),
               fsr.capture_code(IDX[name], orows[0]["store_code"])))
check("E3 a tenant with NO declarations captures nothing — never another tenant's words",
      fsr.capture_code(fsr.build_index([], [], []), "CICERO") is None)

print("\n%s — %d check(s) failed" % ("FAIL" if FAIL else "ALL PROOFS HOLD", FAIL))
sys.exit(1 if FAIL else 0)
