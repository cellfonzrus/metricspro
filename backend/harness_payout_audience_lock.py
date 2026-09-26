"""LOCK — every surface that shows an employee their own commission decides "which lines are paid" and "which
fields an employee may see" from ONE home, on the server (owner 2026-09-26: "on the employee commission payout
report we only need o show the line they are getting paid and other lines should be hidden and carrier commission
not be displayed" — applied platform-wide as a design fix, index §6i). stdlib only; static scan.

THE HOME: backend `commcalc/payout_audience.py` — `resolve` (a self-scoped caller is always 'employee'),
`is_paid_line` (the engine's verdict: plan_pay_gate / activation_events), the EMPLOYEE ALLOW-LISTS, and
`employee_explain` / `employee_rep_row` / `employee_drill`. The router's `_payout_audience` is the one place a
handler supplies the caller; `_refuse_employee_audience` keeps manager-only carrier reports from an employee.
The shared breakdown renders the SERVED audience.

FOLLOW-UPS (owner 2026-09-26, index §6j): the audience comes from WHO IS LOOKING (pages send none — a manager gets
the full report); the manager-only list is ONE registry (`MANAGER_ONLY_SURFACES`) that both the server's refusal
and the nav read (`/me` → `permissions.payout` → `rbac.payoutRefused` inside canSeeItem / canAccessPath); a plan line
names its sale through ONE customer rule (`inventory_sold_recon.sale_customer`) and ONE phone rule; the month-range
statement is the single statement per month (`router._statement_doc`).

FAILS THE BUILD WHEN:
  (a) a second copy of the predicate, the audience decision or an allow-list appears under backend/app, or a
      KNOWN carrier field (Price, GP, the MA cross-reference, the dealer figures, the ledger buckets …) is on an
      employee allow-list;
  (b) an employee-facing surface stops going through the home — every one in SURFACES must carry its dereference
      (the Rep Incentive rows + month range, the drill-down, the statements, the Boost drill, the employee
      dashboard bundle, the emailed Incentives report) — or a manager-only carrier report stops refusing the
      employee audience (MANAGER_ONLY);
  (c) any backend function outside the home filters lines to the PAID ones on its own (`not …get("suppressed")`
      in a comprehension / filter), or the frontend grows a paid-line rule (a function named like isPaid /
      paidLine outside the excused display markers), or the breakdown filters rows by audience itself;
  (d) a payout page forces an audience again (sends `audience=`), the page-audience map comes back, or the
      breakdown stops rendering the served one;
  (f) the manager-only registry un-wires: a refusal names an unregistered key, a registered surface is refused by
      no handler, a registered page has no NAV entry, the nav gates stop asking `payoutRefused` first, `/me` stops
      stamping `viewer_payload`, or the self-scope answer is re-derived instead of `role_is_self_scoped`;
  (g) the paid row's sale identity or the month range un-wires: a second customer rule, the drill-down producer not
      stamping line identity, a statement (single / batch / range) not built by `_statement_doc`;
  (e) negative controls — each planted violation turns this lock RED.

    python3 backend/harness_payout_audience_lock.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
FE = os.path.join(os.path.dirname(ROOT), "frontend", "src", "app", "(platform)", "commcalc")
RBAC = os.path.join(os.path.dirname(ROOT), "frontend", "src", "lib", "rbac.ts")
DRILL = "modules/commcalc/commission_drilldown.py"
ISR = "modules/commcalc/inventory_sold_recon.py"
CORE = "modules/core/router.py"
STOREOPS = "modules/storeops/router.py"
HOME = "modules/commcalc/payout_audience.py"
ROUTER = "modules/commcalc/router.py"
# (file, function) → the dereference an EMPLOYEE-facing surface must carry
SURFACES = {
    (ROUTER, "commission_explain"): ["_payout_audience(authorization, org_id, audience, rep=rep)", "_pa.employee_explain(_res)"],
    (ROUTER, "commission_statement_document"): ["_payout_audience(authorization, org_id, audience, rep=rep)",
                                                "_statement_doc(client, org_id, rep, m, _aud, ctx",
                                                "_statement_doc(client, org_id, rep, period, _aud, ctx",
                                                "_pd.month_range(", "_cst.build_range("],
    (ROUTER, "commission_statements_batch"): ["_payout_audience(authorization, org_id, audience, rep=rep)",
                                              "_statement_doc(client, org_id, rep, period, _aud, ctx"],
    (ROUTER, "_statement_doc"): ["_dd.explain_rep(", "_pa.employee_explain(explain)"],
    (DRILL, "explain_rep"): ["attach_line_identity(client, org_id, out)"],
    (DRILL, "_sale_customers"): ["_isr.invoice_customer_map("],
    (DRILL, "attach_line_identity"): ["_pa.stamp_line_identity("],
    (ISR, "sales_detail_index"): ["sale_customer("],
    (ISR, "invoice_customer_map"): ["sale_customer("],
    (ROUTER, "get_commissions"): ["_payout_audience(authorization, org_id, audience)", "_pa.employee_rep_row(r)"],
    (ROUTER, "get_commissions_range"): ["await get_commissions(m, authorization=authorization, org_id=org_id, audience=audience)"],
    (ROUTER, "commission_drill"): ["_payout_audience(authorization, org_id, audience, rep=rep", "_pa.employee_drill("],
    ("modules/core/router.py", "employee_dashboard"): ["_pa.employee_rep_row(myc)"],
    ("modules/notify/report_registry.py", "_commissions"): ['audience="employee"'],
}
# manager-only reports that carry carrier commission per rep — refused to the employee audience, each by its
# REGISTERED key (payout_audience.MANAGER_ONLY_SURFACES)
MANAGER_ONLY = {"carrier_vs_pay_report": "carrier_vs_pay", "get_discrepancy_results": "pay_discrepancy",
                "get_phantom_payments": "pay_discrepancy", "list_discrepancy_appeals": "commission_discrepancy",
                "commission_device": "commission_device"}
HOME_DEFS = ("resolve", "is_paid_line", "employee_explain", "employee_rep_row", "employee_drill", "disallowed_fields",
             "viewer_payload", "manager_only_label", "line_phone", "event_label", "stamp_line_identity")
PAID_FILTER = re.compile(r"(?:if|filter)[^\n]*\bnot\b[^\n]*\.get\(\s*['\"]suppressed['\"]")
# engine / gate / statement code that reads `suppressed` for its OWN job (not to hand an employee paid lines)
PAID_FILTER_EXCUSED = {
    "modules/commcalc/commission_engine.py": "the engine that STAMPS the verdict",
    "modules/commcalc/plan_pay_gate.py": "the gate that decides it",
    "modules/billing/statement.py": "platform billing statement (usage pricing lines) - not a commission line",
}
PAID_FN = re.compile(r"\b(?:function|const)\s+(is[A-Z]?\w*[Pp]aid\w*|\w*[Pp]aid[Ll]ine\w*)\b")
PAID_FN_EXCUSED = {("_lib/planLines.ts", "isPaying"):
                   "pre-existing dual-membership marker in the manager diagnostic (which rule paid a line two rules "
                   "matched); it labels rows and filters nothing — the employee payload is shaped on the server"}
# the payout pages — they render the SERVED audience and never force one (the server decides by who is looking)
FE_PAGES = ("reports/page.tsx", "commission-explain/page.tsx")
FORCED_AUDIENCE = re.compile(r"[?&]audience=|audienceParam\(|PAYOUT_AUDIENCE_OF_PAGE")
P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:400]))


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def walk(root, exts):
    out = {}
    for dp, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "node_modules", ".next")]
        for f in fs:
            if f.endswith(exts):
                p = os.path.join(dp, f)
                out[os.path.relpath(p, root).replace(os.sep, "/")] = read(p)
    return out


def py_code(src):
    src = re.sub(r'"""[\s\S]*?"""', '""', src)
    return "\n".join(ln.split("#", 1)[0] for ln in src.split("\n"))


def ts_code(src):
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return "\n".join(ln.split("//", 1)[0] if "://" not in ln else ln for ln in src.split("\n"))


def fn_body(src, name):
    m = re.search(r"^(?:async\s+)?def\s+%s\s*\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    rest = src[m.start():]
    nxt = re.search(r"^(?:@router\.|def |class |async def )", rest[m.end() - m.start():], re.M)
    return rest[: (m.end() - m.start()) + nxt.start()] if nxt else rest


def ts_fn_body(src, name):
    m = re.search(r"export function %s\s*\(" % re.escape(name), src)
    if not m:
        return ""
    nxt = re.search(r"^export ", src[m.end():], re.M)
    return src[m.start(): m.end() + (nxt.start() if nxt else len(src))]


def registry(home_src):
    """[(key, [pages])] from MANAGER_ONLY_SURFACES."""
    m = re.search(r"^MANAGER_ONLY_SURFACES\s*=\s*\(([\s\S]*?)^\)", home_src, re.M)
    out = []
    for blk in re.split(r'(?=\{"key":)', m.group(1) if m else "")[1:]:
        k = re.search(r'"key":\s*"([^"]+)"', blk)
        pg = re.search(r'"pages":\s*\(([^)]*)\)', blk)
        if k:
            out.append((k.group(1), re.findall(r'"([^"]+)"', pg.group(1) if pg else "")))
    return out


def tuple_values(src, name):
    m = re.search(r"^%s\s*=\s*\(([\s\S]*?)\)\s*$" % re.escape(name), src, re.M)
    return re.findall(r'"([^"]+)"', m.group(1)) if m else []


def scan(be, fe, rbac=None):
    v = []
    rbac = RBAC_SRC if rbac is None else rbac
    home_src = be.get(HOME, "")
    home = py_code(home_src)
    for fn in HOME_DEFS:
        if not re.search(r"^def\s+%s\s*\(" % fn, home, re.M):
            v.append(("a", HOME, "the home no longer defines " + fn))
    for rel, src in sorted(be.items()):
        if rel == HOME:
            continue
        code = py_code(src)
        for fn in ("is_paid_line", "employee_explain", "employee_rep_row", "employee_drill"):
            if re.search(r"^def\s+%s\s*\(" % fn, code, re.M):
                v.append(("a", rel, "a second " + fn))
        if re.search(r"^EMPLOYEE_\w+_FIELDS\s*=", code, re.M):
            v.append(("a", rel, "a second employee allow-list"))
        if re.search(r"^def\s+_payout_audience\s*\(", code, re.M) and rel != ROUTER:
            v.append(("a", rel, "a second _payout_audience"))
    carrier = set(tuple_values(home_src, "KNOWN_CARRIER_FIELDS"))
    if not {"ext_price", "gp", "ma_matches", "boost_commission", "buckets"} <= carrier:
        v.append(("a", HOME, "KNOWN_CARRIER_FIELDS no longer names the carrier fields"))
    for name in re.findall(r"^(EMPLOYEE_\w+_FIELDS)\s*=", home_src, re.M):
        leak = carrier & set(tuple_values(home_src, name))
        if leak:
            v.append(("a", HOME, "%s allows carrier field(s) %s" % (name, sorted(leak))))
    for (rel, fn), needs in SURFACES.items():
        body = py_code(fn_body(be.get(rel, ""), fn))
        if not body:
            v.append(("b", rel, fn + " missing"))
            continue
        for n in needs:
            if n not in body:
                v.append(("b", rel, "%s no longer carries: %s" % (fn, n)))
    router = be.get(ROUTER, "")
    reg = registry(home_src)
    keys = {k for k, _ in reg}
    for fn, key in MANAGER_ONLY.items():
        if '_refuse_employee_audience(authorization, org_id, "%s")' % key not in py_code(fn_body(router, fn)):
            v.append(("b", ROUTER, "manager-only %s no longer refuses the employee audience (key %s)" % (fn, key)))
    # (f) THE registry: every refusal names a registered key; every registered surface is refused somewhere;
    #     every registered page is a NAV entry; the nav gates ask payoutRefused first; /me stamps the payload
    used = set()
    for rel, src in be.items():
        for k in re.findall(r'_refuse_employee_audience\(authorization, org_id, "([^"]+)"\)', py_code(src)):
            used.add(k)
            if k not in keys:
                v.append(("f", rel, "refuses an UNREGISTERED manager-only surface %r" % k))
    for k in keys - used:
        v.append(("f", HOME, "registered surface %r is refused by no handler" % k))
    if not reg or "pay_discrepancy" not in keys:
        v.append(("f", HOME, "MANAGER_ONLY_SURFACES missing or no longer names pay_discrepancy"))
    rb = ts_code(rbac)
    nav_hrefs = set(re.findall(r"href:\s*'([^']+)'", rb))
    for k, pages in reg:
        for pg in pages:
            if pg not in nav_hrefs:
                v.append(("f", "rbac.ts", "registered page %s (%s) has no NAV entry to hide" % (pg, k)))
    if not re.search(r"export function payoutRefused\([^)]*\)[^{]*\{[^}]*refused_pages", rb):
        v.append(("f", "rbac.ts", "payoutRefused no longer reads the server's refused_pages"))
    for fn in ("canSeeItem", "canAccessPath", "navBlockReason"):
        body = ts_fn_body(rb, fn)
        i_pr, i_sa = body.find("payoutRefused("), body.find("isSuperAdmin(")
        if i_pr < 0 or (i_sa >= 0 and i_sa < i_pr):
            v.append(("f", "rbac.ts", "%s no longer asks payoutRefused before any bypass" % fn))
    if "_pa.viewer_payload(_self_scoped(org_id" not in py_code(fn_body(be.get(CORE, ""), "_me_payload")):
        v.append(("f", CORE, "/me no longer stamps payout_audience.viewer_payload"))
    for fn in ("_caller_rep_keys", "_caller_self_keyset"):
        if "role_is_self_scoped(" not in py_code(fn_body(router, fn)):
            v.append(("f", ROUTER, "%s re-derives the self scope instead of role_is_self_scoped" % fn))
    if not re.search(r"^def role_is_self_scoped\(", py_code(be.get(STOREOPS, "")), re.M):
        v.append(("f", STOREOPS, "role_is_self_scoped is gone"))
    # (g) one customer rule, one phone rule — nowhere else
    for rel, src in sorted(be.items()):
        code = py_code(src)
        if rel != ISR and re.search(r"^def\s+(sale_customer|invoice_customer_map)\s*\(", code, re.M):
            v.append(("g", rel, "a second sale-customer rule"))
        if rel != HOME and re.search(r"^def\s+(stamp_line_identity|line_phone)\s*\(", code, re.M):
            v.append(("g", rel, "a second line-identity rule"))
    pa = py_code(fn_body(router, "_payout_audience"))
    if "_caller_rep_keys(authorization, org_id)" not in pa or "_pa.resolve(" not in pa:
        v.append(("b", ROUTER, "_payout_audience no longer reads the caller / decides through payout_audience.resolve"))
    for rel, src in sorted(be.items()):
        if rel == HOME or rel in PAID_FILTER_EXCUSED:
            continue
        for ln in py_code(src).split("\n"):
            if PAID_FILTER.search(ln):
                v.append(("c", rel, "filters PAID lines on its own: " + ln.strip()[:90]))
                break
    for rel, src in sorted(fe.items()):
        for m in PAID_FN.finditer(ts_code(src)):
            if (rel, m.group(1)) not in PAID_FN_EXCUSED:
                v.append(("c", rel, "a second paid-line rule: " + m.group(1)))
    br = ts_code(fe.get("_lib/PlanLineBreakdown.tsx", ""))
    if "saleLabel(l)" not in br:
        v.append(("g", "_lib/PlanLineBreakdown.tsx", "the employee row no longer names the sale (saleLabel)"))
    if re.search(r"\.filter\([^)]*employee", br) or re.search(r"employee[^\n]*\.filter\(", br):
        v.append(("c", "_lib/PlanLineBreakdown.tsx", "filters rows by audience on its own"))
    if "audience === 'employee'" not in br:
        v.append(("d", "_lib/PlanLineBreakdown.tsx", "no longer renders from the served audience"))
    for page in FE_PAGES:
        code = ts_code(fe.get(page, ""))
        if "servedAudience(" not in code:
            v.append(("d", page, "no longer renders the served audience"))
        m = FORCED_AUDIENCE.search(code)
        if m:
            v.append(("d", page, "forces an audience again (%s) — the server decides by who is looking" % m.group(0)))
    if FORCED_AUDIENCE.search(ts_code(fe.get("_lib/payoutAudience.ts", ""))):
        v.append(("d", "_lib/payoutAudience.ts", "a per-page audience declaration came back"))
    v.extend(inprocess_violations(be))
    return v


def inprocess_violations(be):
    """(h) THE CALLER travels with every in-process call. A scheduled / emailed report (notify) that calls a
    commission handler without `authorization=` binds FastAPI's Header SENTINEL — which the audience decision
    reads as 'no caller' → the MANAGER view: a rep could email themselves a report the server refuses them
    (found 2026-09-26: Pay Discrepancy + Phantom Payments after #306). So: every notify call to a commcalc
    handler that takes `authorization` must pass it, and a builder that reaches a manager-only handler must be
    registered `wants_auth` (else it is always called with "")."""
    import ast
    out = []
    try:
        rt = ast.parse(be.get(ROUTER, ""))
    except SyntaxError:
        return [("h", ROUTER, "router does not parse")]
    takes_auth, refusing = set(), set()
    for n in ast.walk(rt):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(a.arg == "authorization" for a in n.args.args + n.args.kwonlyargs):
                takes_auth.add(n.name)
            if "_refuse_employee_audience(" in ast.unparse(n):
                refusing.add(n.name)
    refusing.discard("_refuse_employee_audience")
    for rel, src in sorted(be.items()):
        if not rel.startswith("modules/notify/"):
            continue
        aliases = set(re.findall(r"from app\.modules\.commcalc import router as (\w+)", src))
        if not aliases:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            out.append(("h", rel, "does not parse"))
            continue
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            reaches_refused = False
            for c in ast.walk(fn):
                if not (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                        and isinstance(c.func.value, ast.Name) and c.func.value.id in aliases):
                    continue
                if c.func.attr in takes_auth and "authorization" not in {k.arg for k in c.keywords}:
                    out.append(("h", rel, "%s calls %s without the caller's authorization" % (fn.name, c.func.attr)))
                if c.func.attr in refusing:
                    reaches_refused = True
            if reaches_refused and not re.search(r'"build":\s*%s\s*,\s*"wants_auth":\s*True' % re.escape(fn.name), src):
                out.append(("h", rel, "%s reaches a manager-only report but is not registered wants_auth" % fn.name))
    return out


be = walk(APP, (".py",))
fe = walk(FE, (".ts", ".tsx"))
RBAC_SRC = read(RBAC)
viol = scan(be, fe)
for part, label in (
        ("a", "(a) ONE home: the paid-line predicate, the audience decision, the allow-lists; no carrier field allowed"),
        ("b", "(b) every employee-facing surface goes through the home; every manager-only carrier report refuses an employee"),
        ("c", "(c) nobody filters paid lines on their own (backend or frontend); no second paid-line rule"),
        ("d", "(d) payout pages force no audience (the server decides by who is looking); the breakdown renders the served one"),
        ("f", "(f) ONE manager-only registry: every refusal registered, every registered page hidden by the nav gates, /me stamps it"),
        ("g", "(g) the paid row names its sale through ONE customer / phone rule; every statement is _statement_doc"),
        ("h", "(h) scheduled / emailed reports carry the CALLER into every commission handler (no manager view by default)")):
    check(label, not [x for x in viol if x[0] == part], [x[1:] for x in viol if x[0] == part])

print("\n  negative controls")


def planted(be_mut=None, fe_mut=None, rbac=None):
    b2, f2 = dict(be), dict(fe)
    b2.update(be_mut or {})
    f2.update(fe_mut or {})
    return scan(b2, f2, rbac)


v = planted(be_mut={"modules/commcalc/shadow.py": "def is_paid_line(line):\n    return True\n"})
check("(e) a second is_paid_line → RED", any(x[0] == "a" and "shadow" in x[1] for x in v))
v = planted(be_mut={HOME: be[HOME].replace('EMPLOYEE_LINE_FIELDS = ("date",', 'EMPLOYEE_LINE_FIELDS = ("gp", "date",')})
check("(e) an employee allow-list that lets GP through → RED", any(x[0] == "a" and "allows carrier" in x[2] for x in v))
v = planted(be_mut={ROUTER: be[ROUTER].replace("return _pa.employee_explain(_res)", "return _res")})
check("(e) /commission-explain returning the raw drill to an employee → RED", any(x[0] == "b" for x in v))
v = planted(be_mut={"modules/core/router.py": be["modules/core/router.py"].replace("_pa.employee_rep_row(myc)", "myc")})
check("(e) the employee dashboard handing back the raw commission row → RED", any(x[0] == "b" and "core" in x[1] for x in v))
v = planted(be_mut={ROUTER: be[ROUTER].replace('_refuse_employee_audience(authorization, org_id, "pay_discrepancy")', "")})
check("(e) a manager-only carrier report open to an employee → RED", any(x[0] == "b" and "get_discrepancy_results" in x[2] for x in v))
v = planted(be_mut={"modules/commcalc/rep_view.py":
                    "def paid(lines):\n    return [l for l in lines if not l.get('suppressed') and l.get('amount')]\n"})
check("(e) a backend surface filtering paid lines on its own → RED", any(x[0] == "c" and "rep_view" in x[1] for x in v))
v = planted(fe_mut={"_lib/employeeLines.ts": "export function isPaidLine(l: any) { return !l.suppressed && l.amount > 0 }"})
check("(e) a second paid-line rule on the frontend → RED", any(x[0] == "c" and "employeeLines" in x[1] for x in v))
v = planted(fe_mut={"reports/page.tsx": fe["reports/page.tsx"].replace(
    "/api/v1/commcalc/commissions-range?period_from=", "/api/v1/commcalc/commissions-range?audience=employee&period_from=")})
check("(e) the Rep Incentive page forcing the employee audience on a manager again → RED",
      any(x[0] == "d" and "reports" in x[1] for x in v))
v = planted(be_mut={ROUTER: be[ROUTER].replace('_refuse_employee_audience(authorization, org_id, "carrier_vs_pay")',
                                               '_refuse_employee_audience(authorization, org_id, "carrier_vs_pay_v2")')})
check("(e) a refusal naming an unregistered surface (the nav could not hide it) → RED",
      any(x[0] == "f" and "UNREGISTERED" in x[2] for x in v))
v = planted(rbac=RBAC_SRC.replace("  if (payoutRefused(perms, item.href)) return false", "", 1))
check("(e) the menu gate no longer hiding a server-refused page from a rep → RED",
      any(x[0] == "f" and "canSeeItem" in x[2] for x in v))
v = planted(rbac=RBAC_SRC.replace("href: '/commcalc/discrepancy'", "href: '/commcalc/discrepancy-v2'"))
check("(e) a registered page with no NAV entry to hide → RED", any(x[0] == "f" and "no NAV entry" in x[2] for x in v))
v = planted(be_mut={CORE: be[CORE].replace("_pa.viewer_payload(_self_scoped(org_id", "_pa.viewer_payload((False")})
check("(e) /me no longer telling the nav what the server refuses → RED", any(x[0] == "f" and "/me" in x[2] for x in v))
v = planted(be_mut={"modules/commcalc/rep_names.py": "def sale_customer(row):\n    return row.get('customer')\n"})
check("(e) a second sale-customer rule → RED", any(x[0] == "g" and "rep_names" in x[1] for x in v))
v = planted(be_mut={ROUTER: be[ROUTER].replace("_statement_doc(client, org_id, rep, m, _aud, ctx",
                                               "_own_month_doc(client, org_id, rep, m, _aud, ctx")})
check("(e) the month range building its own statement instead of the single one → RED",
      any(x[0] == "b" and "commission_statement_document" in x[2] for x in v))
REG = "modules/notify/report_registry.py"
v = planted(be_mut={REG: be[REG].replace("org_id=org_id, authorization=authorization)\n    summary", "org_id=org_id)\n    summary", 1)})
check("(e) the emailed Pay Discrepancy calling its handler without the caller → RED",
      any(x[0] == "h" and "get_discrepancy_results" in x[2] for x in v))
v = planted(be_mut={REG: be[REG].replace('"build": _phantom, "wants_auth": True}', '"build": _phantom}', 1)})
check("(e) a builder reaching a manager-only report without wants_auth → RED",
      any(x[0] == "h" and "_phantom" in x[2] and "wants_auth" in x[2] for x in v))
v = planted()
check("(e) the unmodified tree is GREEN", not v)

print("\n%d passed, %d failed" % (P, F))
if F:
    sys.exit(1)
print("OK — one audience decision, one paid-line predicate, one allow-list, one manager-only registry; no employee "
      "surface carries carrier commission and no rep is offered a report the server refuses.")
