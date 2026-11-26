"""Offline proof harness — "WHICH SEVEN DAYS IS THIS STORE'S WEEK" HAS ONE HOME, AND EVERY CALLER
ASKS IT (index §STOREOPS-17). Drives the pure core of `storeops/work_week` and reads its five former
callers as source. No live database, no network, no framework import.

Run: `cd backend && python3 harness_work_week.py`

WHAT WAS ASKED FOR (owner 2026-11-26, Sanjot, verbatim): *"Work week for all companies starts on a
Monday and the nds kn a Sunday , luxelink Chicago stores start on Thursday and end on Wednesday ,
luxelink ny stores are Monday to Sunday"* and *"All store open 7 days a week"*.

THE TWO CLASSES THIS LOCKS, both already named in CLAUDE.md:

  1. A FACT AT THE WRONG GRAIN CANNOT BE STATED. `storeops.tenants.work_week_start_dow` held one
     week start per TENANT. Luxelink is one tenant whose markets are its two companies, and its
     column reads 3 (Thursday) — right for its 10 open Chicago stores, wrong for its 10 open NY
     ones, with nowhere to put the second answer. The grain widens to the market (mig 1132);
     special-casing the tenant in code would be the per-tenant CODE that RULE TWO forbids.

  2. THE SAME FACT READ FROM FIVE PLACES. That column was selected off `tenants` in
     `payroll_approval`, in `storeops/router` TWICE, in `payroll_run_api` and in
     `schedule_week_api`. Widening four of them leaves the fifth answering the old way, which is
     "the same defect wearing a hat". §B below fails the build on a sixth copy.

Proves:
  A. PURE — the core decides from rows handed to it: no client, no table name, no I/O in the
     functions that make the decision.
  B. THE SIXTH-COPY LOCK — no module outside the home selects `work_week_start_dow`,
     `market_work_week` or `open_dows`, except the PAY-PERIOD bundle reads and the tenant-settings
     editor, which are pinned here BY NAME with their reason.
  C. ORDER OF RESOLUTION — market, then tenant, then the house Monday, and the level is reported.
  D. A TYPO IS NOT MONDAY — an out-of-range or unparseable value falls through and says so.
  E. THE TENANT RULE (owner 2026-10-30) — a house default is right for the week start (a POLICY) and
     BANNED behind the open days (a tenant's own words): undeclared stays undeclared.
  F. MARKET IS COMPARED THE WAY THE ROSTER SPELLS IT — case-folded, so a second spelling cannot
     become a phantom market (§13e).
  G. EVERY CALLER DEREFERENCES — the scheduling board, the hours-budget enforcement, the emailed
     rota and the payroll board's week all ask this home.
  H. DEGRADES UNTIL ITS MIGRATION LANDS (§POS-01) — every read falls back rather than raising, and
     the migration grants what it creates.
  Z. ARMED CONTROLS — each rule above is shown able to go red.
"""
import ast
import io
import os
import re
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def read(p):
    return io.open(p, encoding="utf-8").read()


HOME_PATH = "app/modules/storeops/work_week.py"
MIGRATION = "../database/migrations/1132_per_market_work_week_and_open_days.sql"
HOME_SRC = read(HOME_PATH)

from app.modules.storeops import work_week as ww          # noqa: E402

CHI = [{"market": "Chicago", "work_week_start_dow": 3}]
BOTH = CHI + [{"market": "NY", "work_week_start_dow": 0}]
ALL7 = [0, 1, 2, 3, 4, 5, 6]

# ── §A the core is pure ─────────────────────────────────────────────────────────────────────────
print("\n— §A the decision touches no database —")
_tree = ast.parse(HOME_SRC)
_pure = {"resolve_start_dow", "resolve_open_dows", "_market_key", "_dow", "_dow_set"}
_impure_body = []
for node in ast.walk(_tree):
    if isinstance(node, ast.FunctionDef) and node.name in _pure:
        body = ast.dump(node)
        for bad in ("sb", "get_supabase", "execute", "table"):
            if f"id='{bad}'" in body or f"attr='{bad}'" in body:
                _impure_body.append(f"{node.name}:{bad}")
check("A1 the pure core calls no client, no table and no execute", not _impure_body,
      detail=" | ".join(_impure_body))
check("A2 the home imports no framework and no HTTP library",
      not re.search(r"^\s*(from|import)\s+(fastapi|httpx|requests|starlette)", HOME_SRC, re.M))
check("A3 both decisions are keyword-only, so a caller cannot pass rows in the wrong order",
      "def resolve_start_dow(*," in HOME_SRC and "def resolve_open_dows(*," in HOME_SRC)
check("A4 `org_id` is a REQUIRED argument of every I/O entry point — a caller that has not asked "
      "which tenant cannot get an answer (owner 2026-10-30)",
      all(f"def {fn}(org_id" in HOME_SRC
          for fn in ("start_dow_for", "open_dows_for", "open_dows_map")))

# ── §B the sixth-copy lock ──────────────────────────────────────────────────────────────────────
print("\n— §B nobody else reads these columns —")
# PINNED EXCUSALS, each with the reason it is not a sixth copy:
#   · the PAY-PERIOD bundle. `work_week_start_dow` is one field of the tenant's pay-cycle settings
#     (with pay_period_type / payday_dow / biweekly_anchor), consumed by `core.router.pay_period_for`
#     to decide which DATES a pay period covers. A payroll board resolves ONE period for all of its
#     stores, so splitting it by market would give one board several periods — a money-adjacent
#     change that needs its own PR and the owner's sight. Stated in the index rather than hidden.
#   · the tenant-settings EDITOR. The Pay Period & Work-Week screen reads and writes the tenant's
#     own column; that IS the declaration's editor, not a second answer to the question.
EXCUSED = {
    "app/modules/storeops/payroll_approval.py":
        "the pay-period bundle (_pay_settings) — see the note above",
    "app/modules/storeops/payroll_run_api.py":
        "the pay-period bundle for salary derivation — see the note above",
    "app/modules/storeops/router.py":
        "the tenant-settings editor + _tenant_pp_settings (the pay-period bundle)",
}
# NOT EXCUSED AND NOT AN OFFENDER: `core/router._pp_settings` / `pay_period_for` take the tenant's
# settings ROW as an argument and normalise it. They name the field but read no table, which is the
# shape this lock wants everywhere — a decision over rows handed in. Pinning it would have been a
# stale excusal, and §B2 refuses one.
COLUMNS = ("work_week_start_dow", "open_dows")
# A READ, not a mention. These names are also the PAYLOAD KEYS the flows and the endpoints speak in
# (`declared['week_start_dow']`, `declared['open_dows']`), and a payload key is not a second copy of
# the fact — it is how the answer is carried once resolved. What may not reappear is a QUERY: a
# `.select(...)` naming the column, or a read of the new table.
_SELECT = re.compile(r"""select\(\s*["'][^"']*\b(%s)\b""" % "|".join(COLUMNS))
_TABLE = re.compile(r"""table\(\s*["']market_work_week["']""")
offenders = []
for root, _dirs, files in os.walk("app"):
    for fn in files:
        if not fn.endswith(".py"):
            continue
        rel = os.path.join(root, fn).replace(os.sep, "/")
        if rel == HOME_PATH or rel in EXCUSED:
            continue
        # Only CODE counts: a comment remembering the column is documentation, not a dependency.
        code = "\n".join(l.split("#", 1)[0] for l in read(rel).splitlines())
        hit = _SELECT.search(code)
        if hit:
            offenders.append(f"{rel}: selects {hit.group(1)}")
        if _TABLE.search(code):
            offenders.append(f"{rel}: reads market_work_week")
check("B1-lock no module outside the home reads the week-start or open-days columns",
      not offenders, detail=" | ".join(offenders))
_stale = [p for p in EXCUSED if not os.path.exists(p)
          or not (_SELECT.search(read(p)) or _TABLE.search(read(p)))]
check("B2 every excusal is still real — a stale pin is deleted, never left to rot",
      not _stale, detail=" | ".join(_stale))
check("B3 each excusal carries a written reason", all(bool(v.strip()) for v in EXCUSED.values()))

# ── §C the order of resolution ──────────────────────────────────────────────────────────────────
print("\n— §C market, then tenant, then the house —")
check("C1 a market with its own week answers before its tenant",
      ww.resolve_start_dow(market_rows=CHI, tenant_dow=0, market="Chicago")
      == {"dow": 3, "level": ww.LEVEL_MARKET, "declared": True})
check("C2 THE LUXELINK NY CASE — a market with no row takes its tenant's week, and says so",
      ww.resolve_start_dow(market_rows=CHI, tenant_dow=0, market="NY")
      == {"dow": 0, "level": ww.LEVEL_TENANT, "declared": True})
check("C3 one tenant, two markets, two answers",
      (ww.resolve_start_dow(market_rows=BOTH, tenant_dow=3, market="Chicago")["dow"],
       ww.resolve_start_dow(market_rows=BOTH, tenant_dow=3, market="NY")["dow"]) == (3, 0))
check("C4 a tenant-wide caller is NOT given one market's week",
      ww.resolve_start_dow(market_rows=BOTH, tenant_dow=3, market=None)["level"] == ww.LEVEL_TENANT)
check("C5 nothing declared anywhere is the house Monday AND reports itself undeclared",
      ww.resolve_start_dow(market_rows=(), tenant_dow=None, market="NY")
      == {"dow": 0, "level": ww.LEVEL_HOUSE, "declared": False})
check("C6 a tenant that chose Monday is DECLARED — 0 is an answer, not an absence",
      ww.resolve_start_dow(market_rows=(), tenant_dow=0, market=None)["declared"] is True)

# ── §D a typo is not Monday ─────────────────────────────────────────────────────────────────────
print("\n— §D a typo falls through rather than becoming Monday —")
check("D1 an out-of-range market value falls through to the tenant",
      ww.resolve_start_dow(market_rows=[{"market": "NY", "work_week_start_dow": 9}],
                           tenant_dow=3, market="NY")["dow"] == 3)
check("D2 an unparseable tenant value reaches the house default and says undeclared",
      ww.resolve_start_dow(market_rows=(), tenant_dow="thursday", market=None)["declared"] is False)
check("D3 a market row with no name cannot answer for every market",
      ww.resolve_start_dow(market_rows=[{"market": "", "work_week_start_dow": 3}],
                           tenant_dow=0, market="NY")["level"] == ww.LEVEL_TENANT)
check("D4 the database refuses the same typo too — the weekday CHECK is in the migration",
      "work_week_start_dow BETWEEN 0 AND 6" in read(MIGRATION))

# ── §E the tenant rule ─────────────────────────────────────────────────────────────────────────
print("\n— §E a house default is right for the week, BANNED behind the open days —")
check("E1 nothing declared means nothing identified — never seven days",
      ww.resolve_open_dows(store_dows=None, tenant_dows=None)
      == {"dows": (), "level": None, "declared": False})
check("E2 the home spells no open-days default at all",
      not re.search(r"(DEFAULT_OPEN|HOUSE_OPEN|open_dows\s*=\s*\(0, 1, 2, 3, 4, 5, 6\))", HOME_SRC))
check("E3 a store's own declaration wins over its tenant's",
      ww.resolve_open_dows(store_dows=[0, 1], tenant_dows=ALL7)["level"] == ww.LEVEL_STORE)
check("E4 an EMPTY declaration is a declaration — 'we have not said' and 'never' do not read alike",
      ww.resolve_open_dows(store_dows=[], tenant_dows=[0])["declared"] is True
      and ww.resolve_open_dows(store_dows=[], tenant_dows=[0])["dows"] == ())
check("E5 a set holding a non-weekday is refused WHOLE, never trimmed into a smaller week",
      ww.resolve_open_dows(store_dows=[0, 1, 7], tenant_dows=ALL7)["level"] == ww.LEVEL_TENANT)
check("E6 a column read back as text is not iterated into characters",
      ww.resolve_open_dows(store_dows="0,1", tenant_dows=None)["declared"] is False)

# ── §F market comparison ───────────────────────────────────────────────────────────────────────
print("\n— §F a market is compared the way the roster spells it —")
check("F1 case and surrounding space do not make a second market",
      all(ww.resolve_start_dow(market_rows=CHI, tenant_dow=0, market=sp)["dow"] == 3
          for sp in ("chicago", "CHICAGO", "  Chicago  ")))
check("F2 a market nobody declared does not borrow another's week",
      ww.resolve_start_dow(market_rows=BOTH, tenant_dow=0, market="Detroit")["dow"] == 0)
check("F3 the comparison is case-folded in source, not raw ==",
      "casefold()" in HOME_SRC)
check("F4 the market a store belongs to comes from the CANONICAL index (§14x), not a vocabulary "
      "of this module's own", "from app.core.scope import market_by_code" in HOME_SRC)

# ── §G every caller dereferences ───────────────────────────────────────────────────────────────
print("\n— §G the callers ask rather than re-derive —")
CALLERS = {
    "app/modules/storeops/schedule_week_api.py": ("import work_week as _ww", "_ww.start_dow_for"),
    "app/modules/storeops/payroll_approval.py": ("import work_week as _ww", "_ww.start_dow_for"),
    "app/modules/storeops/router.py": ("import work_week as _ww", "_ww.start_dow_for"),
}
for path, needles in CALLERS.items():
    src = read(path)
    check(f"G1 {path.split('/')[-1]} dereferences the home",
          all(n in src for n in needles))
_rota = read("app/modules/notify/report_registry.py")
check("G2 the emailed rota resolves a store-scoped send on THAT STORE'S week — the sibling that "
      "snapped to Monday until 2026-11-26 must not drift again",
      "_week_start_dow(org_id, store_code=" in _rota)
_api = read("app/modules/storeops/schedule_week_api.py")
check("G3 the scheduling board cuts each store-week over ITS OWN days",
      "_for_store(store, a)" in _api and "by_market" in _api)
check("G4 and it SAYS when the board holds more than one week, rather than implying one",
      "weeks_on_this_board" in _api)
check("G5 the hours-budget enforcement passes the store, so a ceiling is counted over the right "
      "seven days", "store_code=store)" in read("app/modules/storeops/router.py"))
check("G6 every surface reports WHERE its week came from, so none implies a declaration",
      "week_start_level" in _api and "LEVEL_MARKET" in HOME_SRC)

# ── §H degrades, and the migration grants what it creates ──────────────────────────────────────
print("\n— §H unapplied migration degrades, applied table is reachable —")
for fn in ("_market_rows", "_tenant_row", "open_dows_for", "open_dows_map", "market_of"):
    i = HOME_SRC.find(f"def {fn}(")
    body = HOME_SRC[i:HOME_SRC.find("\ndef ", i + 1)]
    check(f"H1 {fn} degrades instead of raising into a calendar question",
          "except Exception" in body)
check("H2 the migration GRANTS what it creates (house convention)",
      "GRANT SELECT, INSERT, UPDATE, DELETE ON storeops.market_work_week TO service_role"
      in read(MIGRATION))
check("H3 it is additive and idempotent — no DROP, no destructive ALTER",
      "CREATE TABLE IF NOT EXISTS" in read(MIGRATION)
      and "ADD COLUMN IF NOT EXISTS" in read(MIGRATION)
      and not re.search(r"^\s*(DROP|TRUNCATE|DELETE FROM)\s", read(MIGRATION), re.M))
check("H4 it carries a REVERT note", "-- REVERT:" in read(MIGRATION))
check("H5 NOTHING is seeded — four identical rows would be four places for one fact to drift",
      not re.search(r"^\s*INSERT INTO", read(MIGRATION), re.M | re.I))

# ── §Z armed controls ──────────────────────────────────────────────────────────────────────────
print("\n— §Z armed controls (each rule must be able to go red) —")
check("Z1 a house seven-day default WOULD be caught by §E1",
      ww.resolve_open_dows(store_dows=None, tenant_dows=None)["dows"] != tuple(ALL7))
check("Z2 raw market comparison WOULD be caught by §F1",
      "Chicago" != "chicago" and ww._market_key("Chicago") == ww._market_key("chicago"))
check("Z3 §B1's scanner really sees a planted copy",
      bool(_SELECT.search('rows = t.select("work_week_start_dow,pay_period_type")'))
      and bool(_TABLE.search('sb().table("market_work_week").select("*")')))
check("Z4 §B1 reads a PAYLOAD KEY as what it is, not as a second copy of the fact",
      not _SELECT.search('return {"week_start_dow": w["dow"], "open_dows": ()}'))
check("Z4b and it ignores the column named in a COMMENT — documentation is not a dependency",
      not _SELECT.search("\n".join(l.split("#", 1)[0] for l in
                                   ['# t.select("work_week_start_dow") used to live here'])))
check("Z5 a market declaring the SAME day as its tenant is still reported at market level — the "
      "level is where the answer came from, not whether it differs",
      ww.resolve_start_dow(market_rows=[{"market": "NY", "work_week_start_dow": 0}],
                           tenant_dow=0, market="NY")["level"] == ww.LEVEL_MARKET)
check("Z6 §G2 would go red if the rota dropped the store again",
      "_week_start_dow(org_id)" not in _rota.replace("_week_start_dow(org_id, store_code=", ""))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
