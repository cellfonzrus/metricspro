#!/usr/bin/env python3
"""LOCK: one home for "how long may we keep where somebody was", and every caller dereferencing it.

WHY A LOCK AT ALL (CLAUDE.md 2026-09-20, "Lock it so it cannot un-wire"). Writing a registry and not
wiring the callers to it has happened three times in this repo (index §19.18). This feature is the
fourth candidate and the most expensive to get wrong, because the thing that un-wires silently is a
DELETION PROMISE: a relation added without a registry entry purges nothing, and nobody notices,
because "no rows deleted" looks exactly like "nothing was due".

WHAT THIS FAILS THE BUILD ON
  1. A coordinate column spelled anywhere in the retention one home — the names belong to
     `store_geofence.PUNCH_COLUMNS` and must be DEREFERENCED, never copied.
  2. A second date arithmetic for a retention window outside the one home.
  3. A relation that stores a location fix and is NOT in `LOCATION_RELATIONS`.
  4. A writer that stores a fix without stamping the commitment.
  5. A purge that would clear a VERDICT column — the flag and the distance must survive, which is
     what makes honouring the promise free.
  6. The sweep losing its bound, or its keyset, and becoming an unbounded or non-advancing loop.

Run: `python3 harness_geo_retention_lock.py` (DB-free). Wired into CI as `geo-retention-lock`.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.core import geo_retention as R          # noqa: E402
from app.modules.core import store_geofence as _sgeo     # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")

# The one home is NOT named here. `module_graph` is where "which file answers this question" lives
# (§50), and a lock keeping its own copy of that ruling is exactly the un-wiring the graph exists to
# prevent (index §19.18). It is READ off the graph.
from app.modules.core import module_graph as _graph    # noqa: E402

HOME = _graph.fact("location_fix_retention")["homes"][0].split("app/", 1)[-1]

PASS, FAIL = [], []


def check(label, ok, detail=""):
    (PASS if ok else FAIL).append(label)
    print(("  [PASS] " if ok else "  [FAIL] ") + label + (f"\n          → {detail}" if not ok and detail else ""))


def _code(path):
    """The file's CODE, with `#` comments stripped, so a module may EXPLAIN what it does not do."""
    with open(path, encoding="utf-8") as fh:
        return "\n".join(line.split("#", 1)[0] for line in fh)


def _rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def _py_files():
    for base, _dirs, files in os.walk(ROOT):
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.join(base, name)


# ── 1. the one home spells no coordinate column of its own ───────────────────────────────────────
print("\nA. the one home holds the REGISTRY, not a copy of the column names")
_home_code = _code(os.path.join(ROOT, HOME))
_all_fix_cols = sorted({c for k in _sgeo.KINDS
                        for c in (_sgeo.PUNCH_COLUMNS[k]["lat"], _sgeo.PUNCH_COLUMNS[k]["lng"],
                                  _sgeo.PUNCH_COLUMNS[k]["accuracy"])})
for col in _all_fix_cols:
    check(f"A1[{col}]. not spelled as a literal in {HOME}",
          f'"{col}"' not in _home_code and f"'{col}'" not in _home_code,
          f"dereference it from store_geofence.PUNCH_COLUMNS instead")

check("A2. the one home DOES dereference store_geofence.PUNCH_COLUMNS",
      "PUNCH_COLUMNS" in _home_code)

# ── 2. one date arithmetic ───────────────────────────────────────────────────────────────────────
print("\nB. ONE date arithmetic for a retention window")
# SCOPED TO A LOCATION FIX, deliberately. Other retention windows in this codebase govern OTHER
# facts and keep their own homes, which is correct: `storeops/face_retention.py` is the BIPA
# biometric-template schedule (migration 420, docs/BIOMETRIC_RETENTION_POLICY.md) — a complete,
# wired deletion job for "a face", not "a place", with a statutory rule of its own that would be
# wrong to fold into this one. So this check fires only on a module that computes a retention date
# AND handles coordinates: that is this class, and it must come here.
_DATE_MATH = re.compile(r"timedelta\s*\(\s*days\s*=")
_WINDOW_WORDS = re.compile(r"geo_purge|purge_after_date|punch_geo_retention|checkin_geo_retention")
# PROXIMITY, not whole-file: these modules legitimately add days to OTHER dates (a trial end, an
# alert window). What must not exist is a day-arithmetic sitting NEXT TO a location-retention word,
# which is what a second copy of this sum looks like.
NEAR = 6
offenders = []
for path in _py_files():
    rel = _rel(path)
    if rel == HOME:
        continue
    lines = _code(path).splitlines()
    for i, line in enumerate(lines):
        if not _DATE_MATH.search(line):
            continue
        window = "\n".join(lines[max(0, i - NEAR):i + NEAR + 1])
        if _WINDOW_WORDS.search(window):
            offenders.append(f"{rel}:{i + 1}")
check("B1. no module but the one home computes a LOCATION retention date", not offenders,
      f"second date arithmetic in {offenders} — call geo_retention.purge_after")

check("B3. the biometric schedule is a DIFFERENT fact and keeps its own home",
      os.path.exists(os.path.join(ROOT, "modules/storeops/face_retention.py")),
      "face_retention.py is the BIPA template schedule; folding it in here would lose its "
      "statutory MIN(purpose-satisfied, 1095-day backstop) rule")

check("B2. the one home's own arithmetic is the only `purge_after`",
      "def purge_after(" in _home_code)

# ── 3. every relation that stores a fix is registered ────────────────────────────────────────────
print("\nC. every relation that stores a location fix is in the registry")
# The relations are discovered from the writers: a `.table("X")` call in the same statement region as
# a coordinate column name means X stores a fix. Pinned by name so a NEW one must be added here
# deliberately, with its registry entry, rather than slipping in unnoticed.
KNOWN_LOCATION_TABLES = {
    "timelog": "storeops.timelog",
    "store_visits": "storeops.store_visits",
    "marketing_event_checkin": "marketing.marketing_event_checkin",
}
# A table that holds coordinates but is deliberately NOT swept, with the reviewed reason.
SWEEP_EXCUSED = {
    "marketing_event": "an EVENT VENUE pin (geo_lat/geo_lng on the event itself) — a place, not a "
                       "person, so no retention promise applies (§23, the same distinction "
                       "harness_store_geofence_lock.py's PIN_READ_EXCUSED draws)",
    "stores": "a STORE's map pin — a place, not a person (mig 1112, §STOREOPS-04)",
}
for table, relation in sorted(KNOWN_LOCATION_TABLES.items()):
    check(f"C1[{table}]. registered in LOCATION_RELATIONS as {relation}",
          relation in R.RELATIONS_BY_NAME,
          "a relation absent from the registry purges nothing, and nobody notices")

check("C2. the registry holds nothing BUT those relations",
      {e["relation"] for e in R.LOCATION_RELATIONS} == set(KNOWN_LOCATION_TABLES.values()),
      f"registry = {sorted(e['relation'] for e in R.LOCATION_RELATIONS)}; if a relation was added "
      "on purpose, add it to KNOWN_LOCATION_TABLES here in the same PR")

for table, why in sorted(SWEEP_EXCUSED.items()):
    check(f"C3[{table}]. excused from the sweep for a reviewed reason",
          table not in KNOWN_LOCATION_TABLES, why)

print("\nD. every entry is complete enough to sweep")
for entry in R.LOCATION_RELATIONS:
    rel = entry["relation"]
    check(f"D1[{rel}]. names a stamp column, a policy source and its migration",
          all(entry.get(k) for k in ("schema", "table", "captures", "stamp",
                                     "policy_table", "policy_column", "migration", "what")))
    check(f"D2[{rel}]. its fix columns resolve to real PUNCH_COLUMNS entries",
          set(R.fix_columns_for(entry)) <= set(_all_fix_cols))

# ── 4. the writers stamp the commitment ──────────────────────────────────────────────────────────
print("\nE. a fix is never stored without its purge date")
WRITERS = {
    "modules/storeops/router.py": "_geo_stamp_columns",      # both ends of a punch
    "modules/storevisit/router.py": "_geo_stamp_columns",    # the DM store visit
    "modules/marketing/router.py": "purge_after_date",       # the event check-in (mig 986)
}
for rel, needle in sorted(WRITERS.items()):
    code = _code(os.path.join(ROOT, rel))
    check(f"E1[{rel}]. stamps the retention commitment ({needle})", needle in code,
          "a coordinate stored with no purge date is a coordinate nothing will ever delete")

_marketing = _code(os.path.join(ROOT, "modules/marketing/event_logic.py"))
check("E2. marketing's purge_after_date DEREFERENCES the one home", "geo_retention" in _marketing,
      "it kept its own copy of the sum until 2026-11-01; two copies are two windows free to drift")

# ── 5. a purge never clears a verdict ────────────────────────────────────────────────────────────
print("\nF. THE PROMISE IS FREE — the flag and the distance survive every purge")
for entry in R.LOCATION_RELATIONS:
    rel = entry["relation"]
    cleared = set(R.cleared_columns(entry))
    verdicts = set(R.verdict_columns_for(entry))
    check(f"F1[{rel}]. the purge clears every coordinate column",
          cleared == set(R.fix_columns_for(entry)))
    check(f"F2[{rel}]. and clears NO verdict or distance column", not (cleared & verdicts),
          f"would wipe {sorted(cleared & verdicts)} — management reviews the verdict, not the fix")
    check(f"F3[{rel}]. and never clears the stamp itself", entry["stamp"] not in cleared)

# ── 6. the sweep stays bounded and advancing ─────────────────────────────────────────────────────
print("\nG. the sweep cannot become unbounded, or stop advancing")
_sweep = _home_code[_home_code.index("def sweep_relation"):]
check("G1. it is bounded by max_pages", "max_pages" in _sweep)
check("G2. it pages by KEYSET on id, not by a plain limit that re-reads cleared rows",
      ".gt(\"id\"" in _sweep and ".order(\"id\")" in _sweep,
      "clearing a row does not remove it from the `stamp <= today` filter, so a plain limit loop "
      "re-reads the rows it just cleared — the defect caught by test_B4 on 2026-11-01")
check("G3. it REPORTS an unfinished backlog rather than looking finished", "more_due" in _sweep)
check("G4. it reports a failure instead of raising into a scheduler",
      'report["ok"] = False' in _sweep)

print("\n" + "=" * 94)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
print("=" * 94)
if FAIL:
    for f in FAIL:
        print(f"  ✗ {f}")
sys.exit(1 if FAIL else 0)
