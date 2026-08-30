"""Guard: the data-lineage registry, the SQL seed, and the code that reads sources stay in agreement.

WHY (owner 2026-08-30). The freshness banner read the MONTHLY table (raw_sales) instead of the LIVE
feed (daily_sales_feed) and cried "stale since 8-09" while the numbers were current. The fact "which
table is the live sales feed" lived only in a human's head. This guard makes the map enforce itself so
that class of "read the wrong table / duplicate a feed / miss a consumer" can't merge again:

  A. STRUCTURE — every edge row in 925_data_lineage_seed.sql has the full 11 columns, seq values are
     unique, and no (source_key → affected_key) edge is declared twice.  → "don't duplicate."
  B. COVERAGE — every raw ingest table the Python registry knows (data_lineage_registry.RAW_INGEST_TABLES)
     appears as an `ingest` edge in the seed.  → a new feed can't land undocumented; "don't miss."
  C. SYNC — the registry's canonical sales tables are present in the seed (registry ↔ SQL in step).
  D. FRESHNESS INVARIANT (the exact 2026-08-30 regression) — the sales-feed freshness code dereferences
     the registry (data_lineage_registry.freshness_source) and does NOT hardcode a raw table name, and
     the freshness report routes sales through that helper rather than reading raw_sales directly.

No DB, no network. Run:  cd backend && python3 harness_data_lineage_guard.py
Exit 0 = all green; exit 1 = a drift the owner asked us to catch before it ships.
"""
import os
import re
import sys

sys.path.insert(0, ".")

from app.modules.commcalc import data_lineage_registry as reg   # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEED = os.path.normpath(os.path.join(_HERE, "..", "database", "migrations", "925_data_lineage_seed.sql"))
_ROUTER = os.path.join(_HERE, "app", "modules", "commcalc", "router.py")

# Column order of the INSERT in 925_data_lineage_seed.sql.
_COLS = ["source_key", "source_label", "entry_point", "affected_key", "affected_label",
         "surface", "kind", "auto_updated", "effect_code", "effect_english", "seq"]

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {msg}")
    else:
        FAIL += 1
        print(f"  ✗ {msg}")


def _strip_line_comments(sql: str) -> str:
    # Drop whole-line SQL comments (-- ...). The seed's between-row comments contain parens like
    # "(raw capture)" that would confuse a paren-depth tokenizer; field-internal text is never on a
    # comment line, so this is safe.
    return "\n".join(ln for ln in sql.splitlines() if not ln.lstrip().startswith("--"))


def _parse_values(sql: str):
    """Return each VALUES row as a list of field tokens (surrounding quotes stripped, '' unescaped).
    Splits on top-level commas, respecting single-quoted strings and the '' escape."""
    body = _strip_line_comments(sql)
    lo = body.lower()
    i = lo.index("values", lo.index("insert into"))
    body = body[i + len("values"):]
    if "notify pgrst" in body.lower():
        body = body[:body.lower().index("notify pgrst")]
    rows, field, fields = [], [], []
    depth, in_q = 0, False
    k = 0
    while k < len(body):
        c = body[k]
        if in_q:
            if c == "'":
                if k + 1 < len(body) and body[k + 1] == "'":  # '' -> literal '
                    field.append("'"); k += 2; continue
                in_q = False; k += 1; continue
            field.append(c); k += 1; continue
        if c == "'":
            in_q = True; k += 1; continue
        if c == "(":
            depth += 1
            if depth == 1:
                field, fields = [], []
            else:
                field.append(c)
            k += 1; continue
        if c == ")":
            depth -= 1
            if depth == 0:
                fields.append("".join(field).strip())
                rows.append(fields)
            else:
                field.append(c)
            k += 1; continue
        if c == "," and depth == 1:
            fields.append("".join(field).strip()); field = []
            k += 1; continue
        field.append(c); k += 1
    return rows


def _clean(tok: str) -> str:
    t = tok.strip()
    if len(t) >= 2 and t[0] == "'" and t[-1] == "'":
        t = t[1:-1]
    return t


def _func_body(src: str, name: str) -> str:
    m = re.search(r"\ndef " + re.escape(name) + r"\b", src)
    if not m:
        return ""
    start = m.start()
    nxt = re.search(r"\ndef ", src[start + 1:])
    return src[start: start + 1 + nxt.start()] if nxt else src[start:]


def main():
    print("A. seed structure (data_lineage 925)")
    seed = open(_SEED, encoding="utf-8").read()
    rows = _parse_values(seed)
    ok(len(rows) > 0, f"parsed {len(rows)} lineage edges from the seed")
    ok(all(len(r) == len(_COLS) for r in rows),
       f"every edge has all {len(_COLS)} columns "
       f"(bad: {[i for i, r in enumerate(rows) if len(r) != len(_COLS)]})")
    seqs = [_clean(r[10]) for r in rows if len(r) == len(_COLS)]
    ok(len(seqs) == len(set(seqs)), "seq values are unique (no two edges share a seq)")
    edges = [(_clean(r[0]), _clean(r[3])) for r in rows if len(r) == len(_COLS)]
    dupes = sorted({e for e in edges if edges.count(e) > 1})
    ok(not dupes, f"no duplicate (source → affected) edge  (dupes: {dupes})")

    print("B. coverage — every registered external-feed ingest table has an ingest edge (per module)")
    ingest_targets = {_clean(r[3]) for r in rows if len(r) == len(_COLS) and _clean(r[6]) == "ingest"}
    for module, tables in reg.INGEST_TABLES_BY_MODULE.items():
        for t in tables:
            ok(t in ingest_targets, f"[{module}] '{t}' has an ingest edge in the seed")

    print("C. live-vs-monthly invariant — freshness reads the LIVE side of every pair")
    all_keys = {_clean(r[3]) for r in rows if len(r) == len(_COLS)}
    for item, (live, monthly) in reg.LIVE_VS_MONTHLY_PAIRS.items():
        ok(reg.freshness_source(item) == live,
           f"freshness_source('{item}') resolves to the LIVE table '{live}', not '{monthly}'")
        ok(live in reg.all_ingest_tables(),
           f"live table '{live}' of pair '{item}' is a registered ingest table")
    ok(reg.LIVE_SALES_FEED in all_keys, f"live feed '{reg.LIVE_SALES_FEED}' appears in the seed")
    ok(reg.MONTHLY_SALES in all_keys, f"monthly table '{reg.MONTHLY_SALES}' appears in the seed")

    print("D. freshness invariant — code reads the LIVE feed via the registry (2026-08-30 regression)")
    src = open(_ROUTER, encoding="utf-8").read()
    fb = _func_body(src, "_sales_feed_freshness")
    ok(bool(fb), "_sales_feed_freshness exists")
    ok("_lineage.freshness_source(" in fb,
       "_sales_feed_freshness dereferences the registry (freshness_source), not a hardcoded table")
    ok('"daily_sales_feed"' not in fb and "'daily_sales_feed'" not in fb,
       "_sales_feed_freshness does NOT hardcode the live-feed table name")
    ok("_lineage.MONTHLY_SALES" in fb,
       "_sales_feed_freshness uses the registry's MONTHLY_SALES for the empty-feed fallback")
    rb = _func_body(src, "_data_freshness_report")
    ok("_sales_feed_freshness(" in rb and '"raw_sales"' not in rb,
       "_data_freshness_report routes sales through _sales_feed_freshness, not raw_sales directly")

    print("E. freshness-COLUMN invariant — daily_sales_feed probes uploaded_at, not created_at")
    ok(reg.freshness_column("daily_sales_feed") == "uploaded_at",
       "freshness_column('daily_sales_feed') is 'uploaded_at' (not created_at)")
    ok(reg.freshness_column("raw_sales") == "created_at",
       "freshness_column defaults to 'created_at' for a table with no override")
    # Regression guard for the feed-only-tenant empty-P&L bug (dcb0807): account/autocompute must probe
    # daily_sales_feed on uploaded_at BEFORE created_at, so a feed-only tenant's books auto-compute.
    ac_path = os.path.join(_HERE, "app", "modules", "account", "autocompute.py")
    ac = open(ac_path, encoding="utf-8").read()
    m = re.search(r'"daily_sales_feed"\s*,\s*\[([^\]]*)\]', ac)
    cand = m.group(1) if m else ""
    ok(bool(m) and "uploaded_at" in cand
       and (cand.index('"uploaded_at"') < cand.index('"created_at"') if '"created_at"' in cand else True),
       "account/autocompute _PERIOD_SOURCES probes daily_sales_feed on uploaded_at first")

    print("F. module census — every module is accounted for (feed-owning XOR feed-less)")
    feed_owning = set(reg.INGEST_TABLES_BY_MODULE)
    feed_less = set(reg.MODULES_WITHOUT_EXTERNAL_FEEDS)
    overlap = sorted(feed_owning & feed_less)
    ok(not overlap, f"no module is both feed-owning and feed-less (overlap: {overlap})")
    # The 21 backend modules under backend/app/modules — every one is in exactly one bucket.
    mods_dir = os.path.join(_HERE, "app", "modules")
    all_mods = {d for d in os.listdir(mods_dir)
                if os.path.isdir(os.path.join(mods_dir, d)) and not d.startswith("__")}
    # commcalc owns the registry itself; it is feed-owning. Everything else must be classified.
    unclassified = sorted(all_mods - feed_owning - feed_less)
    ok(not unclassified, f"every backend module is classified in the registry (unclassified: {unclassified})")

    print(f"\n{'PASS' if FAIL == 0 else 'FAIL'}: {PASS} checks passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
