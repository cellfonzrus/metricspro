#!/usr/bin/env python3
"""PROOF — the PA-market employee roster carries the b2b feed's OWN bytes, and nothing invented.

WHAT THIS GUARDS (owner directive 2026-09-10): "create the users from PA Market using the same names
as in b2b reports … this way the usernames will be same and no mapping needed". NAME FIDELITY IS THE
DELIVERABLE. A near-miss name — an initial for a full name, a re-ordering to "First Last", a
title-case, a stripped trailing space — recreates the very name-mapping problem the roster exists to
remove. So the roster is not allowed to be hand-typed prose: this harness parses it OUT of
`database/migrations/1001_pa_market_employee_roster.sql` and checks every value against feed rows.

It cannot pass against a roster that migration does not contain (same convention as
harness_dm_checklist_carrier_review.py).

THE PROPERTIES ASSERTED (never a moment, never a count pinned for its own sake):
  A. Every roster `name` / `epay_salesperson` / `epay_login` is byte-identical to a value that
     occurs in the b2b feed fixture — no strip, no case fold, no re-ordering.
  B. `name` == `epay_salesperson` for every row (the join key IS the display name; no derivation
     step exists to drift).
  C. Every roster home_store resolves to market PA through the REAL canonical resolver
     (app.core.scope.build_market_index / build_store_market_lookup — §13a), and a store that
     resolves elsewhere or fails closed is rejected. Nothing here re-derives market resolution.
  D. Every home_store that IS set is the store carrying a MAJORITY (>= 80%) of that person's PA feed
     rows; a person without such a store carries NULL, never a filled-in guess.
  E. NO PAY FIGURE. The migration writes pay_rate explicitly NULL and names no numeric literal in
     any money column — the `pay_rate NUMERIC DEFAULT 0` trap (an omitted column lands 0.00, which
     reads as "earns nothing") must stay closed.
  F. The excluded rows stay excluded: the system account, the already-on-roster person, and the
     two-login pairs are named in the file as NOT-CREATED and appear in no INSERT.

DB-FREE by construction: `_harness_dbfree.install()` before any app import, and the only app code
used is `app.core.scope`'s PURE index/lookup builders fed with fixture rows.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _harness_dbfree  # noqa: E402

_harness_dbfree.install(None)

from app.core.scope import build_market_index, build_store_market_lookup  # noqa: E402

REPO = os.path.dirname(HERE)
SQL_PATH = os.path.join(REPO, "database", "migrations",
                        "1001_pa_market_employee_roster.sql")

# ── FIXTURES ──────────────────────────────────────────────────────────────────────────────────
# The two market vocabularies + aliases for the house org, PA rows only, as they stand 2026-09-10.
# (Shape only — this harness never reads a database.)
STORE_ROWS = [
    {"store_code": "B-1710",  "address": None,                       "market": "PA"},
    {"store_code": "B-2701",  "address": None,                       "market": "PA"},
    {"store_code": "B-2778",  "address": None,                       "market": "PA"},
    {"store_code": "B-3605",  "address": None,                       "market": "PA"},
    {"store_code": "B-5619",  "address": None,                       "market": "PA"},
    {"store_code": "B-60TH",  "address": "1 S 60th St, Philadelphia", "market": "PA"},
    {"store_code": "B-6149",  "address": None,                       "market": "PA"},
    {"store_code": "B-6507",  "address": None,                       "market": "PA"},
    {"store_code": "B-723",   "address": None,                       "market": "PA"},
    # a NON-PA store, so "resolves to PA" is a real discriminator and not vacuous
    {"store_code": "B-1800",  "address": "1800 Great Neck rd",       "market": "LI"},
]
MAPPING_ROWS = [
    {"store_code": "B-6149", "store_address": "6149 Woodland Ave",      "market": "PA"},
    {"store_code": "B-6507", "store_address": "6507 Castor Avenue",     "market": "PA"},
    {"store_code": "B-723",  "store_address": "723 N Market St",        "market": "PA"},
    {"store_code": "B-2701", "store_address": "2701 Germantown ave",    "market": "PA"},
    {"store_code": "B-5619", "store_address": "5619 N. Broad St.",      "market": "PA"},
    {"store_code": "B-1710", "store_address": "1710 W 4th St",          "market": "PA"},
    {"store_code": "B-1598", "store_address": "1598 Mount Ephraim Ave", "market": "PA"},
    {"store_code": "B-1",    "store_address": "1 S 60th street",        "market": "PA"},
    {"store_code": "B-2778", "store_address": "B-2778",                 "market": "PA"},
    {"store_code": "B-3605", "store_address": "3605 Germantown Ave",    "market": "PA"},
    {"store_code": "B-1800", "store_address": "1800 Great Neck rd",     "market": "LI"},
]
ALIAS_ROWS = [
    {"alias": "2778 Mount Ephraim Ave", "store_code": "B-1598"},
    {"alias": "2778 Ephraim Ave",       "store_code": "B-1598"},
    {"alias": "1800 Great Neck rd",     "store_code": "B-1800"},
]

# The b2b feed as it actually reads: (salesperson, user_login, store spelling, PA rows).
# Counts are the observed commcalc.daily_sales_feed + raw_sales totals for the house org.
FEED_ROWS = [
    ("Arora, Tanish",          "Tanish",       "5619 N. Broad St.",  16755),
    ("Ranganath, Ranganath",   "Ranganath",    "6149 Woodland Ave",  13025),
    ("Paul, Rikita",           "Rikita",       "6507 Castor Avenue", 11017),
    ("Rani, Nisha",            "Nisha",        "2701 Germantown ave", 10435),
    ("Jaladhi, Akhila",        "Akhila",       "1 S 60th street",     9248),
    ("Jaladhi, Akhila",        "Akhila",       "1710 W 4th St",       1174),
    ("chowdary, Thanvi",       "Thanvi",       "723 N Market St",     9376),
    ("Saul, Mark",             "M.saul",       "2778 Ephraim Ave",    8184),
    ("Saul, Mark",             "M.saul",       "3605 Germantown Ave",   54),
    ("Taneeru, Mona",          "Mona",         "1710 W 4th St",       1556),
    ("Taneeru, Mona",          "Mona",         "723 N Market St",      180),
    ("Reddy, Nithin",          "Nithin",       "3605 Germantown Ave", 1615),
    ("Reddy, Nithin",          "Nithin",       "2778 Ephraim Ave",      16),
    ("onteru, satish",         "satish",       "1 S 60th street",      124),
    ("onteru, satish",         "satish",       "6149 Woodland Ave",    104),
    ("onteru, satish",         "satish",       "6507 Castor Avenue",    88),
    ("onteru, satish",         "satish",       "3605 Germantown Ave",   46),
    ("onteru, satish",         "satish",       "5619 N. Broad St.",     34),
]
# Named in the migration as deliberately NOT created.
MUST_NOT_APPEAR = [
    "Admin, backoffice",       # system / back-office account
    "Escobar, Angelica",       # already on the roster as E232
    "Rehman, Abdur",           # second POS login of a possible single human
    "Sri Addagarla, Teja",     # ditto
    "Tiwari, Sumit", "rogtao, Claron", "Nath, Dipanjan", "Reddy, Manoj",
    "Rahman, Shaf", "Chatterjee, Apurba", "Namir, Md",   # 2024 leavers
]
HOME_STORE_MAJORITY = 0.80

FAILURES = []
CHECKS = [0]


def check(cond, msg):
    CHECKS[0] += 1
    if not cond:
        FAILURES.append(msg)


def _read_sql():
    if not os.path.exists(SQL_PATH):
        raise SystemExit(f"FAIL: anchor missing — {SQL_PATH} (the migration this harness proves)")
    with open(SQL_PATH, encoding="utf-8") as fh:
        return fh.read()


def _uncommented(sql):
    """Only the lines that actually EXECUTE. Section B and the not-created notes are commented out
    and must not be mistaken for roster rows."""
    return "\n".join(ln for ln in sql.splitlines() if not ln.lstrip().startswith("--"))


_VALUES_RE = re.compile(
    r"\(\s*'([0-9a-f-]{36})'::uuid\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*(?:'([^']*)'|NULL)\s*\)")


def parse_roster(sql):
    """The roster, parsed out of the migration's live VALUES tuples.
    (org_id, name, epay_login, home_store) — home_store None when the SQL says NULL."""
    live = _uncommented(sql)
    rows = [(m.group(1), m.group(2), m.group(3), m.group(4))
            for m in _VALUES_RE.finditer(live)]
    if not rows:
        raise SystemExit("FAIL: no roster VALUES tuples found in the executing part of "
                         f"{os.path.basename(SQL_PATH)} — the anchor this harness reads is gone.")
    return rows


def insert_columns_and_exprs(live):
    """(columns, select expressions) of the roster INSERT, positionally aligned.

    Fails loudly and BY NAME if the anchor moves, per the harness rules — a parser that quietly
    returns nothing would score a missing check as a pass."""
    i = live.find("INSERT INTO storeops.employees")
    if i < 0:
        raise SystemExit("FAIL: anchor gone — no `INSERT INTO storeops.employees` in the executing "
                         f"part of {os.path.basename(SQL_PATH)}")
    stmt = live[i:]
    stmt = stmt[:stmt.find("FROM (VALUES")] if "FROM (VALUES" in stmt else stmt
    m = re.search(r"\((.*?)\)\s*SELECT\s+(.*)", stmt, re.S)
    if not m:
        raise SystemExit("FAIL: could not read the INSERT column list / SELECT list — the shape "
                         "this harness reads has changed.")
    cols = [c.strip() for c in m.group(1).split(",") if c.strip()]
    # split the SELECT list on top-level commas only (a quoted note contains none, but be exact)
    exprs, depth, cur, quoted = [], 0, "", False
    for ch in m.group(2):
        if ch == "'":
            quoted = not quoted
        if not quoted:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                exprs.append(cur.strip())
                cur = ""
                continue
        cur += ch
    if cur.strip():
        exprs.append(cur.strip())
    if len(cols) != len(exprs):
        raise SystemExit(f"FAIL: INSERT column/expression count mismatch ({len(cols)} vs "
                         f"{len(exprs)}) — the statement cannot be checked positionally.")
    return cols, exprs


def main():
    sql = _read_sql()
    roster = parse_roster(sql)
    live = _uncommented(sql)

    feed_names = {n for n, _, _, _ in FEED_ROWS}
    feed_logins = {u for _, u, _, _ in FEED_ROWS}
    per_person = {}
    for n, _u, store, cnt in FEED_ROWS:
        per_person.setdefault(n, {})
        per_person[n][store] = per_person[n].get(store, 0) + cnt

    idx = build_market_index(STORE_ROWS, MAPPING_ROWS, ALIAS_ROWS)
    resolve, markets = build_store_market_lookup(idx)

    # sanity: the resolver fixture is discriminating, not a rubber stamp
    check(resolve("1800 Great Neck rd") == "LI",
          "resolver fixture is not discriminating — an LI store resolved "
          f"{resolve('1800 Great Neck rd')!r}, so 'resolves to PA' proves nothing")
    check("PA" in markets, "PA is not in the canonical market vocabulary built from the fixture")

    org_ids = {r[0] for r in roster}
    check(org_ids == {"00000000-0000-0000-0000-000000000001"},
          f"roster is not scoped to exactly one org: {sorted(org_ids)}")

    seen = set()
    for _org, name, login, home in roster:
        # A — verbatim, byte for byte
        known = name in feed_names
        check(known,
              f"roster name {name!r} does not occur in the b2b feed byte-for-byte "
              f"(nearest by casefold: "
              f"{sorted(f for f in feed_names if f.lower() == name.lower()) or 'none'})")
        if not known:
            # Never crash on the very row that is wrong — report it and keep checking the rest.
            # (A KeyError here would read as "the harness is broken", not "the roster is.")
            seen.add(name)
            continue
        check(login in feed_logins,
              f"roster epay_login {login!r} does not occur in the b2b feed byte-for-byte")
        check(name == name.strip() and login == login.strip(),
              f"roster value carries leading/trailing whitespace: {name!r} / {login!r}")
        # the pairing must be the feed's pairing, not a re-pairing
        check(any(n == name and u == login for n, u, _, _ in FEED_ROWS),
              f"roster pairs {name!r} with login {login!r}, which the feed never pairs")
        # B — no derivation step between the display name and the join key
        check(f"'{name}'," in live,
              f"{name!r} is not written verbatim in the executing SQL")
        # C/D — home store
        if home is None:
            top = max(per_person[name].values()) / sum(per_person[name].values())
            check(top < HOME_STORE_MAJORITY,
                  f"{name!r} has a clear home store ({top:.0%} of PA rows) but the roster left it "
                  "NULL — a known fact must not be dropped")
        else:
            check(resolve(home) == "PA",
                  f"roster home_store {home!r} for {name!r} resolves to "
                  f"{resolve(home)!r}, not PA (fail-closed '' means ambiguous)")
            share_store, share = max(per_person[name].items(), key=lambda kv: kv[1])
            frac = share / sum(per_person[name].values())
            check(frac >= HOME_STORE_MAJORITY,
                  f"roster gives {name!r} home_store {home!r} but no store holds "
                  f"{HOME_STORE_MAJORITY:.0%} of their PA rows (top {share_store!r} {frac:.0%}) — "
                  "an assumed home store")
            check(resolve(share_store) == "PA" and resolve(home) == resolve(share_store),
                  f"roster home_store {home!r} for {name!r} is not the market of the store they "
                  f"actually work ({share_store!r})")
        check(name not in seen, f"{name!r} appears twice in the roster")
        seen.add(name)

    # E — NO PAY FIGURE. Checked POSITIONALLY: the money column's own slot in the SELECT list must
    #     be the literal NULL. Proximity to the word "pay_rate" proves nothing — a figure typed into
    #     the SELECT list sits nowhere near the column name (this harness's own first version missed
    #     exactly that, found by self-test).
    cols, exprs = insert_columns_and_exprs(live)
    for money_col in ("pay_rate", "pay_amount"):
        check(money_col in cols,
              f"the INSERT no longer names {money_col} — the `pay_rate NUMERIC DEFAULT 0` trap is "
              "open (an omitted pay_rate lands 0.00 and reads as 'this person earns nothing')")
        if money_col in cols:
            slot = exprs[cols.index(money_col)]
            check(slot.upper() == "NULL",
                  f"the INSERT writes {slot!r} into {money_col} — no pay figure may be invented; "
                  "it must be an explicit NULL")

    # F — the excluded stay excluded
    for bad in MUST_NOT_APPEAR:
        check(f"'{bad}'," not in live,
              f"{bad!r} is named as NOT-created in the migration but appears in an executing "
              "statement")
        check(bad in sql,
              f"{bad!r} is no longer explained anywhere in the migration — an exclusion without a "
              "stated reason is an oversight, not a decision")

    # the guard that makes a second run harmless
    check("NOT EXISTS" in live and "epay_salesperson = v.name" in live,
          "the INSERT lost its NOT EXISTS idempotency guard — a second run would duplicate people")
    check("BEGIN;" in live and "COMMIT;" in live,
          "the migration is no longer a single transaction")

    print(f"harness_pa_roster_names: {CHECKS[0]} checks, {len(FAILURES)} failure(s)")
    print(f"  roster parsed from {os.path.basename(SQL_PATH)}: {len(roster)} people, "
          f"{sum(1 for r in roster if r[3] is None)} with home_store NULL")
    for f in FAILURES:
        print(f"  FAIL: {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
