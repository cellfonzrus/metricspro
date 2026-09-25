"""THE LOCK — franchise royalty, cost & profit centers, the unclaimed sale line: one fact, one home, dereferenced
(mig 1022, index §37). Stdlib only — a static scan; runs in the carrier-vocab-guard CI job.

CLAUDE.md, "A fix is a DESIGN fix": *"Lock it so it cannot un-wire. A design fix ships with a check that FAILS THE
BUILD if a caller stops dereferencing the shared fact, or if a second copy appears."*

WHAT FAILS THE BUILD
  (a) THE LINE VOCABULARY has one home (account/royalty.HOUSE_ROYALTY_LINES ↔ the mig-1022 seed): a distinctive
      report-line label spelled anywhere else under backend/app or frontend/src (a second copy of the vocabulary).
  (b) THE ROUNDING RULE has one home (royalty.fee_schedule): a second caller outside royalty.py, or a fee-rate literal
      in the router / coa / the pages.
  (c) THE P&L BOOKING has one path: coa.build_inputs books the report only through royalty.pl_bookings (and only its
      output); nothing else under backend/app reads royalty_report_line to book or sum money.
  (d) THE VERTICAL is data: no vertical key (derived from core/verticals.HOUSE_VERTICALS) in the royalty / centers /
      sales-map modules, the router, the four pages, the migration, rbac.ts's new items or the report-kind mirror row;
      the report kind's scope dereferences the royalty module's; tenant_declaration passes the vertical from
      core/verticals.tenant_vertical; both twins of `applies` carry the axis.
  (e) ORG SCOPE: every `.table(...)` chain in the royalty router / royalty.py / centers.py carries org_id.
  (f) THE GATE: the royalty router depends on require_module("royalty"); main.py mounts it; the NAV items for the
      four pages carry module 'royalty' and their ScreenLink keys point at those hrefs.
  (g) ONE ENGINE: profit centers reach statement_engine._scopes; the router and centers.py never assemble a P&L of
      their own (no engine._assemble / coa.build_inputs); scope_predicate's profit branch fails closed; the journal
      router knows the prefix.
  (h) THE UNCLAIMED SALE LINE: coa's fallback dereferences sales_line_map (match → royalty suppression → add → tally)
      and attaches the side entry; statement_engine surfaces it via side_meta.
  (i) CI + INDEX: both stdlib proofs and this lock run in carrier-vocab-guard.yml, the app-dependent proof in its own
      job; index §37 exists and names the tables, endpoints and harnesses.
  (j) NEGATIVE CONTROLS: every scanner is run over a synthetic violation and must go RED.

  python3 backend/harness_royalty_lock.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)
from app.modules.account import royalty as R      # noqa: E402  (stdlib-only)
from app.modules.core import verticals as V       # noqa: E402

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  " + label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:500]))


def rd(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def walk(base, exts):
    out = {}
    for dp, _d, fs in os.walk(os.path.join(ROOT, base)):
        if "node_modules" in dp or "__pycache__" in dp:
            continue
        for f in fs:
            if f.endswith(exts):
                p = os.path.join(dp, f)
                out[os.path.relpath(p, ROOT).replace(os.sep, "/")] = open(p, encoding="utf-8").read()
    return out


ACC = "backend/app/modules/account/"
HOME_FILES = {ACC + "royalty.py"}
ROUTER = ACC + "royalty_router.py"
PAGES = ["frontend/src/app/(platform)/accounts/royalty/page.tsx", "frontend/src/app/(platform)/accounts/royalty/recon/page.tsx",
         "frontend/src/app/(platform)/accounts/profit-centers/page.tsx", "frontend/src/app/(platform)/accounts/cost-centers/page.tsx"]
MIG = "database/migrations/" + R.MIGRATION
BE = walk("backend/app", (".py",))
FE = walk("frontend/src", (".ts", ".tsx"))

# ── (a) the vocabulary ────────────────────────────────────────────────────────────────────────────────
# distinctive labels only: multi-word, not generic English that other modules legitimately use
GENERIC = {"sales tax", "money transfer", "money orders", "office supplies", "greeting cards", "total due", "other 1", "other 2",
           "deposits", "exclusions", "commissions", "royalty fees", "printing", "copies", "notary", "pagers", "facsimile"}
LABELS = sorted({r["label"] for r in R.HOUSE_ROYALTY_LINES if " " in r["label"] and r["label"].lower() not in GENERIC})


# (path → reason). A stale entry (the file no longer spells a label) fails too.
VOCAB_ALLOW = {
    "backend/app/modules/commcalc/report_kinds.py":
        "the royalty_report kind's `recognisable_columns` — the layman card lists the headings a person recognises the "
        "report by (display copy of the registry row, seeded by the same migration); it is not read to parse or book",
}


def vocab_copies(files, homes):
    hits = []
    for rel, src in files.items():
        if rel in homes or rel in VOCAB_ALLOW:
            continue
        for lab in LABELS:
            if re.search(r"""['"`]""" + re.escape(lab) + r"""['"`]""", src):
                hits.append((rel, lab))
    return hits


check("(a) no distinctive report-line label (%d derived from the mirror) is spelled outside account/royalty.py" % len(LABELS),
      not vocab_copies({**BE, **FE}, HOME_FILES), vocab_copies({**BE, **FE}, HOME_FILES)[:5])
check("(a) every vocabulary allow entry is live (the file still spells a label — else the entry is stale)",
      all(any(re.search(r"""['"`]""" + re.escape(l) + r"""['"`]""", ({**BE, **FE}).get(rel, "")) for l in LABELS) for rel in VOCAB_ALLOW))
check("(a) the mirror is the seed's source (the seed is generated from it and parsed back by harness_royalty.py §A)",
      "account/royalty.HOUSE_ROYALTY_LINES" in rd(*MIG.split("/")))

# ── (b) the rounding rule ────────────────────────────────────────────────────────────────────────────
callers = [rel for rel, src in BE.items() if "fee_schedule(" in src and rel not in HOME_FILES]
check("(b) fee_schedule is called only inside royalty.py (validate) — no second rounding path", not callers, callers)
RATE_LIT = re.compile(r"(?<![\d.])0\.0(5|1|25|85)(?!\d)")
rate_hits = [rel for rel in [ROUTER, ACC + "coa.py"] + PAGES if RATE_LIT.search(re.sub(r"#.*", "", rd(*rel.split("/"))))]
check("(b) no fee-rate literal in the router, coa or the pages (rates are vocabulary config)", not rate_hits, rate_hits)

# ── (c) one booking path ─────────────────────────────────────────────────────────────────────────────
coa = BE[ACC + "coa.py"]
check("(c) coa.build_inputs books the royalty report through royalty.pl_bookings and adds only its output",
      "_roy.pl_bookings(_roy_reps, _roy_vocab, set(PL_SECTION))" in coa and "for _k, _st, _amt, _dl in _roy_bk:" in coa)
readers = [rel for rel, src in BE.items() if "royalty_report_line" in src]
ALLOWED_READERS = {ACC + "royalty.py", ROUTER, "backend/app/modules/commcalc/landing_identity.py",
                   "backend/app/modules/commcalc/data_lineage_registry.py"}
check("(c) only the royalty module, its router and the two registries name royalty_report_line",
      set(readers) <= ALLOWED_READERS, sorted(set(readers) - ALLOWED_READERS))

# ── (d) the vertical is data ─────────────────────────────────────────────────────────────────────────
VKEYS = [v["key"] for v in V.HOUSE_VERTICALS]


def vertical_literals(rel_srcs):
    return [(rel, k) for rel, src in rel_srcs.items() for k in VKEYS if re.search(r"""['"{,]""" + re.escape(k) + r"""['"},]""", src)]


SCAN_D = {rel: rd(*rel.split("/")) for rel in [ACC + "royalty.py", ACC + "centers.py", ACC + "sales_line_map.py", ROUTER, MIG] + PAGES}
check("(d) no vertical key in the royalty / centers / sales-map modules, the router, the migration or the pages",
      not vertical_literals(SCAN_D), vertical_literals(SCAN_D))
rbac = rd("frontend", "src", "lib", "rbac.ts")
new_items = [ln for ln in rbac.split("\n") if "module: 'royalty'" in ln]
check("(d) rbac.ts: four NAV items carry module 'royalty' and none spells a vertical",
      len(new_items) == 4 and not vertical_literals({"rbac": "\n".join(new_items)}), new_items)
rk = BE["backend/app/modules/commcalc/report_kinds.py"]
roy_row = rk.split('_k(key="royalty_report"', 1)[1].split("sort_order=400)", 1)[0] if '_k(key="royalty_report"' in rk else ""
check("(d) the royalty kind's vertical scope DEREFERENCES the royalty module's (no literal in the mirror row)",
      'applies_to_vertical=list(_HOUSE_MODULE_VERTICALS.get("royalty")' in roy_row and not vertical_literals({"row": roy_row}))
td = rk.split("def tenant_declaration", 1)[1].split("\ndef ", 1)[0]
check("(d) tenant_declaration reads the vertical from core/verticals.tenant_vertical and passes it on",
      "_vert.tenant_vertical(client, org_id)" in td and "vertical=vkey" in td)
ap = rk.split("def applies(", 1)[1].split("\ndef ", 1)[0]
check("(d) backend applies() carries the vertical axis ('{}' = any via .get)", 'row.get("applies_to_vertical")' in ap and "ver_ok" in ap)
cs = rd("frontend", "src", "lib", "carrier-scope.ts")
check("(d) frontend kindApplies carries the same axis", "row.applies_to_vertical" in cs and "verOk" in cs)

# ── (e) org scope ────────────────────────────────────────────────────────────────────────────────────
def unscoped_chains(src):
    bad = []
    for m in re.finditer(r"\.table\(", src):
        end = src.find(".execute(", m.start())
        chain = src[m.start(): end if end > 0 else m.start() + 400]
        head = src[max(0, m.start() - 160): m.start()]
        payload_scoped = (".insert(" in chain or ".upsert(" in chain) and ".delete(" not in chain and \
            '"org_id": org_id' in src[max(0, m.start() - 900): m.start()]
        if "org_id" not in chain and not payload_scoped and "q = " not in head.split("\n")[-1]:
            bad.append(chain[:90].replace("\n", " "))
    return bad


def unscoped_q(src):
    """`q = ….table(x)` then `q.delete()/.upsert()` — the scope must be on the q call."""
    bad = []
    for m in re.finditer(r"\bq\.(delete|upsert|select|update)\(", src):
        seg = src[m.start(): src.find(".execute(", m.start())]
        if "org_id" not in seg:
            bad.append(seg[:90])
    return bad


for rel in (ROUTER, ACC + "royalty.py", ACC + "centers.py"):
    src = BE[rel]
    b = unscoped_chains(src) + unscoped_q(src)
    check("(e) every query chain in %s carries org_id" % rel.split("/")[-1], not b, b[:3])

# ── (f) the gate ─────────────────────────────────────────────────────────────────────────────────────
rr = BE[ROUTER]
check("(f) the royalty router depends on require_module('royalty')", 'dependencies=[Depends(require_module("royalty"))]' in rr)
check("(f) main.py mounts it", "include_router(account_royalty_router" in BE["backend/app/main.py"])
sl = rd("frontend", "src", "components", "ScreenLink.tsx")
check("(f) ScreenLink royalty_report / royalty_recon point at the NAV hrefs",
      "royalty_report: { href: '/accounts/royalty'" in sl and "royalty_recon: { href: '/accounts/royalty/recon'" in sl
      and "href: '/accounts/royalty', label: 'Royalty Report'" in rbac and "href: '/accounts/royalty/recon'" in rbac)
li = BE["backend/app/modules/commcalc/landing_identity.py"]
check("(f) the royalty page renders ShowsIn through the one hook (every upload says where it shows)",
      "useReportKinds" in rd(*PAGES[0].split("/")) and "<ShowsIn" in rd(*PAGES[0].split("/")) and '"royalty_report": {"screen": "royalty_report"' in li)

# ── (g) one engine ───────────────────────────────────────────────────────────────────────────────────
se = BE[ACC + "statement_engine.py"]
check("(g) statement_engine._scopes takes the profit scopes and both entry points pass them",
      "profit_scopes=None" in se and "for sk, label, stores, cw in (profit_scopes or []):" in se
      and len(re.findall(r"(?<!def )_profit_scopes\(client, org_id\)", se)) == 2)
second = [rel for rel in (ROUTER, ACC + "centers.py", ACC + "royalty.py") if re.search(r"engine\._assemble\(|coa\.build_inputs\(", BE[rel])]
check("(g) the router, centers.py and royalty.py never assemble a P&L of their own", not second, second)
sf = BE[ACC + "statement_filter.py"]
pb = sf.split('if scope.startswith("profit_center:"):', 1)[1].split("return lambda addr: True", 1)[0] if 'startswith("profit_center:")' in sf else ""
check("(g) scope_predicate's profit branch fails CLOSED (unknown center and exception → match nothing)",
      pb.count("return lambda addr: False") == 2)
check("(g) journal_scope_entries knows the prefix (a profit center never receives the tenant's entries)",
      'scope_key.startswith("profit_center:")' in BE[ACC + "balance_sheet.py"])

# ── (h) the unclaimed sale line ──────────────────────────────────────────────────────────────────────
check("(h) coa's fallback dereferences sales_line_map: match → royalty suppression → add → tally, side entry attached",
      "_slm.match(_slm_idx, dept, cat, prod)" in coa and "_hit[0] in _roy_covered" in coa
      and "_slm_tally.unclaimed(dept, cat, ext, st)" in coa and "L[_slm.SIDE_KEY] = _slm_tally.side_line()" in coa)
check("(h) statement meta carries it (side_meta) — the P&L payload never does", "def side_meta(inputs)" in se and '"_unbooked_sales"' in se)

# ── (i) CI + index ───────────────────────────────────────────────────────────────────────────────────
wf = rd(".github", "workflows", "carrier-vocab-guard.yml")
check("(i) CI runs harness_royalty.py + this lock (stdlib) and harness_royalty_pl.py (with the app's dependencies)",
      "python3 harness_royalty.py" in wf and "python3 harness_royalty_lock.py" in wf and "python3 harness_royalty_pl.py" in wf)
idx = rd("docs", "SYSTEM_DATA_FLOW_INDEX.md")
sec = idx.split("## 37.", 1)[1].split("\n## ", 1)[0] if "## 37." in idx else ""
check("(i) index §37 exists and names the tables, endpoints and harnesses",
      all(t in sec for t in ("royalty_report_line", "finance_center", "pl_sales_line_map", "/account/royalty/summary",
                             "harness_royalty.py", "harness_royalty_lock.py", "harness_royalty_pl.py")))

# ── (j) negative controls ────────────────────────────────────────────────────────────────────────────
check("(j) a second copy of a line label → RED", bool(vocab_copies({"x.tsx": "const L = ['" + LABELS[0] + "']"}, set())))
check("(j) a vertical literal → RED", bool(vertical_literals({"x.py": "if v == '%s':" % VKEYS[-1]})))
check("(j) an unscoped chain → RED", bool(unscoped_chains('client.schema("commcalc").table("royalty_report").select("*").execute()')))
check("(j) an unscoped q-delete → RED", bool(unscoped_q('q.delete().eq("store_ref", s).execute()')))
check("(j) a rate literal → RED", bool(RATE_LIT.search("fee = str * 0.085")))

print("\n%d passed, %d failed" % (P, F))
print("OK — one vocabulary, one rounding rule, one booking path, the vertical as data, org-scoped, gated, one engine."
      if not F else "FAIL — the royalty lock is open. See this file's docstring for each rule.")
sys.exit(1 if F else 0)
