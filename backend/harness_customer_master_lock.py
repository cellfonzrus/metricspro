"""THE LOCK — THE CUSTOMER MASTER keeps ONE matcher, ONE match decision, ONE phone rule, ONE placeholder home, ONE
line writer for the rebuild, and a pure identity module (owner 2026-09-24; index §30.16; CLAUDE.md 2026-09-20 "A fix is
a DESIGN fix … lock it so it cannot un-wire"). stdlib only, DB-free — runs in CI beside the sales-from-reports lock
(carrier-vocab-guard.yml).
    python3 harness_customer_master_lock.py        (from backend/)

  (a) ONE CREATOR — under backend/app/modules only `pos/receipt_import.py` (THE matcher's write half, inside
      `match_or_create`) inserts a customer from a sale. The pre-existing siblings that also insert into pos.customers
      are NAMED here with the reason each is excused (ALLOW_CUSTOMER_INSERT); a new inserter, or a stale allow entry,
      fails the build.
  (b) ONE DECISION — `def decide(` lives in pos/customer_identity.py alone (under pos/); the matcher's read half
      (`match_customer`) calls `_cid.decide(`; `find_customer` and `_match_or_create_customer` go through
      `match_customer` / `match_or_create`; `import_structured` and `upsert_structured` call `match_or_create(`; the
      rebuild hands the invoice's phone lines (`identity=`).
  (c) ONE PHONE RULE for matching a customer — `def norm_phone(` only in customer_identity.py, which DEREFERENCES the
      CRM's national-number rule (`_national(` = crm.pipeline_core.normalize_phone); the matcher, the master and the
      search call `_cid.norm_phone(`; neither the matcher section nor customer_master.py calls `mobile_key(`,
      `normalize_phone(` or the OCR parser's `_digits(`.
  (d) ONE PLACEHOLDER HOME — the words only in customer_identity.py (also held by the sales-from-reports lock (i));
      the matcher and the dedupe ask `_cid.is_placeholder(`.
  (e) THE LINES — the rebuild writes pos.activations only through `customer_master.sync_invoice_lines`, which reads the
      phone lines through `_cid.invoice_lines(` with the ONE pairing rule injected (`_isr.line_pairings`,
      `_dcr.device_key`) and the plan words through `load_plan_sources(` + `line_matches(`; customer_identity.py
      defines no pairing of its own. Other activation inserters are NAMED (ALLOW_ACTIVATION_INSERT).
  (f) PURE — customer_identity.py imports only the stdlib, crm.pipeline_core and receipt_formats.base (both pure).
  (g) THE MIGRATION — 1017 creates pos.customer_aliases + merged_into + merge_record, idempotent, with a REVERT note;
      the fake client declares EXACTLY the migration's columns; customer_master.MIGRATION names the file.
  (h) THE ENDPOINTS dereference customer_master; the customer search composes no PostgREST or-string.
  (i) THE PAGE calls /lines, /invoices, /aliases, /merge, /unmerge, the existing /activations/{id}/notes and PATCH,
      and prints through `apiPrintHtml(` on /receipt-imports/{id}/print.
  (j) REGISTERED — index §30.16 + §16 / §17 rows; CI runs this lock and the proof, and watches the files.
  Negative controls prove each rule goes red.
"""
import io
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BE = os.path.join(ROOT, "backend", "app", "modules")
_pass = _fail = 0

# THE NAMED SIBLINGS that insert pos.customers outside the matcher — each with why it is excused (index §30.16)
ALLOW_CUSTOMER_INSERT = {
    "pos/router.py": "a PERSON keys a customer in (POST /pos/customers) or imports a customer CSV (POST /pos/import/customers, "
                     "deduped by phone / e-mail) — a human decision, not a sale's bill-to; the dedupe cleanup and merge fold "
                     "any duplicate it makes",
    "crm/router.py": "convert_lead — a won CRM lead becomes a customer; it LINKS by phone first through crm.pipeline_core."
                     "normalize_phone, the same national-number rule customer_identity.norm_phone dereferences",
    "pos/vendor_rebate_report.py": "import_report — the vendor rebate history importer (§27.4: no UI, known wrong four ways, "
                                   "excused by name until it is retired or folded)",
}
# THE NAMED SIBLINGS that insert pos.activations outside the rebuild's line writer
ALLOW_ACTIVATION_INSERT = {
    "pos/router.py": "a person records an activation (POST /pos/activations) or imports an activations CSV — the POS's own entry",
    "pos/vendor_rebate_report.py": "the vendor rebate history importer (§27.4, excused by name)",
    "pos/customer_master.py": "sync_invoice_lines — THE rebuild's line writer (this design)",
}
MATCHER = "pos/receipt_import.py"
IDENTITY = "pos/customer_identity.py"
MASTER = "pos/customer_master.py"


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}" + (f"  — {str(extra)[:400]}" if extra != "" else ""))


def read(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


def scan_files(root, exts=(".py",)):
    out = {}
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            if fn.endswith(exts):
                p = os.path.join(dp, fn)
                out[os.path.relpath(p, root).replace(os.sep, "/")] = io.open(p, encoding="utf-8", errors="replace").read()
    return out


def code_of(body):
    return re.sub(r'"""[\s\S]*?"""|#[^\n]*', "", body)


def inserts_into(body, table):
    """Does this source insert into `table` — a chained `.table("<t>")…insert(`, a builder variable assigned from
    `.table("<t>")` and then `.insert(`ed, or the router's `_batch_insert("<t>"`."""
    c = code_of(body)
    if re.search(rf"[\"']{table}[\"']\s*\)\s*\.insert\(", c):          # .table("<t>").insert( / _pos(client, "<t>").insert(
        return True
    if re.search(rf"_batch_insert\(\s*[\"']{table}[\"']", c):
        return True
    for m in re.finditer(rf"^\s*(\w+)\s*=\s*[^\n]*table\(\s*[\"']{table}[\"']\s*\)\s*$", c, re.M):
        if re.search(rf"\b{re.escape(m.group(1))}\.insert\(", c):
            return True
    return False


def fn_body(body, name):
    m = re.search(rf"^def {name}\(.*?(?=^def |^# ── |\Z)", body, re.M | re.S)
    return m.group(0) if m else ""


def migration_columns(sql, table):
    m = re.search(rf"CREATE TABLE IF NOT EXISTS pos\.{table} \((.*?)\n\);", sql, re.S)
    if not m:
        return None
    cols = []
    for ln in m.group(1).splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("--") or ln.upper().startswith("CONSTRAINT") or ln.upper().startswith("FOREIGN"):
            continue
        cols.append(ln.split()[0])
    return cols


def violations(files, fake_src, sql, allow_cust=None, allow_act=None):
    allow_cust = ALLOW_CUSTOMER_INSERT if allow_cust is None else allow_cust
    allow_act = ALLOW_ACTIVATION_INSERT if allow_act is None else allow_act
    v = []
    # (a) one creator
    for rel, body in files.items():
        if inserts_into(body, "customers") and rel != MATCHER and rel not in allow_cust:
            v.append((rel, "second_creator", "inserts into pos.customers outside the matcher and the named siblings"))
    for rel in allow_cust:
        if not inserts_into(files.get(rel, ""), "customers"):
            v.append((rel, "stale_allow", "named as a customer inserter but no longer inserts — remove it from ALLOW_CUSTOMER_INSERT"))
    ri = files.get(MATCHER, "")
    if not inserts_into(fn_body(ri, "match_or_create"), "customers"):
        v.append((MATCHER, "second_creator", "the matcher's customer insert must live inside match_or_create"))
    # (b) one decision
    for rel, body in files.items():
        if rel.startswith("pos/") and rel != IDENTITY and re.search(r"^def decide\(", body, re.M):
            v.append((rel, "second_decision", "defines decide() outside customer_identity.py"))
    if "_cid.decide(" not in fn_body(ri, "match_customer"):
        v.append((MATCHER, "decision_unwired", "match_customer must call _cid.decide("))
    if "match_customer(" not in fn_body(ri, "find_customer") or "match_or_create(" not in fn_body(ri, "_match_or_create_customer"):
        v.append((MATCHER, "decision_unwired", "find_customer / _match_or_create_customer must go through match_customer / match_or_create"))
    if "match_or_create(" not in fn_body(ri, "import_structured") or "match_or_create(" not in fn_body(ri, "upsert_structured"):
        v.append((MATCHER, "decision_unwired", "import_structured / upsert_structured must call match_or_create("))
    sfr = files.get("pos/sales_from_reports.py", "")
    if "identity={" not in fn_body(sfr, "rebuild"):
        v.append(("pos/sales_from_reports.py", "decision_unwired", "the rebuild must hand the invoice's phone lines to upsert_structured (identity=)"))
    # (c) one phone rule
    ident = files.get(IDENTITY, "")
    for rel, body in files.items():
        if rel.startswith("pos/") and rel != IDENTITY and re.search(r"^def \w*norm\w*phone\w*\(|^def \w*phone\w*norm\w*\(", body, re.M):
            v.append((rel, "second_phone_rule", "defines a phone normaliser outside customer_identity.py"))
    if "_national(" not in fn_body(ident, "norm_phone"):
        v.append((IDENTITY, "second_phone_rule", "norm_phone must dereference crm.pipeline_core.normalize_phone (_national)"))
    matcher_sec = "".join(fn_body(ri, f) for f in ("_incoming", "_candidates", "match_customer", "find_customer", "match_or_create"))
    for rel, body in ((MATCHER, matcher_sec), (MASTER, code_of(files.get(MASTER, "")))):
        for bad in ("mobile_key(", "normalize_phone(", "_digits("):
            if bad in body:
                v.append((rel, "second_phone_rule", f"calls {bad} — a customer phone is compared through _cid.norm_phone"))
    if "_cid.norm_phone(" not in matcher_sec or "_cid.norm_phone(" not in files.get(MASTER, ""):
        v.append((MATCHER, "second_phone_rule", "the matcher and the master must compare phones through _cid.norm_phone("))
    # (d) the placeholder home
    for rel, body in files.items():
        if rel != IDENTITY and re.search(r"[\"']walk ?in[\"']|[\"']no customer[\"']|[\"']cash customer[\"']", code_of(body), re.I):
            v.append((rel, "placeholder_second_home", "spells a placeholder bill-to word outside customer_identity.py"))
    if "_cid.is_placeholder(" not in fn_body(files.get(MASTER, ""), "dedupe_plan") or "_cid.is_placeholder(" not in matcher_sec:
        v.append((MASTER, "placeholder_unwired", "the matcher and the dedupe must ask _cid.is_placeholder("))
    # (e) the lines
    for rel, body in files.items():
        if inserts_into(body, "activations") and rel not in allow_act:
            v.append((rel, "second_line_writer", "inserts into pos.activations outside the named writers"))
    for rel in allow_act:
        if not inserts_into(files.get(rel, ""), "activations"):
            v.append((rel, "stale_allow", "named as an activation inserter but no longer inserts"))
    sync = fn_body(files.get(MASTER, ""), "sync_invoice_lines")
    if "_cid.invoice_lines(" not in sync or "_isr.line_pairings" not in sync or "_dcr.device_key" not in sync:
        v.append((MASTER, "lines_unwired", "sync_invoice_lines must read the lines through _cid.invoice_lines with the one pairing rule injected"))
    ppred = fn_body(files.get(MASTER, ""), "plan_line_predicate")
    if "load_plan_sources(" not in ppred or "line_matches(" not in ppred:
        v.append((MASTER, "lines_unwired", "the plan words must come from onboarding.load_plan_sources + plan_sources.line_matches"))
    if "_cmr.sync_invoice_lines(" not in fn_body(sfr, "rebuild"):
        v.append(("pos/sales_from_reports.py", "lines_unwired", "the rebuild must record each invoice's lines through customer_master.sync_invoice_lines"))
    if re.search(r"^def (line_pairings|sales_mobile_index)\(", ident, re.M) or "same transaction" in code_of(ident):
        v.append((IDENTITY, "second_pairing", "customer_identity must not carry a pairing rule of its own — line_pairings is injected"))
    # (f) pure
    for m in re.finditer(r"^\s*(?:from|import)\s+(app[\w.]*)", code_of(ident), re.M):
        if m.group(1) not in ("app.modules.crm.pipeline_core", "app.modules.pos.receipt_formats.base"):
            v.append((IDENTITY, "impure", f"imports {m.group(1)} — the identity module stays pure (stdlib + the two pure rule homes)"))
    # (g) the migration and the fake
    if not sql or "CREATE TABLE IF NOT EXISTS pos.customer_aliases" not in sql or "ADD COLUMN IF NOT EXISTS merged_into" not in sql \
            or "ADD COLUMN IF NOT EXISTS merge_record" not in sql or "-- REVERT" not in sql:
        v.append(("1017", "migration", "1017 must create pos.customer_aliases and add merged_into / merge_record idempotently, with a REVERT note"))
    mc = migration_columns(sql or "", "customer_aliases")
    fm = re.search(r'"customer_aliases":\s*\[([^\]]*)\]', fake_src or "")
    fake_cols = re.findall(r'"(\w+)"', fm.group(1)) if fm else None
    if mc is None or fake_cols != mc:
        v.append(("harness_intake_fakes.py", "fake_drift", f"the fake declares {fake_cols}; migration 1017 creates {mc}"))
    cm = re.search(r'"customers":\s*\[([^\]]*)\]', fake_src or "")
    if not cm or '"merged_into"' not in cm.group(1) or '"merge_record"' not in cm.group(1) or '"notes"' in cm.group(1):
        v.append(("harness_intake_fakes.py", "fake_drift", "the fake's customers must be mig 725 (no notes) + mig 1017's two columns"))
    if 'MIGRATION = "1017_pos_customer_identity.sql"' not in files.get(MASTER, ""):
        v.append((MASTER, "migration", "customer_master.MIGRATION must name the migration file"))
    # (h) the endpoints
    pr = files.get("pos/router.py", "")
    lc = fn_body(pr, "list_customers")
    if "_cmaster.search_ids(" not in lc or ".or_(" in lc:
        v.append(("pos/router.py", "search_unwired", "list_customers must search through customer_master.search_ids (no or-string)"))
    for fn, call in (("customer_lines", "_cmaster.lines_payload("), ("customer_invoices", "_cmaster.invoices_payload("),
                     ("customer_aliases", "_cmaster.aliases_payload("), ("customer_merge", "_cmaster.merge_customers("),
                     ("customer_unmerge", "_cmaster.unmerge("), ("customers_dedupe", "_cmaster.dedupe_plan(")):
        if call not in fn_body(pr, fn):
            v.append(("pos/router.py", "endpoint_unwired", f"{fn} must call {call}"))
    return v


def main():
    real = scan_files(BE)
    fake = read("backend/harness_intake_fakes.py")
    sql = read("database/migrations/1017_pos_customer_identity.sql")
    viol = violations(real, fake, sql)
    kinds = lambda *k: [x for x in viol if x[1] in k]      # noqa: E731
    check("(a) ONE creator: only the matcher's match_or_create inserts a customer from a sale; the three pre-existing siblings are named with reasons",
          not kinds("second_creator", "stale_allow"), viol)
    check("(b) ONE decision: decide() in customer_identity alone; the matcher calls it; every import path goes through match_or_create; the rebuild hands the phone lines",
          not kinds("second_decision", "decision_unwired"), viol)
    check("(c) ONE phone rule: norm_phone in customer_identity, dereferencing the CRM's national number; the matcher and the master compare through it",
          not kinds("second_phone_rule"), viol)
    check("(d) ONE placeholder home; the matcher and the dedupe ask is_placeholder", not kinds("placeholder_second_home", "placeholder_unwired"), viol)
    check("(e) THE LINES: one writer for the rebuild, the one pairing rule injected, the plan words from plan_sources; other writers named",
          not kinds("second_line_writer", "lines_unwired", "second_pairing"), viol)
    check("(f) customer_identity is pure", not kinds("impure"), viol)
    check("(g) migration 1017 (idempotent, REVERT) and the fake declares exactly its columns", not kinds("migration", "fake_drift"), viol)
    check("(h) the endpoints dereference customer_master; the search composes no or-string", not kinds("search_unwired", "endpoint_unwired"), viol)
    page = read("frontend/src/app/(platform)/pos/customers/page.tsx")
    check("(i) the customer page: Lines / Invoices / aliases / merge / un-merge, notes + edit through the existing activation endpoints, print through apiPrintHtml",
          all(x in page for x in ("/lines`", "/invoices`", "/aliases`", "/merge`", "/unmerge`", "/api/v1/pos/activations/${", "/notes`",
                                  "apiPrintHtml(", "/api/v1/pos/receipt-imports/${")) and "method: 'PATCH'" in page
          and "Activations are not available yet" not in page)
    idx = read("docs/SYSTEM_DATA_FLOW_INDEX.md")
    yml = read(".github/workflows/carrier-vocab-guard.yml")
    check("(j) registered (index §30.16 + the table / endpoint rows) and wired into CI (this lock, the proof, the watched paths)",
          "### 30.16" in idx and "pos.customer_aliases" in idx and "/pos/customers/{id}/lines" in idx and "/pos/customers/dedupe" in idx
          and "harness_customer_master_lock.py" in idx and "harness_customer_master_lock.py" in yml and "harness_customer_master.py" in yml
          and "backend/app/modules/pos/customer_master.py" in yml and "backend/app/modules/pos/customer_identity.py" in yml
          and "database/migrations/1017_pos_customer_identity.sql" in yml)

    # ── negative controls ──
    def red(kind, files=None, fake_src=fake, sql_src=sql, **kw):
        return any(x[1] == kind for x in violations(files if files is not None else real, fake_src, sql_src, **kw))

    ctl = dict(real)
    ctl["pos/other_import.py"] = 'def f(c, o):\n    c.schema("pos").table("customers").insert({"org_id": o}).execute()\n'
    check("N1 a second module creating customers → RED", red("second_creator", ctl))
    ctl = dict(real)
    ctl["pos/other_import.py"] = 'def f(c, o):\n    t = c.schema("pos").table("customers")\n    t.insert({"org_id": o}).execute()\n'
    check("N2 … through a builder variable → RED", red("second_creator", ctl))
    check("N3 a stale allow entry (a sibling that no longer inserts) → RED",
          red("stale_allow", allow_cust={**ALLOW_CUSTOMER_INSERT, "pos/catalog_suggest.py": "no longer inserts"}))
    ctl = dict(real)
    ctl["pos/second_match.py"] = "def decide(incoming, candidates):\n    return {}\n"
    check("N4 a second decide() under pos/ → RED", red("second_decision", ctl))
    ctl = dict(real)
    ctl[MATCHER] = real[MATCHER].replace("_cid.decide(", "_cid.nodecide(")
    check("N5 the matcher no longer calling decide → RED", red("decision_unwired", ctl))
    ctl = dict(real)
    ctl["pos/sales_from_reports.py"] = real["pos/sales_from_reports.py"].replace('identity={"phones": phones', 'ident={"phones": phones')
    check("N6 the rebuild no longer handing the phone lines → RED", red("decision_unwired", ctl))
    ctl = dict(real)
    ctl[MASTER] = real[MASTER] + "\n\ndef norm_phone_last10(v):\n    return str(v)[-10:]\n"
    check("N7 a second phone normaliser → RED", red("second_phone_rule", ctl))
    ctl = dict(real)
    ctl[MASTER] = real[MASTER].replace("q = _cid.norm_phone(digits) or digits", "q = _isr.mobile_key(digits) or digits")
    check("N8 the master comparing phones through another normaliser → RED", red("second_phone_rule", ctl))
    ctl = dict(real)
    ctl[MASTER] = real[MASTER] + '\nSKIP = ("walk in", "cash customer")\n'
    check("N9 a placeholder list in the master → RED", red("placeholder_second_home", ctl))
    ctl = dict(real)
    ctl["pos/sales_from_reports.py"] = real["pos/sales_from_reports.py"].replace("_cmr.sync_invoice_lines(", "_cmr.noop(")
    check("N10 the rebuild no longer recording the lines → RED", red("lines_unwired", ctl))
    ctl = dict(real)
    ctl["pos/other_lines.py"] = 'def f(c, o):\n    c.schema("pos").table("activations").insert({"org_id": o}).execute()\n'
    check("N11 a second activation writer → RED", red("second_line_writer", ctl))
    ctl = dict(real)
    ctl[IDENTITY] = real[IDENTITY] + "\nfrom app.modules.commcalc import inventory_sold_recon as _isr\n"
    check("N12 the identity module importing commcalc (a pairing copy / impurity) → RED", red("impure", ctl))
    check("N13 the fake drifting from the migration → RED",
          red("fake_drift", fake_src=fake.replace('"alias_norm", "source", "first_seen"', '"alias_norm", "first_seen"')))
    ctl = dict(real)
    ctl["pos/router.py"] = real["pos/router.py"].replace("ids = _cmaster.search_ids(client, org_id, search)", "ids = None\n    q0 = client.or_('x')")
    check("N14 the search back to a single-column or-string → RED", red("search_unwired", ctl))
    check("N15 the real tree is clean", viol == [], viol)

    print(f"\n{_pass} passed, {_fail} failed")
    if _fail:
        print("RED — a second customer creator / decision / phone rule / placeholder list / line writer, or an unwired caller. "
              "Dereference pos/customer_identity + pos/customer_master + receipt_import.match_or_create (index §30.16).")
        sys.exit(1)
    print("OK — one matcher, one decision, one phone rule, one placeholder home, one line writer; the page and the endpoints dereference them.")


if __name__ == "__main__":
    main()
