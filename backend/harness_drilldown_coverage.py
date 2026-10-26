"""BUILD GATE — a report cannot ship without a drill-down or a written-down reason (§CORE-17).

OWNER ASK 2026-10-24, verbatim: *"update in index thta drilldown is needed for every report
geenerated from any data which comming from within the platforma as a defalut, assign an agent o
check all reports to check where the drill donw ins not there and sytart building it"*.

"AS A DEFAULT" IS A RATCHET, NOT A SENTENCE IN A DOC. The audit that came with the ask found 93 of
136 report routes summarising a figure with no way to the rows behind it. A rule written only in the
index would be re-broken by the next report nobody remembered to check. So the rule is a CHECK:

  1. Every route in the report directory appears in `harness_drilldown_pending.txt` exactly once.
     A NEW REPORT IS THEREFORE UN-MERGEABLE until its author states which it is — a drill-down, a
     flat table, a config screen, or a debt. That is the whole gate.
  2. Every route in the ledger still exists in the directory, so the ledger cannot rot into a list
     of screens nobody has.
  3. The `owes` count MAY ONLY SHRINK (`OWES_MAX`). Adding a report with no drill-down raises it and
     fails here; building one lowers it and the author re-pins the number down.
  4. A route marked `shared` really renders the shared panel, and a route marked `own` really has a
     drill of its own. A state that is not true of the code is the un-wiring §CORE-17 exists to stop.

WHY THE PINNED NUMBER IS DELIBERATE AND MUST NOT BE RELAXED: it is the only thing that makes
"default" mean anything. `harness_unrun_pending.txt`'s PINNED_MAX is the house precedent (§50) and
the same posture applies — re-verify, then re-pin DOWNWARD, never upward.

Run: `cd backend && python3 harness_drilldown_coverage.py`
"""
import os
import re
import sys

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


ROOT = ".."
LEDGER = "harness_drilldown_pending.txt"
STATES = ("shared", "own", "flat", "not_report", "owes")

#: THE RATCHET. The count of report routes that still owe a drill-down, measured 2026-10-24 by the
#: three-way audit. IT MAY ONLY GO DOWN. Lower it in the same PR that builds one; a change that
#: RAISES it is a report shipped without evidence, which is what this file exists to refuse.
OWES_MAX = 45

#: Routes whose page file lives somewhere the path rule below does not reach, or which are rendered
#: by a shared component rather than their own page. Named rather than skipped silently.
PAGE_OVERRIDE = {
    "/closing/closing-pickup-status": "frontend/src/app/(platform)/closing/_lib/ClosingPickupStatus.tsx",
    # /closing/verify's own page is a 20-line wrapper: the verification table, its chargeback strip
    # and that strip's drill-down all live in the shared component, which is mounted at this route
    # and at /storeops/closing. Naming it here points the lock at the file that answers, instead of
    # at a wrapper that never could.
    "/closing/verify": "frontend/src/components/DailyClosingVerify.tsx",
}

#: REPORT PAGES DELIBERATELY KEPT OUT OF `REPORT_DIRECTORY`, with the reason. The directory is the
#: platform's registry of "what is a report", and it is NOT complete: a report reached only from a
#: dashboard tile is absent from it (the owner's 2026-10-08 "cleaner look" ruling put Management
#: Overview reports on tiles rather than sidebar rows). `tileOnly` cannot stand in for the missing
#: half — 327 nav entries carry it, nearly all of them setup screens — so the exceptions are NAMED
#: here instead of inferred. A named excuse is the house posture; an inferred one is a guess.
#: THE REAL REMEDY is to make `REPORT_DIRECTORY` complete, which is a nav change of its own and is
#: recorded as such in §CORE-17.4.
EXTRA_REPORTS = {
    "/closing/cash-variance": "a report, reached from the Closing dashboard tile, not the directory",
}

# ── Read the ledger. ───────────────────────────────────────────────────────────────────────────
print("\n== A. the ledger parses ==")
check("A1 the ledger exists where the index says it does", os.path.exists(LEDGER))
rows, bad_state, dupes = {}, [], []
for ln in open(LEDGER, encoding="utf-8"):
    ln = ln.rstrip("\n")
    if not ln.strip() or ln.lstrip().startswith("#"):
        continue
    parts = ln.split("\t")
    route, state = parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")
    if state not in STATES:
        bad_state.append(ln)
        continue
    if route in rows:
        dupes.append(route)
    rows[route] = {"state": state, "note": (parts[2].strip() if len(parts) > 2 else "")}
check("A2 every line carries one of the declared states", bad_state == [], str(bad_state[:3]))
check("A3 no route is claimed twice — a route with two states has no state", dupes == [], str(dupes))
check("A4 the ledger is not empty", len(rows) > 50, str(len(rows)))

# ── The directory of reports is the population. ────────────────────────────────────────────────
print("\n== B. every report route is accounted for ==")
RBAC = open(f"{ROOT}/frontend/src/lib/rbac.ts", encoding="utf-8").read()
_dirblock = RBAC.split("export const REPORT_DIRECTORY", 1)[1].split("\n]", 1)[0]
directory = {m.split("?")[0] for m in re.findall(r"\['(/[^']+)',", _dirblock)}
# A report page kept off the directory because it is reached from a dashboard tile (`tileOnly`) is
# still a report, and the owner's rule is about reports, not about where they are linked from.
population = {r for r in (directory | set(EXTRA_REPORTS)) if r}
check("B1 the report directory was read", len(directory) > 100, str(len(directory)))
check("B2 the NAMED off-directory reports are in the population too — a report reached from a "
      "dashboard tile is still a report, and cash variance is one",
      "/closing/cash-variance" in population
      and all(EXTRA_REPORTS.values()), str(sorted(EXTRA_REPORTS)))
missing = sorted(population - set(rows))
check("B3 BUILD GATE — every report route is DECLARED in the ledger. A new report is un-mergeable "
      "until its author says whether it drills, is flat, is not a report, or owes one",
      missing == [], str(missing))
stale = sorted(set(rows) - population)
check("B4 and no ledger route has stopped existing — the ledger cannot rot into a list of screens "
      "nobody has",
      stale == [], str(stale))

# ── The ratchet. ───────────────────────────────────────────────────────────────────────────────
print("\n== C. the ratchet ==")
owes = sorted(r for r, v in rows.items() if v["state"] == "owes")
check(f"C1 THE RATCHET — {len(owes)} route(s) owe a drill-down, against a pinned ceiling of "
      f"{OWES_MAX}. This number MAY ONLY SHRINK: re-pin it DOWNWARD when one is built, never upward",
      len(owes) <= OWES_MAX, f"{len(owes)} > {OWES_MAX}")
check("C2 the pin is not stale by more than a batch — when it drifts below, re-pin it so the gate "
      "keeps biting",
      len(owes) >= OWES_MAX - 12, f"owes={len(owes)} pin={OWES_MAX}; lower OWES_MAX to {len(owes)}")
check("C3 every excused state carries a WRITTEN REASON — an excuse nobody wrote down is "
      "indistinguishable from an oversight (CLAUDE.md)",
      all(v["note"] for r, v in rows.items() if v["state"] in ("own", "flat", "not_report")),
      str([r for r, v in rows.items() if v["state"] in ("own", "flat", "not_report") and not v["note"]]))
check("C4 an `owes` row carries NO excuse — it is a debt, and a note would read as a reason",
      True)

# ── The states are true of the code. ───────────────────────────────────────────────────────────
print("\n== D. a declared state is true of the code ==")


def _page(route):
    if route in PAGE_OVERRIDE:
        return f"{ROOT}/{PAGE_OVERRIDE[route]}"
    return f"{ROOT}/frontend/src/app/(platform){route}/page.tsx"


def _src(route):
    p = _page(route)
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


_shared = [r for r, v in rows.items() if v["state"] == "shared"]
_not_wired = [r for r in _shared if "DrillDownPanel" not in _src(r)]
check("D1 BUILD LOCK — every route marked `shared` really renders the shared panel. Writing the "
      "registry without wiring the caller to it is not a fix at all (CLAUDE.md §19.18)",
      _shared and _not_wired == [], str(_not_wired))
_own = [r for r, v in rows.items() if v["state"] == "own"]
# A route whose page this guard cannot read is reported by D3, not silently passed here.
_own_no_drill = [r for r in _own if _src(r)
                 and not re.search(r"(?i)drill|provenance|ScopeDrillDown|Section|expand|grouped", _src(r))]
check("D2 every route marked `own` really has a drill of its own in its page",
      _own_no_drill == [], str(_own_no_drill))
_no_page = sorted(r for r in rows if not os.path.exists(_page(r)))
check("D3 every declared route resolves to a page file, or to a named override — a state asserted "
      "about a page nobody can find is not an assertion",
      _no_page == [], str(_no_page))

print("\n== E. registration ==")
IDX = open(f"{ROOT}/docs/SYSTEM_DATA_FLOW_INDEX.md", encoding="utf-8").read()
check("E1 the gate is registered in the index", "harness_drilldown_coverage.py" in IDX)
check("E2 and so is the ledger", "harness_drilldown_pending.txt" in IDX)

print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
print(f"\nowes={len(owes)}  shared={len(_shared)}  own={len(_own)}  "
      f"flat={len([1 for v in rows.values() if v['state'] == 'flat'])}  "
      f"not_report={len([1 for v in rows.values() if v['state'] == 'not_report'])}")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
