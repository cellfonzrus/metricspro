"""THE LOCK — a landed row says which report kind wrote it, and "where this upload shows up" has ONE home.

CLAUDE.md, "A fix is a DESIGN fix": *"Lock it so it cannot un-wire. A design fix ships with a check that
FAILS THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears."*

Owner (2026-09-20): *"the data is not flowing into the exec mtd from wherever it is uploaded — need to
know where the data is uploaded and it should be mentioned on the upload page where this upload will be
reflected, with a link."* Measured on org f4f1c16e…: a by-product aggregate landed in raw_sales replaced
48,875 line-level rows (the slice was store × dates, never × kind); the rows that survived were blank on
every column the Executive MTD counts; a wrong file was refused with a column list.

WHAT FAILS THE BUILD
  (a) ONE HOME. `KIND_STAMP` (which column records the kind, per table) and `CONSUMERS` (who reads each
      table and what they need) are defined exactly once, in landing_identity.py. A second consumers map
      — a backend dict literal naming ≥2 consumer screen keys with "screen", or a frontend string-array of
      ≥3 consumer screen keys — outside the ALLOW set (each entry with its reason; a stale entry fails) → RED.
  (b) EVERY MULTI-KIND TABLE IS STAMPED. Every table more than one column_mapping.TABLE_MAP layout targets
      is in KIND_STAMP, or in STAMP_EXCUSED with a reason that is still TRUE (the ledger's rows carry
      `source_report` and its landing wipes per source_report).
  (c) EVERY WRITER STAMPS AND SCOPES. In router.py: any function that inserts into a stamped table by name
      calls `_landing.stamp(`; `_ingest_mapped_df` stamps, partitions the slice by kind, deletes by the
      snapshot's own ids and gates on `blank_consumer_fields`; `_upload_file_impl` stamps; the intake
      re-read filters through `kind_of_row`; `upload_mapped` refuses a contradicting target_table.
      In onboarding_intake.py: SOURCE_KIND_TARGET dereferences TABLE_MAP (no table literal on that line).
  (d) EVERY SCREEN KEY IS A LINK. Every screen key CONSUMERS names exists in ScreenLink.tsx SCREENS (the one
      screen → href map), so "shows in" is always a link, never a dead name.
  (e) EVERY SURFACE SAYS WHERE. Each upload surface the report-kind lock pins imports the ONE component
      (`ShowsIn`), the hook is the only caller of `showsInFor`, the report-kinds endpoint attaches
      `shows_in`, the runbook derives its lines from the consumers map, and the Executive MTD page renders
      the backend's `landing` way-back.
  (f) NEGATIVE CONTROLS over synthetic inputs: a writer that stops stamping → RED; a second consumers map →
      RED; a surface without ShowsIn → RED; a screen key with no SCREENS entry → RED; a second layout on an
      unstamped table → RED; a stale allow entry → RED. A lock that cannot go red proves nothing.

Extends the carrier-vocab guard's posture (a dependency-free static scan, one CI job):
.github/workflows/carrier-vocab-guard.yml runs it beside the report-kind, mapping-key and report-links locks.

  python3 backend/harness_landing_identity_lock.py
"""
import ast
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from app.modules.commcalc import landing_identity as LI          # stdlib + report_kinds only
from app.modules.commcalc import column_mapping as CM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend", "src")
BE_APP = os.path.join(ROOT, "backend", "app")
BE_CC = os.path.join(BE_APP, "modules", "commcalc")
HOME = "modules/commcalc/landing_identity.py"
SCREENLINK = "components/ScreenLink.tsx"
SHOWS_IN = "components/ShowsIn.tsx"
HOOK = "lib/report-kinds.ts"
MTD_PAGE = "app/(platform)/commcalc/exec/mtd/page.tsx"
SURFACES = [                       # the report-kind lock's pinned upload surfaces (minus the shared primitive)
    "app/(platform)/commcalc/upload/page.tsx",
    "app/(platform)/commcalc/upload/wizard/page.tsx",
    "app/(platform)/commcalc/email-imports/page.tsx",
    "app/(platform)/commcalc/ftp-imports/page.tsx",
    "app/(platform)/onboarding/intake/stage2.tsx",
    "app/(platform)/onboarding/intake/page.tsx",
]
CONSUMER_KEYS = set(LI.screen_keys())

# Tables ≥2 layouts target that carry their kind ANOTHER way — each with the reason, re-verified below.
STAMP_EXCUSED = {
    "commission_ledger": "rows carry source_report = <carrier>__<statement type> (§30, §30.10) and _ledger_land_rows wipes per source_report — the kind is already on every row",
}
# (a) ALLOW — (relative path, class) → reason. A stale entry (token no longer present) fails.
ALLOW = {
    ("modules/commcalc/onboarding_intake.py", "backend_screen_list"):
        "the Stage-5 runbook's two named links + two cross-check reports (design §2 Stage 5) — screen KEYS resolved by ScreenLink, not hrefs; the per-line 'shows in' beside them is derived from CONSUMERS",
}
assert all(v for v in ALLOW.values()) and all(v for v in STAMP_EXCUSED.values()), "every allow / excuse carries a reason"

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


def walk(root, exts, subs=None):
    files = {}
    for sub in (subs or [""]):
        for dp, dirs, fs in os.walk(os.path.join(root, sub)):
            dirs[:] = [d for d in dirs if d != "__pycache__" and d != "node_modules"]
            for f in fs:
                if f.endswith(exts):
                    p = os.path.join(dp, f)
                    files[os.path.relpath(p, root).replace(os.sep, "/")] = read(p)
    return files


def code_lines(src):
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


STR_ARRAY = re.compile(r"\[((?:\s*['\"][A-Za-z0-9_]+['\"]\s*,?)+)\s*\]")
SCREEN_LIT = re.compile(r"""["']screen["']\s*:\s*["']([a-z0-9_]+)["']""")


# ── (a) one home / no second copy ────────────────────────────────────────────────────────────────
def scan_backend(files, allow):
    v, seen = [], set()
    for rel, src in sorted(files.items()):
        if rel == HOME:
            continue
        body = "\n".join(code_lines(src))
        # a module-level KIND_STAMP, or a module-level CONSUMERS that names screens (ma_class_wiring.py's
        # CONSUMERS is a different concept — MA class consumers — and names no screen)
        n_stamp = len(re.findall(r"^KIND_STAMP\s*=", body, re.M))
        n_cons = len(re.findall(r"^CONSUMERS\s*=", body, re.M)) if '"screen"' in body else 0
        if n_stamp or n_cons:
            v.append((rel, "second_home", "defines KIND_STAMP / a screen-naming CONSUMERS outside landing_identity.py"))
        keys = {m for m in SCREEN_LIT.findall(body) if m in CONSUMER_KEYS}
        if len(keys) >= 2:
            seen.add((rel, "backend_screen_list"))
            if (rel, "backend_screen_list") not in allow:
                v.append((rel, "backend_screen_list", sorted(keys)))
    stale = [k for k in allow if k[1] == "backend_screen_list" and k not in seen]
    return v, stale


def scan_frontend(files, allow):
    v, seen = [], set()
    for rel, src in sorted(files.items()):
        body = "\n".join(code_lines(src))
        for m in STR_ARRAY.finditer(body):
            items = re.findall(r"['\"]([A-Za-z0-9_]+)['\"]", m.group(1))
            if len([i for i in items if i in CONSUMER_KEYS]) >= 3:
                seen.add((rel, "fe_key_list"))
                if (rel, "fe_key_list") not in allow:
                    v.append((rel, "fe_key_list", m.group(0)[:80]))
                break
        if rel != HOOK and rel != SHOWS_IN and "showsInFor(" in body:
            v.append((rel, "second_caller", "calls showsInFor outside the one hook"))
        if rel in SURFACES and not re.search(r"import\s+ShowsIn\s+from\s+['\"]@/components/ShowsIn['\"]", body):
            v.append((rel, "surface_no_shows_in", "an upload surface that does not render ShowsIn"))
        if rel in SURFACES and "<ShowsIn " not in body:
            v.append((rel, "surface_no_shows_in", "imports ShowsIn but never renders it"))
    stale = [k for k in allow if k[1] == "fe_key_list" and k not in seen]
    return v, stale


# ── (b) multi-kind tables are stamped ────────────────────────────────────────────────────────────
def unstamped_multi(table_map, stamp, excused):
    return [t for t in LI.multi_kind_tables(table_map) if t not in stamp and t not in excused]


# ── (c) writers stamp and scope ──────────────────────────────────────────────────────────────────
def router_functions(src):
    """{function name: its source} for every top-level function — sliced by line number once (the
    router is ~40k lines; ast.get_source_segment re-splits the file per call and takes minutes)."""
    tree = ast.parse(src)
    lines = src.split("\n")
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = (node.decorator_list[0].lineno if node.decorator_list else node.lineno) - 1
            out[node.name] = "\n".join(lines[start:node.end_lineno])
    return out


def unstamped_writers(funcs, stamped_tables):
    bad = []
    for name, body in funcs.items():
        for t in stamped_tables:
            if re.search(r"\.table\(['\"]%s['\"]\)" % re.escape(t), body) and ".insert(" in body:
                if "_landing.stamp(" not in body and name not in ("_restore_rows",):
                    bad.append((name, t))
    return bad


def main():
    print("=" * 78)
    print("LANDING-IDENTITY LOCK — every writer stamps the kind; one consumers map; every surface says where")
    print("=" * 78)
    be = walk(BE_APP, (".py",))
    fe = walk(FE, (".ts", ".tsx"), ("app", "components", "lib"))

    # (a)
    check("KIND_STAMP and CONSUMERS are defined once, in landing_identity.py",
          HOME in be and re.search(r"^KIND_STAMP\s*=", be[HOME], re.M) and re.search(r"^CONSUMERS\s*=", be[HOME], re.M))
    vb, stale_b = scan_backend(be, ALLOW)
    check("(a) no second home / no second consumers list in backend/app", not vb, vb[:4])
    vf, stale_f = scan_frontend(fe, ALLOW)
    check("(a)+(e) no second key list, one caller of showsInFor, every upload surface renders ShowsIn", not vf, vf[:6])
    for rel, cls, what in (vb + vf)[:12]:
        print("      · %s  [%s]  %s" % (rel, cls, what))
    check("no STALE allow entry", not (stale_b + stale_f), stale_b + stale_f)
    for k, why in sorted(ALLOW.items()):
        print("      allow %-58s [%s] — %s" % (k[0], k[1], why[:70]))

    # (b)
    oi = be.get("modules/commcalc/onboarding_intake.py", "")
    um = unstamped_multi(CM.TABLE_MAP, LI.KIND_STAMP, STAMP_EXCUSED)
    check("(b) every table ≥2 layouts target is stamped or excused with a live reason: %s" % LI.multi_kind_tables(CM.TABLE_MAP), not um, um)
    router = be.get("modules/commcalc/router.py", "")
    funcs = router_functions(router)
    ledger = funcs.get("_ledger_land_rows", "")
    ledger_del = funcs.get("_ledger_delete_scoped", "")
    check("(b) the ledger excuse is still TRUE: _ledger_land_rows wipes through _ledger_delete_scoped, which filters the delete by source_report",
          "commission_ledger" in STAMP_EXCUSED and "_ledger_delete_scoped(" in ledger and "source_report" in ledger
          and ".delete()" in ledger_del and 'eq("source_report"' in ledger_del.replace("'", '"'))
    check("(b) the product layout lands in its OWN table, registered in the slice map, and SOURCE_KIND_TARGET dereferences TABLE_MAP",
          CM.TABLE_MAP["pos_product_sales"] != CM.TABLE_MAP["sales"] and CM.TABLE_MAP["pos_product_sales"] in LI.KIND_STAMP
          and CM.TABLE_MAP["sales"] in LI.KIND_STAMP)
    from app.modules.commcalc import ingest_slice as IS
    check("(b) both stamped sales tables are slice-replaced (INGEST_PARTITION) on store × trans_date",
          all(IS.INGEST_PARTITION.get(t) == {"partition": "store", "date": "trans_date"} for t in ("raw_sales", "raw_sales_product")))
    # 2026-09-21 — sales by invoice: the parent + the CHILD table (column_mapping.CHILD_TABLE_MAP, the one home of "which
    # child table") are stamped, sliced and named by ONE migration; the child rows carry the parent's kind; the router and
    # the intake spell the child table nowhere (they dereference CHILD_TABLE_MAP)
    child = CM.CHILD_TABLE_MAP.get("sales_by_invoice")
    check("(b) the invoice layout's parent and child tables are stamped with the SAME default kind, slice-replaced on store × trans_date, gated by one migration, and the child table is dereferenced (no literal in router / intake)",
          child and CM.TABLE_MAP["sales_by_invoice"] in LI.KIND_STAMP and child in LI.KIND_STAMP
          and LI.KIND_STAMP[child]["default"] == LI.KIND_STAMP[CM.TABLE_MAP["sales_by_invoice"]]["default"] == "sales_by_invoice"
          and all(IS.INGEST_PARTITION.get(t) == {"partition": "store", "date": "trans_date"} for t in (CM.TABLE_MAP["sales_by_invoice"], child))
          and LI.TABLE_MIGRATION.get(child) == LI.TABLE_MIGRATION.get(CM.TABLE_MAP["sales_by_invoice"])
          and ("'%s'" % child) not in be.get("modules/commcalc/router.py", "") and ('"%s"' % child) not in be.get("modules/commcalc/router.py", "")
          and ("'%s'" % child) not in oi and ('"%s"' % child) not in oi, child)
    skt = re.search(r"^SOURCE_KIND_TARGET\s*=\s*\{.*?\}", oi, re.M | re.S)
    check("(c) SOURCE_KIND_TARGET dereferences column_mapping.TABLE_MAP for the mapped kinds (no sales-table literal)",
          skt and "CM.TABLE_MAP[" in skt.group(0) and '"raw_sales"' not in skt.group(0), skt.group(0)[:120] if skt else "missing")

    # (c)
    bad = unstamped_writers(funcs, list(LI.KIND_STAMP))
    check("(c) every router function that inserts into a stamped table by name calls _landing.stamp(", not bad, bad)
    imd = funcs.get("_ingest_mapped_df", "")
    check("(c) _ingest_mapped_df: stamps, refuses blank-consumer frames BEFORE writing, partitions the slice by kind, deletes by the snapshot's ids, refuses a cross-kind replace unless confirmed",
          all(t in imd for t in ("_landing.stamp(", "_landing.blank_consumer_fields(", "_landing.partition_slice(", '.in_("id", ids', "_landing.cross_kind_refusal(", "replace_other_kinds"))
          and imd.index("_landing.blank_consumer_fields(") < imd.index("_select_replace_slice("))
    ufi = funcs.get("_upload_file_impl", "")
    check("(c) _upload_file_impl (the legacy sales route) stamps the kind and scopes its period replace through apply_kind_filter",
          "_landing.stamp(" in ufi and "_landing.apply_kind_filter(" in ufi)
    promo = funcs.get("_promote_feed_impl", "")      # the wrapper _promote_feed_to_raw_sales only takes the mutex
    check("(c) the promotion writer (_promote_feed_impl) stamps the default kind and scopes its delete through apply_kind_filter",
          "_landing.stamp(" in promo and "_landing.apply_kind_filter(" in promo)
    rr = funcs.get("_intake_reread_sales", "")
    check("(c) the intake re-read takes the table + kind and filters through kind_of_row (never counts another kind's rows)",
          "table=None, kind=None" in rr and "_landing.kind_of_row(" in rr)
    um2 = funcs.get("upload_mapped", "")
    check("(c) /upload-mapped refuses a target_table that contradicts TABLE_MAP and carries replace_other_kinds",
          "contradicts" in um2 and "replace_other_kinds" in um2)
    check("(c) the wrong-file refusal on /upload/{file_type} names the right page through the registry's detection",
          "_looks_like_sentence(" in ufi and "_landing.looks_like(" in router)

    # (d)
    sl = fe.get(SCREENLINK, "")
    missing = [k for k in CONSUMER_KEYS if not re.search(r"^\s+%s:\s*\{" % re.escape(k), sl, re.M)]
    check("(d) every consumer screen key (%d) exists in ScreenLink SCREENS — 'shows in' is always a link" % len(CONSUMER_KEYS), not missing, missing)

    # (e)
    hook = fe.get(HOOK, "")
    check("(e) the hook exposes showsIn and the ONE selector showsInFor", "export function showsInFor(" in hook and "showsIn" in hook)
    check("(e) the report-kinds endpoint attaches shows_in + where per row and the consumers map", 'r["shows_in"] = _landing.shows_in(' in router and 'out["consumers"]' in router)
    check("(e) the runbook derives each monthly line's 'shows in' from the consumers map", "_li.consumers_for_table(" in oi)
    mtd = fe.get(MTD_PAGE, "")
    check("(e) the Executive MTD renders the backend's way-back (`landing`: blank fields + the feeds, linked)", "data?.landing" in mtd and "landing.feeds" in mtd and "ScreenLink" in mtd)
    check("(e) the Executive MTD endpoint computes it from the one home", "_landing.feeds_for_table(" in router and "_landing.blank_fields_over(" in router)
    check("(e) the six surfaces all render ShowsIn", all("<ShowsIn " in fe.get(s, "") for s in SURFACES), [s for s in SURFACES if "<ShowsIn " not in fe.get(s, "")])

    # (f) negative controls
    print("\n— negative controls (a lock that cannot go red proves nothing) —")
    ctl = {"modules/x/second.py": 'CONSUMERS = {"raw_sales": [{"screen": "exec_mtd", "label": "x"}, {"screen": "sales_report", "label": "y"}]}\n'}
    v1, _ = scan_backend({**{HOME: be[HOME]}, **ctl}, ALLOW)
    check("a second consumers map in a backend module → RED", any(c in ("second_home", "backend_screen_list") for _, c, _ in v1), v1)
    base_fe = {k: fe[k] for k in SURFACES + [HOOK, SHOWS_IN, SCREENLINK] if k in fe}
    ctl = dict(base_fe)
    ctl["app/(platform)/commcalc/upload/wizard/page.tsx"] = base_fe["app/(platform)/commcalc/upload/wizard/page.tsx"].replace("ShowsIn", "Elsewhere")
    v2, _ = scan_frontend(ctl, ALLOW)
    check("a surface that stops rendering ShowsIn → RED", any(c == "surface_no_shows_in" and "wizard" in r for r, c, _ in v2), v2)
    ctl = dict(base_fe)
    ctl["app/(platform)/commcalc/upload/page.tsx"] += "\nconst WHERE = ['exec_mtd', 'sales_report', 'gp_report']\n"
    v3, _ = scan_frontend(ctl, ALLOW)
    check("a hardcoded consumer-key list on a page → RED", any(c == "fe_key_list" for _, c, _ in v3), v3)
    ctl = dict(base_fe)
    ctl["app/(platform)/onboarding/intake/page.tsx"] += "\nconst s = showsInFor([], 'x')\n"
    v4, _ = scan_frontend(ctl, ALLOW)
    check("a page calling showsInFor itself (a second caller) → RED", any(c == "second_caller" for _, c, _ in v4), v4)
    check("a consumer screen key with no SCREENS entry → RED",
          bool([k for k in CONSUMER_KEYS | {"nowhere_page"} if not re.search(r"^\s+%s:\s*\{" % re.escape(k), sl, re.M)]))
    check("a second layout on an unstamped table → RED",
          unstamped_multi({**CM.TABLE_MAP, "another_layout": "raw_payment_detail", "payment_detail": "raw_payment_detail"}, LI.KIND_STAMP, STAMP_EXCUSED) == ["raw_payment_detail"])
    check("a writer that stops stamping → RED",
          unstamped_writers({"_promote_feed_impl": promo.replace("_landing.stamp(", "nothing(")}, list(LI.KIND_STAMP)) == [("_promote_feed_impl", "raw_sales")])
    _, stale2 = scan_backend(be, {**ALLOW, ("modules/commcalc/nothing.py", "backend_screen_list"): "stale on purpose"})
    check("a stale allow entry → RED", ("modules/commcalc/nothing.py", "backend_screen_list") in stale2, stale2)

    print("\n%d passed, %d failed" % (P, F))
    if F:
        print("\nFAIL — the landing-identity lock is open. Fix: stamp the kind through landing_identity.stamp in the "
              "writer, scope its delete by kind, render ShowsIn on the surface, add the screen key to ScreenLink "
              "SCREENS, or add a REVIEWED allow entry with its reason. See this file's docstring.")
        sys.exit(1)
    print("OK — every writer stamps the kind; one consumers map; every upload surface says where it shows.")
    sys.exit(0)


if __name__ == "__main__":
    main()
