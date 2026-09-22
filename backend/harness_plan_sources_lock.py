"""THE LOCK — which landed sources carry a tenant's plan names has ONE registry, ONE resolver, ONE writer.

CLAUDE.md, "A fix is a DESIGN fix": *"Lock it so it cannot un-wire. A design fix ships with a check that
FAILS THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears."*

Owner (2026-09-22): *"we have enough plans in the system to bring over but it does not give an option to
bring over"* — the POS wizard's plan step read a FIXED PAIR of per-feed tables; a tenant whose plan names
lived in another landed source saw 0 and no way to bring anything over (index §23n, the 2026-09-22 addendum).

WHAT FAILS THE BUILD
  (a) ONE REGISTRY. The landed sources that can carry a plan name are `plan_sources.HOUSE_SOURCES` —
      schema-qualified tables, in precedence order. onboarding.py reads NO plan table by literal name: no
      `_page(c, "commcalc", "product_mrc"|"raw_mi"|"commission_ledger"|"raw_sales" …)` and no
      `.table("<those>")` outside the registry module; every reader dereferences `src["table"]`. A tenant
      row cannot add a table (behavioural: an unknown key is ignored).
  (b) ONE RESOLVER. `preview_import` and `apply_import` each call `resolve_service_plans(c, org_id)` and
      nothing else in backend/app calls the per-kind readers (`_catalog_plans` / `_observed_plans` /
      `_harvest_plans`); the save's too-broad guard reads the rows through the SAME reader the harvest
      uses (`_plan_source_rows`). A second derivation → RED.
  (c) ONE HOME, ONE WRITER. The rules live in `pos.pos_settings` under `plan_sources.CONFIG_KEY` (mig 725
      — no migration); the row is written ONLY by `pos.router.upsert_pos_setting` (the PUT /pos/settings
      endpoint delegates to it; onboarding.py's save calls it and performs no insert / update / upsert of
      its own); the save measures the too-broad words (`_ps.refused_words(`) BEFORE the writer runs.
  (d) THE EMPTY STATE names every source checked: `plans_empty_reason` spells no "Neither source"; over
      a four-source diag every table is named (behavioural).
  (e) THE CARD. The wizard page renders `preview.sources` through `PlanSourcesCard`, saves through the one
      `/config` endpoint, seeds its words from `seedFromPreview` (plan-sources-logic.ts) which reads
      `suggest.proposal` only; neither the page nor the logic file names a plan table, a source key or a
      hint word; the frontend proof exists; the CI workflow runs this lock; the vocab guard scans the
      registry module.
  (f) RULE TWO. No carrier or POS vendor word in the registry module, the logic file or the card (the
      carrier-vocab guard's own derived vocabulary + its two carrier-term regexes).
  (g) THE SIBLINGS, by name. `_dealer_sync` (the other "bring over" that harvests from reports) reads its
      table and column from per-carrier config (`dealer_code_source_table`, mig 293) — not a fixed pair,
      excused; `catalog_suggest.own_signals` reads `pos.activations` (the POS's OWN recorded plans, a
      signal for the catalog presets — a different question) and no landed feed — excused, and pinned so.
  (h) NEGATIVE CONTROLS over synthetic sources: a literal plan table in onboarding.py → RED; a second
      resolver call site → RED; a second writer → RED; the save writing before it measures → RED; a card
      that seeds from a hint list → RED; "Neither source" back in the copy → RED.

Runs beside the other locks (.github/workflows/carrier-vocab-guard.yml). stdlib only.

  python3 backend/harness_plan_sources_lock.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BE = os.path.join(ROOT, "backend")
BE_APP = os.path.join(BE, "app")
FE = os.path.join(ROOT, "frontend", "src")
REGISTRY = "modules/core/plan_sources.py"
ONBOARDING = "modules/core/onboarding.py"
POS_ROUTER = "modules/pos/router.py"
CATSUG = "modules/pos/catalog_suggest.py"
PAGE = "app/(platform)/pos/onboarding/page.tsx"
LOGIC = "app/(platform)/pos/onboarding/plan-sources-logic.ts"
FE_PROOF = os.path.join(ROOT, "frontend", "prove_plan_sources_card.mjs")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml")
VOCAB_GUARD = os.path.join(BE, "harness_carrier_vocab_guard.py")
sys.path.insert(0, BE)

PLAN_TABLES = ("product_mrc", "raw_mi", "commission_ledger", "raw_sales")
LITERAL_TABLE = re.compile(r"""(?:_page\(\s*\w+\s*,\s*"commcalc"\s*,\s*|\.table\(\s*)["'](?:%s)["']""" % "|".join(PLAN_TABLES))
SOURCE_KEYS = ("catalogue", "subscribers", "sales_lines", "statement_lines")

PASS, FAIL = [], []


def ok(label):
    PASS.append(label); print(f"  PASS  {label}")


def bad(label, detail=""):
    FAIL.append(label); print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


def check(label, cond, detail=""):
    (ok if cond else lambda l: bad(l, detail))(label)
    return bool(cond)


def read(base, rel):
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


def py_files(base):
    for d, _, fs in os.walk(base):
        for f in fs:
            if f.endswith(".py"):
                yield os.path.relpath(os.path.join(d, f), base).replace(os.sep, "/")


# ── the rules as functions over source text (so the negative controls can run them over synthetic sources) ──
def rule_registry_only(ob_src):
    """(a) onboarding.py names no plan table by literal."""
    return [m.group(0) for m in LITERAL_TABLE.finditer(ob_src)]


def rule_one_resolver(ob_src, app_sources):
    """(b) preview + apply call the resolver (exactly two literal call sites); the per-kind readers are
    called only inside the resolver / harvest; the guard reads through _plan_source_rows."""
    errs = []
    if ob_src.count("resolve_service_plans(c, org_id)") != 2:
        errs.append(f"resolve_service_plans(c, org_id) call sites = {ob_src.count('resolve_service_plans(c, org_id)')} (want 2: preview + apply)")
    for rel, src in app_sources.items():
        if rel == ONBOARDING:
            continue
        for fn in ("_catalog_plans(", "_observed_plans(", "_harvest_plans(", "resolve_service_plans("):
            if fn in src:
                errs.append(f"{rel} calls {fn} — a second derivation")
    # inside onboarding.py the readers are CALLED (not defined) from the resolver / the harvest only
    body_after_resolver = ob_src.split("def resolve_service_plans(")[1] if "def resolve_service_plans(" in ob_src else ""
    for fn in ("_catalog_plans", "_observed_plans"):
        call = re.compile(r"(?<!def )%s\(c, org_id" % fn)
        n = len(call.findall(ob_src))
        if n != 1 or len(call.findall(body_after_resolver)) != 1:
            errs.append(f"{fn}( called {n}× (want exactly once, inside resolve_service_plans)")
    if "def save_plan_sources(" in ob_src:
        save = ob_src.split("def save_plan_sources(")[1].split("\ndef ")[0]
        if "_plan_source_rows(c, org_id, src)" not in save:
            errs.append("the save's guard does not read through _plan_source_rows")
    else:
        errs.append("save_plan_sources missing")
    return errs


def rule_one_writer(ob_src, pos_src, app_sources):
    """(c) pos_settings is written by upsert_pos_setting only; the save calls it after measuring."""
    errs = []
    if "def upsert_pos_setting(" not in pos_src:
        errs.append("pos/router.py has no upsert_pos_setting")
    for rel, src in app_sources.items():
        if rel == POS_ROUTER:
            continue
        for m in re.finditer(r'\.table\(\s*"pos_settings"\s*\)([^\n]*)', src):
            tail = m.group(1)
            if any(w in tail for w in (".insert(", ".update(", ".upsert(", ".delete(")):
                errs.append(f"{rel} writes pos_settings directly: {m.group(0)[:80]}")
    if pos_src.count('.table("pos_settings")') and pos_src.count('.table("pos_settings")\n         .update') + pos_src.count(".update({\"value\"") == 0:
        errs.append("upsert_pos_setting no longer updates the row")
    if "def save_plan_sources(" in ob_src:
        save = ob_src.split("def save_plan_sources(")[1].split("\ndef ")[0]
        i_meas, i_write = save.find("_ps.refused_words("), save.find("upsert_pos_setting(c, org_id, _ps.CONFIG_KEY")
        if i_meas < 0:
            errs.append("the save does not measure the too-broad words")
        if i_write < 0:
            errs.append("the save does not write through upsert_pos_setting")
        if 0 <= i_write < i_meas:
            errs.append("the save WRITES before it measures")
    return errs


_DOCSTRING = re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'')
_COMMENT = re.compile(r"^\s*#.*$", re.M)


def py_code(src):
    """The CODE of a module — docstrings and comment lines removed (the owner's verbatim quote of the old
    empty state lives in the registry's docstring; what must never come back is the STRING the card shows)."""
    return _COMMENT.sub("", _DOCSTRING.sub("", src))


def rule_empty_state(ob_src, reg_src):
    return [w for w in ("Neither source",) if w in py_code(ob_src) or w in py_code(reg_src)]


def rule_card(page_src, logic_src):
    """(e) the page renders the sources from the payload through the card; the logic seeds from the
    proposal; neither names a table / key / hint."""
    errs = []
    for needle in ("<PlanSourcesCard", "preview?.sources", "/config`", "seedFromPreview(", "buildConfigBody(", "refusalOf("):
        if needle not in page_src:
            errs.append(f"page lacks {needle}")
    if "suggest?.proposal" not in logic_src and "suggest.proposal" not in logic_src:
        errs.append("the logic does not seed from suggest.proposal")
    # the KIND vocabulary ('catalogue' | 'subscribers' | 'lines') is a payload shape the type may spell; the
    # source KEYS (which happen to share two spellings) may not — strip the type union before the key scan
    logic_no_kind = re.sub(r"kind: 'catalogue' \| 'subscribers' \| 'lines'", "kind: KIND", logic_src)
    for src_name, src in (("page", page_src), ("logic", logic_no_kind)):
        for t in PLAN_TABLES:
            if re.search(r"['\"]%s['\"]" % t, src) or f"commcalc.{t}" in src:
                errs.append(f"{src_name} names the table {t}")
        for k in SOURCE_KEYS:
            if re.search(r"['\"]%s['\"]" % k, src):
                errs.append(f"{src_name} names the source key {k}")
        for w in ("HOUSE_INCLUDE_HINTS", "HOUSE_EXCLUDE_HINTS", "hints:"):
            if w in src:
                errs.append(f"{src_name} carries a hint list ({w})")
    return errs


def main():
    print("═" * 92)
    print("  PLAN-SOURCES LOCK — one registry, one resolver, one writer; the card seeds from the proposal")
    print("═" * 92)
    app_sources = {rel: read(BE_APP, rel) for rel in py_files(BE_APP)}
    reg_src, ob_src, pos_src, cat_src = (app_sources[REGISTRY], app_sources[ONBOARDING],
                                         app_sources[POS_ROUTER], app_sources[CATSUG])
    page_src, logic_src = read(FE, PAGE), read(FE, LOGIC)

    # (a)
    check("(a) the registry lists the four landed sources, schema-qualified, mig-074 pair first",
          all(f'"table": "commcalc.{t}"' in reg_src for t in PLAN_TABLES)
          and reg_src.index('"commcalc.product_mrc"') < reg_src.index('"commcalc.raw_mi"') < reg_src.index('"commcalc.raw_sales"'))
    lits = rule_registry_only(ob_src)
    check("(a) onboarding.py reads no plan table by literal — every reader dereferences the registry", not lits, str(lits))
    other = [rel for rel, src in app_sources.items() if rel not in (REGISTRY,) and "HOUSE_SOURCES = (" in src]
    check("(a) HOUSE_SOURCES is defined nowhere else in backend/app", not other, str(other))
    try:
        from app.modules.core import plan_sources as ps
        cfg = ps.resolve_config({"sources": {"evil": {"enabled": True, "table": "public.users", "name_field": "email"}}})
        check("(a) behavioural: a tenant row cannot add a table — an unknown key is ignored",
              [s["key"] for s in cfg["sources"]] == list(SOURCE_KEYS) and not cfg["declared"])
        check("(a) behavioural: a line-level source with no confirmed words matches nothing",
              not ps.line_matches({"category": "rate plans", "product_desc": "x rate plan"}, ps.source_of(None, "sales_lines")))
    except Exception as e:                                          # pragma: no cover
        bad("(a) behavioural checks", str(e)[:160])

    # (b)
    errs = rule_one_resolver(ob_src, app_sources)
    check("(b) ONE resolver: preview and apply call resolve_service_plans; no second derivation; the guard reads the same rows", not errs, " | ".join(errs))

    # (c)
    errs = rule_one_writer(ob_src, pos_src, app_sources)
    check("(c) ONE home, ONE writer: pos_settings written by upsert_pos_setting only; the save measures before it writes", not errs, " | ".join(errs))
    check("(c) the PUT /pos/settings endpoint delegates to the same writer",
          "return {\"setting\": upsert_pos_setting(sb(), org_id, key, body[\"value\"], store)}" in pos_src)
    check("(c) the config home and key are spelled once, in the registry module",
          'CONFIG_HOME = "pos.pos_settings"' in reg_src and 'CONFIG_KEY = "plan_sources"' in reg_src
          and '"plan_sources"' not in ob_src and "_ps.CONFIG_KEY" in ob_src)
    check("(c) no migration: the home is the mig-725 kv table", not [f for f in os.listdir(os.path.join(ROOT, "database", "migrations")) if "plan_source" in f])

    # (d)
    errs = rule_empty_state(ob_src, reg_src)
    check("(d) the empty state never says 'Neither source'", not errs, str(errs))
    try:
        from app.modules.core import onboarding as ob
        diag = {"carriers": 1, "sources": [{"key": s["key"], "kind": s["kind"], "table": s["table"], "label": s["label"],
                                            "enabled": s["enabled"], "rows": 0, "plans": 0} for s in ps.HOUSE_SOURCES]}
        er = ob.plans_empty_reason(diag)["empty_reason"]
        check("(d) behavioural: over a four-source diag every table is named", all(f"commcalc.{t}" in er for t in PLAN_TABLES), er[:200])
    except Exception as e:                                          # pragma: no cover
        bad("(d) behavioural", str(e)[:160])

    # (e)
    errs = rule_card(page_src, logic_src)
    check("(e) the card renders the payload's sources, saves through /config, seeds from the proposal; no table / key / hint in the frontend", not errs, " | ".join(errs))
    check("(e) the frontend proof exists and drives the real logic file", os.path.exists(FE_PROOF) and "plan-sources-logic.ts" in read(ROOT, "frontend/prove_plan_sources_card.mjs"))
    check("(e) the CI workflow runs this lock", "harness_plan_sources_lock.py" in read(ROOT, ".github/workflows/carrier-vocab-guard.yml"))
    check("(e) the carrier-vocab guard scans the registry module", '"core/plan_sources.py"' in read(BE, "harness_carrier_vocab_guard.py"))
    check("(e) the wizard step's import source is the configurable one", 'CONFIGURABLE_SOURCES = {PLAN_SOURCE_KEY: save_plan_sources}' in ob_src
          and 'import_source="service_plans_from_product_mrc"' in ob_src)

    # (f)
    try:
        import harness_carrier_vocab_guard as vg
        rx = vg.pos_regex(vg.pos_vocabulary())
        hits = []
        # the page's OTHER cards carry their own reviewed copy (the vocab guard's file-level decisions);
        # this lock owns the plan-sources card — the block between its comment marker and the catalog builder
        card_block = page_src.split("WHERE YOUR PLAN NAMES COME FROM")[1].split("// CATALOG BUILDER")[0] \
            if "WHERE YOUR PLAN NAMES COME FROM" in page_src else page_src
        for name, src in (("registry", reg_src), ("logic", logic_src), ("card", card_block)):
            for line in src.splitlines():
                if rx and rx.search(line):
                    hits.append(f"{name}: {line.strip()[:80]}")
                if vg.TOTAL_TERMS.search(line) or vg.BOOST_TERMS.search(line):
                    hits.append(f"{name}: {line.strip()[:80]}")
        check("(f) RULE TWO: no POS vendor or carrier word in the registry module, the logic file or the card", not hits, " | ".join(hits[:4]))
    except Exception as e:                                          # pragma: no cover
        bad("(f) vocabulary", str(e)[:160])

    # (g)
    check("(g) sibling _dealer_sync still reads its source from per-carrier config (mig 293) — excused, not a fixed pair",
          "dealer_code_source_table" in pos_src and "def _dealer_sync(" in pos_src)
    check("(g) sibling catalog_suggest.own_signals reads the POS's OWN activations only — no landed plan feed",
          '_page(client, "pos", "activations"' in cat_src and not LITERAL_TABLE.search(cat_src))

    # (h) negative controls
    print("\n  negative controls")
    bad_ob = ob_src + '\n    rows = _page(c, "commcalc", "raw_mi", "customer_plan", org_id)\n'
    check("(h) a literal plan table in onboarding.py → RED", bool(rule_registry_only(bad_ob)))
    bad_ob2 = ob_src.replace("allp, _diag = resolve_service_plans(c, org_id)", "allp = _catalog_plans(c, org_id, {}, '')")
    check("(h) apply re-deriving on its own → RED", bool(rule_one_resolver(bad_ob2, app_sources)))
    bad_ob3 = ob_src.replace("upsert_pos_setting(c, org_id, _ps.CONFIG_KEY, new_raw)",
                             'c.schema("pos").table("pos_settings").upsert({"value": new_raw}).execute()')
    check("(h) a second writer of pos_settings → RED", bool(rule_one_writer(bad_ob3, pos_src, {**app_sources, ONBOARDING: bad_ob3})))
    save = ob_src.split("def save_plan_sources(")[1].split("\ndef ")[0]
    hoisted = save.replace("    refused, shares = [], []", "    upsert_pos_setting(c, org_id, _ps.CONFIG_KEY, new_raw)\n    refused, shares = [], []", 1)
    bad_ob4 = ob_src.replace(save, hoisted)
    check("(h) the save writing before it measures → RED", any("WRITES before" in e for e in rule_one_writer(bad_ob4, pos_src, app_sources)))
    check("(h) 'Neither source' back in the copy → RED", bool(rule_empty_state(ob_src + '\nX = "Neither source has anything"\n', reg_src)))
    check("(h) …but the owner's quote in a docstring is not the copy", not rule_empty_state(ob_src + '\n"""Neither source"""\n', reg_src))
    bad_logic = logic_src.replace("s.suggest?.proposal", "s.hints").replace("suggest.proposal", "hints")
    check("(h) a card seeding from a hint list → RED", bool(rule_card(page_src, bad_logic)))
    check("(h) a card naming a plan table → RED", bool(rule_card(page_src, logic_src + "\nconst T = 'commcalc.raw_mi'\n")))

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
        sys.exit(1)
    print("OK — one registry of plan-name sources, one resolver, one writer; the card seeds from the proposal.")


if __name__ == "__main__":
    main()
