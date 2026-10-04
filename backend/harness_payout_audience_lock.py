"""LOCK — every surface that shows an employee their own commission decides "which lines are paid" and "which
fields an employee may see" from ONE home, on the server (owner 2026-09-26: "on the employee commission payout
report we only need o show the line they are getting paid and other lines should be hidden and carrier commission
not be displayed" — applied platform-wide as a design fix, index §6i). stdlib only; a static scan, plus the home's
PURE shapers executed from their source (part i1).

THE HOME: backend `commcalc/payout_audience.py` — `resolve` (a self-scoped caller is always 'employee'),
`is_paid_line` (the engine's verdict: plan_pay_gate / activation_events), the EMPLOYEE ALLOW-LISTS, and
`employee_explain` / `employee_rep_row` / `employee_drill`. The router's `_payout_audience` is the one place a
handler supplies the caller; `_require_carrier_view` (was `_refuse_employee_audience`, employee-only, until
2026-09-28) keeps the carrier surfaces from everyone without THE carrier permission.
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
  (i) CARRIER COMMISSION IS FOR MANAGEMENT'S EYES ONLY (owner 2026-09-28, index §6m: "it should not show any
      commission received on the rep incentive report, that is only for the eyes of the management, gated out from
      all levels"):
        i1 any Rep Incentive surface (the rows, the plan drill, the Boost drill, the statement single / batch / range
           and its CSV, the emailed Incentives report, the page's tables) can return or render a carrier field to ANY
           audience — static over every return of each handler, plus the home's shapers RUN on a payload carrying every
           carrier field at every level, for every audience;
        i2 a carrier surface is reachable without THE permission — a registered handler not refusing through
           `_require_carrier_view`, that refusal not deciding through `_can_view_carrier_commission` →
           `carrier_view_allowed`, the old employee-only refusal coming back, /me not telling the nav, or a link to a
           menu-less carrier page that does not ask `payoutRefused`;
        i3 the permission's role list is read anywhere but its one home (`payout_audience.carrier_view_allowed`):
           the grant key read in backend code outside the home, or read on the frontend with hasDataGrant;
        i4 a second permission for the same question appears: another carrier-commission gate function, another
           carrier grant key / constant, or a carrier surface that also asks another gate;
  (e) negative controls — each planted violation turns this lock RED.

    python3 backend/harness_payout_audience_lock.py
"""
import os
import functools
import re
import sys

# The ruling for which file is the one home lives in the module graph (index 50), not in a
# literal here — owner directive 2026-10-04 "the tree should be interlinked properly".
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.core.module_graph import home_under_app  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
FE = os.path.join(os.path.dirname(ROOT), "frontend", "src", "app", "(platform)", "commcalc")
RBAC = os.path.join(os.path.dirname(ROOT), "frontend", "src", "lib", "rbac.ts")
DRILL = "modules/commcalc/commission_drilldown.py"
ISR = "modules/commcalc/inventory_sold_recon.py"
CORE = "modules/core/router.py"
STOREOPS = "modules/storeops/router.py"
HOME = home_under_app("payout_audience")     # the graph is the one home for the ruling
ROUTER = "modules/commcalc/router.py"
# (file, function) → the dereference an EMPLOYEE-facing surface must carry
SURFACES = {
    (ROUTER, "commission_explain"): ["_payout_audience(authorization, org_id, audience, rep=rep)",
                                     "_pa.rep_incentive_explain(_res, _aud)"],
    (ROUTER, "commission_statement_document"): ["_payout_audience(authorization, org_id, audience, rep=rep)",
                                                "_statement_doc(client, org_id, rep, m, _aud, ctx",
                                                "_statement_doc(client, org_id, rep, period, _aud, ctx",
                                                "_pd.month_range(", "_cst.build_range("],
    (ROUTER, "commission_statements_batch"): ["_payout_audience(authorization, org_id, audience, rep=rep)",
                                              "_statement_doc(client, org_id, rep, period, _aud, ctx"],
    (ROUTER, "_statement_doc"): ["_dd.explain_rep(", "_pa.rep_incentive_explain(explain, aud)", "buckets=None"],
    (DRILL, "explain_rep"): ["attach_line_identity(client, org_id, out)"],
    (DRILL, "_sale_customers"): ["_isr.invoice_customer_map("],
    (DRILL, "attach_line_identity"): ["_pa.stamp_line_identity("],
    (ISR, "sales_detail_index"): ["sale_customer("],
    (ISR, "invoice_customer_map"): ["sale_customer("],
    (ROUTER, "get_commissions"): ["return [_pa.rep_incentive_row(r) for r in rows]"],
    (ROUTER, "get_commissions_range"): ["await get_commissions(m, authorization=authorization, org_id=org_id, audience=audience)"],
    (ROUTER, "commission_drill"): ["_payout_audience(authorization, org_id, audience, rep=rep", "_pa.rep_incentive_drill(out, _aud)"],
    ("modules/core/router.py", "employee_dashboard"): ["_pa.employee_rep_row(myc)"],
    ("modules/notify/report_registry.py", "_commissions"): ['audience="employee"', "C.get_commissions("],
}
# the carrier surfaces (what the carrier paid the store, per rep) — each refused to every viewer without THE
# carrier permission, by its REGISTERED key (payout_audience.MANAGER_ONLY_SURFACES)
MANAGER_ONLY = {"carrier_vs_pay_report": "carrier_vs_pay", "get_discrepancy_results": "pay_discrepancy",
                "get_phantom_payments": "pay_discrepancy", "run_discrepancy_check": "pay_discrepancy",
                "list_discrepancy_appeals": "commission_discrepancy", "commission_device": "commission_device",
                "commission_explain": "commission_explain_carrier"}
HOME_DEFS = ("resolve", "is_paid_line", "employee_explain", "employee_rep_row", "employee_drill", "disallowed_fields",
             "viewer_payload", "manager_only_label", "line_phone", "event_label", "stamp_line_identity",
             "rep_incentive_explain", "rep_incentive_row", "rep_incentive_drill", "rep_incentive_line",
             "carrier_view_allowed", "refusal_message", "carrier_fields_in")
# (i) THE CARRIER PERMISSION — its one home and the only callers of it
GRANT_KEY = "carrier_commission_view"
GATE_FN = {(ROUTER, "_can_view_carrier_commission"), (ROUTER, "_require_carrier_view"), (HOME, "carrier_view_allowed")}
ALLOWED_DECIDERS = {(ROUTER, "_can_view_carrier_commission"), ("modules/core/router.py", "_me_payload")}
SECOND_GATE = re.compile(r"^(?:async\s+)?def\s+(_?(?:can_view|require|can_see|may_see|allow)\w*carrier\w*"
                         r"|\w*carrier_(?:comm|commission|money)\w*(?:allowed|visible|gate|view)\w*)\s*\(", re.M)
# other gates a carrier surface must NOT also ask (two permissions answering one question)
OTHER_GATES = ("_can_view_carrier_residual(", "_require_carrier_residual(", "_can_view_device_commission(",
               "_refuse_employee_audience(", "_pay_visible(", "can_see_pay(")
# DATA_GRANTS keys that mention the carrier: THE one, plus the excused residual posture (a different question — the
# raw_mi residual reports under the tenant's `residual_visibility`, default open; not carrier COMMISSION on a report)
CARRIER_GRANT_KEYS_EXCUSED = {
    "carrier_residual": "raw_mi residual reports under residual_visibility (default 'all') — a tenant posture over the "
                        "residual REPORTS, not carrier commission per rep on a commission report",
    "whatif_carrier_income": "the What-If simulator's Company Payout / Carrier Income tab (whatif_gates) — a PROJECTION "
                             "of company income, not the carrier's recorded commission per rep; default-closed like "
                             "this one. Folding it under carrier_commission_view is an owner decision (index §6m)",
}
# gate functions whose NAME mentions the carrier but answer another question
SECOND_GATE_EXCUSED = {
    "_require_carrier_template_edit": "who may IMPORT a carrier template (money-config write) — the "
                                      "'commission_plans' settings permission, not who may SEE carrier commission",
    "_can_view_carrier_residual": "the residual posture (see CARRIER_GRANT_KEYS_EXCUSED)",
    "_require_carrier_residual": "the residual posture (see CARRIER_GRANT_KEYS_EXCUSED)",
}
# every return a Rep Incentive handler may make — anything else is a way to hand back a carrier field
REP_INCENTIVE_RETURNS = {
    "commission_explain": {"return {**_res, 'audience': 'manager', 'carrier_view': True}",
                           "return _pa.rep_incentive_explain(_res, _aud)"},
    "commission_drill": {"return _pa.rep_incentive_drill(out, _aud)"},
    "get_commissions": {"return [_pa.rep_incentive_row(r) for r in rows]"},
}
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


@functools.lru_cache(maxsize=None)
def py_code(src):
    src = re.sub(r'"""[\s\S]*?"""', '""', src)
    return "\n".join(ln.split("#", 1)[0] for ln in src.split("\n"))


@functools.lru_cache(maxsize=None)
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


def registry(home_src, with_menu=False):
    """[(key, [pages])] from MANAGER_ONLY_SURFACES — or [(key, [pages], menu)] (menu False = a page with no NAV
    entry, reached only by in-app links)."""
    m = re.search(r"^MANAGER_ONLY_SURFACES\s*=\s*\(([\s\S]*?)^\)", home_src, re.M)
    out = []
    for blk in re.split(r'(?=\{"key":)', m.group(1) if m else "")[1:]:
        k = re.search(r'"key":\s*"([^"]+)"', blk)
        pg = re.search(r'"pages":\s*\(([^)]*)\)', blk)
        if k:
            pages = re.findall(r'"([^"]+)"', pg.group(1) if pg else "")
            menu = not re.search(r'"menu":\s*False', blk)
            out.append((k.group(1), pages, menu) if with_menu else (k.group(1), pages))
    return out


@functools.lru_cache(maxsize=None)
def _parse(src):
    """ast.parse, once per distinct source (the router is parsed by several checks, in every planted scan)."""
    import ast
    return ast.parse(src)


@functools.lru_cache(maxsize=None)
def _router_facts(src):
    """(handlers taking `authorization`, handlers that refuse through `_require_carrier_view`) — once per source."""
    import ast
    takes_auth, refusing = set(), set()
    lines = src.split("\n")
    for n in ast.walk(_parse(src)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(a.arg == "authorization" for a in n.args.args + n.args.kwonlyargs):
                takes_auth.add(n.name)
            if "_require_carrier_view(" in py_code("\n".join(lines[n.lineno - 1:n.end_lineno])):
                refusing.add(n.name)
    refusing.discard("_require_carrier_view")
    return frozenset(takes_auth), frozenset(refusing)


@functools.lru_cache(maxsize=None)
def top_returns(src, name):
    """The unparsed Return statements of function `name` itself (nested defs excluded) — [] when absent."""
    import ast
    try:
        tree = _parse(src)
    except SyntaxError:
        return None
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            out, stack = [], list(n.body)
            while stack:
                s = stack.pop()
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    continue
                if isinstance(s, ast.Return):
                    out.append(ast.unparse(s))
                for f in ("body", "orelse", "finalbody", "handlers"):
                    stack.extend(getattr(s, f, None) or [])
            return out
    return []


def load_home(home_src):
    """The home module EXECUTED from its source (pure — only `copy` at import; line_class is imported lazily and
    never reached here) so the lock can run its shapers, not only read them."""
    ns = {"__name__": "payout_audience_under_lock"}
    exec(compile(home_src, HOME, "exec"), ns)
    return ns


def tuple_values(src, name):
    m = re.search(r"^%s\s*=\s*\(([\s\S]*?)\)\s*$" % re.escape(name), src, re.M)
    return re.findall(r'"([^"]+)"', m.group(1)) if m else []


def scan(be, fe, rbac=None, fe_all=None):
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
        for fn in ("is_paid_line", "employee_explain", "employee_rep_row", "employee_drill", "rep_incentive_explain",
                   "rep_incentive_row", "rep_incentive_drill", "rep_incentive_line", "carrier_fields_in"):
            if re.search(r"^def\s+%s\s*\(" % fn, code, re.M):
                v.append(("a", rel, "a second " + fn))
        if re.search(r"^EMPLOYEE_\w+_FIELDS\s*=", code, re.M):
            v.append(("a", rel, "a second employee allow-list"))
        if re.search(r"^def\s+_payout_audience\s*\(", code, re.M) and rel != ROUTER:
            v.append(("a", rel, "a second _payout_audience"))
    carrier = set(tuple_values(home_src, "KNOWN_CARRIER_FIELDS"))
    reasons = set(tuple_values(home_src, "MANAGER_REASON_FIELDS"))
    if not {"ext_price", "gp", "implied_cost", "ma_matches", "ma_says_paid", "mi_ref", "boost_commission",
            "boost_reimbursement", "buckets"} <= carrier:
        v.append(("a", HOME, "KNOWN_CARRIER_FIELDS no longer names the carrier fields"))
    if not {"suppressed", "suppressed_reason"} <= reasons or reasons & carrier:
        v.append(("a", HOME, "MANAGER_REASON_FIELDS missing the ⛔ reasons, or overlapping the carrier's money"))
    for name in re.findall(r"^(EMPLOYEE_\w+_FIELDS)\s*=", home_src, re.M):
        leak = carrier & set(tuple_values(home_src, name))
        if leak:
            v.append(("a", HOME, "%s allows carrier field(s) %s" % (name, sorted(leak))))
        if reasons & set(tuple_values(home_src, name)):
            v.append(("a", HOME, "%s allows a ⛔ reason field — an employee sees paid lines only" % name))
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
        if '_require_carrier_view(authorization, org_id, "%s")' % key not in py_code(fn_body(router, fn)):
            v.append(("b", ROUTER, "carrier surface %s no longer refuses a viewer without the carrier permission "
                                   "(key %s)" % (fn, key)))
    # (f) THE registry: every refusal names a registered key; every registered surface is refused somewhere;
    #     every registered page is a NAV entry (or, menu-less, linked only behind payoutRefused — (i)); the nav gates
    #     ask payoutRefused first; /me stamps the payload
    used = set()
    for rel, src in be.items():
        for k in re.findall(r'_require_carrier_view\(authorization, org_id, "([^"]+)"\)', py_code(src)):
            used.add(k)
            if k not in keys:
                v.append(("f", rel, "refuses an UNREGISTERED carrier surface %r" % k))
    for k in keys - used:
        v.append(("f", HOME, "registered surface %r is refused by no handler" % k))
    if not reg or "pay_discrepancy" not in keys:
        v.append(("f", HOME, "MANAGER_ONLY_SURFACES missing or no longer names pay_discrepancy"))
    rb = ts_code(rbac)
    nav_hrefs = set(re.findall(r"href:\s*'([^']+)'", rb))
    for k, pages, menu in registry(home_src, with_menu=True):
        for pg in pages:
            if menu and pg not in nav_hrefs:
                v.append(("f", "rbac.ts", "registered page %s (%s) has no NAV entry to hide" % (pg, k)))
    if not re.search(r"export function payoutRefused\([^)]*\)[^{]*\{[^}]*refused_pages", rb):
        v.append(("f", "rbac.ts", "payoutRefused no longer reads the server's refused_pages"))
    for fn in ("canSeeItem", "canAccessPath", "navBlockReason"):
        body = ts_fn_body(rb, fn)
        i_pr, i_sa = body.find("payoutRefused("), body.find("isSuperAdmin(")
        if i_pr < 0 or (i_sa >= 0 and i_sa < i_pr):
            v.append(("f", "rbac.ts", "%s no longer asks payoutRefused before any bypass" % fn))
    _me = py_code(fn_body(be.get(CORE, ""), "_me_payload"))
    if ("_is_self = _self_scoped(org_id" not in _me or "_pa.viewer_payload(_is_self, _carrier)" not in _me
            or "_carrier = _pa.carrier_view_allowed(" not in _me):
        v.append(("f", CORE, "/me no longer stamps payout_audience.viewer_payload (the self scope + THE carrier permission)"))
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
    v.extend(carrier_violations(be, fe, rbac, fe_all))
    return v


def carrier_violations(be, fe, rbac, fe_all=None):
    """(i) carrier commission is for management's eyes only (owner 2026-09-28, index §6m). See the module header."""
    v = []
    fe_all = FE_ALL if fe_all is None else fe_all
    home_src = be.get(HOME, "")
    router = be.get(ROUTER, "")
    carrier = set(tuple_values(home_src, "KNOWN_CARRIER_FIELDS"))
    # ── i1 · no Rep Incentive surface can return / render a carrier field, for ANY audience ──────────────────
    for fn, allowed in REP_INCENTIVE_RETURNS.items():
        rets = top_returns(router, fn)
        if rets is None:
            v.append(("i1", ROUTER, "router does not parse"))
            continue
        extra = [r for r in rets if r not in allowed]
        if not rets or extra:
            v.append(("i1", ROUTER, "%s returns something other than THE Rep Incentive shaping: %s" % (fn, extra or "none")))
    ce = py_code(fn_body(router, "commission_explain"))
    i_req = ce.find('_require_carrier_view(authorization, org_id, "commission_explain_carrier")')
    i_raw = ce.find('return {**_res, "audience": "manager", "carrier_view": True}')
    if not re.search(r'carrier = str\(view or ""\)\.strip\(\)\.lower\(\) == "carrier"', ce) or i_req < 0 \
            or (i_raw >= 0 and i_raw < i_req) or not re.search(r"if carrier:\s*\n\s*return \{\*\*_res", ce):
        v.append(("i1", ROUTER, "commission_explain's raw drill-down is no longer behind view=carrier + the carrier refusal"))
    sd = py_code(fn_body(router, "_statement_doc"))
    if "_statement_buckets(" in sd or not re.search(r"build_statement\(explain, buckets=None", sd):
        v.append(("i1", ROUTER, "_statement_doc reads / passes the commission-ledger buckets (the carrier's statement)"))
    cst = be.get("modules/commcalc/commission_statement.py", "")
    sli = py_code(fn_body(cst, "_sale_line_items"))
    hit = sorted(k for k in carrier if re.search(r"['\"]%s['\"]" % re.escape(k), sli))
    if not sli or hit:
        v.append(("i1", "commission_statement.py", "a statement sale row carries carrier field(s) %s" % hit))
    for name in ("_CSV_EMPLOYEE_COLS", "_CSV_MANAGER_COLS"):
        cols = set(tuple_values(cst, name))
        if not cols or cols & carrier:
            v.append(("i1", "commission_statement.py", "%s exports carrier column(s) %s" % (name, sorted(cols & carrier))))
    # the page itself renders nothing it was not served: no "MA says paid", no carrier view on the Rep Incentive page
    rp = ts_code(fe.get("reports/page.tsx", ""))
    for bad in ("ma_says_paid", "view=carrier", "carrier_view", "servedCarrierView", "carrier={"):
        if bad in rp:
            v.append(("i1", "reports/page.tsx", "the Rep Incentive page reaches for carrier money (%s)" % bad))
    br = ts_code(fe.get("_lib/PlanLineBreakdown.tsx", ""))
    if "const showCarrier = carrier === true && !employee" not in br:
        v.append(("i1", "_lib/PlanLineBreakdown.tsx", "Price / GP no longer decided by the SERVED carrier view"))
    for ln in br.split("\n"):
        if re.search(r"\bl\.(ext_price|gp)\b", ln) and "showCarrier &&" not in ln:
            v.append(("i1", "_lib/PlanLineBreakdown.tsx", "renders Price / GP outside the carrier view: " + ln.strip()[:80]))
    # RUN the home's shapers on a payload carrying every carrier field at every level, for every audience
    try:
        ns = load_home(home_src)
        kc = list(ns["KNOWN_CARRIER_FIELDS"])
        poison = {k: 150.0 for k in kc}
        ex = dict(poison, plan_component=dict(poison, rules=[dict(poison, lines=[
            dict(poison, amount=10.0, trans_id="T1"), dict(poison, amount=0.0, suppressed=True, suppressed_reason="dup")])]),
            multimonth_component=dict(poison, devices=[dict(poison, installments=[dict(poison, amount=7.5)])]),
            reconciliation={"total_payout": 10.0})
        for aud in ("employee", "manager", "", "carrier", "admin"):
            out = ns["rep_incentive_explain"](ex, aud)
            if ns["carrier_fields_in"](out):
                v.append(("i1", HOME, "rep_incentive_explain(%r) lets through %s" % (aud, ns["carrier_fields_in"](out)[:4])))
            dr = ns["rep_incentive_drill"]({"premium": dict(poison, items=[dict(poison, trans_id="T1")])}, aud)
            if ns["disallowed_fields"](dr, "drill"):
                v.append(("i1", HOME, "rep_incentive_drill(%r) lets through %s" % (aud, ns["disallowed_fields"](dr, "drill")[:4])))
        if ns["carrier_fields_in"](ns["rep_incentive_row"](dict(poison, total_payout=10.0))):
            v.append(("i1", HOME, "rep_incentive_row lets a carrier field through"))
        if not ns["carrier_fields_in"](ex):
            v.append(("i1", HOME, "carrier_fields_in no longer SEES a carrier field (the proof would be blind)"))
    except Exception as e:
        v.append(("i1", HOME, "the home's shapers could not be run: %s" % e))
    # ── i2 · no carrier surface reachable without THE permission ────────────────────────────────────────
    rq = py_code(fn_body(router, "_require_carrier_view"))
    if "_can_view_carrier_commission(authorization, org_id)" not in rq or "HTTPException(403" not in rq \
            or "_pa.refusal_message(key)" not in rq:
        v.append(("i2", ROUTER, "_require_carrier_view no longer refuses 403 through THE permission"))
    cv = py_code(fn_body(router, "_can_view_carrier_commission"))
    if "_pa.carrier_view_allowed(caller, caller_is_self=" not in cv or "except Exception:\n        return False" not in cv:
        v.append(("i2", ROUTER, "_can_view_carrier_commission no longer decides through carrier_view_allowed, failing closed"))
    for rel, src in sorted(be.items()):
        if re.search(r"^def\s+_refuse_employee_audience\s*\(", py_code(src), re.M):
            v.append(("i2", rel, "the employee-only refusal came back (a store manager would see carrier money)"))
    vp = py_code(fn_body(home_src, "viewer_payload"))
    if "if allowed else list(MANAGER_ONLY_PAGES)" not in vp or 'aud != "employee"' not in vp:
        v.append(("i2", HOME, "viewer_payload no longer refuses every carrier page to a viewer without the permission"))
    for k, pages, menu in registry(home_src, with_menu=True):
        if menu:
            continue
        for pg in pages:
            own = pg.split("/commcalc/", 1)[-1] + "/"
            for rel, src in sorted(fe_all.items()):
                if ("app/(platform)/commcalc/" + own) in rel:
                    continue
                code = ts_code(src)
                # any string literal naming the page (an href, or a constant an href reads) — the file must ask the
                # nav's own gate, the one /me feeds (a link shown to a viewer the server refuses is the defect)
                if re.search(r"""['"`]%s(?=[?'"`/#]|$)""" % re.escape(pg), code, re.M) and "payoutRefused(" not in code:
                    v.append(("i2", rel, "links to the carrier page %s without asking payoutRefused" % pg))
    # ── i3 · the permission's role list is read in ONE home ─────────────────────────────────────────────
    if not re.search(r'^CARRIER_VIEW_GRANT\s*=\s*"%s"' % GRANT_KEY, home_src, re.M):
        v.append(("i3", HOME, "THE grant key is no longer defined in the home"))
    for rel, src in sorted(be.items()):
        if rel == HOME:
            continue
        code = py_code(src)
        if GRANT_KEY in code or "CARRIER_VIEW_GRANT" in code or "CARRIER_VIEW_DEFAULT" in code:
            v.append(("i3", rel, "reads the carrier permission's role list outside its home"))
        n_all = code.count("carrier_view_allowed(")
        n_ok = sum(py_code(fn_body(src, fn)).count("carrier_view_allowed(") for (r2, fn) in ALLOWED_DECIDERS if r2 == rel)
        if n_all > n_ok:
            v.append(("i3", rel, "asks carrier_view_allowed outside the server gate and /me (%d of %d calls)"
                      % (n_all - n_ok, n_all)))
    for rel, src in sorted(fe_all.items()):
        code = ts_code(src)
        if re.search(r"hasDataGrant\([^)]*%s" % GRANT_KEY, code):
            v.append(("i3", rel, "decides the carrier permission on the client (hasDataGrant) instead of /me"))
        if GRANT_KEY in code and not rel.endswith("lib/rbac.ts"):
            v.append(("i3", rel, "names the carrier grant key outside its DATA_GRANTS registration"))
    # ── i4 · no second permission for the same question ─────────────────────────────────────────────────
    for rel, src in sorted(be.items()):
        for m in SECOND_GATE.finditer(py_code(src)):
            if (rel, m.group(1)) not in GATE_FN and m.group(1) not in SECOND_GATE_EXCUSED:
                v.append(("i4", rel, "a second carrier-commission gate: " + m.group(1)))
        if rel != HOME and re.search(r"^CARRIER_\w*GRANT\w*\s*=", py_code(src), re.M):
            v.append(("i4", rel, "a second carrier grant constant"))
    dg = re.search(r"export const DATA_GRANTS[\s\S]*?\n\]", ts_code(rbac))
    for key in re.findall(r"key:\s*'([^']*carrier[^']*)'", dg.group(0) if dg else ""):
        if key != GRANT_KEY and key not in CARRIER_GRANT_KEYS_EXCUSED:
            v.append(("i4", "rbac.ts", "a second carrier grant key: " + key))
    if GRANT_KEY not in (dg.group(0) if dg else ""):
        v.append(("i4", "rbac.ts", "THE carrier grant is not registered in DATA_GRANTS (the Roles editor cannot set it)"))
    for fn in MANAGER_ONLY:
        body = py_code(fn_body(router, fn))
        for g in OTHER_GATES:
            if g in body:
                v.append(("i4", ROUTER, "carrier surface %s ALSO asks %s — two permissions for one question" % (fn, g)))
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
        takes_auth, refusing = _router_facts(be.get(ROUTER, ""))
    except SyntaxError:
        return [("h", ROUTER, "router does not parse")]
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
FE_ALL = walk(os.path.join(os.path.dirname(ROOT), "frontend", "src"), (".ts", ".tsx"))
RBAC_SRC = read(RBAC)
viol = scan(be, fe)
for part, label in (
        ("a", "(a) ONE home: the paid-line predicate, the audience decision, the allow-lists; no carrier field allowed"),
        ("b", "(b) every employee-facing surface goes through the home; every carrier surface refuses a viewer without the permission"),
        ("c", "(c) nobody filters paid lines on their own (backend or frontend); no second paid-line rule"),
        ("d", "(d) payout pages force no audience (the server decides by who is looking); the breakdown renders the served one"),
        ("f", "(f) ONE carrier-surface registry: every refusal registered, every registered page hidden by the nav gates, /me stamps it"),
        ("g", "(g) the paid row names its sale through ONE customer / phone rule; every statement is _statement_doc"),
        ("h", "(h) scheduled / emailed reports carry the CALLER into every commission handler (no manager view by default)"),
        ("i1", "(i1) NO Rep Incentive surface returns or renders a carrier field to ANY audience (handlers, statement, "
               "CSV, page; the home's shapers run on a poisoned payload)"),
        ("i2", "(i2) no carrier surface is reachable without THE permission (server refusal, /me, menu-less links)"),
        ("i3", "(i3) the carrier permission's role list is read in ONE home (carrier_view_allowed), never on the client"),
        ("i4", "(i4) no second permission for the same question (no second gate, grant key or constant; no double gate)")):
    check(label, not [x for x in viol if x[0] == part], [x[1:] for x in viol if x[0] == part])

print("\n  negative controls")


def planted(be_mut=None, fe_mut=None, rbac=None, fe_all_mut=None):
    b2, f2, fa = dict(be), dict(fe), dict(FE_ALL)
    b2.update(be_mut or {})
    f2.update(fe_mut or {})
    fa.update(fe_all_mut or {})
    return scan(b2, f2, rbac, fa)


v = planted(be_mut={"modules/commcalc/shadow.py": "def is_paid_line(line):\n    return True\n"})
check("(e) a second is_paid_line → RED", any(x[0] == "a" and "shadow" in x[1] for x in v))
v = planted(be_mut={HOME: be[HOME].replace('EMPLOYEE_LINE_FIELDS = ("date",', 'EMPLOYEE_LINE_FIELDS = ("gp", "date",')})
check("(e) an employee allow-list that lets GP through → RED", any(x[0] == "a" and "allows carrier" in x[2] for x in v))
v = planted(be_mut={ROUTER: be[ROUTER].replace("return _pa.rep_incentive_explain(_res, _aud)", "return _res")})
check("(e) /commission-explain returning the raw drill (Price / GP) on the Rep Incentive view → RED",
      any(x[0] == "b" for x in v) and any(x[0] == "i1" and "commission_explain" in x[2] for x in v))
v = planted(be_mut={"modules/core/router.py": be["modules/core/router.py"].replace("_pa.employee_rep_row(myc)", "myc")})
check("(e) the employee dashboard handing back the raw commission row → RED", any(x[0] == "b" and "core" in x[1] for x in v))
v = planted(be_mut={ROUTER: be[ROUTER].replace('_require_carrier_view(authorization, org_id, "pay_discrepancy")', "")})
check("(e) a carrier report open to a viewer without the permission → RED",
      any(x[0] == "b" and "get_discrepancy_results" in x[2] for x in v))
v = planted(be_mut={"modules/commcalc/rep_view.py":
                    "def paid(lines):\n    return [l for l in lines if not l.get('suppressed') and l.get('amount')]\n"})
check("(e) a backend surface filtering paid lines on its own → RED", any(x[0] == "c" and "rep_view" in x[1] for x in v))
v = planted(fe_mut={"_lib/employeeLines.ts": "export function isPaidLine(l: any) { return !l.suppressed && l.amount > 0 }"})
check("(e) a second paid-line rule on the frontend → RED", any(x[0] == "c" and "employeeLines" in x[1] for x in v))
v = planted(fe_mut={"reports/page.tsx": fe["reports/page.tsx"].replace(
    "/api/v1/commcalc/commissions-range?period_from=", "/api/v1/commcalc/commissions-range?audience=employee&period_from=")})
check("(e) the Rep Incentive page forcing the employee audience on a manager again → RED",
      any(x[0] == "d" and "reports" in x[1] for x in v))
v = planted(be_mut={ROUTER: be[ROUTER].replace('_require_carrier_view(authorization, org_id, "carrier_vs_pay")',
                                               '_require_carrier_view(authorization, org_id, "carrier_vs_pay_v2")')})
check("(e) a refusal naming an unregistered surface (the nav could not hide it) → RED",
      any(x[0] == "f" and "UNREGISTERED" in x[2] for x in v))
v = planted(rbac=RBAC_SRC.replace("  if (payoutRefused(perms, item.href)) return false", "", 1))
check("(e) the menu gate no longer hiding a server-refused page from a rep → RED",
      any(x[0] == "f" and "canSeeItem" in x[2] for x in v))
v = planted(rbac=RBAC_SRC.replace("href: '/commcalc/discrepancy'", "href: '/commcalc/discrepancy-v2'"))
check("(e) a registered page with no NAV entry to hide → RED", any(x[0] == "f" and "no NAV entry" in x[2] for x in v))
v = planted(be_mut={CORE: be[CORE].replace("_pa.viewer_payload(_is_self, _carrier)", "_pa.viewer_payload(_is_self)")})
check("(e) /me no longer telling the nav what the server refuses (the carrier permission dropped) → RED",
      any(x[0] == "f" and "/me" in x[2] for x in v))
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

print("\n  negative controls — (i) carrier commission is for management's eyes only (owner 2026-09-28)")
CST = "modules/commcalc/commission_statement.py"
PLB = "_lib/PlanLineBreakdown.tsx"


def red(v, part, needle=""):
    return any(x[0] == part and needle in (x[1] + " " + x[2]) for x in v)


# i1 — a Rep Incentive surface handing a carrier field to some audience
v = planted(be_mut={ROUTER: be[ROUTER].replace("    return [_pa.rep_incentive_row(r) for r in rows]",
                                               "    return rows if _aud == 'manager' else [_pa.rep_incentive_row(r) for r in rows]")})
check("(e) i1 the Rep Incentive rows handing a manager the raw row (boost_commission) again → RED", red(v, "i1", "get_commissions"))
v = planted(be_mut={ROUTER: be[ROUTER].replace("    return _pa.rep_incentive_drill(out, _aud)",
                                               "    if _aud == 'manager':\n        return out\n    return _pa.rep_incentive_drill(out, _aud)")})
check("(e) i1 the Boost drill handing a manager Price / GP again → RED", red(v, "i1", "commission_drill"))
v = planted(be_mut={ROUTER: be[ROUTER].replace("    explain = _pa.rep_incentive_explain(explain, aud)\n",
                                               "    explain = _pa.rep_incentive_explain(explain, aud)\n"
                                               "    buckets = _statement_buckets(client, org_id, period, rep)\n")})
check("(e) i1 the statement reading the commission-ledger buckets again → RED", red(v, "i1", "_statement_doc"))
v = planted(be_mut={CST: be[CST].replace('            if not employee:\n                row["product"] = _s(ln.get("product")) or None\n',
                                         '            if not employee:\n                row["product"] = _s(ln.get("product")) or None\n'
                                         '                row["ext_price"] = ln.get("ext_price")\n')})
check("(e) i1 a manager statement row carrying Price again → RED", red(v, "i1", "sale row"))
v = planted(be_mut={CST: be[CST].replace('"status", "amount")', '"status", "ext_price", "gp", "amount")', 1)})
check("(e) i1 the statement CSV exporting Price / GP again → RED", red(v, "i1", "_CSV_MANAGER_COLS"))
v = planted(be_mut={HOME: be[HOME].replace(
    '    allow = EMPLOYEE_LINE_FIELDS + (() if audience == "employee" else MANAGER_REASON_FIELDS)',
    '    allow = EMPLOYEE_LINE_FIELDS + (() if audience == "employee" else MANAGER_REASON_FIELDS + ("ext_price", "gp"))')})
check("(e) i1 the home's manager line shaping letting Price / GP through (caught by RUNNING it) → RED",
      red(v, "i1", "rep_incentive_explain('manager')"))
v = planted(fe_mut={"reports/page.tsx": fe["reports/page.tsx"].replace(
    "'Status / hold reason', 'Paid $'", "'Status / hold reason', 'MA says paid', 'Paid $'").replace(
    "mrc_at_pay: i.mrc_at_pay, paid:", "mrc_at_pay: i.mrc_at_pay, ma_says_paid: d.ma_says_paid, paid:")})
check("(e) i1 the Rep Incentive multi-month table showing 'MA says paid' again → RED", red(v, "i1", "ma_says_paid"))
v = planted(fe_mut={PLB: fe[PLB].replace("{showCarrier && <td style={{ ...td, textAlign: 'right' }}>{fmt(l.ext_price)}</td>}",
                                         "{!employee && <td style={{ ...td, textAlign: 'right' }}>{fmt(l.ext_price)}</td>}")})
check("(e) i1 the breakdown rendering Price for every manager again (the #309 §6j shape) → RED", red(v, "i1", "outside the carrier view"))
# i2 — a carrier surface reachable without the permission
v = planted(be_mut={ROUTER: be[ROUTER].replace("    if not _can_view_carrier_commission(authorization, org_id):\n        raise",
                                               "    if _payout_audience(authorization, org_id, 'manager') == 'employee':\n        raise")})
check("(e) i2 the refusal going back to employee-only (a store manager sees carrier money) → RED",
      red(v, "i2", "_require_carrier_view"))
v = planted(be_mut={"modules/commcalc/legacy_gate.py": "def _refuse_employee_audience(authorization, org_id, key):\n    pass\n"})
check("(e) i2 the old employee-only refusal defined again → RED", red(v, "i2", "legacy_gate"))
v = planted(be_mut={HOME: be[HOME].replace('"refused_pages": [] if allowed else list(MANAGER_ONLY_PAGES)',
                                           '"refused_pages": list(MANAGER_ONLY_PAGES) if aud == "employee" else []')})
check("(e) i2 /me's payload hiding carrier pages from reps only (store managers offered them) → RED", red(v, "i2", "viewer_payload"))
_DASH = "app/(platform)/commcalc/page.tsx"
v = planted(fe_all_mut={_DASH: FE_ALL[_DASH].replace("payoutRefused", "canSeeReport")})
check("(e) i2 a link to the menu-less carrier diagnostic that does not ask payoutRefused → RED", red(v, "i2", _DASH))
v = planted(be_mut={ROUTER: be[ROUTER].replace('    if carrier:\n        _require_carrier_view(authorization, org_id, "commission_explain_carrier")\n',
                                               '    if carrier:\n        pass\n')})
check("(e) i2 commission-explain's carrier view served without the refusal → RED",
      red(v, "i1", "commission_explain") and red(v, "b", "commission_explain"))
# i3 — the role list read outside its one home
v = planted(be_mut={ROUTER: be[ROUTER] + "\n\ndef _mgr_sees_carrier(caller):\n    return bool(((caller or {}).get('perms') or {}).get('data', {}).get('carrier_commission_view'))\n"})
check("(e) i3 a second reader of the carrier grant in the router → RED", red(v, "i3", ROUTER))
v = planted(fe_all_mut={"lib/carrierGate.ts": "export const canSeeCarrier = (p: any) => hasDataGrant(p, 'carrier_commission_view')\n"})
check("(e) i3 the client deciding the carrier permission with hasDataGrant → RED", red(v, "i3", "carrierGate"))
v = planted(be_mut={"modules/commcalc/rep_view.py": "from app.modules.commcalc import payout_audience as _pa\n"
                    "def show(c):\n    return _pa.carrier_view_allowed(c)\n"})
check("(e) i3 carrier_view_allowed asked outside the server gate and /me → RED", red(v, "i3", "rep_view"))
# i4 — a second permission for the same question
v = planted(be_mut={"modules/commcalc/rep_view.py": "def _can_view_carrier_money(authorization, org_id):\n    return True\n"})
check("(e) i4 a second carrier-commission gate function → RED", red(v, "i4", "_can_view_carrier_money"))
v = planted(rbac=RBAC_SRC.replace("  { key: 'device_commission',",
                                  "  { key: 'carrier_money_view', label: 'Carrier money' },\n  { key: 'device_commission',", 1))
check("(e) i4 a second carrier grant key in DATA_GRANTS → RED", red(v, "i4", "carrier_money_view"))
v = planted(be_mut={ROUTER: be[ROUTER].replace('    _require_carrier_view(authorization, org_id, "carrier_vs_pay")\n',
                                               '    _require_carrier_view(authorization, org_id, "carrier_vs_pay")\n'
                                               '    _require_carrier_residual(authorization, org_id)\n')})
check("(e) i4 a carrier surface asking a SECOND permission as well → RED", red(v, "i4", "carrier_vs_pay_report"))
v = planted()
check("(e) the unmodified tree is GREEN", not v, v[:6])

print("\n%d passed, %d failed" % (P, F))
if F:
    sys.exit(1)
print("OK — one audience decision, one paid-line predicate, one allow-list, one carrier-surface registry, one carrier "
      "permission; no Rep Incentive surface carries carrier commission for anyone, and no carrier surface is offered to "
      "a viewer the server refuses.")
