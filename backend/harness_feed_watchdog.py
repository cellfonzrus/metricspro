"""DB-FREE PROOF — every registered feed is WATCHED, or its absence is a declared decision.

OWNER DIRECTIVE, 2026-10-03: *"need a root cause analysis why this fails and a fool prrof system to
avoid such fails"*, after two data feeds died unnoticed for weeks and were found only because a human
questioned a number.

THE ROOT CAUSE, stated once so nobody re-derives it. `router._data_freshness_report` watched THREE
feeds — activation details, bill payments and sales — named by hand at the call site, while
`data_lineage_registry.INGEST_TABLES_BY_MODULE` registered more than twenty. So REGISTERING A FEED DID
NOT GET IT WATCHED. Measured on the house org 2026-10-03: `raw_comp_report` last carried data for
2026-08-06 and `asset_ledger` for 2026-09-23. Neither was ever on the monitor's list, so neither could
raise an alarm; both surfaced weeks later as a wrong figure in the P&L. This is not a monitor that
missed a feed — it is a monitor that could not see it.

THE CLASS, and it is the one the house rules already name (§19.18, three times over): a fact every
caller needs is written into a registry, and the callers keep their own copies. Here the fact is
"which feeds exist, how often each is due, and which column names the day its data is about". Three
copies existed: the registry's table list, the monitor's list of three, and `router._DUTY_DATE_COL`
— which had already drifted, calling `raw_ma_commission` and `raw_ma_daily_tx` `created_at` (an
ARRIVAL stamp) when both carry `tx_date`, so a daily-upload duty measured its missing range from when
we loaded rather than from what the data covered.

THE DESIGN FIX THIS LOCKS:
  • The watched set is DERIVED from the registry (`watched_feeds()`), never listed at a call site, so
    a feed registered today is watched today.
  • Cadence, data-date column and human label live in the registry and callers READ them.
  • An unwatched feed costs a sentence in `NOT_WATCHED_REASONS`. Omission does not compile.

WHAT THIS HARNESS LOCKS:
  §A every registered ingest table is watched or explicitly excused — no silent gaps
  §B a MONTHLY archive is never watched (that is the 2026-08-30 false alarm, and the one I repeated
     on 2026-10-03 by reading raw_sales and declaring the sales feed dead)
  §C cadence and the data-date column are declared for every watched feed, and lateness is judged
     against the feed's OWN cadence
  §D the callers DEREFERENCE the registry and keep no second copy
  §E the human label — every feed is nameable, and the fallback never yields a bare identifier
  §F the un-wiring lock: the monitor may not go back to a hand-written feed list

Run:  cd backend && python3 harness_feed_watchdog.py
"""
import ast
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import app.modules.commcalc.data_lineage_registry as LIN

ROUTER = "app/modules/commcalc/router.py"
REGISTRY = "app/modules/commcalc/data_lineage_registry.py"

PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def eq(name, got, want):
    ok(name, got == want, f"got={got!r} want={want!r}")


def _src(rel):
    return open(os.path.join(_HERE, rel), encoding="utf-8").read()


def _func_src(rel, name):
    """SOURCE TEXT of one top-level function, located by PARSING. A missing anchor raises by name —
    a harness that dies reads as 'not run', which is worse than one that fails."""
    text = _src(rel)
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"function {name!r} not found in {rel}")


def _code_only(src):
    """The EXECUTABLE text of a function — docstring and comment lines stripped.

    A table name inside a comment is documentation (this module's own history names the two feeds that
    died); a table name in CODE is the hand-wiring these rules forbid. Testing the raw source would
    force the fix to be undocumented, which is the wrong trade, so the no-literals rules below read
    code only."""
    out = []
    for line in src.splitlines():
        body = line.split("#", 1)[0] if "#" in line else line
        out.append(body)
    text = "\n".join(out)
    # Drop the function's docstring (the first string expression in its body).
    try:
        node = ast.parse(src).body[0]
        doc = ast.get_docstring(node, clean=False)
        if doc:
            text = text.replace(doc, "")
    except Exception:
        pass
    return text


WATCHED = {w["table"]: w for w in LIN.watched_feeds()}
ALL_INGEST = set(LIN.all_ingest_tables())

print("\n§A  every registered feed is WATCHED or EXPLICITLY EXCUSED — an omission does not compile")
gaps = sorted(t for t in ALL_INGEST
              if t not in WATCHED and t not in LIN.NOT_WATCHED_REASONS)
ok("A1 no registered ingest table is unaccounted for", not gaps,
   f"unaccounted: {gaps} — add to DATA_DATE_COLUMN_BY_TABLE (watch it) or NOT_WATCHED_REASONS (say why)")
ok("A2 the watched set is non-empty and covers most of the registry",
   len(WATCHED) >= 20, f"watched={len(WATCHED)} of {len(ALL_INGEST)} registered")
double = sorted(set(WATCHED) & set(LIN.NOT_WATCHED_REASONS))
ok("A3 no table is both watched and excused", not double, f"both: {double}")
ok("A4 every excuse carries a REASON, never a bare flag",
   all(isinstance(v, str) and len(v.strip()) >= 15 for v in LIN.NOT_WATCHED_REASONS.values()),
   "an exclusion with no stated reason is an omission wearing a hat")
ok("A5 every excused table is actually registered (no excuse for a table nobody ingests)",
   all(t in ALL_INGEST for t in LIN.NOT_WATCHED_REASONS),
   f"stray: {sorted(set(LIN.NOT_WATCHED_REASONS) - ALL_INGEST)}")
# The two feeds whose silence caused this work. If either ever leaves the watched set, this goes red.
ok("A6 the compensation report is watched (silent since 2026-08-06 because it was not)",
   "raw_comp_report" in WATCHED)
ok("A7 the asset lending ledger is watched (silent since 2026-09-23 because it was not)",
   "asset_ledger" in WATCHED)
ok("A8 the live sales feed is watched", LIN.freshness_source("sales") in WATCHED)

print("\n§B  a MONTHLY archive is NEVER watched — that is the false alarm, twice over")
ok("B1 raw_sales is recognised as a monthly archive", LIN.is_monthly_archive("raw_sales"))
ok("B2 daily_sales_feed is NOT a monthly archive", not LIN.is_monthly_archive("daily_sales_feed"))
archived = sorted(t for t in WATCHED if LIN.is_monthly_archive(t))
ok("B3 no monthly archive is in the watched set", not archived,
   f"watching {archived} reads 'stale' at every month close while the live feed is current")
ok("B4 every monthly archive is excused BY NAME, so its exclusion is deliberate",
   all(pair[1] in LIN.NOT_WATCHED_REASONS for pair in LIN.LIVE_VS_MONTHLY_PAIRS.values()))
ok("B5 each live/monthly pair names two DIFFERENT tables",
   all(pair[0] != pair[1] for pair in LIN.LIVE_VS_MONTHLY_PAIRS.values()))
for item, pair in LIN.LIVE_VS_MONTHLY_PAIRS.items():
    eq(f"B6 freshness_source({item!r}) is the LIVE side", LIN.freshness_source(item), pair[0])

print("\n§C  cadence and the data-date column are DECLARED for every watched feed")
ok("C1 every watched table has a data-date entry (None counts — it is a declaration)",
   all(t in LIN.DATA_DATE_COLUMN_BY_TABLE for t in WATCHED),
   f"undeclared: {sorted(t for t in WATCHED if t not in LIN.DATA_DATE_COLUMN_BY_TABLE)}")
ok("C2 every cadence is a positive whole number of days",
   all(isinstance(w["cadence_days"], int) and w["cadence_days"] >= 1 for w in WATCHED.values()))
eq("C3 the default cadence is daily — an unconsidered feed is watched keenly, not ignored",
   LIN.DEFAULT_FEED_CADENCE, LIN.CADENCE_DAILY)
eq("C4 a feed with no declared cadence gets the default",
   LIN.feed_cadence_days("raw_comp_report"), LIN.CADENCE_DAILY)
eq("C5 a monthly snapshot is declared monthly, so two quiet days are not an alarm",
   LIN.feed_cadence_days("raw_mi"), LIN.CADENCE_MONTHLY)
ok("C6 cadence is declared per TABLE, so a slow feed cannot inherit a fast one's alarm",
   LIN.feed_cadence_days("raw_mi") > LIN.feed_cadence_days(LIN.freshness_source("sales")))
eq("C7 the data-date column is the DATA's day, not the arrival stamp (comp report)",
   LIN.data_date_column("raw_comp_report"), "begin_date")
eq("C8 …and for the asset ledger", LIN.data_date_column("asset_ledger"), "acquired_date")
eq("C9 …and the two MA feeds carry tx_date, which the old duty map got wrong",
   (LIN.data_date_column("raw_ma_commission"), LIN.data_date_column("raw_ma_daily_tx")),
   ("tx_date", "tx_date"))
ok("C10 a period-keyed snapshot declares None rather than borrowing an arrival column",
   LIN.data_date_column("raw_mi") is None)
ok("C11 data_date_column and freshness_column answer DIFFERENT questions for the live feed",
   LIN.data_date_column("daily_sales_feed") != LIN.freshness_column("daily_sales_feed"))
ok("C12 an unregistered table yields None, never a guessed column",
   LIN.data_date_column("some_table_nobody_registered") is None)

print("\n§D  the callers DEREFERENCE the registry — no second copy of any of these facts")
REPORT = _func_src(ROUTER, "_data_freshness_report")
ok("D1 the monitor derives its feed set from watched_feeds()",
   "_lineage.watched_feeds()" in REPORT,
   "a hand-written list here is the defect this whole harness exists to stop")
ok("D2 …and reads each feed's cadence rather than a literal threshold",
   "cadence_days" in REPORT and "_lineage.DEFAULT_FEED_CADENCE" in REPORT)
ok("D3 …and judges lateness against that cadence, not a fixed 2 days",
   "cad + 1" in REPORT)
ok("D4 …and takes each feed's label from the registry",
   "_lineage.feed_label(" in REPORT)
ok("D5 …and does not re-spell a raw table name in its CODE",
   "raw_comp_report" not in _code_only(REPORT),
   "naming one feed here means the next one gets forgotten again")
DUTY = _func_src(ROUTER, "_duty_last_loaded")
ok("D6 the upload duty reads the data-date column from the registry",
   "_lineage.data_date_column(" in DUTY)
ok("D7 …and falls back to the registry's ARRIVAL column, not a literal",
   "_lineage.freshness_column(" in DUTY and "'created_at'" not in DUTY)
ROUTER_SRC = _src(ROUTER)
ok("D8 the router's own copy of the data-date map is GONE",
   "_DUTY_DATE_COL = {" not in ROUTER_SRC,
   "this dict was copy #3 of the fact, and it had already drifted")
ok("D9 …and nothing in the router re-declares a data-date map under another name",
   ROUTER_SRC.count("DATA_DATE_COLUMN_BY_TABLE = ") == 0)

print("\n§E  every feed is NAMEABLE to a human — a line nobody can read is a line nobody acts on")
ok("E1 every watched feed has a label", all(LIN.feed_label(t) for t in WATCHED))
ok("E2 no label is left as the bare table identifier",
   all(LIN.feed_label(t) != t for t in WATCHED),
   f"bare: {sorted(t for t in WATCHED if LIN.feed_label(t) == t)}")
ok("E3 no label still carries a raw_ / table_ prefix",
   not any(LIN.feed_label(t).startswith(("raw_", "pos_builtin_")) for t in WATCHED))
eq("E4 an unnamed table derives a readable label rather than failing",
   LIN.feed_label("raw_brand_new_thing"), "Brand new thing")
ok("E5 a schema-qualified table loses the schema in its label",
   "." not in LIN.feed_label("storeops.some_new_feed"))
ok("E6 labels name OUR tables and reports — no carrier, tenant or product branch (RULE TWO)",
   not any(w in " ".join(LIN.FEED_LABEL_BY_TABLE.values()).lower()
           for w in ("boost", "luxelink", "vzone", "vidapay", "epay", "att", "t-mobile")),
   "the carrier-vocabulary guard reads this file too")

print("\n§F  THE UN-WIRING LOCK — the next change cannot quietly restore the patchwork")
REG_SRC = _src(REGISTRY)
ok("F1 the registry still exposes the four accessors the callers depend on",
   all(f"def {n}(" in REG_SRC for n in
       ("watched_feeds", "data_date_column", "feed_cadence_days", "feed_label")))
ok("F2 watched_feeds() is DERIVED from INGEST_TABLES_BY_MODULE, not a hand-written tuple",
   "INGEST_TABLES_BY_MODULE" in _func_src(REGISTRY, "watched_feeds"),
   "a literal list here would fall behind the registry exactly as the monitor's list of three did")
ok("F3 …and it honours NOT_WATCHED_REASONS, so an exclusion is the only way out",
   "NOT_WATCHED_REASONS" in _func_src(REGISTRY, "watched_feeds"))
_report_code = _code_only(REPORT)
ok("F4 the monitor's CODE names no feed table of its own",
   not any(t in _report_code for t in
           ("raw_payment_detail", "raw_dlar_rep", "asset_ledger", "raw_mi", "raw_comp_report")),
   "the two custom-import report KEYS it still names are report_keys, not feed tables")
ok("F4b …and the stripper really does remove comments (so F4 cannot pass vacuously)",
   "raw_comp_report" in REPORT and "raw_comp_report" not in _report_code)
# The pure accessors must stay pure: the registry is imported everywhere, including into the ingest
# path, so a DB or network call in it would be a latency bug in every lander.
reg_tree = ast.parse(REG_SRC)
imports = {a.name.split(".")[0] for n in ast.walk(reg_tree) if isinstance(n, ast.Import) for a in n.names}
imports |= {(n.module or "").split(".")[0] for n in ast.walk(reg_tree) if isinstance(n, ast.ImportFrom)}
ok("F5 the registry imports nothing heavy — no client, no network, no DB",
   not (imports & {"supabase", "requests", "httpx", "app", "pandas", "psycopg2"}),
   f"imports={sorted(i for i in imports if i)}")
ok("F6 watched_feeds() is deterministic — same answer twice",
   LIN.watched_feeds() == LIN.watched_feeds())
ok("F7 every watched entry carries all four keys the caller reads",
   all(set(w) == {"table", "module", "data_date_column", "cadence_days"} for w in WATCHED.values()))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
