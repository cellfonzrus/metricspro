"""Offline proof (no DB/network) for THE STACKED WORKFLOW TILE and the "what next" prompt.

OWNER DIRECTIVE 2026-09-10 (verbatim):
    "Also based ont th flowcharts you create the modules for these should be stacked properly based
     on thr work flow in one tile so the user does not have to loo for the next module it is user
     friendly and also the current module should ask the chart what do they want to do next — so
     changes needed in the dm verify modules also"

TWO SURFACES, ONE SEQUENCE. The hub tile (migration 1003) stacks the modules in the order the work
happens; `WorkflowNext` at the foot of each screen offers the next one by name. Both read the SAME
`stages` array in frontend/src/lib/flowcharts.tsx — the array the runbook itself is built from.

§A IS THE POINT OF THIS FILE: it parses the sequence out of the MIGRATION and compares it, in order,
against the sequence parsed out of the TypeScript. Three copies of a workflow (tile, prompt, runbook)
is exactly how the version a user is walked through stops matching the version they were trained on,
and neither tsc nor a SQL linter can see that drift. A stacked tile that disagrees with the training
material is worse than no tile at all.

§C pins the two refusals that make the prompt trustworthy:
  · it gates on the DESTINATION'S OWN nav entry (the shared `useCanOpen`, one predicate), so it can
    never advertise a screen the viewer would be bounced out of; and
  · it never says the work is finished — it has no idea whether the user completed anything, and a
    "done!" over an unfinished job is the confident-but-wrong statement this codebase avoids.

Run: `cd backend && python3 harness_workflow_stacking.py`
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harnesslib import js_code_only   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def read(rel, code_only=False):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        raw = fh.read()
    return js_code_only(raw) if code_only else raw


# ══ A. ONE SEQUENCE, TWO SURFACES ════════════════════════════════════════════════════════════════
lib = read("frontend/src/lib/flowcharts.tsx", code_only=True)
mig = read("database/migrations/1003_daily_closing_workflow_tile.sql")

# The TS side: the first `stages: [ … ]` block, in order.
ts_block = lib.split("stages: [", 1)[1].split("\n  ],", 1)[0]
ts_seq = re.findall(r"href:\s*'([^']+)'", ts_block)
check("A1 the runbook defines a workflow sequence", len(ts_seq) >= 5, ts_seq)

# The SQL side: the items of the tile titled "The closing workflow", in order.
tile_json = mig.split("VALUES ('00000000-0000-0000-0000-000000000001', 'tiles', 'daily-closing', '", 1)[1]
tile_json = tile_json.split("')\nON CONFLICT", 1)[0]
layout = json.loads(tile_json)
wf = [t for t in layout["tiles"] if t["title"] == "The closing workflow"]
check("A2 the migration seeds a tile that IS the workflow", len(wf) == 1,
      [t["title"] for t in layout["tiles"]])
sql_seq = [i["href"] for i in wf[0]["items"]] if wf else []

check("A3 THE TILE AND THE RUNBOOK ARE THE SAME SEQUENCE, IN THE SAME ORDER — this is the check "
      "that stops the stacked tile drifting from the training material",
      sql_seq == ts_seq, f"sql={sql_seq} ts={ts_seq}")

# The migration's own post-flight must assert the same thing server-side.
check("A4 the migration refuses to land a reordered or short tile",
      "RAISE EXCEPTION" in mig and "is not the expected sequence" in mig)
check("A5 ... and its expected list matches the tile it just wrote",
      re.findall(r"'(/closing/[a-z-]+)'", mig.split("want TEXT\\[\\] := ARRAY[")[-1]
                 if "want TEXT[] := ARRAY[" not in mig else
                 mig.split("want TEXT[] := ARRAY[")[1].split("];")[0]) == sql_seq)

# Steps are NUMBERED on the tile — numbering is information here (it is a real sequence), which is
# the only case where it earns its place.
labels = [i.get("label", "") for i in (wf[0]["items"] if wf else [])]
check("A6 the steps are numbered, because this genuinely IS an order",
      all(re.match(r"^\d+ · ", l) for l in labels), labels)
check("A7 ... numbered consecutively from 1",
      [int(l.split(" · ")[0]) for l in labels] == list(range(1, len(labels) + 1)), labels)

# Everything else in the module keeps its own tiles: pretending all work is a sequence is as
# unhelpful as pretending none of it is.
check("A8 the rest of the module is still reachable, in its own tiles",
      len(layout["tiles"]) >= 4, [t["title"] for t in layout["tiles"]])
check("A9 the workflow tile is FIRST — it is the thing a DM opens the hub to do",
      layout["tiles"][0]["title"] == "The closing workflow")

# ══ B. THE MIGRATION IS SAFE TO RUN ══════════════════════════════════════════════════════════════
check("B1 house org only", mig.count("'00000000-0000-0000-0000-000000000001'") >= 1)
check("B2 it does not overwrite a tenant's own designed dashboard",
      "ON CONFLICT (org_id, scope, key) DO NOTHING" in mig)
check("B3 one transaction", "BEGIN;" in mig and "COMMIT;" in mig)
check("B4 it carries a -- REVERT: note", "-- REVERT:" in mig)

# ══ C. THE PROMPT ASKS THE CHART ═════════════════════════════════════════════════════════════════
wn = read("frontend/src/components/WorkflowNext.tsx", code_only=True)
check("C1 the prompt reads the flowchart rather than a second list of its own",
      "stagesAfter" in wn and "@/lib/flowcharts" in wn
      and "/closing/" not in wn)
check("C2 it gates on the DESTINATION'S OWN nav entry — the shared predicate, not a new one",
      "useCanOpen" in wn and "@/components/ScreenLink" in wn)
check("C3 a stage the viewer may not open is skipped to the next one they CAN, not shown as a "
      "dead end", "after.find(s => canOpen(s.href))" in wn)
check("C4 ... and the skip is NAMED, so the chain does not appear to have a gap",
      "not yours" in wn)
check("C5 it never claims the work is finished — it asks, it does not congratulate",
      "Next in this workflow" in wn
      and not re.search(r"(Done!|Well done|✓ Complete|All done)", wn))
check("C6 it offers the whole flow, so the next step is never the only thing on offer",
      "/training/flowcharts/" in wn)

# ══ D. THE SCREENS THAT ASK ══════════════════════════════════════════════════════════════════════
SCREENS = {
    "frontend/src/app/(platform)/closing/verify/page.tsx": "/closing/verify",
    "frontend/src/app/(platform)/closing/submit/page.tsx": "/closing/submit",
    "frontend/src/app/(platform)/closing/pickup/page.tsx": "/closing/pickup",
    "frontend/src/app/(platform)/closing/deposit-recon/page.tsx": "/closing/deposit-recon",
}
for rel, here in SCREENS.items():
    body = read(rel, code_only=True)
    check(f"D1 {here} asks what comes next", f'<WorkflowNext here="{here}" />' in body)
    check(f"D2 {here} names ITSELF, so the chart is asked the right question",
          body.count("<WorkflowNext") == 1)
    check(f"D3 {here}'s stage exists in the workflow", here in ts_seq, ts_seq)

# DM Verify is mounted at two routes; the prompt belongs to the ROUTE, not the shared table.
shared = read("frontend/src/components/DailyClosingVerify.tsx", code_only=True)
check("D4 the shared verify component carries no hard-coded position in the workflow",
      "WorkflowNext" not in shared)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
