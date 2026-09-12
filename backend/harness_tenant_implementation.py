"""Offline proof (no DB/network) for THE TENANT IMPLEMENTATION SPINE.

OWNER COMPLAINT 2026-09-12 (verbatim):
    "Similar to the Cash Deposit Workflow, we need to organize the set up of a new tenant in an
     organized way, right now we have too many modules which do not have a flow and one thing leads
     to the other by links on their respective pages to a different module altogether, the
     implementation wizard should only give options relevant to the carrier they are working with
     with an option to add a carrier and then surfacing their respective upload links and automation
     links, the automation links could be linked to the upload links."

AND, on a real carrier commission export (47,253 rows), the addition that makes the mapping half of
this honest: `Invoiced At` holds a STORE NAME, `Related Tracking Number` holds the IMEI, and `Region`
holds a PERSON's name. A mapper that matches on column names is confidently wrong three times on one
file, so a proposal must carry its basis AND the values, and a human must confirm.

§A IS THE POINT OF THIS FILE, and it is the same job `harness_workflow_stacking.py` §A does for the
closing tile: it parses the ordered sequence out of the BACKEND (implementation_spine.SPINE) and
compares it, in order, against the sequence parsed out of the TYPESCRIPT runbook. Those two are in
different languages, so neither `tsc` nor a Python linter can see them drift — and a flow whose steps
disagree with the training material is worse than no flow at all.

§B pins the one refactor that could have broken an unrelated, working feature: `stagesAfter` used to
read `CLOSING_WORKFLOW` directly and now asks `runbookForScreen`. That is byte-identical for the
closing screens ONLY because `dailyClosing` is first in FLOWCHARTS. If someone reorders that array,
every closing screen would start walking a different chain — silently. This proves it cannot.

Run: `cd backend && python3 harness_tenant_implementation.py`
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harnesslib import js_code_only   # noqa: E402
from app.modules.commcalc import implementation_spine as spine   # noqa: E402  (PURE — no DB import)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def read(rel, code_only=False):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        raw = fh.read()
    return js_code_only(raw) if code_only else raw


FLOWCHARTS = "frontend/src/lib/flowcharts.tsx"
ONBOARD_PAGE = "frontend/src/app/(platform)/commcalc/onboarding/page.tsx"
IMPL_PAGE = "frontend/src/app/(platform)/commcalc/implementation/page.tsx"
UPLOAD_PAGE = "frontend/src/app/(platform)/commcalc/upload/page.tsx"
SPINE_PY = "backend/app/modules/commcalc/implementation_spine.py"
ROUTER = "backend/app/modules/commcalc/router.py"

lib_raw = read(FLOWCHARTS)
lib = read(FLOWCHARTS, code_only=True)


def ts_stages(const_name):
    """The ordered stage hrefs of ONE runbook in flowcharts.tsx, parsed from its own block."""
    i = lib.index(f"const {const_name}: Runbook = {{")
    block = lib[i:].split("stages: [", 1)[1].split("\n  ],", 1)[0]
    return re.findall(r"href:\s*'([^']+)'", block)


# ══ A. ONE SEQUENCE, TWO LANGUAGES ═══════════════════════════════════════════════════════════════
ts_seq = ts_stages("tenantImplementation")
py_seq = spine.SPINE_HREFS

check("A1 the backend defines an ordered implementation spine", len(py_seq) >= 5, py_seq)
check("A2 the runbook defines the same number of stages", len(ts_seq) == len(py_seq),
      f"ts={ts_seq} py={py_seq}")
check("A3 THE BACKEND SPINE AND THE RUNBOOK ARE THE SAME SEQUENCE, IN THE SAME ORDER — this is the "
      "check that stops the wizard's steps drifting from the training material, across a language "
      "boundary neither tsc nor a Python linter can see",
      ts_seq == py_seq, f"ts={ts_seq} py={py_seq}")

# The labels a user reads must match too: a step called one thing in the wizard and another in the
# runbook is the same drift wearing a disguise.
i = lib.index("const tenantImplementation: Runbook = {")
ts_block = lib[i:].split("stages: [", 1)[1].split("\n  ],", 1)[0]
ts_labels = re.findall(r"label:\s*'([^']+)'", ts_block)
check("A4 ... and the same step LABELS, in the same order",
      ts_labels == [s["label"] for s in spine.SPINE], f"ts={ts_labels}")

check("A5 the runbook is registered, so /training/flowcharts/<slug> resolves",
      "tenantImplementation]" in lib or "tenantImplementation," in lib.split("FLOWCHARTS: Runbook[]")[1])
check("A6 its slug is the one the flow strip and the prompt link to",
      "slug: 'tenant-implementation'" in lib
      and "/training/flowcharts/tenant-implementation" in read(ONBOARD_PAGE))
check("A7 every spine step names a distinct screen — a flow that revisits a screen is not a sequence",
      len(set(py_seq)) == len(py_seq), py_seq)

# Each stage must carry the two things WorkflowNext renders, or the prompt is a bare link.
check("A8 every backend stage says who it is for and what you do there",
      all(s.get("who") and s.get("does") for s in spine.SPINE))
check("A9 every stage but the last says what it HANDS OVER — the reason to go there, not just the "
      "instruction to", all(s.get("handoff") for s in spine.SPINE[:-1]))

# ══ B. ONE SEQUENCER, N WORKFLOWS — and the closing chain is untouched ════════════════════════════
check("B1 stagesAfter asks which runbook this screen belongs to, rather than one hardcoded workflow",
      "const book = runbookForScreen(href)" in lib and "book.stages.slice(i + 1)" in lib)
check("B2 nextStage is defined in terms of stagesAfter — one traversal, not two",
      "return stagesAfter(href)[0]" in lib)

order = re.search(r"FLOWCHARTS: Runbook\[\] = \[([^\]]+)\]", lib).group(1)
names = [n.strip() for n in order.split(",") if n.strip()]
check("B3 dailyClosing is FIRST in FLOWCHARTS — this is WHY the refactor is byte-identical for the "
      "closing screens (runbookForScreen returns the first match, and dailyClosing.stages IS "
      "CLOSING_WORKFLOW). Reordering this array would silently re-route every closing prompt",
      names and names[0] == "dailyClosing", names)
check("B4 CLOSING_WORKFLOW is still dailyClosing.stages", "CLOSING_WORKFLOW = dailyClosing.stages" in lib)

closing = ts_stages("dailyClosing")
for href in ("/closing/verify", "/closing/submit", "/closing/pickup", "/closing/deposit-recon"):
    check(f"B5 {href} still resolves to the closing chain, not the new one",
          href in closing and href not in ts_seq)
check("B6 the two workflows share no screen, so no prompt is ambiguous",
      not (set(closing) & set(ts_seq)), sorted(set(closing) & set(ts_seq)))

# ══ C. RULE TWO — NOT ONE CARRIER NAME, ANYWHERE IN THIS FEATURE ══════════════════════════════════
# The vocabulary the house guard bans, plus the carriers/POS this work was prompted by. If any of
# these reaches the spine, the flow has moved carrier knowledge back into code.
CARRIER_WORDS = re.compile(
    r"\bboost\b|vidapay|t-?cetra|total\s+wireless|\bverizon\b|\bcricket\b|metro\s+by|\bepay\b"
    r"|\bacima\b|pay-?go|b2bsoft|b2b\s+soft|\bvip\s+wireless\b", re.I)

def py_code_only(src):
    """Python source with comments and docstrings removed.

    THE CLAIM IS ABOUT BEHAVIOUR, NOT PROSE. These files deliberately NAME the carrier vocabulary
    they removed — "the literal `'boost' | 'total'` union made a third carrier unrepresentable" is
    the reason the change exists, and deleting that sentence to satisfy a regex would make the code
    harder to understand in order to look cleaner. What must be true is that no carrier name
    REACHES A DECISION: no branch, no literal, no comparison. So the scan is over code only."""
    import io as _io
    import tokenize as _tok
    out, prev_type = [], _tok.INDENT
    for t in _tok.generate_tokens(_io.StringIO(src).readline):
        if t.type == _tok.COMMENT:
            continue
        # A STRING that is the whole statement is a docstring (module/class/def or a bare literal).
        if t.type == _tok.STRING and prev_type in (_tok.INDENT, _tok.DEDENT, _tok.NEWLINE, _tok.NL):
            prev_type = t.type
            continue
        out.append(t.string)
        if t.type not in (_tok.NL, _tok.NEWLINE):
            prev_type = t.type
        else:
            prev_type = t.type
    return " ".join(out)


for rel, body in ((SPINE_PY, py_code_only(read(SPINE_PY))),
                  (ONBOARD_PAGE, read(ONBOARD_PAGE, code_only=True))):
    hits = sorted({m.group(0).lower() for m in CARRIER_WORDS.finditer(body)})
    check(f"C1 {os.path.basename(rel)} names NO carrier, processor or POS IN ITS CODE — the scoping "
          f"is rows, never a branch on a name", not hits, hits)

impl_block = lib_raw[lib_raw.index("const implFigure"):lib_raw.index("// ── the registry")]
hits = sorted({m.group(0).lower() for m in CARRIER_WORDS.finditer(impl_block)})
check("C2 the tenant-implementation runbook names no carrier either — the training material must "
      "read the same for every tenant", not hits, hits)

up = read(UPLOAD_PAGE)
up_code = js_code_only(up)
check("C3 THE UPLOAD PAGE'S CARRIER UNION IS GONE — a two-name union made a third carrier "
      "unrepresentable, so a tenant could not be offered their own report",
      "'boost' | 'total'" not in up_code and "'total' | 'boost'" not in up_code)
check("C4 ... and carrier scope is now read from the registry endpoint",
      "/api/v1/commcalc/upload-registry" in up and "carrierScope" in up)
check("C5 ... with the shipped tag demoted to a FALLBACK the registry overrides",
      "carrierScope[id]?.carrier_code || fallback" in up_code)
check("C6 ... and the fallback field is an open string, so any carrier can be expressed",
      "carrier?: string" in up_code)

# ══ D. THE PURE LOGIC (this is what DB-free buys us) ══════════════════════════════════════════════
C1, C2 = "c-one", "c-two"
carriers = [{"id": C1, "name": "Carrier One", "code": "one", "is_default": True},
            {"id": C2, "name": "Carrier Two", "code": "two"}]
have = {C1: "Carrier One", C2: "Carrier Two"}

check("D1 a carrier-agnostic row is shown to everyone",
      spine.carrier_visible({"carrier_id": None}, have))
check("D2 a row for a carrier the tenant runs is shown",
      spine.carrier_visible({"carrier_id": C1}, have))
check("D3 a row for a carrier the tenant does NOT run is hidden — this is the clutter the owner "
      "was complaining about", not spine.carrier_visible({"carrier_id": "c-other"}, have))
check("D4 with NO carrier list, nothing is filtered — a failed carrier lookup must never hide a "
      "report somebody needs to upload", spine.carrier_visible({"carrier_id": "c-other"}, {}))

# The upload route: only a rooted path is a destination.
check("D5 a rooted upload_endpoint is used as-is",
      spine.upload_href({"upload_endpoint": "/commcalc/ma-upload"}) == "/commcalc/ma-upload")
check("D6 an UNROOTED fragment is NOT turned into a route by gluing a slash on — that would "
      "manufacture an href that may not exist",
      spine.upload_href({"upload_endpoint": "commcalc/upload/sales"}) == spine.MAPPING_HREF)
check("D7 a non-route token falls back to the one screen that can map+import any report key",
      spine.upload_href({"upload_endpoint": "custom"}) == spine.MAPPING_HREF)
check("D8 so does a blank/absent endpoint", spine.upload_href({}) == spine.MAPPING_HREF)
check("D8b every item carries a FALLBACK screen, because a live upload_endpoint may be an API route "
      "rather than a page (mig 1004 seeds '/commcalc/upload-mapped', the ingest endpoint) — the UI "
      "gates the primary on its own NAV entry and uses this when it cannot be opened",
      all(i["upload"]["fallback_href"] == spine.MAPPING_HREF
          for b in spine.build([{"id": "c", "name": "C"}],
                               [{"report_key": "r", "carrier_id": "c",
                                 "upload_endpoint": "/commcalc/upload-mapped"}], [])["blocks"]
          for i in b["items"]))

# THE BINDING the owner asked for.
conns = {"k1": {"id": "k1", "vendor_name": "Portal A", "label": "Portal A",
                "sweep_kind": "sweepa", "enabled": True, "automatable": True},
         "k2": {"id": "k2", "vendor_name": "Portal B", "sweep_kind": "sweepb",
                "enabled": True, "automatable": False}}
a = spine.automation_for({"connector_id": "k1", "auto": True}, conns)
check("D9 A REPORT'S AUTOMATION IS RESOLVED FROM THE REPORT ROW ITSELF (connector_id -> connector) "
      "— the owner's 'the automation links could be linked to the upload links', answered by config "
      "that has existed since mig 039 rather than by a new table",
      a and a["connector_id"] == "k1" and a["state"] == "on", a)
check("D10 a connector that is ON but not pulling THIS report reads 'off', not 'on'",
      (spine.automation_for({"connector_id": "k1", "auto": False}, conns) or {})["state"] == "off")
check("D11 a source that cannot be automated says so plainly, and is not offered as switchable",
      (spine.automation_for({"connector_id": "k2", "auto": True}, conns) or {})["state"] == "manual_only")
check("D12 ... and explains why, because 'manual only' is a vendor fact, not a thing to retry",
      (spine.automation_for({"connector_id": "k2", "auto": True}, conns) or {}).get("note"))
check("D13 a report naming NO connector returns None — a real state, not a link to a page that "
      "cannot help", spine.automation_for({"connector_id": None}, conns) is None)
check("D14 a report naming a connector that does not exist also returns None, rather than inventing "
      "one", spine.automation_for({"connector_id": "gone"}, conns) is None)

defs = [
    {"report_key": "r_one", "label": "Report One", "carrier_id": C1, "connector_id": "k1",
     "auto": True, "upload_endpoint": "/commcalc/ma-upload", "sort_order": 10},
    {"report_key": "r_two", "label": "Report Two", "carrier_id": C1, "connector_id": None,
     "auto": False, "sort_order": 20},
    {"report_key": "r_three", "label": "Report Three", "carrier_id": "c-other", "sort_order": 5},
    {"report_key": "r_shared", "label": "Shared Report", "carrier_id": None, "sort_order": 30},
]
readiness = {"r_one": {"required": 4, "required_mapped": 4, "ready": True},
             "r_two": {"required": 4, "required_mapped": 1, "ready": False}}
built = spine.build(carriers, defs, list(conns.values()), readiness, {"r_one"})
blocks = {b["carrier_name"]: b for b in built["blocks"]}

check("D15 a foreign carrier's report never reaches the flow",
      all(i["report_key"] != "r_three" for b in built["blocks"] for i in b["items"]))
check("D16 the tenant's own carrier gets its own reports, in registry order",
      [i["report_key"] for i in blocks["Carrier One"]["items"]] == ["r_one", "r_two"])
check("D17 A CARRIER WITH NOTHING REGISTERED IS NAMED, NOT DROPPED — an unfinished implementation "
      "must not look finished",
      blocks["Carrier Two"]["total"] == 0 and blocks["Carrier Two"]["empty_reason"]
      and blocks["Carrier Two"]["empty_next"], blocks.get("Carrier Two"))
check("D18 carrier-agnostic reports appear ONCE in their own block, not repeated under every "
      "carrier (which would tell a two-carrier tenant to upload the same file twice)",
      blocks["Every carrier"]["total"] == 1
      and sum(1 for b in built["blocks"] for i in b["items"] if i["report_key"] == "r_shared") == 1)
check("D19 mapping readiness is REUSED from the existing readiness payload, not recomputed",
      blocks["Carrier One"]["items"][0]["mapping"]["ready"] is True
      and blocks["Carrier One"]["items"][1]["mapping"]["required_mapped"] == 1)
check("D20 PHASE-3 HOOK: which sample reports a carrier supplied, and how confidently each mapped, "
      "is already answerable with no new schema",
      blocks["Carrier One"]["items"][0]["mapping"]["sample_seen"] is True
      and blocks["Carrier One"]["items"][1]["mapping"]["sample_seen"] is False
      and blocks["Carrier One"]["items"][1]["mapping"]["confidence"] == 0.25)
check("D21 progress counts distinguish 'nothing to do' from 'nothing set up'",
      built["progress"]["carriers_without_feeds"] == ["Carrier Two"], built["progress"])
check("D22 the carrier pick-list is built FROM THE ROWS — the literal ['Boost','Total','Other'] is "
      "gone from the wizard profile",
      [o["label"] for o in built["carrier_options"]] == ["Carrier One", "Carrier Two"])
check("D23 add-a-carrier points at the endpoint that already exists, rather than a new one",
      built["add_carrier"]["endpoint"] == "/commcalc/carriers")

# The upload-tile scope map.
scope = spine.upload_scope_map(defs, list(conns.values()), carriers,
                               lambda v: (v or "").strip().lower())
check("D24 a report registered to a carrier the tenant runs is scoped to it",
      scope.get("r_one", {}).get("carrier_code") == "one")
check("D25 an auto-source is scoped by its connector's sweep_kind, which IS the tile's id",
      "sweepa" not in scope or scope["sweepa"]["carrier_code"] == "one")
check("D26 a carrier-agnostic report is ABSENT from the map — the caller reads absence as "
      "'always show', never as 'hide'", "r_shared" not in scope)
check("D27 a FOREIGN carrier's report is also absent, so it can never scope a tile to a carrier "
      "the tenant does not run", "r_three" not in scope)

# ══ E. THE SCREENS THAT ASK ══════════════════════════════════════════════════════════════════════
SCREENS = {
    "frontend/src/app/(platform)/commcalc/onboarding/page.tsx": "/commcalc/onboarding",
    "frontend/src/app/(platform)/commcalc/connectors/page.tsx": "/commcalc/connectors",
    "frontend/src/app/(platform)/commcalc/implementation/page.tsx": "/commcalc/implementation",
    "frontend/src/app/(platform)/commcalc/store-match/page.tsx": "/commcalc/store-match",
    "frontend/src/app/(platform)/commcalc/email-imports/page.tsx": "/commcalc/email-imports",
}
for rel, here in SCREENS.items():
    body = read(rel, code_only=True)
    check(f"E1 {here} asks what comes next", f'<WorkflowNext here="{here}" />' in body)
    check(f"E2 {here} names ITSELF, once — so the chart is asked the right question, and asked once",
          body.count("<WorkflowNext") == 1)
    check(f"E3 {here} is a stage of this workflow", here in py_seq, py_seq)

# ══ F. THE REFUSALS ══════════════════════════════════════════════════════════════════════════════
onb = read(ONBOARD_PAGE, code_only=True)
check("F1 the flow strip gates every step on the destination's OWN nav entry — the shared predicate, "
      "not a new one", "useCanOpen" in onb and "@/components/ScreenLink" in onb)
check("F2 a step the viewer may not open is shown as plain text, never as a link to a 403",
      "canOpen(s.href)" in onb)
check("F3 add-a-carrier is a first-class action IN the flow, posting to the existing endpoint",
      "/api/v1/commcalc/carriers" in onb and "Add a carrier" in read(ONBOARD_PAGE))
check("F4 ... and it is reachable even before the profile is complete, so the flow cannot deadlock "
      "the one action that unblocks it (the §23l shape)",
      "profileStep?.done && <CarrierFlow" not in onb and "<CarrierFlow" in onb)

impl = read(IMPL_PAGE)
check("F5 THE MAPPER SHOWS ITS BASIS: the confidence that produced each proposal is rendered, not "
      "just consumed", "basis[f.target_field]" in impl and "sg.confidence" in impl)
check("F6 ... and the SAMPLE VALUES of the proposed column, which is the only thing that catches a "
      "column whose name lies about its contents",
      "samples[src[f.target_field]]" in impl and "What is actually in it" in impl)
check("F7 ... and the weakest match is marked as weak rather than shown as a result",
      "'fuzzy'" in impl and "the weakest kind of match" in impl)
check("F8 the copy calls them PROPOSALS and tells the reader not to trust the column name",
      "PROPOSALS" in impl and "not proof of what is in it" in impl)
router = read(ROUTER)
check("F9 the detect endpoint returns sample values alongside the suggestions",
      '"samples": samples' in router)

# THE REGRESSION, against the real file's shape. This reproduces the exact extraction the endpoint
# does — including a REPEATED header, which `df[name]` would return as a DataFrame and 500 on — and
# proves the three columns whose NAMES lie about their contents are each shown for what they are.
_headers = ["Invoiced At", "Related Tracking Number", "Tracking Number", "Region", "Region"]
_rows = [["Wireless Zone Brooklyn", "355123456789012", "TN-1", "Dana Alvarez", "dup"],
         ["Wireless Zone Brooklyn", "355123456789013", "TN-2", "Dana Alvarez", "dup"]]
_samples = {}
for _i, _h in enumerate(_headers):
    _vals = []
    for _r in _rows[:5]:
        _v = "" if _r[_i] is None else str(_r[_i]).strip()
        if _v and _v.lower() not in ("nan", "none", "nat"):
            _vals.append(_v[:60])
    _samples.setdefault(_h, _vals[:3])
check("F9a A COLUMN CALLED `Invoiced At` IS SHOWN HOLDING A STORE NAME — the values are what stop a "
      "date field being mapped to it", _samples["Invoiced At"][0] == "Wireless Zone Brooklyn")
check("F9b `Related Tracking Number` is shown holding a 15-digit IMEI, and `Tracking Number` is "
      "shown holding something else entirely — the two are distinguishable ONLY by their values",
      len(_samples["Related Tracking Number"][0]) == 15
      and _samples["Tracking Number"][0] != _samples["Related Tracking Number"][0])
check("F9c `Region` is shown holding a person's name, not a geography",
      _samples["Region"][0] == "Dana Alvarez")
check("F9d a REPEATED header does not break the extraction (the first occurrence wins) — indexing "
      "by name would return a DataFrame and 500 the one call that reveals what is in the file",
      len(_samples) == 4 and _samples["Region"][0] == "Dana Alvarez")
check("F9e the endpoint indexes POSITIONALLY for that reason", "df.iloc[:, i]" in router)
check("F9f ... and a failed sample never fails the detect — a nicety must not break the call",
      "sample values unavailable" in router)
check("F10 nothing is saved by detecting: saving is still a separate, human action",
      "Save mappings" in impl)

check("F11 add-a-POS-system writes the EXISTING report_term vocabulary, not a new registry",
      "report-labels" in impl and "pos_system" in impl)
check("F12 add-a-report-type writes the EXISTING report_definitions registry, carrier-scoped",
      "report-definitions" in impl and "carrier_id: carrierId" in impl)
check("F13 ... and SAYS what it has not done — a registered report still has no fields until they "
      "are defined on Column Mapping", "Column Mapping" in impl and "does not by itself" in impl)

check("F14 there is ONE carrier-visibility predicate: the router delegates to the pure module",
      "return implementation_spine.carrier_visible(d, carriers)" in router)
check("F15 readiness is computed ONCE and shared, rather than derived a second way for the flow",
      router.count("def _readiness_payload") == 1
      and router.count("_readiness_payload(") >= 3, router.count("_readiness_payload("))
check("F15b the flow bounds the per-report registry lookups to the tenant's OWN registered reports, "
      "so adding mapping status to the setup page does not turn one load into twenty round-trips",
      "only_keys={d.get(\"report_key\") for d in reg_defs" in router)
check("F15c ... and the Implementation Wizard passes no filter, so its payload is unchanged",
      "_readiness_payload(sb(), org_id, carrier_id)" in router)
check("F16 NO SIXTH WIZARD: the flow extends the existing Setup Wizard payload rather than mounting "
      "a new onboarding route",
      '"implementation": implementation' in router
      and not os.path.exists(os.path.join(ROOT, "frontend/src/app/(platform)/commcalc/setup")))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
