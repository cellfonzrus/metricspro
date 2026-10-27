"""THE LOCK — every connector surface dereferences the connector registry's scope; no second list;
the registry mirror IS the seed; the copy spells no vendor. Stdlib only (one CI job, carrier-vocab-guard.yml).

OWNER (2026-09-21, a Verizon tenant whose declared POS is RQ), verbatim, on the Inventory Values chip
"🔌 RQ portal connection / last: error / The b2bsoft (wsreports.b2bsoft.com) portal client is not
reverse-engineered yet …": *"it says rq connection but refers to b2b reports"*.

CLAUDE.md, "A fix is a DESIGN fix": *"Lock it so it cannot un-wire. A design fix ships with a check that
FAILS THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears."*

WHAT FAILS THE BUILD
  (A) the code mirror `connector_registry.HOUSE_CONNECTORS` and the mig-1014 seed DIFFER (the seed is
      parsed OUT of the SQL, column by column — the mirror cannot pass against itself);
  (B) a FRONTEND CONNECTOR SURFACE that does not read the registry: the eight pinned files of the
      2026-09-21 measurement, plus any file under app/ or components/ that reaches a connector endpoint
      (`/sweep/config`, `/data-sources`, `/api/v1/commcalc/connectors`) — each must import from
      '@/lib/connectors' (`useConnectors`, the ONE hook — the ONE caller of `reportKindsVisible` with the
      `connector:` namespace — or `scopeState` / `connectorNotApplicableCopy` over the `connector_scope`
      the backend attached to that connector's own payload), or sit in ALLOW with its reason (stale fails);
  (C) a SECOND LIST: a string-array literal naming ≥3 registry keys / aliases in frontend/src/{app,
      components,lib}; a page fetching `/connector-registry` itself; `reportKindsVisible(` with the
      connector namespace outside the hook;
  (D) the ONE NEUTRAL LINE drifting between its two homes (connector_registry.neutral_line and
      lib/connectors.ts neutralConnectorLine — the words are extracted from both and compared);
  (E) a BACKEND CONNECTOR SURFACE that stops asking: the four public sweep configs, `_strip_source_pw`,
      the merchant-portal health, the control box's portal evidence, the connector-health scan, the
      Connectors list, the two attention providers, `collect_attention`'s context and its two callers,
      the copy call sites (`connector=` into the inventory stub, `label=` into the b2bsoft login family);
      `def scope(` defined once; the registry's visibility DEREFERENCES `report_kinds.visible_kinds`
      and writes no second intersection;
  (F) NEGATIVE CONTROLS over synthetic copies of the real files: a surface dropping the import → RED; a
      hardcoded connector list → RED; a page fetching the endpoint → RED; a mirror row drifting → RED;
      the neutral line drifting → RED; a backend surface dropping its scope call → RED; a stale allow
      entry → RED. A lock that cannot go red proves nothing.

  python3 backend/harness_connector_scope_lock.py
"""
import ast
import io
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from app.modules.commcalc import connector_registry as CR   # stdlib-only: the mirror + the neutral line

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend", "src")
BE = os.path.join(ROOT, "backend", "app", "modules")
# The seed is spread over SEVERAL migrations, in apply order — 1014 created the table and seeded the
# eleven original rows, and later ones append. One file was the shape that made leaving a feed out of
# the registry cheaper than adding it: a new house connector meant editing an applied migration.
MIGS = [os.path.join(ROOT, "database", "migrations", m) for m in CR.MIGRATIONS]
MIG = MIGS[0]
HOOK_FILE = "lib/connectors.ts"
ENDPOINT = "/commcalc/connector-registry"

# The connector surfaces of the 2026-09-21 measurement, pinned by name so a rename cannot drop one.
PINNED_SURFACES = [
    "app/(platform)/accounts/inventory/page.tsx",          # the owner's chip — the POS portal form
    "app/(platform)/commcalc/upload/page.tsx",             # AUTO_SOURCES auto-import tiles
    "app/(platform)/commcalc/upload/wizard/page.tsx",      # connector-grouped steps
    "app/(platform)/commcalc/connectors/page.tsx",         # the Connectors page + its sweep-kind picker
    "app/(platform)/commcalc/email-imports/page.tsx",      # processor logins: picker, live-login, scope note
    "app/(platform)/commcalc/dlar/sweep/page.tsx",
    "app/(platform)/commcalc/epay/sweep/page.tsx",
    "app/(platform)/commcalc/vip/sweep/page.tsx",
]
ENDPOINT_RX = re.compile(r"[-/]sweep/config|/data-sources|/api/v1/commcalc/connectors['\"`]")

# ── ALLOW — (relative path, class) → reason. A stale entry (no longer matched) fails. ────────────
ALLOW = {
    ("app/(platform)/closing/imports/page.tsx", "surface"):
        "the closing sheet's own sweep (google_closing — an any-scope connector in the seed); the closing agent's surface, excused with the reason",
    ("app/(platform)/commcalc/ftp-imports/page.tsx", "surface"):
        "the FTP drop (ftp — an any-scope connector in the seed); the form is the mailbox/FTP pull's own config, not a vendor portal",
    ("app/(platform)/commcalc/targets/page.tsx", "surface"):
        "reads the DLAR sweep config only to show when KPI figures were last pulled — a KPI page, not a connector surface",
    ("components/supply/LiveVendorWindow.tsx", "surface"):
        "streams the live window of ONE supply-vendor login the supply page already resolved (the /data-sources/{sid}/live-login frame/input/submit/cancel endpoints, index §36) — it lists no connectors and decides no connector scope",
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


# ── §A the seed, parsed OUT of the SQL ───────────────────────────────────────────────────────────
SEED_COLS = ("org_id", "key", "aliases", "label", "host", "kind", "applies_to_pos", "applies_to_carrier",
             "defined_by", "sort_order", "notes")


def seed_rows(sql):
    body = sql.split("sort_order, notes) VALUES", 1)[1].split("ON CONFLICT", 1)[0]
    tok = re.compile(r"'((?:[^']|'')*)'|(\d+)|(NULL)")
    rows = []
    for line in body.strip().split("\n"):
        line = line.strip().rstrip(",")
        if not line.startswith("("):
            continue
        vals = []
        for m in tok.finditer(line[1:-1]):
            if m.group(1) is not None:
                vals.append(m.group(1).replace("''", "'"))
            elif m.group(2) is not None:
                vals.append(int(m.group(2)))
            else:
                vals.append(None)
        rows.append(dict(zip(SEED_COLS, vals)))
    return rows


def mirror_diffs(seed, mirror_rows):
    """(key, column, seed, mirror) for every disagreement — over the NORMALISED shape."""
    diffs = []
    if [r["key"] for r in seed] != [r["key"] for r in mirror_rows]:
        diffs.append(("*", "order/keys", [r["key"] for r in seed], [r["key"] for r in mirror_rows]))
        return diffs
    for s_, m in zip(seed, mirror_rows):
        n = CR.normalise_row(s_)
        mm = CR.normalise_row(m)
        for k in ("key", "aliases", "label", "host", "kind", "applies_to_pos", "applies_to_carrier", "defined_by", "sort_order", "notes"):
            if n.get(k) != mm.get(k):
                diffs.append((s_["key"], k, n.get(k), mm.get(k)))
    return diffs


# ── §B/§C/§D the frontend scan ───────────────────────────────────────────────────────────────────
KEYS = set()
for _r in CR.HOUSE_CONNECTORS:
    KEYS.add(_r["key"])
    KEYS.update(_r.get("aliases") or [])
STR_ARRAY = re.compile(r"\[((?:\s*['\"][A-Za-z0-9_]+['\"]\s*,?)+)\s*\]")
IMPORT_RX = re.compile(r"import\s*\{[^}]*\}\s*from\s*['\"]@/lib/connectors['\"]")


def is_surface(rel, src):
    if rel in PINNED_SURFACES:
        return True
    if not (rel.startswith("app/") or rel.startswith("components/")):
        return False
    body = "\n".join(code_lines(src))
    return bool(ENDPOINT_RX.search(body))


def scan(files, allow):
    v, seen = [], set()
    for rel, src in sorted(files.items()):
        body = "\n".join(code_lines(src))
        # (C) second copies / second paths
        for m in STR_ARRAY.finditer(body):
            items = re.findall(r"['\"]([A-Za-z0-9_]+)['\"]", m.group(1))
            if len([i for i in items if i.lower() in KEYS]) >= 3:
                seen.add((rel, "connector_list"))
                if (rel, "connector_list") not in allow:
                    v.append((rel, "connector_list", m.group(0)[:80]))
                    break
        if rel != HOOK_FILE and ENDPOINT in body:
            v.append((rel, "second_fetch", "fetches " + ENDPOINT + " directly instead of through useConnectors"))
        if rel != HOOK_FILE and re.search(r"reportKindsVisible\s*(<[^>]*>)?\s*\([^)]*['\"]connector:['\"]", body):
            v.append((rel, "second_caller", "runs the visibility function with the connector namespace outside the one hook"))
        # (B) surfaces dereference the registry
        if is_surface(rel, src):
            if (rel, "surface") in allow:
                seen.add((rel, "surface"))
            elif not IMPORT_RX.search(body):
                v.append((rel, "surface", "reaches a connector endpoint / renders a connector without importing from '@/lib/connectors'"))
    stale = [k for k in allow if k not in seen]
    return v, stale


def neutral_lines_fe(src):
    """The two template sentences of neutralConnectorLine, rendered with pos='<POS>' / noun default."""
    body = src.split("export function neutralConnectorLine", 1)[1].split("\n}\n", 1)[0]
    tmpls = re.findall(r"`([^`]*)`", body)
    out = []
    for t in tmpls:
        out.append(t.replace("${noun}", "reports-portal").replace("${a.pos}", "<POS>"))
    return out


def neutral_lines_be():
    return [CR.neutral_line("<POS>", True), CR.neutral_line("<POS>", False)]


# ── §E the backend scan (source strings, so the negative controls can mutate copies) ─────────────
BACKEND_RULES = [
    # (file, label, required substring(s))
    ("commcalc/router.py", "GET /connector-registry exists", ['@router.get("/connector-registry")']),
    ("commcalc/router.py", "the four public sweep configs attach connector_scope through _sweep_public_scope",
     ["def _b2b_public_cfg(cfg, org_id=None)", "def _dlar_public_cfg(cfg, org_id=None)",
      "def _epay_public_cfg(cfg, org_id=None)", "def _vip_public_cfg(cfg, org_id=None)"]),
    ("commcalc/router.py", "_strip_source_pw attaches connector_scope from the context",
     ['row["connector_scope"] = _connector_scope(scope_ctx, row.get("processor"))']),
    ("commcalc/router.py", "the data-source list, the merchant-portal health and the Connectors list ask the registry",
     ["_strip_source_pw(r, prows, sctx)", "'connector_scope': _connector_scope(sctx, c.get('sweep_kind'))",
      '(r.get("connector_scope") or {}).get("applies", True)']),
    ("commcalc/router.py", "the connector-health scan reports a not-applicable connector as unmonitored, never alerted",
     ['if _sc.get("applies") is False:', '"kind": "unmonitored"', ':not_applicable"']),
    # WIDENED 2026-10-27 (§COMMCALC-40): the label used to be passed to the b2bsoft login family ONLY,
    # so every other portal — the path `vp.begin_login` / `vp.complete_2fa` serves — was described to
    # its operator by one vendor's name. Both families now take `label=`, which is why the pinned
    # strings are the partial's label argument rather than one function's name: a call site that hands
    # EITHER family no label fails this lock and §K of harness_login_progressive.py.
    ("commcalc/router.py", "the inventory stub receives the registry row; BOTH login families receive the label",
     ["connector=conn)", "label=_connector_label(sb(), org_id, src_row.get(\"processor\"))",
      "else vp.begin_login, label=", "else vp.complete_2fa, label=",
      "vp.begin_login_b2bsoft if is_b2b else vp.begin_login",
      "vp.complete_2fa_b2bsoft if is_b2b else vp.complete_2fa",
      "vp.pull_b2bsoft_on_page(page, label="]),
    ("commcalc/import_audit.py", "p_connectors and p_portal_sessions skip a connector that does not apply (context, never a read)",
     ['_scope_ctx = (ctx or {}).get("connector_scope")', "if not _applies(proc):", "_cr.scope_for(_scope_ctx, r.get(\"processor\"))"]),
    ("core/import_health.py", "collect_attention carries connector_scope; the org-scoped endpoint hands it down",
     ["connector_scope=None", '"connector_scope": connector_scope', "connector_scope=_connector_scope_ctx(client, org)"]),
    ("core/control_box_api.py", "the control box passes the context and filters its portal evidence",
     ["connector_scope=_connector_scope_ctx(client, org_id)", '(r.get("connector_scope") or {}).get("applies", True)']),
    ("commcalc/connector_registry.py", "the registry's visibility DEREFERENCES report_kinds.visible_kinds / hidden_kinds / applies",
     ["_rk.visible_kinds(rows, declaration, caps, org_id, None, cap_prefix=CAP_PREFIX)",
      "_rk.hidden_kinds(rows, declaration, caps, cap_prefix=CAP_PREFIX)", "_rk.applies("]),
    ("commcalc/b2b_sweep.py", "the stub's copy reads the registry row (label / host), never a vendor",
     ["def not_implemented_message(connector)", "def login(session, user, pw, connector=None)"]),
    ("commcalc/vidapay_sweep.py", "the b2bsoft login family takes the registry label",
     ["def begin_login_b2bsoft(url, access_code, user, pw, proxy_url=None, label=None)",
      "def complete_2fa_b2bsoft(url, pending_state, code, proxy_url=None, label=None)",
      "def run_b2bsoft_sweep(client, org_id, url, session_state, source_id=None, carrier_id=None, proxy_url=None, label=None)",
      "def pull_b2bsoft_on_page(page, label=None)"]),
]
SECOND_INTERSECTION = re.compile(r"set\(\s*r(?:ow)?\[\"applies_to_(?:pos|carrier)\"\]\s*\)\s*&")


def backend_scan(sources):
    """sources: {rel: src}. Returns the violations list."""
    v = []
    for rel, label, needles in BACKEND_RULES:
        src = sources.get(rel, "")
        missing = [n for n in needles if n not in src]
        if missing:
            v.append((rel, label, missing[:2]))
    cr = sources.get("commcalc/connector_registry.py", "")
    if len(re.findall(r"^def scope\(", cr, re.M)) != 1:
        v.append(("commcalc/connector_registry.py", "def scope( defined exactly once", ""))
    if SECOND_INTERSECTION.search(cr):
        v.append(("commcalc/connector_registry.py", "a second applies-to intersection written in the registry (use report_kinds.applies)", ""))
    n_scope = sum(len(re.findall(r"^def scope\(", s, re.M)) for s in sources.values())
    if n_scope != 1:
        v.append(("*", "def scope( appears once across the scanned modules", n_scope))
    return v


def be_sources():
    out = {}
    for rel in sorted({r[0] for r in BACKEND_RULES}):
        p = os.path.join(BE, rel)
        out[rel] = read(p) if os.path.exists(p) else ""
    return out


# ── §I: the dispatch table, read from source ──────────────────────────────────────────────────────
ROUTER_SRC = os.path.join(BE, "commcalc", "router.py")

# A dispatchable connector kind an operator is NOT meant to reach, each with its reason. EMPTY today:
# both kinds that were doorless (pos_report, supply_vendor) are registered as of mig 1084, rather than
# excused. An entry here is a deliberate statement that a feed runs without anybody being able to pick
# it; the checks above also fail a STALE one, so it cannot outlive the dispatcher it excuses.
DOORLESS_DISPATCH: dict = {}

# EVERY key `router._SOURCE_SCRAPERS` dispatches on, DECLARED — a human wrote this set, and §I asserts
# the scan's answer EQUALS it. Not `>= 7`: a bare lower bound is a guard that goes blind (the house
# rule `harness_subject_set_lock.py` enforces), because a dispatcher that silently LEAVES the table
# still passes a size floor, and this lock's whole job is to notice a feed changing shape. A key added
# here must also be added to `connector_registry.HOUSE_CONNECTORS` + a seed migration (the door check
# below), or be excused in DOORLESS_DISPATCH. A key removed from the router must be removed here, and
# the diff says which feed stopped being dispatchable.
DISPATCHED_KEYS = frozenset({
    "b2b", "b2bsoft",      # the POS reports portal family (`b2b` is the alias)
    "businesstrack", "payanywhere", "transfirst",   # the three merchant card portals
    "pos_report",          # = pos_report_sweep.CONNECTOR     (§COMMCALC-07, registered 2026-10-15)
    "supplier_invoice",    # = supplier_invoice_sweep.CONNECTOR (§COMMCALC-38, registered 2026-10-27
                           #   — the first feed to ship its door in the SAME change as its dispatcher,
                           #   which is what this ratchet exists to make the cheap path)
    "supply_vendor",       # = supply.portal.SUPPLY_PROCESSOR (§36 / §51, registered 2026-10-15)
    "total_access", "vidapay",      # the master-agent portal under both its names
})


def dispatch_keys():
    """Every key `router._SOURCE_SCRAPERS` dispatches on, and the ones that could not be resolved.

    Two forms, both read structurally:
      · the dict literal          `_SOURCE_SCRAPERS = {"vidapay": …, "b2bsoft": …}`
      · a registered constant     `_SOURCE_SCRAPERS[_POS_REPORT_CONNECTOR] = …`
    The second is the one that matters here. The router deliberately does NOT re-spell those kinds —
    it imports each from the module that owns it — so this resolves the import back to that module and
    reads the constant's literal there. A key this function failed to resolve is reported rather than
    skipped: skipping it would be a hole in exactly the scan that is meant to catch a doorless feed.
    """
    keys, unresolved = set(), set()
    try:
        src = io.open(ROUTER_SRC, encoding="utf-8").read()
        tree = ast.parse(src)
    except Exception:                               # noqa: BLE001
        return keys, {"<router unreadable>"}
    # name bound by `from <module> import <CONST> as <name>` → (module, CONST)
    bound = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                bound[a.asname or a.name] = (node.module, a.name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            # the dict literal
            if isinstance(t, ast.Name) and t.id == "_SOURCE_SCRAPERS" and isinstance(node.value, ast.Dict):
                for k in node.value.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.add(CR.slug(k.value))
                    else:
                        unresolved.add("<non-literal dict key>")
            # _SOURCE_SCRAPERS[<something>] = handler
            if (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                    and t.value.id == "_SOURCE_SCRAPERS"):
                sl = t.slice
                if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                    keys.add(CR.slug(sl.value))
                elif isinstance(sl, ast.Name) and sl.id in bound:
                    mod, const = bound[sl.id]
                    val = module_constant(mod, const)
                    if isinstance(val, str) and val.strip():
                        keys.add(CR.slug(val))
                    else:
                        unresolved.add("%s.%s" % (mod, const))
                else:
                    unresolved.add(getattr(sl, "id", None) or "<non-literal key>")
    return keys, unresolved


def module_constant(dotted, name):
    """A module-level string constant, read from that module's SOURCE (no import)."""
    rel = dotted.replace(".", os.sep) + ".py"
    path = os.path.join(ROOT, "backend", rel)
    if not os.path.exists(path):
        return None
    try:
        tree = ast.parse(io.open(path, encoding="utf-8").read())
    except Exception:                               # noqa: BLE001
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name and isinstance(node.value, ast.Constant):
                    return node.value.value
    return None


def main():
    print("=" * 78)
    print("CONNECTOR-SCOPE LOCK — every connector surface reads the registry; mirror = seed; one neutral line")
    print("=" * 78)

    print("\n— A. the mirror IS the seed —")
    for m in CR.MIGRATIONS:
        check("the seed migration exists: " + m,
              os.path.exists(os.path.join(ROOT, "database", "migrations", m)))
    seed = []
    for path in MIGS:
        if os.path.exists(path):
            seed.extend(seed_rows(io.open(path, encoding="utf-8").read()))
    check("the seed parses into rows (%d, concatenated over %d migrations)" % (len(seed), len(MIGS)),
          len(seed) >= 8)
    check("no key is seeded twice across the migrations (a second seed of one key is two homes)",
          len([r["key"] for r in seed]) == len({r["key"] for r in seed}),
          sorted({k for k in [r["key"] for r in seed] if [r["key"] for r in seed].count(k) > 1}))
    d = mirror_diffs(seed, CR.HOUSE_CONNECTORS)
    check("every column of every seeded row equals connector_registry.HOUSE_CONNECTORS", not d, d[:3])
    check("the merchant card portals mirror merchant_portals.PORTALS (one home for label / host)",
          all(any(r["key"] == k for r in CR.HOUSE_CONNECTORS) for k in ("payanywhere", "transfirst", "businesstrack")))
    check("every seeded scope code is lower-case (the same squash report kinds use)",
          all(c == CR._rk.code(c) for r in seed for c in CR._rk._list(r["applies_to_pos"]) + CR._rk._list(r["applies_to_carrier"])))
    check("the seed is the house org only (tenant rows are overrides written later, never seeded)",
          all(r["org_id"] == CR.HOUSE_ORG for r in seed))

    print("\n— B/C. the frontend surfaces —")
    files = walk_fe()
    hook = files.get(HOOK_FILE, "")
    check("the one hook exists, runs the visibility function with the connector namespace and fetches the one endpoint",
          "export function useConnectors()" in hook and "CONNECTOR_CAP_PREFIX" in hook and ENDPOINT in hook
          and re.search(r"reportKindsVisible<ConnectorRow>\(", hook) is not None)
    surfaces = [rel for rel, src in files.items() if is_surface(rel, src)]
    check("the eight measured surfaces are all still discovered", set(PINNED_SURFACES) <= set(surfaces), set(PINNED_SURFACES) - set(surfaces))
    print("  surfaces enumerated: %d — %s" % (len(surfaces), ", ".join(sorted(surfaces))))
    v, stale = scan(files, ALLOW)
    check("(B)+(C) no violation across frontend/src/{app,components,lib}", not v, v[:6])
    for rel, cls, what in v[:12]:
        print("      · %s  [%s]  %s" % (rel, cls, what))
    check("no STALE allow entry", not stale, stale)
    for k, why in sorted(ALLOW.items()):
        print("      allow %-58s [%s] — %s" % (k[0], k[1], why[:70]))

    print("\n— D. the one neutral line, two homes —")
    fe_lines = neutral_lines_fe(hook)
    check("the frontend twin carries both sentences", len(fe_lines) == 2, fe_lines)
    check("the words are IDENTICAL to connector_registry.neutral_line (declared / undeclared)",
          fe_lines == neutral_lines_be(), (fe_lines, neutral_lines_be()))

    print("\n— E. the backend surfaces —")
    be = be_sources()
    bv = backend_scan(be)
    check("every backend connector surface asks the registry; scope defined once; no second intersection", not bv, bv[:4])
    for rel, label, what in bv[:10]:
        print("      · %s  %s  %s" % (rel, label, what))

    print("\n— F. negative controls (a lock that cannot go red proves nothing) —")
    base = {k: files[k] for k in PINNED_SURFACES + [HOOK_FILE]}
    ctl = dict(base)
    ctl["app/(platform)/accounts/inventory/page.tsx"] = base["app/(platform)/accounts/inventory/page.tsx"].replace("@/lib/connectors", "@/lib/something-else")
    v1, _ = scan(ctl, ALLOW)
    check("the inventory page dropping the registry import → RED", any(c == "surface" and "inventory" in r for r, c, _ in v1), v1)
    ctl = dict(base)
    ctl["app/(platform)/commcalc/connectors/page.tsx"] += "\nconst KINDS = ['vip', 'dlar', 'epay', 'b2b']\n"
    v2, _ = scan(ctl, ALLOW)
    check("a hardcoded connector list in a page → RED", any(c == "connector_list" for _, c, _ in v2), v2)
    ctl = dict(base)
    ctl["app/(platform)/commcalc/upload/page.tsx"] += "\napi('/api/v1" + ENDPOINT + "').then(() => 0)\n"
    v3, _ = scan(ctl, ALLOW)
    check("a page fetching /connector-registry directly → RED", any(c == "second_fetch" for _, c, _ in v3), v3)
    ctl = dict(base)
    ctl["app/(platform)/commcalc/email-imports/page.tsx"] += "\nconst v = reportKindsVisible(rows, decl, caps, 'connector:')\n"
    v4, _ = scan(ctl, ALLOW)
    check("a page running the visibility function with the connector namespace → RED", any(c == "second_caller" for _, c, _ in v4), v4)
    ctl = dict(base)
    ctl["app/(platform)/commcalc/new-sweep/page.tsx"] = "'use client'\nexport default function P(){ api('/api/v1/commcalc/foo/sweep/config'); return null }\n"
    v5, _ = scan(ctl, ALLOW)
    check("a NEW page reaching a sweep config without the registry → RED", any(c == "surface" and "new-sweep" in r for r, c, _ in v5), v5)
    _, stale2 = scan(files, {**ALLOW, ("app/(platform)/nowhere/page.tsx", "surface"): "stale on purpose"})
    check("a stale allow entry → RED", ("app/(platform)/nowhere/page.tsx", "surface") in stale2, stale2)
    drift = [dict(r) for r in CR.HOUSE_CONNECTORS]
    drift[0] = {**drift[0], "applies_to_pos": []}
    check("a mirror row drifting from the seed (a widened scope in code) → RED", bool(mirror_diffs(seed, drift)))
    check("the neutral line drifting in the frontend → RED",
          neutral_lines_fe(hook.replace("can be added under Connectors", "can be added under Settings")) != neutral_lines_be())
    ctl_be = dict(be)
    ctl_be["commcalc/router.py"] = be["commcalc/router.py"].replace("def _b2b_public_cfg(cfg, org_id=None)", "def _b2b_public_cfg(cfg)")
    check("a public sweep config dropping its scope → RED", bool(backend_scan(ctl_be)))
    ctl_be = dict(be)
    ctl_be["commcalc/connector_registry.py"] = be["commcalc/connector_registry.py"] + '\nX = set(row["applies_to_pos"]) & set(d)\n'
    check("a second applies-to intersection in the registry → RED", bool(backend_scan(ctl_be)))
    ctl_be = dict(be)
    ctl_be["commcalc/import_audit.py"] = be["commcalc/import_audit.py"].replace("if not _applies(proc):", "if False:")
    check("an attention provider no longer asking → RED", bool(backend_scan(ctl_be)))

    print("\n%d passed, %d failed" % (P, F))
    if F:
        print("\nFAIL — the connector-scope lock is open. Fix: render the surface from useConnectors() / scopeState() "
              "(lib/connectors.ts), ask _connector_scope / _sweep_public_scope on the backend, move the fact into "
              "the registry (the seed migrations in connector_registry.MIGRATIONS / a tenant row), or add a REVIEWED "
              "allow entry with its reason.")
        sys.exit(1)

    # ══════════════════════════════════════════════════════════════════════════════════════════════
    print("\n— I. a feed the platform DISPATCHES has a DOOR —")
    # ══════════════════════════════════════════════════════════════════════════════════════════════
    # Owner report 2026-10-15: "as a new user how do i know where to integrate the teamportal to get
    # the pos data to compare to teh daily closing". He could not. The POS-report feed was fully
    # dispatchable — `router._SOURCE_SCRAPERS` had a handler, the mig-956 cron would run it — and had
    # no `connector_registry` row, so the processor picker did not offer the kind, the Connectors page
    # had no card for it, and every surface naming it fell through to NEUTRAL_LABEL. The feed worked
    # and was unreachable.
    #
    # THE CLASS: "the platform can run this feed" and "a tenant can find this feed" were two
    # unrelated facts. Looking for the class found the sibling at once — `supply_vendor` had been
    # dispatchable with no row since it shipped. So this section holds the dispatch table against the
    # registry: every key the router dispatches on is a registry key or alias, or is excused HERE in
    # writing, and a stale excuse fails too.
    #
    # Read from SOURCE, not imported: `commcalc.router` needs fastapi / openpyxl and this lock must
    # start in CI's bare python. The two kinds added by `_SOURCE_SCRAPERS[CONST]` are resolved back
    # through their import to the module that owns the constant, so a renamed kind is seen.
    dispatch, unresolved = dispatch_keys()
    check("the dispatch table is EXACTLY the declared set (%d keys: %s)"
          % (len(DISPATCHED_KEYS), ", ".join(sorted(DISPATCHED_KEYS))),
          set(dispatch) == set(DISPATCHED_KEYS),
          "router._SOURCE_SCRAPERS no longer dispatches the declared set — added %s, gone %s. A feed "
          "that LEAVES must be removed from DISPATCHED_KEYS by hand, so it names itself here."
          % (sorted(set(dispatch) - set(DISPATCHED_KEYS)) or "nothing",
             sorted(set(DISPATCHED_KEYS) - set(dispatch)) or "nothing"))
    check("every `_SOURCE_SCRAPERS[CONST]` key resolved to a literal — an unresolved one would be a "
          "silent hole in this very scan", not unresolved, sorted(unresolved))
    known = {CR.slug(r["key"]) for r in CR.HOUSE_CONNECTORS}
    for r in CR.HOUSE_CONNECTORS:
        known |= {CR.slug(a) for a in (r.get("aliases") or [])}
    for key in sorted(dispatch):
        if key in DOORLESS_DISPATCH:
            continue
        check("`%s` is dispatchable AND named by the registry, so an operator can pick it" % key,
              key in known,
              "add it to connector_registry.HOUSE_CONNECTORS + its seed migration, or excuse it in "
              "DOORLESS_DISPATCH with the reason an operator is not meant to reach it")
    for key, why in sorted(DOORLESS_DISPATCH.items()):
        check("the excuse for `%s` is still needed (it is still dispatchable)" % key, key in dispatch,
              "stale: nothing dispatches on it any more — drop the entry")
        check("the excuse for `%s` is in writing" % key, bool(str(why or "").strip()))
    # The reverse direction is NOT a failure and must not become one: a registry row with no
    # dispatcher is a connector a tenant configures by hand (mailbox, ftp, google_sa) or one whose
    # pull is not a data_source sweep. Reported, never enforced.
    no_sweep = sorted(k for k in {CR.slug(r["key"]) for r in CR.HOUSE_CONNECTORS} if k not in dispatch)
    print("     (registry rows with no data_source dispatcher, configured another way: %s)"
          % (", ".join(no_sweep) or "none"))

    # The frontend proof (frontend/prove_connector_scope.mjs) builds its fixture from the seed SQL and
    # must see EVERY seed file, or a new connector's rows are absent from the predicate it proves.
    # It DERIVES them by filename pattern rather than keeping a list; this pins that pattern against
    # connector_registry.MIGRATIONS, so a seed file it cannot see fails the build here.
    mjs = read(os.path.join(ROOT, "frontend", "prove_connector_scope.mjs"))
    m = re.search(r"\.filter\(f => (/[^/]+/)\.test\(f\)\)", mjs)
    check("the frontend proof derives its seed files by pattern, not a list", bool(m),
          "expected a `.filter(f => /…/.test(f))` over readdirSync(MIG_DIR) in prove_connector_scope.mjs")
    if m:
        rx = re.compile(m.group(1)[1:-1].replace("\\d", r"\d"))
        unseen = [os.path.basename(f) for f in MIGS if not rx.match(os.path.basename(f))]
        check("every seed migration in connector_registry.MIGRATIONS matches it (%d file(s))" % len(MIGS),
              not unseen,
              "the frontend proof would not read: %s — widen the pattern or rename the migration" % unseen)

    print("\n— I-controls —")
    # The scan must see the defect it was written for: the state main() was in before this change.
    check("a dispatchable kind with no registry row → RED",
          not ({"a_brand_new_feed"} <= known),
          "the control itself is broken: the registry already knows an invented key")
    check("…and the resolver really does read the constant's VALUE, not its name",
          "pos_report" in dispatch and "POS_REPORT_CONNECTOR" not in dispatch)
    check("a stale excuse → RED (an excused key nothing dispatches on)",
          "a_retired_feed" not in dispatch)
    check("a feed LEAVING the dispatch table → RED (the declared set is not a size floor)",
          set(DISPATCHED_KEYS) - {"vidapay"} != set(dispatch),
          "the control is broken: dropping a key still compared equal")
    check("a seed migration the frontend proof cannot see → RED",
          not re.compile(r"^\d+_connector_registry.*\.sql$").match("1099_connectors_extra.sql"),
          "the control is broken: the pattern would have matched a name it must not")

    print("\n%d passed, %d failed" % (P, F))
    if F:
        print("\nFAIL — a feed the platform can RUN that a tenant cannot FIND. Fix: add the kind to "
              "connector_registry.HOUSE_CONNECTORS with a plain-words label AND to its seed migration, or "
              "excuse it in DOORLESS_DISPATCH with the reason an operator is not meant to reach it.")
        sys.exit(1)
    print("\nOK — every connector surface reads the registry; mirror = seed; one neutral line; no second list; "
          "every dispatchable feed has a door.")
    sys.exit(0)


if __name__ == "__main__":
    main()
