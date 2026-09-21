#!/usr/bin/env python3
"""THE LOCK — the P&L's commission source has ONE resolver, the ledger booking DEREFERENCES the bucket
registry, the feed-suppression set is DERIVED, no second sum over ledger rows exists outside the
commission module, and every surface renders the one answer. Fails the build otherwise.

CLAUDE.md, "A fix is a DESIGN fix": *"Lock it so it cannot un-wire. A design fix ships with a check that
FAILS THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears."*

Owner (2026-09-21): *"p&l is not showing the commission received, it shows in the commission ledger but
not populating the p&l - check platform wide not bandaid."* The class: the P&L's commission lines derived
from per-feed tables; the canonical commission ledger was not a P&L source (index §4b, mig 1013).

WHAT FAILS THE BUILD (a dependency-free static scan — this job runs on bare Python beside the
carrier-vocab guard, so nothing here imports the app):
  (a) ONE RESOLVER. `def resolve_source(` is defined exactly once under backend/app (ledger_pnl.py);
      coa.build_inputs resolves the switch through it and compares nothing against a source word of its
      own; the source VOCABULARY ('feeds' / 'ledger' / 'ledger_else_feeds') is spelled in ma_store_pnl
      (its home) and ledger_pnl (the labels) only — a third module spelling 'ledger_else_feeds' → RED.
  (b) THE BOOKING DEREFERENCES THE REGISTRY. ledger_pnl.py reads `pl_line_key` from `summarize(`'s
      categories and names NO P&L line key of coa.PL_SPEC in code (a literal bucket → line map → RED);
      it never sums a `payout_total` (the amounts are the commission module's).
  (c) THE SUPPRESSION SET IS DERIVED. coa assigns `_lp_covered` only from `_lp.covered_lines(` (and its
      empty reset); a literal set of line keys there → RED.
  (d) EVERY COMMISSION-FEED PATH GOES THROUGH THE GUARDED ADDER. The raw_mi, MA sheet, MA daily-tx (both
      branches), comp-report, activation-report commission and activation-report rebate sites call
      `add_comm(`; the un-guarded spelling of any of them → RED. The device COST site stays on `add(`.
  (e) NO SECOND SUM. Outside app/modules/commcalc, `payout_total` appears only in ledger_pnl.py — and there
      never beside `sum(` or `+=`.
  (f) THE PASSTHROUGH AND THE SURFACES. engine._assemble copies `commission_source`; the P&L page renders it
      and links back through ScreenLink `commission_ledger`; ShowsIn renders the consumer's `lines`; the
      one panel reads GET /pl-commission-source and writes ONLY through PUT /commission-settings, spells
      no source word, and is mounted on the Commission Ledger page and the intake; CONSUMERS names the
      P&L for the ledger table and `consumers_for_table` takes the link.
  (g) REGISTERED. The migration file exists, the index names the column, this lock runs in CI.
  (h) NEGATIVE CONTROLS over synthetic sources — each rule is broken in memory and must go RED.

  python3 backend/harness_pl_commission_source_lock.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BE = os.path.join(ROOT, "backend", "app")
FE = os.path.join(ROOT, "frontend", "src")
COA = "modules/account/coa.py"
LP = "modules/account/ledger_pnl.py"
MSP = "modules/account/ma_store_pnl.py"
ENGINE = "modules/account/engine.py"
ROUTER = "modules/commcalc/router.py"
LANDING = "modules/commcalc/landing_identity.py"
PL_PAGE = "app/(platform)/accounts/pl/page.tsx"
SHOWS_IN = "components/ShowsIn.tsx"
PANEL = "components/PlCommissionSourcePanel.tsx"
LEDGER_PAGE = "app/(platform)/commcalc/commission-ledger/page.tsx"
INTAKE_PAGE = "app/(platform)/onboarding/intake/page.tsx"
MIGRATION = os.path.join(ROOT, "database", "migrations", "1013_pl_commission_source.sql")
INDEX = os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md")
CI = os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml")

RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append((bool(ok), label, detail))
    print(("  PASS  " if ok else "  FAIL  ") + label + ("" if ok else f"   {detail}"))
    return ok


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def walk(root, exts):
    for base, _d, files in os.walk(root):
        for f in files:
            if f.endswith(exts):
                yield os.path.join(base, f)


def code_lines(src):
    """(line_no, text) for lines that are not whole-line comments or inside a docstring."""
    out, in_doc, delim = [], False, None
    for i, ln in enumerate(src.splitlines(), 1):
        s = ln.strip()
        if in_doc:
            if delim in s:
                in_doc = False
            continue
        if s.startswith(('"""', "'''")):
            delim = s[:3]
            if not (len(s) > 3 and s.endswith(delim)):
                in_doc = True
            continue
        if s.startswith("#"):
            continue
        out.append((i, ln))
    return out


def code_text(src):
    return "\n".join(t for _i, t in code_lines(src))


def function_body(src, name):
    lines = src.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(f"def {name}(")), None)
    if start is None:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if ln and not ln[0].isspace() and ln.startswith(("def ", "class ", "@")):
            end = j
            break
    return "\n".join(lines[start:end])


def pl_spec_keys(coa_src):
    """The chart's line keys, parsed statically from `PL_SPEC = [ ("key", …), … ]`."""
    m = re.search(r"^PL_SPEC = \[(.*?)^\]", coa_src, re.S | re.M)
    return sorted(set(re.findall(r'^\s*\("([a-z_]+)",', m.group(1), re.M))) if m else []


# ── the rules, each over a `sources` dict so the negative controls can break them in memory ─────
def rule_one_resolver(be):
    defs = [rel for rel, src in be.items() if re.search(r"^def resolve_source\(", code_text(src), re.M)]
    ok = defs == [LP]
    body = function_body(be.get(COA, ""), "build_inputs")
    ok2 = ".resolve_source(" in body
    # coa compares nothing against a source word of its own — only the resolver's constants
    ok3 = not re.search(r"""==\s*["'](feeds|ledger|ledger_else_feeds)["']""", code_text(be.get(COA, "")))
    spellers = sorted(rel for rel, src in be.items() if "ledger_else_feeds" in code_text(src) and rel not in (MSP, LP))
    return ok and ok2 and ok3 and spellers == [], f"defs={defs} coa_calls={ok2} coa_literal_free={ok3} spellers={spellers}"


def rule_booking_dereferences(be):
    src = code_text(be.get(LP, ""))
    keys = pl_spec_keys(be.get(COA, ""))
    named = sorted(k for k in keys if re.search(r"""["']%s["']""" % re.escape(k), src))
    ok = ("pl_line_key" in src and "summarize(" in src and named == [] and bool(keys))
    sums = [ln for _i, ln in code_lines(be.get(LP, "")) if "payout_total" in ln and ("sum(" in ln or "+=" in ln)]
    return ok and sums == [], f"named_line_keys={named} sums={sums} keys_parsed={len(keys)}"


def rule_suppression_derived(be):
    body = function_body(be.get(COA, ""), "build_inputs")
    assigns = re.findall(r"_lp_covered\s*=\s*(.+)", body)
    derived = [a for a in assigns if "_lp.covered_lines(" in a]
    literal = [a for a in assigns if "{" in a and "covered_lines(" not in a]
    resets = [a for a in assigns if a.strip().startswith("frozenset()")]
    other = [a for a in assigns if a not in derived and a not in literal and a not in resets and "frozenset()" not in a]
    return len(derived) == 1 and literal == [] and other == [], f"assigns={assigns}"


GUARDED_SITES = (
    'add_comm("mi_income"', 'add_comm("atu_income"', "add_comm(_line, _ma_store(_acct), _amt, detail_label=_dlabel)",
    "add_comm(_line, None, _amt)", 'add_comm("carrier_comm", _norm_store(r.get("business_address"))',
    'add_comm("carrier_comm", st, safe_float(r.get("commission_amount"))', "add_comm(_reb_line, st, _reb_sign",
)
UNGUARDED_SPELLINGS = (
    'add("mi_income"', 'add("atu_income"', "add(_line, _ma_store(", "add(_line, None, _amt)",
    'add("carrier_comm"', "add(_reb_line",
)


def rule_guarded_adder(be):
    body = code_text(function_body(be.get(COA, ""), "build_inputs"))
    missing = [s for s in GUARDED_SITES if s not in body]
    # the MA-sheet and the MA daily-tx loops both spell the same guarded line: it must appear twice
    twice = body.count("add_comm(_line, _ma_store(_acct), _amt, detail_label=_dlabel)") >= 2
    unguarded = [s for s in UNGUARDED_SPELLINGS if s in body]
    defined = "def add_comm(" in body
    cost_plain = 'add("device_cost", st, safe_float(r.get("device_cost"))' in body
    return defined and missing == [] and twice and unguarded == [] and cost_plain, f"missing={missing} twice={twice} unguarded={unguarded} defined={defined} cost_plain={cost_plain}"


def rule_no_second_sum(be):
    hits = []
    for rel, src in be.items():
        if rel.startswith("modules/commcalc/") or rel == LP:
            continue
        for i, ln in code_lines(src):
            if "payout_total" in ln:
                hits.append(f"{rel}:{i}")
    return hits == [], f"payout_total outside the commission module: {hits}"


def rule_surfaces(be, fe):
    eng = function_body(be.get(ENGINE, ""), "_assemble")
    a = 'get("commission_source")' in eng and 'row["commission_source"]' in eng
    pl = fe.get(PL_PAGE, "")
    b = "l.commission_source" in pl and 'to="commission_ledger"' in pl and "commission_source.words" in pl
    si = fe.get(SHOWS_IN, "")
    c = "c.lines" in si and "data-pl-lines" in si
    panel = fe.get(PANEL, "")
    d = ("/pl-commission-source" in panel and "/commission-settings" in panel and "pl_commission_source" in panel
         and not re.search(r"""['"](ledger|feeds|ledger_else_feeds)['"]""", panel) and "<ShowsIn" in panel)
    writers = [rel for rel, src in fe.items() if "pl_commission_source" in src and "method: 'PUT'" in src and rel != PANEL]
    e = "PlCommissionSourcePanel" in fe.get(LEDGER_PAGE, "") and "PlCommissionSourcePanel" in fe.get(INTAKE_PAGE, "") and "commitRes.shows_in" in fe.get(INTAKE_PAGE, "")
    landing = be.get(LANDING, "")
    m = re.search(r'"commission_ledger": \[(.*?)\n    \],', landing, re.S)
    f = bool(m) and '"screen": "pl_statement"' in m.group(1) and "def consumers_for_table(table, pl_link=None)" in landing and "def shows_in(row, table_map, route_tables=None, landing_tables=None, pl_link=None)" in landing
    router = be.get(ROUTER, "")
    g = ('@router.get("/pl-commission-source")' in router and "_ledger_pl_link(" in router
         and router.count("pl_link=_ledger_pl_link(client, org_id)") >= 3)
    return a and b and c and d and writers == [] and e and f and g, f"engine={a} pl_page={b} shows_in={c} panel={d} other_writers={writers} mounts={e} landing={f} router={g}"


def rule_registered(files):
    mig = files.get("migration", "")
    idx = files.get("index", "")
    ci = files.get("ci", "")
    a = "pl_commission_source" in mig and "NOT APPLIED" in mig and "REVERT" in mig
    b = "pl_commission_source" in idx and "ledger_pnl" in idx and "harness_pl_commission_source_lock" in idx
    c = "harness_pl_commission_source_lock.py" in ci
    return a and b and c, f"migration={a} index={b} ci={c}"


def main():
    print("P&L COMMISSION SOURCE — THE LOCK")
    print("=" * 78)
    be = {os.path.relpath(p, BE).replace(os.sep, "/"): read(p) for p in walk(BE, (".py",))}
    fe = {os.path.relpath(p, FE).replace(os.sep, "/"): read(p) for p in walk(FE, (".ts", ".tsx"))}
    files = {"migration": read(MIGRATION) if os.path.exists(MIGRATION) else "",
             "index": read(INDEX) if os.path.exists(INDEX) else "",
             "ci": read(CI) if os.path.exists(CI) else ""}

    ok, d = rule_one_resolver(be);          check("(a) ONE resolver: ledger_pnl.resolve_source, coa calls it, no source word compared or spelled elsewhere", ok, d)
    ok, d = rule_booking_dereferences(be);  check("(b) the booking dereferences pl_line_key from summarize(); no P&L line key and no payout_total sum in ledger_pnl", ok, d)
    ok, d = rule_suppression_derived(be);   check("(c) the suppression set is derived: _lp_covered comes only from _lp.covered_lines(", ok, d)
    ok, d = rule_guarded_adder(be);         check("(d) every commission-feed path goes through add_comm; the un-guarded spellings are gone; device cost stays plain", ok, d)
    ok, d = rule_no_second_sum(be);         check("(e) no payout_total outside the commission module except ledger_pnl (which never sums it)", ok, d)
    ok, d = rule_surfaces(be, fe);          check("(f) the passthrough and the surfaces: engine, P&L page, ShowsIn lines, the one panel (one writer, no source word), both mounts, CONSUMERS, the endpoint", ok, d)
    ok, d = rule_registered(files);         check("(g) registered: migration file (surfaced, REVERT), index names the column and this lock, CI runs it", ok, d)

    print("\n(h) NEGATIVE CONTROLS — each rule must go RED on a broken source")
    b2 = dict(be); b2[COA] = be[COA].replace('add_comm("carrier_comm", _norm_store(r.get("business_address"))', 'add("carrier_comm", _norm_store(r.get("business_address"))')
    check("h1 the comp-report site back on add( → (d) RED", not rule_guarded_adder(b2)[0])
    b2 = dict(be); b2[LP] = be[LP] + '\nLINE_MAP = {"commission": "carrier_comm"}\n'
    check("h2 a literal bucket → line map in ledger_pnl → (b) RED", not rule_booking_dereferences(b2)[0])
    b2 = dict(be); b2[LP] = be[LP] + '\ndef _own_sum(rows):\n    return sum(r["payout_total"] for r in rows)\n'
    check("h3 ledger_pnl summing payout_total itself → (b) RED", not rule_booking_dereferences(b2)[0])
    b2 = dict(be); b2[COA] = be[COA].replace("_lp_covered = frozenset(_lp.covered_lines(_lp_buckets, PL_SECTION, _ma314_cfg))", '_lp_covered = frozenset({"carrier_comm", "mi_income"})')
    check("h4 a literal covered set in coa → (c) RED", not rule_suppression_derived(b2)[0])
    b2 = dict(be); b2["modules/account/statement_engine.py"] = be.get("modules/account/statement_engine.py", "") + '\ntotal = sum(r["payout_total"] for r in rows)\n'
    check("h5 a second sum over ledger rows in another account module → (e) RED", not rule_no_second_sum(b2)[0])
    b2 = dict(be); b2["modules/account/other_pnl.py"] = 'def resolve_source(c, h):\n    return "ledger" if c == "ledger_else_feeds" else "feeds"\n'
    check("h6 a second resolver → (a) RED", not rule_one_resolver(b2)[0])
    b2 = dict(be); b2[COA] = be[COA].replace('.resolve_source(', '.resolve_source_(')
    check("h7 coa no longer calling the resolver → (a) RED", not rule_one_resolver(b2)[0])
    b2 = dict(be); b2[ENGINE] = be[ENGINE].replace('row["commission_source"] = cs', 'pass')
    check("h8 the engine passthrough removed → (f) RED", not rule_surfaces(b2, fe)[0])
    f2 = dict(fe); f2[PANEL] = fe[PANEL].replace("onChange={() => save(o.value)}", "onChange={() => save('ledger')}")
    check("h9 the panel spelling a source word → (f) RED", not rule_surfaces(be, f2)[0])
    f2 = dict(fe); f2["app/(platform)/x/page.tsx"] = "api('/api/v1/commcalc/x', { method: 'PUT', body: JSON.stringify({ pl_commission_source: v }) })"
    check("h10 a second writer of the switch → (f) RED", not rule_surfaces(be, f2)[0])
    check("h11 the lock missing from CI → (g) RED", not rule_registered({**files, "ci": ""})[0])

    print("\n" + "=" * 78)
    failed = [r for r in RESULTS if not r[0]]
    print(f"{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("FAIL — the P&L commission source is un-wired or copied somewhere; see above.")
        return 1
    print("OK — one resolver, one registry dereference, a derived suppression set, one sum, every surface wired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
