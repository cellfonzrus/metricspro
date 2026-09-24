"""THE LOCK — the multi-month commission upload is N single imports: ONE reader/mapper, ONE lander, ONE month
detector, ONE period canonicaliser, ONE "already landed" measure. (index §30.17)

CLAUDE.md, "A fix is a DESIGN fix": *"Lock it so it cannot un-wire. A design fix ships with a check that FAILS
THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears."*

Owner 2026-09-24: "give me an option to upload commission for multiple periods at the same time since they only
give monthly commission reports". Task #36 (index §30.15) found duplicate ledger copies caused by non-canonical
period spellings and route-dependent keys; a batch path that parsed, mapped, spelled or landed on its own would
be that defect's next instance. So:

WHAT FAILS THE BUILD
  (a) the batch endpoints stop going through the single import's own steps: `commission_ledger_import_batch`
      must call `_ledger_batch_plan(` and `_ledger_import_prepared(`, and must NOT call `_ledger_land_rows(`,
      `build_row(`, `_read_upload_df(`, `apply_mapping(`, `_ledger_map_records(` or `.insert(` itself; the
      preview must call `_ledger_batch_plan(` and write nothing; `_ledger_batch_plan` must read each file through
      `_ledger_prepare_file(`, build through `_ledger_build_rows(`, measure through `_ledger_family_rows(` and
      plan through `_lb.plan_batch(` — never `_read_upload_df(` / `apply_mapping(` / `canonical_period(` itself.
  (b) the single import stops being prepare + land: `commission_ledger_import` → `_ledger_prepare_file(` +
      `_ledger_import_prepared(`; `_ledger_import_prepared` → `_ledger_build_rows(` + `_ledger_land_rows(`;
      `_ledger_prepare_file` → `_ledger_map_records(` + `_ledger_footer_drop(` + `_ledger_source_rules_meta(`;
      `_ledger_landings_present` (the lander's measure) → `_ledger_family_rows(`. Each defined ONCE.
  (c) ledger_batch.py grows its own month logic or I/O: it must call `CL.canonical_period(`,
      `OI.period_proposal(` and `CL.landings_for(`; it must NOT define `period_proposal` / `canonical_period` /
      `parse_period` / `period_keys` / `landings_for`, spell a month-name table, call `strftime(` / `datetime(`,
      touch a client (`.table(` / `.insert(` / `.delete(` / `.execute(`) or name a carrier.
  (d) the page: the component calls both endpoints, sends the months as `periods`, and holds no month
      vocabulary of its own (no month-name list, no `toLocaleString(... { month` date formatting of a period).
  (e) NEGATIVE CONTROLS over synthetic sources — each of the above violated → RED.
  (f) CI: this lock and the proof run in carrier-vocab-guard.yml.
  (g) ONE MAPPING PER STATEMENT (index §30.17a, 2026-09-24): "which column mapping reads this statement" has ONE
      resolver, `column_mapping.statement_rules` (the carrier's own set whole, else the global set). The ledger's
      resolver `_ledger_source_rules_meta` must call it; `_ledger_prepare_file` (the single import AND the batch)
      and the setup wizard's `commission_ledger_analyze` must resolve the template's carrier through
      `_ledger_carrier_of_source_report` and read the mapping through the resolver; no router function that
      derives a ledger mapping key may call `column_mapping.load_rules(` except wrapped in `statement_rules(` /
      `carrier_rules(`; the intake's "the carrier's own set" is `column_mapping.carrier_rules(` (no inline
      filter); the ledger's footer rule is the intake's `split_footer`; `_ingest_mapped_df` refuses the ledger
      table (one lander); the pages send the template's carrier and the wizard saves where the import reads.

  python3 backend/harness_ledger_batch_lock.py      (stdlib only)
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROUTER = os.path.join(ROOT, "backend/app/modules/commcalc/router.py")
BATCH = os.path.join(ROOT, "backend/app/modules/commcalc/ledger_batch.py")
COMP = os.path.join(ROOT, "frontend/src/components/LedgerBatchUpload.tsx")
PAGE = os.path.join(ROOT, "frontend/src/app/(platform)/commcalc/commission-ledger/page.tsx")
WORKFLOW = os.path.join(ROOT, ".github/workflows/carrier-vocab-guard.yml")
COLUMN_MAPPING = os.path.join(ROOT, "backend/app/modules/commcalc/column_mapping.py")
SETUP = os.path.join(ROOT, "frontend/src/app/(platform)/commcalc/commission-ledger/setup/page.tsx")

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


_DOCSTRING = re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'')


def strip_comments(src):
    src = _DOCSTRING.sub('""', src)
    return "\n".join(ln.split("  # ")[0] for ln in src.split("\n") if not ln.strip().startswith("#"))


def functions(src):
    tree = ast.parse(src)
    lines = src.split("\n")
    out, counts = {}, {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = (node.decorator_list[0].lineno if node.decorator_list else node.lineno) - 1
            out[node.name] = "\n".join(lines[start:node.end_lineno])
            counts[node.name] = counts.get(node.name, 0) + 1
    return out, counts


# ── the rules, as functions over source text (so the negative controls run the SAME rules) ─────────
BATCH_MUST = ("_ledger_batch_plan(", "_ledger_import_prepared(")
BATCH_MUST_NOT = ("_ledger_land_rows(", "build_row(", "_read_upload_df(", "apply_mapping(", "_ledger_map_records(", ".insert(")
PLAN_MUST = ("_ledger_prepare_file(", "_ledger_build_rows(", "_ledger_family_rows(", "_lb.plan_batch(")
PLAN_MUST_NOT = ("_read_upload_df(", "apply_mapping(", "canonical_period(", "period_fields(", ".insert(", "_ledger_land_rows(")
PREVIEW_MUST_NOT = (".insert(", ".delete(", ".upsert(", "_ledger_import_prepared(", "_ledger_land_rows(")
CHAIN = {
    "commission_ledger_import": ("_ledger_prepare_file(", "_ledger_import_prepared("),
    "_ledger_import_prepared": ("_ledger_build_rows(", "_ledger_land_rows("),
    "_ledger_prepare_file": ("_ledger_map_records(", "_ledger_footer_drop(", "_ledger_source_rules_meta(", "_ledger_convention("),
    "_ledger_landings_present": ("_ledger_family_rows(",),
}
ONCE = ("_ledger_prepare_file", "_ledger_build_rows", "_ledger_import_prepared", "_ledger_family_rows",
        "_ledger_batch_plan", "commission_ledger_import_batch", "commission_ledger_import_batch_preview",
        "commission_ledger_import", "_ledger_land_rows")


WRAPPED_LOAD = re.compile(r"(?:statement_rules|carrier_rules)\(\s*column_mapping\.load_rules\(")
RAW_LOAD = re.compile(r"column_mapping\.load_rules\(")
INLINE_CARRIER_FILTER = re.compile(r"""r\.get\(["']carrier_id["']\)\s*==\s*carrier\[""")


def mapping_violations(src):
    """(g) — which mapping reads a statement: one resolver, every ledger route."""
    fns, _ = functions(src)
    bad = []
    meta = strip_comments(fns.get("_ledger_source_rules_meta", ""))
    if "column_mapping.statement_rules(" not in meta:
        bad.append("_ledger_source_rules_meta does not call column_mapping.statement_rules(")
    if "_ledger_source_rules_meta(" not in strip_comments(fns.get("_ledger_source_rules", "")):
        bad.append("_ledger_source_rules does not go through _ledger_source_rules_meta(")
    for fn in ("_ledger_prepare_file", "commission_ledger_analyze"):
        body = strip_comments(fns.get(fn, ""))
        for t in ("_ledger_carrier_of_source_report(", "_ledger_source_rules_meta("):
            if t not in body:
                bad.append(f"{fn} lacks {t}")
    for name, body in fns.items():
        b = strip_comments(body)
        if name == "_ledger_source_rules_meta" or not ("_ledger_mapping_key(" in b or "mapping_report_key(" in b):
            continue
        raw, wrapped = len(RAW_LOAD.findall(b)), len(WRAPPED_LOAD.findall(b))
        if raw > wrapped:
            bad.append(f"{name} reads a ledger mapping with column_mapping.load_rules( outside the resolver")
    for fn in ("_intake_prepare_commission", "_intake_commit_commission"):
        body = strip_comments(fns.get(fn, ""))
        if "column_mapping.carrier_rules(" not in body:
            bad.append(f"{fn} lacks column_mapping.carrier_rules(")
        if INLINE_CARRIER_FILTER.search(body):
            bad.append(f"{fn} filters the carrier's set inline (a second copy of carrier_rules)")
    fd = strip_comments(fns.get("_ledger_footer_drop", ""))
    if "_intake.split_footer(" not in fd or "drop_footer_rows(" in fd:
        bad.append("_ledger_footer_drop is not the intake's split_footer (two footer rules for one statement)")
    if "commission_ledger.LEDGER_TABLE" not in strip_comments(fns.get("_ingest_mapped_df", "")):
        bad.append("_ingest_mapped_df does not refuse the ledger table (a second statement lander)")
    for fn in ("commission_ledger_import_batch", "commission_ledger_import_batch_preview", "_ledger_batch_plan"):
        body = strip_comments(fns.get(fn, ""))
        if RAW_LOAD.search(body) or "_ledger_source_rules(" in body:
            bad.append(f"{fn} resolves a mapping itself (it must go through _ledger_prepare_file)")
    return bad


def column_mapping_violations(cm_src):
    fns, counts = functions(cm_src)
    bad = [f"column_mapping.{fn} defined {counts.get(fn, 0)} times" for fn in ("statement_rules", "carrier_rules")
           if counts.get(fn, 0) != 1]
    if "carrier_rules(" not in strip_comments(fns.get("statement_rules", "")):
        bad.append("statement_rules does not build on carrier_rules (two answers to 'the carrier's own set')")
    return bad


def page_carrier_violations(comp, page, setup):
    bad = []
    if "fd.append('carrier_id', carrierId)" not in comp:
        bad.append("the batch component does not send the template's carrier")
    if "fd.append('carrier_id', tmpl.carrier_id)" not in page or "carrierId={tmpl?.carrier_id" not in page:
        bad.append("the Commission Ledger page does not send the template's carrier (single import / batch)")
    if setup.count("fd.append('carrier_id', carrierId)") < 3 or "carrier_id: analysis?.save_carrier_id" not in setup:
        bad.append("the setup wizard does not read and save the mapping the import reads")
    return bad


def router_violations(src):
    fns, counts = functions(src)
    bad = []
    b = strip_comments(fns.get("commission_ledger_import_batch", ""))
    bad += [f"batch lacks {t}" for t in BATCH_MUST if t not in b]
    bad += [f"batch calls {t}" for t in BATCH_MUST_NOT if t in b]
    pv = strip_comments(fns.get("commission_ledger_import_batch_preview", ""))
    if "_ledger_batch_plan(" not in pv:
        bad.append("preview lacks _ledger_batch_plan(")
    bad += [f"preview calls {t}" for t in PREVIEW_MUST_NOT if t in pv]
    pl = strip_comments(fns.get("_ledger_batch_plan", ""))
    bad += [f"plan lacks {t}" for t in PLAN_MUST if t not in pl]
    bad += [f"plan calls {t}" for t in PLAN_MUST_NOT if t in pl]
    for fn, toks in CHAIN.items():
        body = strip_comments(fns.get(fn, ""))
        bad += [f"{fn} lacks {t}" for t in toks if t not in body]
    bad += [f"{fn} defined {counts.get(fn, 0)} times" for fn in ONCE if counts.get(fn, 0) != 1]
    return bad


BATCH_MUST_CALL = ("CL.canonical_period(", "OI.period_proposal(", "CL.landings_for(")
BATCH_NO_DEF = ("period_proposal", "canonical_period", "parse_period", "period_keys", "landings_for", "is_canonical_period")
MONTH_TABLE = re.compile(r"""["'](?:January|February|March|April|June|July|August|September|October|November|December)["']""")
NO_IO = re.compile(r"""\.(?:table|insert|delete|upsert|execute|schema)\(|strftime\(|\bdatetime\(""")
CARRIER = re.compile(r"\b(boost|verizon|cricket|metro|vidapay|total\s+wireless|luxelink|novawave|t-mobile|at&t)\b", re.I)


def batch_module_violations(src):
    body = strip_comments(src)
    bad = [f"lacks {t}" for t in BATCH_MUST_CALL if t not in body]
    bad += [f"defines {fn}" for fn in BATCH_NO_DEF if re.search(r"^\s*def %s\(" % fn, src, re.M)]
    if MONTH_TABLE.search(body):
        bad.append("spells a month-name table")
    m = NO_IO.search(body)
    if m:
        bad.append(f"I/O or date formatting: {m.group(0)}")
    if CARRIER.search(src):
        bad.append("names a carrier")
    return bad


COMP_MONTHS = re.compile(r"""['"](?:January|February|March|April|June|July|August|September|October|November|December)['"]|\{\s*month\s*:""")


def component_violations(comp, page):
    bad = []
    for t in ("'/api/v1/commcalc/commission-ledger/import-batch/preview'", "'/api/v1/commcalc/commission-ledger/import-batch'",
              "fd.append('periods', JSON.stringify(ms))", "fd.append('files', f)", "multiple", "confirm_replace", "confirm_other_origin"):
        if t not in comp:
            bad.append(f"component lacks {t}")
    if COMP_MONTHS.search(comp):
        bad.append("component carries its own month vocabulary / date formatting")
    if "<LedgerBatchUpload" not in page:
        bad.append("the Commission Ledger page does not render the component")
    return bad


def main():
    router, batch = read(ROUTER), read(BATCH)
    comp, page = read(COMP), read(PAGE)
    cm_src, setup = read(COLUMN_MAPPING), read(SETUP)

    print("\n(a)(b) THE ROUTER — the batch is the single import's own steps, once per file")
    rv = router_violations(router)
    check("the batch endpoints, the plan and the single import are wired through ONE reader/mapper and ONE lander", not rv, rv)

    print("\n(c) ledger_batch.py — the plan only: no month logic, no I/O, no carrier of its own")
    bv = batch_module_violations(batch)
    check("ledger_batch dereferences the one canonicaliser, the intake's detector and the lander's measure — and copies none", not bv, bv)

    print("\n(d) THE PAGE")
    cv = component_violations(comp, page)
    check("the Commission Ledger page renders the batch upload; the component calls both endpoints and works out no month itself", not cv, cv)

    print("\n(e) NEGATIVE CONTROLS — each violation turns the rule RED")
    fns, _ = functions(router)
    good_batch = fns["commission_ledger_import_batch"]

    def swap(src, name, new):
        return src.replace(fns[name], new)
    ctl = swap(router, "commission_ledger_import_batch",
               good_batch.replace("_ledger_import_prepared(", "_ledger_land_rows("))
    check("a batch that calls the lander directly (skipping the single import's land step) → RED",
          any("batch calls _ledger_land_rows(" in v or "batch lacks _ledger_import_prepared(" in v for v in router_violations(ctl)))
    ctl = swap(router, "commission_ledger_import_batch", good_batch + "\n    df = _read_upload_df(b'', 'x.csv')\n")
    check("a batch that reads a file itself (a second parser) → RED", any("_read_upload_df(" in v for v in router_violations(ctl)))
    ctl = swap(router, "_ledger_batch_plan", fns["_ledger_batch_plan"] + "\n    per = commission_ledger.canonical_period(x)\n")
    check("a plan that spells a period itself (a second canonicaliser call site) → RED",
          any("plan calls canonical_period(" in v for v in router_violations(ctl)))
    ctl = swap(router, "commission_ledger_import", fns["commission_ledger_import"].replace("_ledger_prepare_file(", "_prep_copy("))
    check("the single import no longer going through _ledger_prepare_file (the two drifting apart) → RED",
          any("commission_ledger_import lacks _ledger_prepare_file(" in v for v in router_violations(ctl)))
    ctl = router + "\n\ndef _ledger_prepare_file(client):\n    return None\n"
    check("a second definition of _ledger_prepare_file → RED", any("defined 2 times" in v for v in router_violations(ctl)))
    ctl = swap(router, "commission_ledger_import_batch_preview",
               fns["commission_ledger_import_batch_preview"] + "\n    client.table('commission_ledger').insert([])\n")
    check("a preview that writes → RED", any("preview calls .insert(" in v for v in router_violations(ctl)))
    check("ledger_batch with a strftime month label → RED",
          any("I/O or date formatting" in v for v in batch_module_violations(batch + "\nX = d.strftime('%B %Y')\n")))
    check("ledger_batch with its own month-name table → RED",
          any("month-name table" in v for v in batch_module_violations(batch + "\n_M = ['January', 'February']\n")))
    check("ledger_batch defining its own period_proposal → RED",
          any("defines period_proposal" in v for v in batch_module_violations(batch + "\ndef period_proposal(rows):\n    return {}\n")))
    check("ledger_batch no longer calling the canonicaliser → RED",
          any("lacks CL.canonical_period(" in v for v in batch_module_violations(batch.replace("CL.canonical_period(", "_mine("))))
    check("ledger_batch touching a client → RED",
          any("I/O" in v for v in batch_module_violations(batch + "\ndef f(c):\n    return c.table('x').execute()\n")))
    check("a component with its own month list → RED",
          any("month vocabulary" in v for v in component_violations(comp + "\nconst M = ['January', 'February']\n", page)))
    check("a page that stops rendering the component → RED",
          any("does not render" in v for v in component_violations(comp, page.replace("<LedgerBatchUpload", "<Other"))))

    print("\n(g) ONE MAPPING PER STATEMENT — one resolver, one footer rule, one lander, every route (index §30.17a)")
    mv = mapping_violations(router)
    check("every ledger route resolves the statement's mapping through column_mapping.statement_rules (the template's carrier), "
          "the intake's own set is carrier_rules, one footer rule, one lander", not mv, mv)
    cv2 = column_mapping_violations(cm_src)
    check("statement_rules / carrier_rules are defined once and build on each other", not cv2, cv2)
    pv = page_carrier_violations(comp, page, setup)
    check("the Ledger page, the batch and the setup wizard send the template's carrier; the wizard saves where the import reads", not pv, pv)
    fns2, _ = functions(router)

    def swap2(name, new):
        return router.replace(fns2[name], new)
    ctl = swap2("_ledger_prepare_file", fns2["_ledger_prepare_file"].replace("_ledger_source_rules_meta(client, org_id, cid, report_key=rk)",
                                                                              "(column_mapping.load_rules(client, org_id, rk, None), 'global')"))
    check("NEGATIVE: the single import / batch reading load_rules directly (the pre-fix global read) → RED",
          any("_ledger_prepare_file" in v for v in mapping_violations(ctl)))
    ctl = swap2("_ledger_source_rules_meta", fns2["_ledger_source_rules_meta"].replace("column_mapping.statement_rules(", "(lambda r, c: (r, 'x'))("))
    check("NEGATIVE: the ledger resolver not dereferencing statement_rules (the per-field merge back) → RED",
          any("statement_rules" in v for v in mapping_violations(ctl)))
    ctl = swap2("commission_ledger_analyze", fns2["commission_ledger_analyze"].replace("_ledger_carrier_of_source_report(", "_nobody("))
    check("NEGATIVE: the setup wizard's preview not resolving the template's carrier → RED",
          any("commission_ledger_analyze lacks _ledger_carrier_of_source_report(" in v for v in mapping_violations(ctl)))
    ctl = swap2("_intake_prepare_commission", fns2["_intake_prepare_commission"].replace(
        "column_mapping.carrier_rules(column_mapping.load_rules(client, org_id, report_key, carrier[\"id\"]),",
        "[r for r in column_mapping.load_rules(client, org_id, report_key, carrier[\"id\"]) if r.get(\"carrier_id\") == carrier[\"id\"]] or ("))
    check("NEGATIVE: the intake filtering the carrier's set inline again (a second copy) → RED",
          any("_intake_prepare_commission" in v for v in mapping_violations(ctl)))
    ctl = swap2("_ledger_footer_drop", fns2["_ledger_footer_drop"].replace("_intake.split_footer(", "column_mapping.drop_footer_rows("))
    check("NEGATIVE: the ledger's footer rule back to rule 1 alone (the store-stamped total booked twice) → RED",
          any("split_footer" in v for v in mapping_violations(ctl)))
    ctl = swap2("_ingest_mapped_df", fns2["_ingest_mapped_df"].replace("commission_ledger.LEDGER_TABLE", "'nothing'"))
    check("NEGATIVE: /upload-mapped able to land a statement into the ledger again → RED",
          any("_ingest_mapped_df" in v for v in mapping_violations(ctl)))
    ctl = router + "\n\ndef _sneaky(client, org_id):\n    rk = _ledger_mapping_key(client, org_id)\n    return column_mapping.load_rules(client, org_id, rk, None)\n"
    check("NEGATIVE: any new router function reading a ledger mapping around the resolver → RED",
          any("_sneaky" in v for v in mapping_violations(ctl)))
    check("NEGATIVE: a second statement_rules definition → RED",
          any("defined 2 times" in v for v in column_mapping_violations(cm_src + "\n\ndef statement_rules(r, c):\n    return r, ''\n")))
    check("NEGATIVE: the page no longer sending the template's carrier → RED",
          any("Commission Ledger page" in v for v in page_carrier_violations(comp, page.replace("fd.append('carrier_id', tmpl.carrier_id)", ""), setup)))

    print("\n(f) CI")
    wf = read(WORKFLOW)
    check("carrier-vocab-guard.yml runs this lock and the proof",
          "python3 harness_ledger_batch_lock.py" in wf and "python3 harness_ledger_batch.py" in wf)

    print("\n%d passed, %d failed" % (P, F))
    if F:
        print("FAIL — the multi-month upload is no longer N single imports; see above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
