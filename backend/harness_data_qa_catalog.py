"""THE REPORT CATALOGUE IS DERIVED — generator and proof in one file.

    python3 backend/harness_data_qa_catalog.py --bless     # regenerate the catalogue
    python3 backend/harness_data_qa_catalog.py             # prove it matches, else FAIL THE BUILD

WHY (owner 2026-10-06, measured)
--------------------------------
Owner: *"not a good experience it is not giving the correct answers as expected"*, and before that,
plainly: *"we dont want to have any registered questions, the assistant should be able to answer all
questions"*.

Measured when this was written: `data_qa_registry.DATA_QUESTIONS` held **13 hand-written questions
reaching 11 endpoints**, against **58 reports** in `frontend/src/lib/reports.ts` and **815 GET
endpoints** in the app. Six of the fifty-eight were answerable. Gross Profit, KPI Metrics, Failing
KPIs, Activations, Zero Sales, Rep Coaching, Retention, Sales Comparison, Chargebacks, Pay
Discrepancy, Payroll, Closing, the Balance Sheet, Trends and the whole Watchdog family were not.
Asked about any of them the assistant answered from the nearest thing it did know, which reads as a
wrong answer. That is a COVERAGE defect, and a hand list is its cause, not its cure: the index rule
says a new report registers itself, and a rule that depends on remembering is a rule that decays.

THE CLASS: **a catalogue of what can be answered must be derived from what exists, or it drifts.**
Same fix as the derived page index (index 54.7): generate it from the registries that already
exist, and make the generator double as the proof so drift fails the build.

WHAT IS DERIVED, AND FROM WHERE
-------------------------------
  · label, answers, module  <- `frontend/src/lib/reports.ts` (the report's own wording — the thing
                               the owner reads on the Reports index, so the assistant and the screen
                               describe a report with the same sentence).
  · page                    <- the same registry's href.
  · path                    <- the GET endpoints the report's own page.tsx fetches, scored. A page
                               names several; the one that answers the report is picked by rule and
                               can be overridden by hand (the override is preserved across a bless).
  · params                  <- the live OpenAPI spec for that path, intersected with the registry's
                               own PARAM_KINDS. Only a parameter the registry can pattern-match is
                               offered, so nothing a model types becomes free-form input.

WHAT IS NOT DERIVED (and so is a human field, preserved across a re-bless)
  · `path`   — an override, when the rule picks the wrong one of a page's endpoints.
  · `skip`   — **the REASON** a report answers no question (a setup wizard, a schematic, an index).
               A sentence, never a bare flag, so an exclusion can never be silent.
  · `answers`— a rewording, when the report's own description does not read as a question.

NOT declared here either way: the COLUMNS a report returns. They are a fact about the endpoint and
`data_qa_agent` discovers them from the live response (index 52.1).
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
FE = os.path.join(os.path.dirname(ROOT), "frontend", "src")
APP_DIR = os.path.join(FE, "app")
REPORTS_TS = os.path.join(FE, "lib", "reports.ts")
OUT = os.path.join(ROOT, "app", "modules", "core", "data_qa_derived.py")

sys.path.insert(0, ROOT)
from app.modules.core.data_qa_registry import DATA_QUESTIONS, PARAM_KINDS  # noqa: E402

# ── which query parameters a derived question may offer ──────────────────────────────────────────
# The endpoint's own parameter NAMES, mapped onto the registry's pattern kinds. A name that is not
# here is not offered at all: the model gets the report without that filter rather than a free-form
# input. RULE TWO's sibling — a question is config, not a typed query.
PARAM_NAME_KIND = {
    "period": "period", "month": "period", "as_of_period": "period",
    "date": "date", "start": "date", "end": "date", "date_from": "date", "date_to": "date",
    "week_start": "date", "week_end": "date", "as_of": "date", "day": "date",
    "store": "store_code", "store_code": "store_code", "location": "store_code",
    "rep": "person", "employee": "person", "person": "person", "salesperson": "person",
}

# A path segment that names an ACTION rather than a read. A page fetches these too (a run-now, a
# scan, an import), and none of them answers a question.
ACTION = re.compile(r"/(run-now|run|scan[a-z-]*|import[a-z-]*|export[a-z-]*|upload[a-z-]*|send[a-z-]*"
                    r"|recompute|recalc[a-z-]*|calculate|compute|apply|save|set[a-z-]*|clear|delete|seed"
                    r"|resolve|approve|reject|dismiss|snooze|ack[a-z-]*|test[a-z-]*)(/|$)")
# A path that serves the CHROME of a page rather than its report: the filter pickers, the config it
# edits, the lookups it renders a dropdown from.
# Also the job state a dashboard polls (`calc-status`) and the lists a page renders a picker from
# (`stores`, `change-log`): real endpoints, but none of them is the report on the page.
CHROME = re.compile(r"/(filter-options|config|configs|settings|tenant-settings|options|carriers"
                    r"|store-aliases|dealer-code-map|metric-source-config|tenant-vertical|me|health"
                    r"|calc-status|[a-z-]*-status|status|stores|[a-z-]*change-log)(/|$)")


# ── reading the frontend's own registry ──────────────────────────────────────────────────────────
def _field(blk: str, name: str):
    m = re.search(name + r":\s*'((?:[^'\\]|\\.)*)'", blk)
    return m.group(1).replace("\\'", "'") if m else None


def report_rows(src: str | None = None):
    """Every entry of REPORT_CATEGORIES, in file order: href, label, desc, module."""
    src = src if src is not None else open(REPORTS_TS, encoding="utf-8").read()
    out = []
    for blk in re.findall(r"\{[^{}]*?href:\s*'[^']+'[^{}]*?\}", src):
        href = _field(blk, "href")
        if not href or not href.startswith("/"):
            continue
        out.append({"href": href.split("?")[0], "label": _field(blk, "label") or "",
                    "desc": _field(blk, "desc") or "", "module": _field(blk, "module")})
    # One entry per destination; the first wins (the categories list a page once).
    seen, rows = set(), []
    for r in out:
        if r["href"] in seen:
            continue
        seen.add(r["href"])
        rows.append(r)
    return rows


def _route_of(root: str, app_dir: str):
    rel = os.path.relpath(root, app_dir)
    segs = [s for s in rel.split(os.sep) if not (s.startswith("(") and s.endswith(")"))]
    return [s for s in segs if s not in (".", "")]


def page_file(href: str, app_dir: str | None = None):
    """The page.tsx that serves `href`.

    Route groups like `(platform)` are stripped, and a DYNAMIC segment matches any one segment —
    which is not a nicety: the eight Watchdog reports are all served by `/watchdog/[area]`, so a
    literal-only match declared them non-existent. An exact match always wins over a dynamic one."""
    app_dir = app_dir or APP_DIR
    want = [s for s in href.strip("/").split("/") if s]
    exact = dynamic = None
    for root, _dirs, files in os.walk(app_dir):
        if "page.tsx" not in files:
            continue
        segs = _route_of(root, app_dir)
        if len(segs) != len(want):
            continue
        if segs == want:
            exact = os.path.join(root, "page.tsx")
            break
        if all(a == b or (a.startswith("[") and a.endswith("]")) for a, b in zip(segs, want)):
            dynamic = dynamic or os.path.join(root, "page.tsx")
    return exact or dynamic


def page_endpoints(code: str):
    """Every /api/v1 path the page names, cut at the first interpolation or query string."""
    out = set()
    for raw in re.findall(r"""['"`](/api/v1/[^'"`]+)""", code):
        p = raw.split("${")[0].split("?")[0].rstrip("/")
        if p.count("/") >= 3 and not p.endswith("/api/v1"):
            out.add(p)
    return sorted(out)


def pick_path(href: str, candidates):
    """WHICH of a page's endpoints answers its report.

    A page fetches several and only one is the report; picking by rule rather than by hand is what
    keeps this file a derivation. The rule, in order, and the losers are kept so an ambiguity is
    REPORTED rather than silently resolved:

      1. an action or a chrome endpoint is never the report;
      2. an endpoint whose last segment IS the page's last segment wins outright;
      3. otherwise the one sharing the most words with the page path, shortest on a tie;
      4. a single survivor wins by default; none means the report has no readable endpoint.
    """
    tail = href.strip("/").split("/")[-1]
    words = {w for w in re.split(r"[/-]", href.strip("/")) if w}
    live = [c for c in candidates if not ACTION.search(c) and not CHROME.search(c)]
    if not live:
        return None, []
    # A singular endpoint beside its own plural is the DETAIL route (`/vip/invoice` next to
    # `/vip/invoices`): it answers about one row the model has no way to name. Drop it.
    plural = {c for c in live if c + "s" in live}
    live = [c for c in live if c not in plural] or live
    exact = [c for c in live if c.rstrip("/").split("/")[-1] == tail]
    if len(exact) == 1:
        return exact[0], [c for c in live if c != exact[0]]
    # A tail that SHARES ITS STEM with the page's beats one that merely shares a word: the
    # Activations report is `/activation-counts`, and word overlap alone picked `/metric-recon`
    # because it was shorter.
    stem = tail[:-1] if tail.endswith("s") and len(tail) > 3 else tail
    stemmed = [c for c in live if c.rstrip("/").split("/")[-1].startswith(stem)]
    pool = exact or stemmed or live
    scored = sorted(pool, key=lambda c: (-len(words & {w for w in re.split(r"[/-]", c) if w}), len(c), c))
    return scored[0], [c for c in live if c != scored[0]]


def resolve_in_spec(path: str, spec_paths: dict, want_tail: str = ""):
    """The REAL route for a path read off a page, and its path parameters.

    A page builds its URL by interpolation — `/api/v1/commcalc/gp/${period}` — and the literal
    prefix is not itself a route. So a prefix that is not in the spec is matched against the routes
    that extend it by path parameters only (`/api/v1/commcalc/gp/{period}`). Without this, every
    period-keyed report read as "no such endpoint", which is how 8 real reports looked unreachable."""
    if not path:
        return None, ()
    if path in spec_paths:
        return path, ()
    best = None
    for cand in spec_paths:
        if not cand.startswith(path.rstrip("/") + "/"):
            continue
        tail = [t for t in cand[len(path.rstrip("/")) + 1:].split("/") if t]
        if not tail:
            continue
        params = [t for t in tail if t.startswith("{") and t.endswith("}")]
        literals = [t for t in tail if t not in params]
        # Only path PARAMETERS may separate the prefix from the route — plus, when the page's own
        # address names it, one literal segment: `/commcalc/targets/action-plan` is served by
        # `/api/v1/commcalc/targets/{period}/action-plan`, and matching parameters alone picked the
        # bare targets report for three different pages.
        if literals and not (len(literals) == 1 and literals[0] == want_tail):
            continue
        # A detail route keyed by a row id answers about one row nothing here can name.
        if any(re.search(r"(^|_)(id|uuid|key)\}?$", t.strip("{}")) for t in params):
            continue
        rank = (0 if literals else 1, len(tail))
        if best is None or rank < best[2]:
            best = (cand, tail, rank)
    if not best:
        return None, ()
    return best[0], tuple(t.strip("{}") for t in best[1]
                          if t.startswith("{") and t.endswith("}"))


def spec_params(path: str, spec_paths: dict):
    """The offerable query parameters of `path`, read off the OpenAPI spec."""
    op = (spec_paths.get(path) or {}).get("get") or {}
    out = {}
    for prm in op.get("parameters") or []:
        name = prm.get("name")
        kind = PARAM_NAME_KIND.get(str(name))
        if kind and kind in PARAM_KINDS:
            out[name] = kind
    return out


# The reason the GENERATOR writes when a report has no readable endpoint. It is recomputed every
# bless, and must never be preserved as if a person had written it — that is how a report stays
# excluded after it becomes answerable, which happened on the first bless of this file: eight
# Watchdog reports kept yesterday's auto reason and sat out even though their endpoint resolved.
AUTO_SKIP = ("its page reads no report endpoint that resolves to a GET route — nothing to answer from")
# Earlier wordings of the same auto reason, so an older generated file is not mistaken for a ruling.
AUTO_SKIP_OLD = ("its page reads no report endpoint — nothing to answer from",)


def _skip(prev, path):
    """A human reason survives; the generator's own reason is recomputed."""
    if prev and prev != AUTO_SKIP and prev not in AUTO_SKIP_OLD:
        return prev
    return None if path else AUTO_SKIP


def bound_params(href: str, page_path: str | None, path_params):
    """Path parameters the PAGE already fixes, taken from its own URL.

    A dynamic page carries its subject in the address bar: `/watchdog/[area]` serves
    `/watchdog/cash`, and reads `/api/v1/commcalc/watchdog/area/{area}` with `area` = "cash". So the
    value is a fact about the report, not a question for the model — it is bound here, and the model
    is not offered it. The eight Watchdog reports are all one endpoint this way; without binding,
    they were all "no readable endpoint"."""
    if not page_path or not path_params:
        return {}
    segs = [s for s in href.strip("/").split("/") if s]
    names = [s for s in page_path.replace("\\", "/").split("/") if s.startswith("[") and s.endswith("]")]
    # Line the dynamic page's own segments up with the href's, by position.
    page_segs = [s for s in os.path.dirname(page_path).replace("\\", "/").split("/")]
    dyn = {}
    tail = [s for s in page_segs if s and not (s.startswith("(") and s.endswith(")"))]
    tail = tail[-len(segs):] if len(tail) >= len(segs) else tail
    for a, b in zip(tail, segs):
        if a.startswith("[") and a.endswith("]"):
            dyn[a.strip("[]")] = b
    if not names:
        return {}
    return {n: v for n, v in dyn.items() if n in set(path_params)}


def key_for(href: str):
    """A stable question key from the page path: the last two segments, snake_cased."""
    parts = [p for p in href.strip("/").split("/") if p]
    return re.sub(r"[^a-z0-9]+", "_", "_".join(parts[-2:]).lower()).strip("_") or "report"


# ── building ─────────────────────────────────────────────────────────────────────────────────────
def build(existing: dict | None = None, spec_paths: dict | None = None, rows=None, app_dir=None):
    """One derived entry per report, with the human fields of `existing` carried over."""
    rows = report_rows() if rows is None else rows
    spec_paths = spec_paths if spec_paths is not None else {}
    out = []
    hand_paths = {q["path"] for q in DATA_QUESTIONS.values()}
    for r in rows:
        key = key_for(r["href"])
        prev = (existing or {}).get(key) or {}
        f = page_file(r["href"], app_dir)
        cands = page_endpoints(open(f, encoding="utf-8").read()) if f else []
        picked, others = pick_path(r["href"], cands)
        # THE TRAP, twice over: a field that holds BOTH the generator's answer and a person's
        # override cannot tell them apart, so the generator's own stale answer survives as if it
        # were a ruling. It happened here with `skip` (eight Watchdog reports kept yesterday's auto
        # reason) and again with `path` (every improvement to the picking rule was a no-op because
        # the previous pick was preserved as an override). So each human field is its OWN field, and
        # the derived value is always recomputed.
        want_tail = r["href"].strip("/").split("/")[-1]
        # Both halves are resolved against the spec, so `picked` and `path` are comparable: a page
        # names a literal prefix (`/commcalc/gp`) and the route is `/commcalc/gp/{period}`.
        picked = (resolve_in_spec(picked, spec_paths, want_tail)[0] if picked else None)
        path = prev.get("override") or picked
        resolved, _ = resolve_in_spec(path, spec_paths, want_tail)
        # Read the placeholders off the RESOLVED route itself. Reading them off the resolution step
        # looked equivalent and was not: once `picked` is already resolved, resolving it again finds
        # it verbatim in the spec and reports no parameters, so every period-keyed report declared
        # none and `validate()` could never have filled one.
        path_params = tuple(re.findall(r"\{([^}]+)\}", resolved or ""))
        bound = bound_params(r["href"], f, path_params)
        # A path that resolves to no route at all is not an endpoint, whatever the page spells.
        path = resolved
        entry = {
            "key": key,
            "label": r["label"],
            # A rewording is a human field; the report's own description is the default, and it is
            # what the owner already reads on the Reports index.
            "answers": prev.get("answers_override") or r["desc"],
            "answers_override": prev.get("answers_override"),
            "page": r["href"],
            "path": path,
            # What the RULE chose, recomputed every bless, and the override that beats it (if any).
            "picked": picked,
            "override": prev.get("override"),
            "params": spec_params(path, spec_paths) if path else {},
            # Declared because the route demands them: a period-keyed report has no URL without one.
            # A parameter the page itself fixes is BOUND and never asked of the model.
            "path_params": [x for x in path_params if x not in bound],
            "bound": bound,
            "module": r["module"],
            # The REASON, never a flag. A report with no readable endpoint says so itself.
            "skip": _skip(prev.get("skip"), path),
            # Kept so an ambiguity is visible in the diff rather than resolved in silence.
            "also": others,
            # A report the hand-written registry already answers; the hand entry wins and this one
            # is carried only so the diff shows the overlap rather than hiding it.
            "hand": bool(path and path in hand_paths),
        }
        out.append(entry)
    return out


def render(entries):
    head = '''"""GENERATED — do not hand-edit except the human fields named below.

    python3 backend/harness_data_qa_catalog.py --bless

One entry per report in `frontend/src/lib/reports.ts`: the report's own label and description become
the question, its page's own data endpoint becomes what is read, and the endpoint's own parameters
become what may be filtered. Derived so that a report shipped tomorrow is answerable tomorrow —
`harness_data_qa_catalog.py` FAILS THE BUILD when this file and the reports disagree (index 54.11).

HUMAN FIELDS, preserved across a re-bless:
  · `path`    — an override, when the rule picked the wrong one of a page's endpoints (`also` lists
                the ones it did not pick).
  · `skip`    — THE REASON this report answers no question. A sentence, never a bare flag.
  · `answers` — a rewording, when the report's description does not read as a question.

Everything else is recomputed. Pure data: stdlib only, no imports, so the semantic layer stays
provable with no database.
"""
SCHEMA = 1

DERIVED = [
'''
    lines = [head]
    for e in entries:
        lines.append("    {\n")
        for f in ("key", "label", "answers", "page", "path", "picked", "override",
                  "answers_override", "module", "skip"):
            lines.append("        %r: %r,\n" % (f, e.get(f)))
        lines.append("        'params': %r,\n" % (e.get("params") or {},))
        lines.append("        'path_params': %r,\n" % (list(e.get("path_params") or ()),))
        lines.append("        'bound': %r,\n" % (e.get("bound") or {},))
        lines.append("        'also': %r,\n" % (list(e.get("also") or ()),))
        lines.append("        'hand': %r,\n" % (bool(e.get("hand")),))
        lines.append("    },\n")
    lines.append("]\n")
    return "".join(lines)


def parse(text: str):
    """Read the generated file back as data, WITHOUT importing it, so the blesser can verify its own
    write the way the module-graph blesser could not (it deleted 11 of 12 facts while printing OK)."""
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "DERIVED":
            try:
                return {e["key"]: e for e in ast.literal_eval(node.value)}
            except Exception:
                return None
    return None


def openapi_paths():
    from app.main import app
    return app.openapi().get("paths") or {}


def bless():
    existing = parse(open(OUT, encoding="utf-8").read()) if os.path.exists(OUT) else None
    entries = build(existing, openapi_paths())
    if not entries:
        print("REFUSED: nothing derived — the reports registry read empty")
        return 1
    want = len(report_rows())
    if len(entries) != want:
        print("REFUSED: derived %d entries for %d reports" % (len(entries), want))
        return 1
    open(OUT, "w", encoding="utf-8").write(render(entries))
    back = parse(open(OUT, encoding="utf-8").read())
    if back is None or len(back) != len(entries):
        print("REFUSED: the file did not read back as %d entries" % len(entries))
        return 1
    for e in entries:
        got = back.get(e["key"])
        if not got or got.get("path") != e["path"] or got.get("skip") != e["skip"] \
                or got.get("params") != e["params"] or got.get("answers") != e["answers"]:
            print("REFUSED: %s did not survive the write" % e["key"])
            return 1
    answerable = [e for e in entries if e["path"] and not e["skip"]]
    print("blessed: %d reports (%d answerable, %d skipped with a reason); verified by re-reading"
          % (len(entries), len(answerable), len(entries) - len(answerable)))
    return 0


# ── the proof ────────────────────────────────────────────────────────────────────────────────────
PASS = FAIL = 0


def ck(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % label)
    else:
        FAIL += 1
        print("  FAIL  %s   %s" % (label, detail))


def section(title):
    print("\n%s" % title)


def main():
    if not os.path.exists(OUT):
        print("FAIL — %s does not exist. Run with --bless." % OUT)
        return 1
    text = open(OUT, encoding="utf-8").read()
    have = parse(text)
    rows = report_rows()
    spec = openapi_paths()
    want = {e["key"]: e for e in build(have, spec)}

    section("A. every report that exists is in the catalogue")
    ck("A1  the file parses as one entry per line", have is not None)
    ck("A2  one entry per report", have is not None and len(have) == len(rows),
       "%s entries vs %s reports" % (len(have or {}), len(rows)))
    missing = sorted(set(want) - set(have or {}))
    ck("A3  no report is missing", not missing, missing[:8])
    extra = sorted(set(have or {}) - set(want))
    ck("A4  no entry names a report that is gone", not extra, extra[:8])
    ck("A5  the derivation is settled — a re-bless would change nothing",
       all((have or {}).get(k, {}).get("path") == v["path"] for k, v in want.items()),
       [k for k, v in want.items() if (have or {}).get(k, {}).get("path") != v["path"]][:6])
    ck("A5b every path is the rule's pick or a declared override",
       all(e.get("path") == (e.get("override") or e.get("picked")) for e in (have or {}).values()),
       [k for k, e in (have or {}).items()
        if e.get("path") != (e.get("override") or e.get("picked"))][:6])
    ck("A6  and its parameters are settled too",
       all((have or {}).get(k, {}).get("params") == v["params"] for k, v in want.items()),
       [k for k, v in want.items() if (have or {}).get(k, {}).get("params") != v["params"]][:6])

    section("B. an exclusion carries a REASON, never a bare flag")
    skipped = {k: e for k, e in (have or {}).items() if e.get("skip")}
    bad = [k for k, e in skipped.items() if not isinstance(e["skip"], str) or len(e["skip"].split()) < 3]
    ck("B1  every skip is a sentence", not bad, bad[:8])
    ck("B2  a skipped report offers no endpoint",
       all(not e.get("path") or e.get("skip") for e in skipped.values()))
    ck("B3  an answerable report HAS an endpoint",
       all(e.get("path") for k, e in (have or {}).items() if not e.get("skip")),
       [k for k, e in (have or {}).items() if not e.get("skip") and not e.get("path")][:8])

    section("C. the catalogue says only what exists")
    ck("C1  every path is a real GET route",
       all(e["path"] in spec for e in (have or {}).values() if e.get("path")),
       [e["path"] for e in (have or {}).values() if e.get("path") and e["path"] not in spec][:8])
    ck("C2  every page is a real page",
       all(page_file(e["page"]) for e in (have or {}).values()),
       [e["page"] for e in (have or {}).values() if not page_file(e["page"])][:8])
    ck("C3  no parameter is offered that the registry cannot pattern-match",
       all(k in PARAM_KINDS for e in (have or {}).values() for k in (e.get("params") or {}).values()))
    ck("C4  no action or chrome endpoint was picked as a report",
       not [e["path"] for e in (have or {}).values()
            if e.get("path") and (ACTION.search(e["path"]) or CHROME.search(e["path"]))],
       [e["path"] for e in (have or {}).values()
        if e.get("path") and (ACTION.search(e["path"]) or CHROME.search(e["path"]))][:8])

    section("D. coverage — the defect this file exists to fix")
    answerable = [e for e in (have or {}).values() if e.get("path") and not e.get("skip")]
    ck("D1  the catalogue answers MOST of the reports, not a handful",
       len(answerable) >= int(0.7 * len(rows)),
       "%d of %d" % (len(answerable), len(rows)))
    # THE REGRESSION, by name: the reports the owner asked about on 2026-10-06 that the hand list
    # could not reach. Each must now be answerable.
    REGRESSION = ["/commcalc/gp", "/commcalc/kpi", "/commcalc/kpi-failing", "/commcalc/activations",
                  "/commcalc/zero-sales", "/commcalc/coaching", "/commcalc/sales-comparison",
                  "/commcalc/chargebacks", "/account/balance-sheet", "/closing"]
    pages = {e["page"]: e for e in (have or {}).values()}
    unreachable = [p for p in REGRESSION if p in pages and not pages[p].get("path")]
    ck("D2  every report named in the report is answerable", not unreachable, unreachable)

    section("E. the human half survives a re-bless")
    probe = dict(have or {})
    k0 = sorted(k for k, e in probe.items() if e.get("path"))[0]
    probe[k0] = dict(probe[k0], override="/api/v1/core/filter-options",
                     answers_override="hand reworded",
                     skip="a hand-written reason that must survive")
    again = {e["key"]: e for e in build(probe, spec)}
    ck("E1  a path override survives", again[k0]["path"] == "/api/v1/core/filter-options")
    ck("E2  a rewording survives", again[k0]["answers"] == "hand reworded")
    ck("E3  a skip reason survives", again[k0]["skip"] == "a hand-written reason that must survive")
    ck("E3b ARMED — the generator's OWN reason does NOT survive once an endpoint resolves",
       build({k0: dict((have or {})[k0], skip=AUTO_SKIP)}, spec)[0] is not None
       and {e["key"]: e for e in build({k: dict(v, skip=AUTO_SKIP) for k, v in (have or {}).items()},
                                       spec)}[k0]["skip"] is None)
    ck("E4  ARMED — without the override the rule's own pick comes back",
       build({}, spec)[0]["path"] == build({}, spec)[0]["picked"])
    ck("E4b ARMED — the generator's OWN pick never survives as an override",
       all(e.get("override") is None or e["override"] != e["picked"]
           for e in (have or {}).values())
       and {e["key"]: e for e in build({k: dict(v, override=None) for k, v in (have or {}).items()},
                                       spec)}[k0]["path"] == want[k0]["picked"])
    ck("E5  ARMED — a report removed from the frontend registry leaves the catalogue",
       len(build(have, spec, rows=rows[:-1])) == len(rows) - 1)
    ck("E6  ARMED — a report added to the frontend registry enters it",
       len(build(have, spec, rows=rows + [{"href": "/zzz/new-thing", "label": "New Thing",
                                           "desc": "something", "module": None}])) == len(rows) + 1)

    section("G. a route's own placeholders are declared or bound")
    for k, e in sorted((have or {}).items()):
        if not e.get("path"):
            continue
        holes = set(re.findall(r"\{([^}]+)\}", e["path"]))
        ck("G1  %s declares or binds every placeholder in its route" % k,
           holes <= set(e.get("path_params") or ()) | set(e.get("bound") or {}),
           sorted(holes - (set(e.get("path_params") or ()) | set(e.get("bound") or {}))))
    ck("G2  a period-keyed report declares its period",
       all("period" in set(e.get("path_params") or ()) for e in (have or {}).values()
           if e.get("path") and "{period}" in e["path"]))
    ck("G3  and the catalogue is not all placeholder-free by accident",
       any("{" in (e.get("path") or "") for e in (have or {}).values()))

    section("F. the picker's rule, stated as cases")
    ck("F1  an exact tail match wins",
       pick_path("/commcalc/gp", ["/api/v1/commcalc/gp", "/api/v1/commcalc/gp-trend"])[0]
       == "/api/v1/commcalc/gp")
    ck("F2  an action endpoint is never picked",
       pick_path("/commcalc/zero-sales", ["/api/v1/commcalc/zero-sales/alerts/run-now",
                                          "/api/v1/commcalc/zero-sales"])[0]
       == "/api/v1/commcalc/zero-sales")
    ck("F3  chrome is never picked",
       pick_path("/commcalc/zero-sales", ["/api/v1/core/filter-options"])[0] is None)
    ck("F4  the ones not picked are reported, not dropped",
       pick_path("/commcalc/gp", ["/api/v1/commcalc/gp", "/api/v1/commcalc/gp-trend"])[1]
       == ["/api/v1/commcalc/gp-trend"])
    ck("F5  a page with nothing readable yields nothing", pick_path("/x/y", [])[0] is None)
    ck("F6  a dynamic page binds its own subject from the URL",
       bound_params("/watchdog/cash", "src/app/(platform)/watchdog/[area]/page.tsx", ("area",))
       == {"area": "cash"})
    ck("F7  and a parameter the page does not fix stays unbound",
       bound_params("/commcalc/gp", "src/app/(platform)/commcalc/gp/page.tsx", ("period",)) == {})
    ck("F8  a bound parameter is not also asked of the model",
       all(not (set(e.get("bound") or {}) & set(e.get("path_params") or ()))
           for e in (have or {}).values()))
    # THE REGRESSION for the dynamic case: all eight Watchdog reports are one endpoint, bound.
    wd = [e for e in (have or {}).values() if e["page"].startswith("/watchdog/")]
    ck("F10 a shared stem beats a shared word",
       pick_path("/commcalc/activations", ["/api/v1/commcalc/metric-recon",
                                           "/api/v1/commcalc/activation-counts"])[0]
       == "/api/v1/commcalc/activation-counts")
    ck("F9  every Watchdog area is answerable", wd and all(e.get("path") for e in wd),
       [e["page"] for e in wd if not e.get("path")])

    print("\n%d passed, %d failed" % (PASS, FAIL))
    if FAIL:
        print("The report catalogue and the reports disagree. Run: "
              "python3 backend/harness_data_qa_catalog.py --bless")
        return 1
    print("OK — every report the platform has is a question the assistant can be asked, "
          "or carries the reason it is not.")
    return 0


if __name__ == "__main__":
    sys.exit(bless() if "--bless" in sys.argv else main())
