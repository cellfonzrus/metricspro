"""THE LOCK — every upload surface dereferences the report-kind registry, and no second list exists.

CLAUDE.md, "A fix is a DESIGN fix": *"Lock it so it cannot un-wire. A design fix ships with a check
that FAILS THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears."*
Design §7: *"A build-failing check enumerates every surface that renders an upload choice and asserts
it calls the one visibility function against the registry; a second list of report kinds anywhere in
code fails the build."*

WHAT FAILS THE BUILD
  (a) an UPLOAD SURFACE that does not read the registry. The surfaces are the five files of the
      2026-09-20 measurement plus every file that IMPORTS an intake upload primitive (`Dropzone` /
      `KindDetectZone` from intake-shared) or that renders a file input beside report-kind / route
      keys. Each must import `useReportKinds` from '@/lib/report-kinds' (the ONE hook, which is the
      ONE caller of `reportKindsVisible`, which fetches the ONE endpoint) — or, for the shared
      primitive itself, take the visible set as a prop and fetch nothing.
  (b) a SECOND COPY of the fact: a filename-pattern glob, or a string-literal list of ≥3 report-kind /
      upload-route keys in frontend/src/{app,components,lib}/** — outside the ALLOW set below, where
      every entry carries its reason and a STALE entry (token no longer present) fails too. (POS VENDOR
      NAMES — in page copy, as a bare literal, or in the backend registry / intake / spine logic — are
      the carrier-vocab guard's job since 2026-09-20: harness_carrier_vocab_guard.py derives the
      vocabulary from the seeds and owns the ONE allow set. The `pos_vendor` class this lock carried
      was folded there so one lock owns that fact.)
  (c) the backend reads the declaration through ONE function (`report_kinds.tenant_declaration`);
      no page re-derives POS visibility through the legacy `posOK` / `posVisible` gate or by comparing
      the `pos_system` term against a literal.
  (d) NEGATIVE CONTROLS run the same scanner over synthetic files: a hardcoded kind list → RED; a
      surface that bypasses the hook → RED; a glob → RED; a legacy gate call in a page → RED; a stale
      allow entry → RED. A lock that cannot go red proves nothing. (Eight controls here; the vendor-name
      control moved to the guard with its class.)

Extends the carrier-vocab guard's posture (a dependency-free static scan on bare Python, one CI job)
rather than adding a sibling workflow: .github/workflows/carrier-vocab-guard.yml runs both.

  python3 backend/harness_report_kind_lock.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from app.modules.commcalc import report_kinds as RK   # stdlib-only module: the mirror gives the keys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend", "src")
BE = os.path.join(ROOT, "backend", "app", "modules", "commcalc")
HOOK_FILE = "lib/report-kinds.ts"
VIS_FILE = "lib/carrier-scope.ts"
SHARED_PRIMITIVE = "app/(platform)/onboarding/intake/intake-shared.tsx"

# The surfaces of the 2026-09-20 measurement (the class), pinned by name so a rename cannot drop one.
PINNED_SURFACES = [
    "app/(platform)/commcalc/upload/page.tsx",
    "app/(platform)/commcalc/upload/wizard/page.tsx",
    "app/(platform)/commcalc/email-imports/page.tsx",
    "app/(platform)/commcalc/ftp-imports/page.tsx",     # the SIXTH sibling, found by this lock's first run
    "app/(platform)/onboarding/intake/stage2.tsx",
    "app/(platform)/onboarding/intake/page.tsx",
    SHARED_PRIMITIVE,
]
BACKEND_FILES = ["report_kinds.py", "onboarding_intake.py", "implementation_spine.py"]

KIND_KEYS = set(RK.HOUSE_KEYS)
ROUTE_KEYS = {u for r in RK.HOUSE_KINDS for u in r["upload_types"]}
LIST_KEYS = KIND_KEYS | ROUTE_KEYS
GLOB_LITERAL = re.compile(r"""['"](\*[^'"\n]*\*)['"]""")
STR_ARRAY = re.compile(r"\[((?:\s*['\"][A-Za-z0-9_]+['\"]\s*,?)+)\s*\]")
LEGACY_GATE = re.compile(r"\b(posOK|posVisible)\s*\(")
TERM_COMPARE = re.compile(r"term\(\s*['\"]pos_system['\"][^)]*\)\s*(===|!==|==|!=)")

# ── ALLOW SET — (relative path, class) → reason. A stale entry fails. ────────────────────────────
ALLOW = {
    # route metadata keyed by route key — HOW a route posts, gated by the registry before render
    ("app/(platform)/commcalc/_lib/uploadRoutes.ts", "kind_list"):
        "PERIODLESS = the day-grain route keys whose write is not period-scoped (upload semantics, not a list of what is offered; rendered only when the registry names the key)",
    # a generic placeholder shown when the registry has no rule yet — not a report pattern
    ("app/(platform)/commcalc/email-imports/page.tsx", "glob"):
        "'*Report*Name*' is the input placeholder when the declared POS has no filename standard; the real examples come from kinds.filenameRules",
    ("app/(platform)/commcalc/ftp-imports/page.tsx", "glob"):
        "'*Report*Name*' is the input placeholder when the declared POS has no filename standard (same as email imports)",
    # closing-module files the surface heuristic catches (a file input beside 'sales' / 'x_report'):
    # those two words are the closing recon's TENDER-SOURCE vocabulary (mig 062), not a list of kinds
    # on offer, and the X-report attach is the one POS-agnostic x_report kind. Routed in the closing
    # agent's own PR; excused here with the reason, not silently.
    ("app/(platform)/closing/tender-config/page.tsx", "surface"):
        "closing tender-source picker ('sales' vs 'x_report' = where a tender's truth comes from); the file input uploads the tender map, not a report kind",
    ("components/DailyClosingVerify.tsx", "surface"):
        "the DM verify's X-report attach for ONE closing day — the x_report kind is POS-agnostic ({} applies-to) so the registry answer is 'shown' for every tenant; routing it is the closing agent's PR",
}
assert all(v for v in ALLOW.values()), "every allow entry carries a reason"

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:400]))


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def code_lines(src):
    """Non-comment lines (crude //, /* */, {/* */} and # tracking)."""
    out, inblock = [], False
    for ln in src.split("\n"):
        s = ln.strip()
        if inblock:
            if "*/" in s:
                inblock = False
            continue
        if s.startswith(("//", "*", "#")):
            continue
        if s.startswith(("/*", "{/*")):
            if "*/" not in s:
                inblock = True
            continue
        out.append(ln)
    return out


def walk_fe():
    files = {}
    for sub in ("app", "components", "lib"):
        for dp, _d, fs in os.walk(os.path.join(FE, sub)):
            for f in fs:
                if f.endswith((".ts", ".tsx")):
                    p = os.path.join(dp, f)
                    files[os.path.relpath(p, FE).replace(os.sep, "/")] = read(p)
    return files


def is_surface(rel, src):
    if rel in PINNED_SURFACES:
        return True
    if "intake-shared" in src and re.search(r"\b(Dropzone|KindDetectZone)\b", src) and rel != SHARED_PRIMITIVE:
        return True
    if 'type="file"' in src:
        keys = {m for m in re.findall(r"['\"]([a-z0-9_]+)['\"]", src) if m in LIST_KEYS}
        if len(keys) >= 2:
            return True
    return False


def scan(files, allow):
    """files: {rel: src}. Returns (violations, stale) over the frontend rule set."""
    v, seen = [], set()
    for rel, src in sorted(files.items()):
        lines = code_lines(src)
        body = "\n".join(lines)
        # (b) second copies (POS vendor names: harness_carrier_vocab_guard.py, the one lock for that fact)
        for m in GLOB_LITERAL.finditer(body):
            seen.add((rel, "glob"))
            if (rel, "glob") not in allow:
                v.append((rel, "glob", m.group(1)))
                break
        for m in STR_ARRAY.finditer(body):
            items = re.findall(r"['\"]([A-Za-z0-9_]+)['\"]", m.group(1))
            if len([i for i in items if i in LIST_KEYS]) >= 3:
                seen.add((rel, "kind_list"))
                if (rel, "kind_list") not in allow:
                    v.append((rel, "kind_list", m.group(0)[:80]))
                    break
        # (c) no page re-derives the gate
        if rel.startswith("app/") or rel.startswith("components/"):
            if LEGACY_GATE.search(body):
                seen.add((rel, "legacy_gate"))
                if (rel, "legacy_gate") not in allow:
                    v.append((rel, "legacy_gate", LEGACY_GATE.search(body).group(0)))
            if TERM_COMPARE.search(body):
                seen.add((rel, "term_compare"))
                if (rel, "term_compare") not in allow:
                    v.append((rel, "term_compare", TERM_COMPARE.search(body).group(0)))
            if rel != HOOK_FILE and re.search(r"/commcalc/report-kinds['\"`$]", body):
                v.append((rel, "second_fetch", "fetches /report-kinds directly instead of through useReportKinds"))
            if rel != HOOK_FILE and "reportKindsVisible(" in body:
                v.append((rel, "second_caller", "calls reportKindsVisible outside the one hook"))
        # (a) surfaces dereference the registry
        if is_surface(rel, src):
            if (rel, "surface") in allow:
                seen.add((rel, "surface"))
            elif rel == SHARED_PRIMITIVE:
                ok = "visible:" in body and "useReportKinds" not in body and "/commcalc/report-kinds'" not in body
                if not ok:
                    v.append((rel, "surface", "the shared primitive must take the visible set as a prop and fetch nothing"))
            elif not re.search(r"import\s*\{[^}]*\buseReportKinds\b[^}]*\}\s*from\s*['\"]@/lib/report-kinds['\"]", body) or "useReportKinds(" not in body:
                v.append((rel, "surface", "renders an upload choice without useReportKinds() from '@/lib/report-kinds'"))
    stale = [k for k in allow if k not in seen]
    return v, stale


def main():
    print("=" * 78)
    print("REPORT-KIND LOCK — every upload surface reads the registry; no second list (design §7)")
    print("=" * 78)
    files = walk_fe()
    hook = files.get(HOOK_FILE, "")
    vis = files.get(VIS_FILE, "")
    check("the one visibility function exists in carrier-scope.ts", "export function reportKindsVisible(" in vis)
    check("the one hook exists, calls it, and fetches the one endpoint",
          "reportKindsVisible(" in hook and "/api/v1/commcalc/report-kinds" in hook and "export function useReportKinds()" in hook)
    surfaces = [rel for rel, src in files.items() if is_surface(rel, src)]
    check("the five measured surfaces are all still discovered as surfaces", set(PINNED_SURFACES) <= set(surfaces), set(PINNED_SURFACES) - set(surfaces))
    print("  surfaces enumerated: %d — %s" % (len(surfaces), ", ".join(sorted(surfaces))))
    v, stale = scan(files, ALLOW)
    check("(a)+(b)+(c) no violation across frontend/src/{app,components,lib}", not v, v[:6])
    for rel, cls, what in v[:12]:
        print("      · %s  [%s]  %s" % (rel, cls, what))
    check("no STALE allow entry (every allowed token is still present)", not stale, stale)
    for k, why in sorted(ALLOW.items()):
        print("      allow %-62s [%s] — %s" % (k[0], k[1], why[:70]))

    # backend (the vendor-name scan of these files lives in harness_carrier_vocab_guard.py since 2026-09-20)
    for f in BACKEND_FILES:
        check("backend registry/intake/spine module present for the guard's logic scan: " + f, os.path.exists(os.path.join(BE, f)))
    n_decl = 0
    for dp, _d, fs in os.walk(os.path.join(ROOT, "backend", "app")):
        for f in fs:
            if f.endswith(".py"):
                n_decl += read(os.path.join(dp, f)).count("def tenant_declaration(")
    check("(c) the backend declaration reader is ONE function", n_decl == 1, n_decl)
    router = read(os.path.join(BE, "router.py"))
    check("(c) the router reads the declaration only through it (no direct pos_system term read for gating)",
          '"pos_system"' not in router and "_report_kinds.tenant_declaration(" in router)
    check("(c) the report-kinds endpoint and the detect route exist and run visible_kinds", '@router.get("/report-kinds")' in router and '@router.post("/report-kinds/detect")' in router and "_report_kinds.visible_kinds(" in router)

    # (d) NEGATIVE CONTROLS — the scanner must go red
    print("\n— negative controls (a lock that cannot go red proves nothing) —")
    base = {k: files[k] for k in PINNED_SURFACES + [HOOK_FILE, VIS_FILE]}
    ctl = dict(base)
    ctl["app/(platform)/commcalc/upload/page.tsx"] += "\nconst KINDS = ['sales_imei_phone', 'x_report', 'inventory_aging']\n"
    v1, _ = scan(ctl, ALLOW)
    check("reintroduce a hardcoded kind list on a surface → RED", any(c == "kind_list" for _, c, _ in v1), v1)
    ctl = dict(base)
    ctl["app/(platform)/commcalc/upload/wizard/page.tsx"] = base["app/(platform)/commcalc/upload/wizard/page.tsx"].replace("useReportKinds", "somethingElse")
    v2, _ = scan(ctl, ALLOW)
    check("bypass the hook on ONE surface → RED", any(c == "surface" and "wizard" in r for r, c, _ in v2), v2)
    ctl = dict(base)
    ctl["app/(platform)/onboarding/intake/stage2.tsx"] += "\nconst RULE = '*Sales*Transaction*Details*'\n"
    v3, _ = scan(ctl, ALLOW)
    check("a filename glob literal on a surface → RED", any(c == "glob" for _, c, _ in v3), v3)
    ctl = dict(base)
    ctl["app/(platform)/commcalc/email-imports/page.tsx"] += "\nconst shown = posOK('x', 'a', 'b', {})\n"
    v5, _ = scan(ctl, ALLOW)
    check("a page re-deriving the gate through the legacy posOK → RED", any(c == "legacy_gate" for _, c, _ in v5), v5)
    ctl = dict(base)
    ctl["app/(platform)/commcalc/email-imports/page.tsx"] += "\nconst gate = term('pos_system', '') === 'b2bsoft'\n"
    v6, _ = scan(ctl, ALLOW)
    check("a page comparing the pos_system term against a literal → RED", any(c == "term_compare" for _, c, _ in v6), v6)
    ctl = dict(base)
    ctl["app/(platform)/onboarding/intake/page.tsx"] += "\napi('/api/v1/commcalc/report-kinds').then(() => 0)\n"
    v7, _ = scan(ctl, ALLOW)
    check("a page fetching /report-kinds directly (a second path) → RED", any(c == "second_fetch" for _, c, _ in v7), v7)
    ctl = dict(base)
    ctl["app/(platform)/onboarding/intake/intake-shared.tsx"] = base[SHARED_PRIMITIVE].replace("visible:", "vis_:")
    v8, _ = scan(ctl, ALLOW)
    check("the shared primitive no longer taking the visible set → RED", any(c == "surface" and "intake-shared" in r for r, c, _ in v8), v8)
    ctl = dict(base)
    ctl["app/(platform)/commcalc/new-upload/page.tsx"] = "'use client'\nimport { x } from './intake-shared'\nexport default function P(){ return <div><input type=\"file\" /><Dropzone /></div> }\n"
    v9, _ = scan(ctl, ALLOW)
    check("a NEW file importing the intake upload primitive without the hook → RED", any(c == "surface" and "new-upload" in r for r, c, _ in v9), v9)
    _, stale2 = scan(base, {**ALLOW, ("app/(platform)/commcalc/upload/page.tsx", "glob"): "stale on purpose"})
    check("a stale allow entry → RED", ("app/(platform)/commcalc/upload/page.tsx", "glob") in stale2, stale2)

    print("\n%d passed, %d failed" % (P, F))
    if F:
        print("\nFAIL — the report-kind lock is open. Fix: render the surface from useReportKinds() "
              "(lib/report-kinds.ts), move the fact into the registry (mig 1010 seed / a tenant row), or "
              "add a REVIEWED allow entry with its reason. See this file's docstring.")
        sys.exit(1)
    print("OK — every upload surface reads the registry; no second list of report kinds in code.")
    sys.exit(0)


if __name__ == "__main__":
    main()
