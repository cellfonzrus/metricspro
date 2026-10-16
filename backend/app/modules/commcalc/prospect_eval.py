"""Store-takeover evaluation — a P&L for a store we do NOT own yet (index §COMMCALC-03).

Owner, 2026-10-12, verbatim: *"need to make a module for evaluating the store sales for the store we
need to take over, but without creating the actual stores but upload the ma comm, max tx and handset
prdering data, import the expenses from our average expenses, keep the rent as what the actual rent
is for the new store and calculate the p&l also need to fillter out the family plans with more than 2
lines from these reports for each month to assess if fraud is going on and cjhecking which phones are
sold on those, create a sub report in that menu to take out those phones from profitability or leave
them. thisis platform wide but situation at hand is for total, nothing hardocded and everything should
be clear with analysis"*.

══ WHY A PROSPECT IS NOT A STORE ══════════════════════════════════════════════════════════════════

The obvious implementation — create the store, upload its feeds, read the P&L — is the one thing this
module exists to avoid. A `store_mapping` row is reachable by EVERY live surface: the consolidated
P&L, the GP report, the expense matrix, the commission engine, payroll, the dashboards. A store we
are only *considering* would therefore move the company's real books, its real commission payouts and
its real targets, and would keep doing so after we walked away from the deal.

So a prospect is a row in `commcalc.store_prospect` — and its uploaded feed rows land in
`commcalc.prospect_feed_row`, which NO live reader reads. The evaluation is computed on demand and
stored nowhere a statement can find it. Nothing here writes a `raw_*` table, a store row or an
`account_statements` snapshot.

══ ONE FACT, ONE HOME — WHAT THIS MODULE DOES NOT DECIDE ══════════════════════════════════════════

An evaluation whose arithmetic disagrees with the company's own P&L is worse than no evaluation: it
would price a deal on numbers nobody can reconcile afterwards. So not one booking rule is re-derived
here. Every fact is DEREFERENCED from the home that already owns it, and `FEED_REGISTRY` names which:

| the question | the one home this module reads |
|---|---|
| which P&L line does MA commission money book to, with what sign | `account/ma_store_pnl.ma_commission_bookings` |
| which P&L line does MA daily-TX money book to (residual / MDF / month spiff) | `account/ma_store_pnl.ma_tx_bookings` |
| where does a rebate dollar present (contra-COGS vs income) | `account/ma_store_pnl.rebate_route` |
| how is an uploaded sheet read and mapped into those columns | `commcalc/column_mapping` (`load_rules`/`default_mapping`/`map_records`) |
| what did a handset cost | `account/device_cogs.ma_unit_price_from_rows` |
| what did the devices activated in the month cost | `account/device_cogs.ma_sold_cost_from_rows` |
| which P&L line does an expense row book to | `account/coa.route_expense_line` |
| what is the chart of accounts | `account/coa.PL_SPEC` / `PL_SECTION` |
| how is a statement assembled from an inputs dict | `account/engine._assemble` |
| what were a month's effective store expenses | `commcalc/expenses_effective.effective_series` |
| how is a period spelled | `commcalc/expenses_effective.period_sort_key` |

`harness_prospect_eval.py` fails the build if any of those stops being dereferenced (§D), so the
copy-and-drift this module could so easily become cannot come back quietly.

══ RULE TWO — EVERY RULE IS A DECLARED CONFIG ROW ═════════════════════════════════════════════════

No carrier, tenant or product name appears below. The four decisions a tenant could reasonably make
differently are config, with house defaults that are MEASURED rather than invented (see each key):

  · `prospect_account_key_columns`   which column carries "which subscriber account is this line on"
  · `prospect_family_line_threshold` more than how many lines on one account is worth a look
  · `prospect_expense_basis_months`  how many complete months the expense average is taken over
  · `prospect_rent_expense_names`    which expense line is the rent the prospect's own rent replaces

══ WHAT THIS EVALUATION CANNOT ANSWER, SAID OUT LOUD ══════════════════════════════════════════════

The three uploads are the DISTRIBUTOR's view of a store: what the carrier paid, what airtime margin
the store earned, what handsets it bought. They are not a point-of-sale feed, so retail device and
accessory REVENUE, bill-pay fee income and the store's own cash are simply not in them. A P&L that
booked those as `0.00` would read as a measurement of zero and would understate the prospect's gross
profit by whatever its retail counter actually takes.

So `coverage()` walks the whole chart and marks every line `measured`, `not_measured` (with the feed
that would answer it) or `not_applicable`. The evaluation's bottom line is labelled for what it is —
a CARRIER-SIDE contribution, not net income — and `not_measured` lines carry `None`, never `0.0`.
That is the house rule ("a column that cannot be answered is None, never 0") applied to a whole
statement.

DB-free. Proof: `backend/harness_prospect_eval.py`.
"""

from app.modules.commcalc.calculator import safe_float
from app.modules.commcalc.expenses_effective import period_sort_key
from app.modules.commcalc import product_mix as _pm
# THE activation classifier — what KIND of activation a sale line is (§6f/§19.31). Dereferenced,
# never re-derived: a prospect counting activations on its own rule would be a second answer to a
# question the platform already answers for every live store.
from app.modules.commcalc import line_class as _lc

# ── THE FEED REGISTRY ──────────────────────────────────────────────────────────────────────────────
# The declared set of reports a prospect can be evaluated from.
#
# ITS KEYS ARE THE PLATFORM'S OWN REPORT KEYS, not a second naming scheme — `column_mapping.TABLE_MAP`
# already answers "which table does this report land in", and `target_fields` / `load_rules` already
# answer "how is this report's sheet parsed". A prospect upload therefore goes through the SAME
# reader, the SAME mapping rules and the SAME feed-shape rules a live upload goes through, and
# diverges from it at exactly one point: the destination row. `live_table` below is DEREFERENCED from
# TABLE_MAP (`live_table_of`), never copied, so a report that is ever re-pointed at another table
# cannot leave a stale copy here.
#
# `live_table_of(key)` is the table whose COLUMN SHAPE a prospect row mirrors, which is what lets the
# booking one-homes read a prospect row and a live row identically with no translation layer to
# drift. `books_through` is documentation of record AND what §D of the harness checks is still
# imported.
FEED_REGISTRY = {
    "ma_commission": {
        "label": "MA Commission Details",
        "grain": "one row per activated line",
        "books_through": ("account.ma_store_pnl", "ma_commission_bookings"),
        "carries_account_identity": True,
        "carries_device": True,
        "answers": ("carrier commission, spiffs, device margin, fees, consumer financing, "
                    "device purchase rebate — and which lines sit on one subscriber account"),
        "period_scope": "month",
    },
    "ma_daily_tx": {
        "label": "MA Daily Transactions",
        "grain": "one row per airtime / top-up / order transaction",
        "books_through": ("account.ma_store_pnl", "ma_tx_bookings"),
        "carries_account_identity": False,
        "carries_device": False,
        "answers": "airtime merchant discount, residual, MDF market spiffs, month-n spiff cash",
        "period_scope": "month",
    },
    "ma_marketplace_orders": {
        "label": "MA Marketplace Handset Fulfillment Orders",
        "grain": "one row per handset order line",
        "books_through": ("account.device_cogs", "ma_unit_price_from_rows"),
        "carries_account_identity": False,
        "carries_device": True,
        "answers": "what each handset model cost — the price list the activated devices are costed at",
        # READ WHOLE, NEVER SLICED BY MONTH. A handset ordered in June and activated in July must
        # still be priced, exactly as `device_cogs.ma_unit_price_map` reads the live fulfilment sheet
        # across periods: this feed is a PRICE LIST as much as a purchase log.
        "period_scope": "all_periods",
        "period_scope_reason": ("read across every month — the fulfilment sheet is the price list the "
                                "activated devices are costed at, and a handset ordered in one month "
                                "is activated in another"),
    },
}
FEED_KEYS = tuple(FEED_REGISTRY)

# WHAT A STORE'S COLUMN IS CHARGED, in one sentence, in ONE place — the payload carries it and the
# screen prints it, so the reader of a column is never left to assume (owner directive 2026-10-13).
STORE_EXPENSE_BASIS = (
    "the FULL average monthly expense of one of our own stores, charged to this store in full — so "
    "the column answers whether this site could stand on its own. The expense and net-income rows "
    "therefore do not add across to the all-stores column; revenue, device cost and activations do.")


# ── WHAT A FILE ACTUALLY PUT IN THE TABLE ─────────────────────────────────────────────────────────
# OWNER REPORT 2026-10-13: "i just uploaded handset info monthwise and also all the others" — and the
# evaluation was blank. The upload had reported `rows_written: 66,388` and that read as success. What
# had actually landed was 66,388 rows of one feed (the other two were never uploaded at all), and of
# the rows that did land only 897 carried a store and 798 a price: the marketplace export lists twenty
# order types and carries a ship-to, a cost and a date on the handset orders ALONE.
#
# THE CLASS, not the instance. A count of rows written is not a measure of what a file answered. A
# row that mapped nothing but its own period columns occupies the table and answers no question, and
# a surface reporting only the total cannot tell a good file from a file read with the wrong layout.
# So every prospect upload reports its SUBSTANCE, and the screen shows it next to the total.
#
# Deliberately feed-AGNOSTIC: it asks only whether a mapped row carries any value at all outside the
# period columns, and those columns come from `data_lineage_registry.MONTH_COLUMNS` — the one home for
# "which columns hold a row's month" — never from a list kept here. No per-feed column knowledge, so
# it cannot drift from a feed's real shape, and it is right about a feed that does not exist yet.
# ── WHICH MONTH THE REPORT OPENS ON ───────────────────────────────────────────────────────────────
# OWNER REPORT 2026-10-13, same report as `substance` above: the handset months HAD been uploaded
# (March to September 2026) and the evaluation still showed nothing.
#
# THE CLASS: "which month does this prospect cover" was derived from ONE feed. The default period was
# the latest month the COMMISSION feed named, and with no commission rows it fell through to today's
# month — a month no uploaded feed covers — so every uploaded row was filtered out of the read and
# the screen was blank on data that was sitting in the table. A prospect with only two of the three
# feeds is the ordinary case while a pack is being assembled, and that is exactly when the report
# must still show what it has.
#
# The rule is now: the latest month ANY of the prospect's feeds names. Commission still LEADS where
# it has rows, because it is the feed the P&L's revenue comes from and opening on a month with money
# in it is the useful default; a month only the other feeds name is better than a month nothing names.
# ── THE PROSPECT'S OWN STORES, FROM ITS OWN UPLOADS ───────────────────────────────────────────────
# OWNER ASK 2026-10-13, verbatim: *"it shoud automatically pick up the store names from teh uploaded
# sheets ... similarly the c P&l will also be displayed in columns based ont eh stores infor uploaed"*.
#
# NOTHING NEW RESOLVES A STORE HERE. `account/ma_store_pnl.account_store_index` is already THE pure
# answer to "which store is this processor account" — it reads the dealer's own fulfilment rows
# (account + ship-to on every order line) and DROPS an account seen at two different addresses rather
# than mis-attributing it, the phantom-store lesson the house already paid for. It is injected and
# dereferenced, so a prospect and a live store are resolved by one function.
#
# What is added is only what a prospect needs and a live store does not:
#   • the display NAME, which the fulfilment feed carries beside the address (`business_name`);
#   • the accounts that appear ONLY in the commission or transaction feed. Those have no address
#     because no handset was ever shipped to them, and dropping them would silently move their money
#     out of the per-store view. They are kept, labelled by their account number, and `has_address`
#     says which is which.
# A prospect's addresses are deliberately NOT collapsed onto our canonical store spellings: they are
# not our stores, and resolving them through `coa.store_resolver` is how a prospect would start
# appearing in the company's own rollups.
def store_index(feeds, account_store_index):
    """PURE: the prospect's stores → `{account: {account, address, name, has_address, feeds}}`.

    `account_store_index` is `ma_store_pnl.account_store_index` injected (the one home for
    account → store). `feeds` is the same `{feed_key: [rows]}` `evaluate` takes."""
    ful = list((feeds or {}).get("ma_marketplace_orders") or [])
    derived = account_store_index(ful) or {}
    names, out = {}, {}
    for r in ful:
        r = r or {}
        acct = str(r.get(_store_column("ma_marketplace_orders")) or "").strip()
        nm = str(r.get("business_name") or "").strip()
        if acct and nm and acct not in names:
            names[acct] = nm
    for feed_key in FEED_KEYS:
        col = _store_column(feed_key)
        if not col:
            continue
        for r in (feeds or {}).get(feed_key) or []:
            acct = str((r or {}).get(col) or "").strip()
            if not acct:
                continue
            row = out.setdefault(acct, {"account": acct, "address": derived.get(acct),
                                        "name": names.get(acct), "feeds": []})
            if feed_key not in row["feeds"]:
                row["feeds"].append(feed_key)
    for acct, row in out.items():
        row["has_address"] = bool(row["address"])
        row["label"] = row["address"] or f"account {acct}"
        row["feeds"] = [k for k in FEED_KEYS if k in row["feeds"]]
    return out


def rows_by_store(feeds, accounts=None):
    """PURE: `{account: {feed_key: [rows]}}` — each feed's rows split by its own store column.

    A row whose store column is blank is NOT dropped and NOT guessed onto a store: it lands under
    `UNATTRIBUTED_STORE`, so the per-store columns always sum back to the whole-prospect figures and
    a feed that names no store is visible as itself. That is the same posture the live per-store P&L
    takes with an account it cannot resolve (booked company-wide, never mis-attributed)."""
    want = set(accounts) if accounts is not None else None
    out = {}
    for feed_key in FEED_KEYS:
        col = _store_column(feed_key)
        for r in (feeds or {}).get(feed_key) or []:
            acct = str((r or {}).get(col) or "").strip() if col else ""
            key = acct or UNATTRIBUTED_STORE
            if want is not None and acct and acct not in want:
                key = UNATTRIBUTED_STORE
            out.setdefault(key, {k: [] for k in FEED_KEYS})[feed_key].append(r)
    return out


# A row the feed names no store on. A LABEL, never a store: it keeps the per-store columns summing to
# the whole, which is the only way a reader can tell "this store earned nothing" from "this money
# belongs to no store named in the file".
UNATTRIBUTED_STORE = "(no store named on the row)"


def month_has_ended(period, today=None):
    """PURE: has this month finished, as at `today`? THE one home for that comparison.

    `today` is the caller's date (this module reads no clock). With `today` omitted the answer is
    True for every parseable month — i.e. "no month counts as in progress" — which is exactly the
    behaviour every caller had before this fact was given a home, so an un-dated caller is
    byte-identical. An unparseable period has not "ended": it names no month at all.

    WHY IT IS A HOME AND NOT AN INLINE COMPARISON. Two separate places need it and they must not
    drift: `default_period` refuses to OPEN on a month that has not ended (§COMMCALC-03.17), and
    `expense_window` refuses to AVERAGE one (§COMMCALC-03.28) — a part-month's expense sheet drags
    every line down by however much of the month is left. One answer, one place, both dereference
    it; `harness_prospect_eval.py` §Y fails the build if a second copy of the comparison appears.
    """
    k = period_sort_key(period)
    if k == (0, 0):
        return False
    if today is None:
        return True
    return k < (today.year, today.month)


def default_period(periods_by_feed, leading_feed="ma_commission", today=None):
    """PURE: the month a prospect's report should open on, and why.

    `periods_by_feed` {feed_key: [period strings]} — whatever each feed's rows are stamped with.
    Returns `{period, basis, from_feed, months_available, in_progress}`; `period` is "" only when no
    feed names a single month, and `basis` always says which rule chose it so the screen can state it.

    A MONTH THAT HAS NOT ENDED IS NOT THE MONTH TO OPEN ON (owner report 2026-10-13). The rule was
    "the latest month the feed covers", and recency alone opened the report on its EMPTIEST month:
    on 13 October the prospect's commission feed held 3 October rows against 5,495 over eight
    months, so the statement, the per-store columns and the break-even all rendered off 0.05% of the
    data and read as broken. A part-month is a fact about the calendar, not a judgement about the
    data, so no threshold is guessed here: a month is skipped only when it has not finished yet.

    `today` is the caller's date (purity: this module reads no clock). Omitted, no month counts as in
    progress and the answer is byte-identical to the old rule — which is what keeps every existing
    caller and the §J checks unchanged. `in_progress` names the later, unfinished month that was
    skipped, so the screen can say it is there and the picker can still reach it."""
    by_feed = {}
    for k, ps in (periods_by_feed or {}).items():
        months = sorted({str(p).strip() for p in (ps or []) if str(p or "").strip()},
                        key=period_sort_key)
        if months:
            by_feed[k] = months
    every = sorted({m for ms in by_feed.values() for m in ms}, key=period_sort_key)
    if not every:
        return {"period": "", "basis": "no uploaded row names a month", "from_feed": None,
                "months_available": [], "in_progress": None}

    now = (today.year, today.month) if today is not None else None

    def _pick(months):
        """(month to open on, the later unfinished month it skipped). The latest month that has
        ENDED; the latest month outright when none has, because a report must still open.

        "Has ended" is `month_has_ended`, dereferenced — the SAME answer `expense_window` refuses to
        average an unfinished month by, never a second comparison of its own."""
        if now is None:
            return months[-1], None
        ended = [m for m in months if month_has_ended(m, today)]
        if ended:
            later = [m for m in months
                     if period_sort_key(m) != (0, 0) and not month_has_ended(m, today)]
            return ended[-1], (later[-1] if later else None)
        return months[-1], None

    if by_feed.get(leading_feed):
        period, skipped = _pick(by_feed[leading_feed])
        if skipped:
            basis = (f"the latest COMPLETE month the commission feed covers — {skipped} has started "
                     f"but is not finished, so opening on it would read a part-month as the whole")
        elif now is not None and period_sort_key(period) >= now:
            basis = ("the only month the commission feed covers is the current one, which is not "
                     "finished — these figures are a part-month")
        else:
            basis = "the latest month the commission feed covers"
        return {"period": period, "basis": basis, "from_feed": leading_feed,
                "months_available": every, "in_progress": skipped}

    period, skipped = _pick(every)
    src = next((k for k, ms in sorted(by_feed.items()) if ms and ms[-1] == period), None)
    tail = (f" {skipped} has started but is not finished, so it is not opened on." if skipped else "")
    return {"period": period,
            "basis": ("the latest complete month any uploaded feed covers — the commission feed has "
                      "no rows yet, so the P&L's revenue lines will read as not uploaded." + tail),
            "from_feed": src, "months_available": every, "in_progress": skipped}


def _row_period(row):
    """PURE: the month stamped on one feed row, as the grouping keys spell it."""
    return str((row or {}).get("period") or "").strip()


def substance(rows):
    """PURE: what a mapped file actually put in the table.

    `rows` are the mapped dicts, exactly as they are about to be written. Returns
    `{rows, empty, carrying_data, dated, undated}` where `empty` counts rows that carry nothing but
    their own period stamp — the shape a file read with the wrong column layout produces.

    `dated` counts rows that ended up with a period. It is NOT the same question as `empty`: a row can
    carry a date and nothing else, or carry money and no readable date, and both are worth saying."""
    from app.modules.commcalc.data_lineage_registry import MONTH_COLUMNS
    month = set(MONTH_COLUMNS)
    n = empty = dated = 0
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        n += 1
        if _row_period(r):
            dated += 1
        if not any(k not in month and v not in (None, "", 0, 0.0) for k, v in r.items()):
            empty += 1
    return {"rows": n, "empty": empty, "carrying_data": n - empty,
            "dated": dated, "undated": n - dated}


def substance_note(sub, feed_label):
    """PURE: the one sentence a screen shows when a file landed rows that answer nothing. None when
    every row carries data — an absence is reported, never a finding invented."""
    sub = sub or {}
    n, empty = int(sub.get("rows") or 0), int(sub.get("empty") or 0)
    if not n or not empty:
        return None
    if empty == n:
        return (f"Every one of the {n:,} rows read from this file carries nothing but a date. "
                f"That is what a file read with the wrong column layout looks like — check the "
                f"column mapping for '{feed_label}' before reading anything into the evaluation.")
    return (f"{empty:,} of the {n:,} rows read from this file carry nothing but a date, so "
            f"{n - empty:,} rows are what the evaluation can actually see.")


def _store_column(feed_key):
    """PURE: the column `feed_key`'s rows name the dealer account in — the registry's answer, through
    this module's own `live_table_of`, so neither the table nor the column is spelled twice."""
    from app.modules.commcalc.data_lineage_registry import store_account_column
    return store_account_column(live_table_of(feed_key))


def live_table_of(feed_key):
    """PURE: the live table whose column shape this prospect feed mirrors, read from the ONE home of
    that fact (`column_mapping.TABLE_MAP`). None when the key is not a prospect feed."""
    if feed_key not in FEED_REGISTRY:
        return None
    from app.modules.commcalc.column_mapping import TABLE_MAP
    return TABLE_MAP.get(feed_key)


def feed_registry_view():
    """PURE-ish: the registry as a screen reads it, with the live table dereferenced in."""
    out = {}
    for k, v in FEED_REGISTRY.items():
        row = {kk: vv for kk, vv in v.items() if kk != "books_through"}
        row["report_key"] = k
        row["live_table"] = live_table_of(k)
        out[k] = row
    return out

# ── CONFIG (RULE TWO) ─────────────────────────────────────────────────────────────────────────────
# WHICH COLUMN IS THE ACCOUNT. The MA commission sheet carries two candidate account columns and a
# tenant's portal populates whichever its platform emits, so this is a declared ORDERED LADDER read
# per row (first populated column wins) — never a single column name, and never a tenant branch.
#
# The house default order is MEASURED, not guessed. Live house platform, LuxeLink August 2026,
# `raw_ma_commission`, 1,248 rows:
#     `ban`              non-null on 27 rows      — present, but on 2% of the file
#     `bin`              non-null on 1,248 rows   — 1,031 distinct values, max 5 lines on one value
#     `activation_order` non-null on 1,248 rows   — 1,248 distinct: that is the LINE, not the account
# and every one of the 163 multi-line `bin` values resolved to ONE store on ONE day with consecutive
# activation orders, all of them `activation_type='Add'`. That is the signature of a multi-line
# account and nothing else. `ban` leads the ladder because it is the account number by name wherever
# a platform fills it; `bin` is what answers when it does not.
HOUSE_ACCOUNT_KEY_COLUMNS = ("ban", "bin")
# MORE THAN how many lines on one account in one month is worth looking at. The owner asked for "more
# than 2 lines": two lines on one account is an ordinary couple, so the threshold is EXCLUSIVE.
HOUSE_FAMILY_LINE_THRESHOLD = 2
# How many COMPLETE months the expense average is taken over.
HOUSE_EXPENSE_BASIS_MONTHS = 3
# WHICH expense line is rent. House default is the label the house expense matrix actually carries
# (live house org, every month since March 2026: 'Rent / Lease' on all 28 stores). Matched
# case-insensitively by exact label, so a tenant whose sheet says 'Rent' declares that instead.
HOUSE_RENT_EXPENSE_NAMES = ("rent / lease",)
# WHICH figure books: what a store that HAS the line pays, or what a TYPICAL store pays once the
# stores with no such line are counted. The full arithmetic and the two faults it had to survive are
# in `average_expense_matrix`. Both figures are always reported on every line; this only picks which
# one books. House default: what a store that has the line pays, because the store being taken over
# will pay rent, electric and salaries, and assuming otherwise understates the deal.
AVERAGE_BASES = ("all_stores", "stores_with_the_line")
HOUSE_AVERAGE_BASIS = "stores_with_the_line"
# WHAT HAPPENS TO A FLAGGED ACCOUNT'S MONEY. The owner's sub-report: "take out those phones from
# profitability or leave them". Both figures are always computed; this is only the default the screen
# opens on, so no number is ever hidden behind a toggle.
EXCLUSION_MODES = ("keep", "exclude")
HOUSE_EXCLUSION_MODE = "keep"

CONFIG_COLUMNS = (
    ("prospect_account_key_columns", "1077_store_prospect_eval.sql"),
    ("prospect_family_line_threshold", "1077_store_prospect_eval.sql"),
    ("prospect_expense_basis_months", "1077_store_prospect_eval.sql"),
    ("prospect_rent_expense_names", "1077_store_prospect_eval.sql"),
    ("prospect_expense_average_basis", "1077_store_prospect_eval.sql"),
    ("prospect_activation_rules", "1082_prospect_activation_rules.sql"),
)


def default_config():
    """PURE: the house defaults, as a resolved config dict."""
    return {
        "account_key_columns": list(HOUSE_ACCOUNT_KEY_COLUMNS),
        "family_line_threshold": HOUSE_FAMILY_LINE_THRESHOLD,
        "expense_basis_months": HOUSE_EXPENSE_BASIS_MONTHS,
        "rent_expense_names": list(HOUSE_RENT_EXPENSE_NAMES),
        "expense_average_basis": HOUSE_AVERAGE_BASIS,
        # The RAW declaration, not the resolved rules: `activation_rules(cfg)` resolves it through
        # `line_class` so the resolution has one implementation and this dict stays JSON-shaped.
        "activation_declaration": house_activation_declaration(),
    }


def _list_or_house(row, key, house):
    """PURE: a declared list, honouring an explicitly EMPTY declaration.

    `row.get(key) or house` is WRONG and has been the same bug twice in this repo (see
    add_a_line_one_home): an empty list is a real answer ("our file does not carry that") and `or`
    silently restores the house words. Presence of the key decides, never truthiness."""
    if not isinstance(row, dict) or key not in row or row[key] is None:
        return list(house)
    v = row[key]
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    return [p.strip() for p in s.split(",") if p.strip()] if s else []


def resolve_config(row):
    """PURE: a `commission_org_config` row (or None / a row missing every column) → resolved config.

    Any subset of columns (§4b.1): a column the migration has not reached falls back to its house
    default, so this module behaves identically before and after mig 1077 is applied."""
    row = row if isinstance(row, dict) else {}
    cfg = default_config()
    cfg["account_key_columns"] = [c.lower() for c in _list_or_house(
        row, "prospect_account_key_columns", HOUSE_ACCOUNT_KEY_COLUMNS)]
    cfg["rent_expense_names"] = [n.lower() for n in _list_or_house(
        row, "prospect_rent_expense_names", HOUSE_RENT_EXPENSE_NAMES)]
    thr = row.get("prospect_family_line_threshold")
    if thr is not None:
        try:
            cfg["family_line_threshold"] = max(0, int(thr))
        except Exception:
            pass
    mo = row.get("prospect_expense_basis_months")
    if mo is not None:
        try:
            cfg["expense_basis_months"] = max(1, int(mo))
        except Exception:
            pass
    basis = (row.get("prospect_expense_average_basis") or "").strip().lower()
    if basis in AVERAGE_BASES:
        cfg["expense_average_basis"] = basis
    # A DECLARED VOCABULARY REPLACES THE HOUSE ONE WHOLE, never merges into it: a half-merged token
    # map is how a tenant that deliberately drops a word gets it silently handed back (the same trap
    # `_list_or_house` exists for, one level up).
    decl = row.get("prospect_activation_rules")
    if isinstance(decl, dict) and decl:
        cfg["activation_declaration"] = decl
    return cfg


def load_config(client, org_id):
    """I/O: the org's resolved prospect config. NEVER raises — an unreadable row is house defaults."""
    try:
        from app.core import column_tolerant as _ct
        cols = tuple(c for c, _m in CONFIG_COLUMNS)
        sc = client.schema("commcalc")
        present = _ct.present_columns(lambda: sc.table("commission_org_config"),
                                      lambda q: q.eq("org_id", org_id), cols)
        if not present:
            return default_config()
        row = (sc.table("commission_org_config").select(",".join(present))
               .eq("org_id", org_id).limit(1).execute().data or [{}])[0]
        return resolve_config(row)
    except Exception:
        return default_config()


def config_columns_missing(present):
    """PURE: the config columns mig 1077 has not reached, as {column: migration}."""
    have = {str(c) for c in (present or ())}
    return {c: m for c, m in CONFIG_COLUMNS if c not in have}


# ── THE ACCOUNT IDENTITY, AND THE MULTI-LINE QUESTION ─────────────────────────────────────────────

# WHAT WAS SOLD, and whether a device was sold at all — one home each, for the same reason
# `_store_column` exists: a report that spells a feed column itself is a copy waiting to diverge.
# Measured on the live feed (5,495 rows, 2026-10-13): `sku` names the product on every row that has
# one, and `activation_type2` is the only column that declares 'byop' — 383 rows where no device was
# sold, every one of which reads 'Product Not Available' in `sku`.
PRODUCT_DESC_COLUMN = "sku"
NO_DEVICE_COLUMN = "activation_type2"
LINE_ID_COLUMN = "activation_order"       # measured 1,248 distinct over 1,248 rows — one per line
NO_ACCOUNT_KEY = "(no account identifier on the row)"


def resolve_account_key(row, columns):
    """PURE: (account_key, which_column) for one commission row, or (None, None).

    The ladder is read per ROW, not per file: a file that fills `ban` on some rows and `bin` on the
    rest resolves each row from the column that actually carries its account. The column is RETURNED
    alongside the value so the report can say which one answered — a figure whose identity column
    nobody can name is a figure nobody can audit."""
    if not isinstance(row, dict):
        return None, None
    for col in (columns or ()):
        v = row.get(col)
        v = "" if v is None else str(v).strip()
        if v:
            return v, col
    return None, None


def line_id(row):
    """PURE: the stable identity of one activated line, or None."""
    v = (row or {}).get(LINE_ID_COLUMN)
    v = "" if v is None else str(v).strip()
    return v or None

def account_groups(rows, columns):
    """PURE: commission rows → {(period, account_key): {...}} plus the rows that carry no account.

    Grouped WITHIN A MONTH, because the owner's question is per month ("for each month"): three lines
    added to one account across three separate months is a customer who grew their plan, not a
    multi-line sale. Returns:
        {"groups": {(period, key): {"period","account_key","account_column","lines":[...]}},
         "unidentified": [rows], "columns_used": {column: n}}
    """
    groups, unidentified, used = {}, [], {}
    for r in rows or []:
        key, col = resolve_account_key(r, columns)
        if not key:
            unidentified.append(r)
            continue
        used[col] = used.get(col, 0) + 1
        period = _row_period(r)
        g = groups.setdefault((period, key), {"period": period, "account_key": key,
                                              "account_column": col, "lines": []})
        g["lines"].append(r)
    return {"groups": groups, "unidentified": unidentified, "columns_used": used}


def device_keys_of(row):
    """PURE: the device sold on one line, as its IDENTITY keys (sku, imei).

    Named `device_keys_of`, not `device_of`: `line_class.device_of` is the ONE device CLASSIFIER
    ('tablet' | 'watch' | None) and that name is reserved for it by
    harness_exec_mtd_device_rates_lock. This answers a different question — WHICH handset is on the
    row, not what KIND it is — so it carries a different name rather than a second meaning for the
    same one. Nothing here classifies; a caller wanting the kind dereferences line_class.

    `sku` may be the feed's own 'not available' placeholder — reported verbatim rather than cleaned,
    because the placeholder is itself the finding when it lands on a flagged account."""
    r = row or {}
    return (str(r.get(PRODUCT_DESC_COLUMN) or "").strip(), str(r.get("imei") or "").strip())


def flag_multi_line_accounts(grouped, threshold, rules=None):
    """PURE: the accounts carrying MORE THAN `threshold` lines in one month, with their devices.

    This is a WHO-TO-LOOK-AT report, and the wording matters: nothing here concludes that fraud
    happened. A family of four buying four phones on one account on one day is a good sale and reads
    identically in this feed to the thing the owner is worried about. So every flagged account
    carries the evidence a human needs to tell them apart — the lines, the devices, the day span, the
    store account, how many are add-a-lines — and no verdict.

    Returns a list sorted by line count descending, then account key, so the output is stable.

    `rules` is the activation vocabulary; the add-a-line count is the ONE classifier's answer
    (`line_class.is_add_a_line`), not a token spelled here. This line used to read
    `activation_type == 'add'` — a bare value comparison on one column, which is the §19.18 shape
    twice over: it answered a question `line_class` already answers, and it would have read zero for
    any feed whose files spell an add-a-line anywhere else. Verified byte-identical on the live
    upload: 1,588 both ways."""
    thr = max(0, int(threshold or 0))
    rules = rules if rules is not None else activation_rules(None)
    out = []
    for (_period, _key), g in (grouped.get("groups") or {}).items():
        lines = g["lines"]
        if len(lines) <= thr:
            continue
        devices, days, stores, adds = {}, set(), set(), 0
        for r in lines:
            sku, imei = device_keys_of(r)
            label = sku or "(no model on the row)"
            d = devices.setdefault(label, {"sku": sku, "lines": 0, "imeis": []})
            d["lines"] += 1
            if imei:
                d["imeis"].append(imei)
            day = str((r or {}).get("tx_date") or "").strip()
            if day:
                days.add(day)
            # The store column is DEREFERENCED, not spelled. This line was a fourth hand-written
            # copy of the fact (found by §L of the lock the moment it was written, 2026-10-13).
            acct = str((r or {}).get(_store_column("ma_commission")) or "").strip()
            if acct:
                stores.add(acct)
            if _lc.is_add_a_line(r, rules):
                adds += 1
        out.append({
            "period": g["period"],
            "account_key": g["account_key"],
            "account_column": g["account_column"],
            "lines": len(lines),
            "over_threshold_by": len(lines) - thr,
            "add_a_lines": adds,
            "days": sorted(days),
            "same_day": len(days) == 1,
            "processor_accounts": sorted(stores),
            "same_processor_account": len(stores) == 1,
            "devices": sorted(devices.values(), key=lambda d: (-d["lines"], d["sku"])),
            "line_ids": sorted(x for x in (line_id(r) for r in lines) if x),
        })
    out.sort(key=lambda a: (-a["lines"], a["period"], a["account_key"]))
    return out


def flagged_line_ids(flagged):
    """PURE: the set of line ids on flagged accounts — what `exclude` drops."""
    out = set()
    for a in flagged or []:
        out.update(a.get("line_ids") or ())
    return out


def flagged_imeis(flagged):
    """PURE: the IMEIs of the handsets on flagged accounts — what `exclude` drops from device COST.

    A line is excluded by its line id; the handset it carried has to leave the COGS side too or the
    excluded revenue would be measured against a cost that stayed."""
    out = set()
    for a in flagged or []:
        for d in a.get("devices") or ():
            out.update(i for i in (d.get("imeis") or ()) if i)
    return out


def split_rows(rows, excluded_ids):
    """PURE: (kept, dropped) commission rows under an exclusion set."""
    ex = set(excluded_ids or ())
    kept, dropped = [], []
    for r in rows or []:
        (dropped if (line_id(r) in ex) else kept).append(r)
    return kept, dropped


def multi_line_summary(grouped, flagged, threshold):
    """PURE: the distribution a human reads before trusting the flag list.

    The shape of the whole file matters: "35 accounts carry more than 2 lines" means one thing when
    1,031 accounts carry one line and something else entirely when half the file is multi-line."""
    groups = (grouped.get("groups") or {})
    dist = {}
    for g in groups.values():
        n = len(g["lines"])
        dist[n] = dist.get(n, 0) + 1
    return {
        "threshold": max(0, int(threshold or 0)),
        "accounts": len(groups),
        "lines": sum(len(g["lines"]) for g in groups.values()),
        "lines_with_no_account": len(grouped.get("unidentified") or ()),
        "account_columns_used": dict(grouped.get("columns_used") or {}),
        "lines_per_account": {str(k): dist[k] for k in sorted(dist)},
        "flagged_accounts": len(flagged or ()),
        "flagged_lines": sum(a["lines"] for a in (flagged or ())),
        "max_lines_on_one_account": max(dist) if dist else 0,
    }


# ── EVERY MONTH, NOT JUST THE ONE ON SCREEN (owner ask 2026-10-13) ────────────────────────────────
# OWNER, VERBATIM: *"I need the accounts which are 2 more summarized at the bottom for each month with
# the drill down"*. The month report already answered this for ONE month, and the whole point of the
# question is the comparison a single month cannot make: an account carrying four lines in one month
# is a family; the same shape in five consecutive months is a pattern.
#
# NOT A SECOND COUNT. This reads the SAME three functions the month report reads — `account_groups`,
# `flag_multi_line_accounts`, `multi_line_summary` — and slices their output per period. It deliberately
# does NOT re-group or re-tally: a per-month counter of its own is exactly the duplicate derivation the
# index rules forbid, and it would drift from the figure on screen the first time either changed. §R1
# fails the build if the month on screen and this report's row for that month ever disagree.
#
# `period` is already on every group key and on every flagged account, because `account_groups` groups
# WITHIN a month (three lines added across three separate months is a customer who grew their plan, not
# a multi-line sale). So the per-month view needs no new identity — only the slice.
def multi_line_by_month(rows, columns, threshold, rules=None):
    """PURE: every uploaded month's multi-line picture, from the SAME grouping the month report uses.

    `rows` is the commission feed across EVERY period, not one month's. Returns:
        {"threshold", "months", "periods", "by_month": [summary + {"period"}], "totals": summary,
         "flagged": [accounts, every month]}

    `by_month` carries a row for EVERY month that has rows, including months with nothing flagged —
    reported as zero rather than omitted, because a month missing from a list reads as a month with no
    data, which is a different fact and the one this report must never fake."""
    grouped = account_groups(rows, columns)
    flagged = flag_multi_line_accounts(grouped, threshold, rules)
    groups = grouped.get("groups") or {}
    unident = grouped.get("unidentified") or []
    periods = sorted({str(k[0]) for k in groups} | {_row_period(r) for r in unident})
    by_month = []
    for p in periods:
        sub_groups = {k: g for k, g in groups.items() if str(k[0]) == p}
        sub_unident = [r for r in unident if _row_period(r) == p]
        used = {}
        for g in sub_groups.values():
            col = g.get("account_column")
            used[col] = used.get(col, 0) + len(g["lines"])
        sub = {"groups": sub_groups, "unidentified": sub_unident, "columns_used": used}
        row = multi_line_summary(sub, [a for a in flagged if str(a.get("period")) == p], threshold)
        row["period"] = p
        by_month.append(row)
    return {
        "threshold": max(0, int(threshold or 0)),
        "months": len(periods),
        "periods": periods,
        "by_month": by_month,
        "totals": multi_line_summary(grouped, flagged, threshold),
        "flagged": flagged,
    }

# ── WHAT KIND OF ACTIVATION, AND HOW MANY A MONTH (owner ask 2026-10-14) ─────────────────────────
# Owner, verbatim: *"the activations per month details is also missing which breaks up teh new acts
# upgrade tablet fios etc"*.
#
# THE DUPLICATE CHECK CAME FIRST, and it changed the design. The platform already owns "what kind of
# activation is this line" — `line_class` (§6f), whose classes are exactly the words the owner used:
# `activation` / `port` / `byod` / `upgrade`, with an add-a-line as a MODIFIER on one of them rather
# than a fifth class, and `new_activation_units` as the ONE derivation of the count the Sales report,
# Executive MTD, the Boost pay engine and the DLAR reconciliation all print (§19.31). A private
# New/Add tally here would have been a second answer to that question, so this counts through
# `line_class` and nothing else.
#
# WHAT THAT COST, MEASURED BEFORE DECIDING. The house classifier reads `contract_type`, which this
# feed does not have, so on the live upload it classified **0 of 5,495 rows** — every class would have
# read zero, which is the fake answer this report must never give. The feed carries the fact in two
# narrow columns instead, and their values are exact rather than prose:
#     `activation_type`   New 3,907 · Add 1,588                     (no other value)
#     `activation_type2`  branded 5,083 · byop 383 · blank 29        (no other value)
# So the vocabulary is DECLARED for this feed's two columns — fields and tokens as config, per RULE
# TWO — and the declaration was then measured against every row before it shipped: 5,112 activation +
# 383 byod = 5,495 exactly, 1,588 add-a-lines, 0 unplaced, 0 excluded. An 'Add' line can itself be
# byop, which is precisely why 'add' is declared as the add-a-line MODIFIER and not as a class: as a
# class it would have mis-called 166 bring-your-own lines.
#
# UPGRADE AND PORT READ ZERO, AND SAY SO. This feed records neither; `activations_by_month` therefore
# reports them as classes the feed carries no value for, never as a count of zero sales. A class
# absent from a list reads as a class nobody sells — a different fact, and the one the §COMMCALC-03.21
# breakup already refuses to fake.
ACTIVATION_RULES_FIELDS = ("activation_type", "activation_type2")
# Generic words, in the same character as `line_class.HOUSE_TOKENS` — no carrier, tenant or product
# name (RULE TWO). `port` / `upgrade` carry the house words so a feed that DOES spell them is placed.
HOUSE_ACTIVATION_TOKENS = {
    "activation": ["new", "add"],
    "byod": ["byop", "bring your own", "own device"],
    "port": ["port"],
    "upgrade": ["upgrade"],
}
HOUSE_ACTIVATION_ADD_A_LINE = ["add"]


def house_activation_declaration():
    """PURE: the house declaration for a takeover pack's commission sheet — the two columns that
    carry the fact and the generic words that name each class."""
    return {"fields": list(ACTIVATION_RULES_FIELDS),
            "tokens": {k: list(v) for k, v in HOUSE_ACTIVATION_TOKENS.items()},
            "add_a_line": list(HOUSE_ACTIVATION_ADD_A_LINE)}


def activation_rules(cfg=None):
    """PURE: the prospect feed's activation vocabulary, resolved by the ONE classifier.

    `cfg` is `resolve_config()`'s output (or None). A tenant whose takeover pack spells these columns
    differently declares its own in `prospect_activation_rules`; the classifier, the count and every
    class label still come from `line_class`."""
    raw = (cfg or {}).get("activation_declaration") if isinstance(cfg, dict) else None
    if not isinstance(raw, dict) or not raw:
        raw = house_activation_declaration()
    return _lc.resolve_rules(raw)


def _activation_txn_of(row):
    """The unit id one activated line belongs to — `LINE_ID_COLUMN`, dereferenced, so the count and
    the line list on the flagged-account report cannot be keyed differently."""
    return str((row or {}).get(LINE_ID_COLUMN) or "").strip()


def rows_by_period(rows):
    """PURE: feed rows → {month: [rows]}, keyed exactly as the grouping keys spell it.

    THE ONE HOME for "split this feed into its months". It dereferences `_row_period` (itself the one
    home for the month stamped on a row, §COMMCALC-03.20), so a per-month sub-report and the month
    report above it cannot disagree about which month a row is in."""
    out = {}
    for r in rows or []:
        out.setdefault(_row_period(r), []).append(r)
    return out


# ── WHICH OF A PROSPECT'S ROWS BELONG TO A MONTH — ONE HOME (§COMMCALC-03.28) ─────────────────────
# THE CLASS THAT WAS WRONG. "A prospect's rows for a month" was answered by a HAND-WRITTEN rule in
# the router — `if feed != "ma_marketplace_orders" and stamped not in wanted` — with the exception
# spelled as a feed name in code. That is two facts copied out of the registry: WHICH feeds are
# sliced by month, and HOW two spellings of one month compare. The month-over-month P&L needs the
# same answer for every month at once, and a second slicer would have drifted from the month report
# the first time either changed. So the fact lives on the registry (`period_scope`) and both the
# month report and the trend dereference THIS function; §Y of the harness fails the build if a
# caller starts spelling a feed key or a period comparison of its own again.

def feed_is_period_sliced(feed_key):
    """PURE: is this feed's contribution narrowed to the evaluated month?

    The registry's own `period_scope` answers it — "month" for the two money feeds, "all_periods"
    for the handset fulfilment sheet, which is a PRICE LIST and must be read whole (a handset
    ordered in June and activated in July still has to be costed). An unknown key is sliced, which
    is the safe answer: a feed nobody has declared must not leak another month's rows into a month.
    """
    return (FEED_REGISTRY.get(feed_key, {}) or {}).get("period_scope", "month") != "all_periods"


def feeds_for_months(every_month, months):
    """PURE: `{feed_key: [rows]}` for a set of months — THE one home for the month slice.

    `every_month` is `{feed_key: [rows]}` over the WHOLE upload, each row carrying its own `period`
    stamp (the router stamps it on from the feed row, because the month is a fact about the table —
    §COMMCALC-03.20). `months` are the month labels wanted, in ANY spelling.

    Both spellings of a month collapse into one slot through `period_sort_key`, this module's only
    period parser, so a file stamped `2026-04` and one stamped `April 2026` select the same rows —
    the row-level reason the old string-variant comparison was replaced rather than reused.
    A feed whose `period_scope` is not "month" is carried WHOLE, never sliced.
    `months` empty selects no month, which is not the same as every month: a caller that means the
    whole upload passes `every_month` itself.
    """
    wanted = {period_sort_key(m) for m in (months or ())} - {(0, 0)}
    out = {}
    for k in FEED_KEYS:
        rows = list((every_month or {}).get(k) or [])
        if not feed_is_period_sliced(k):
            out[k] = rows
            continue
        out[k] = [r for r in rows if period_sort_key(_row_period(r)) in wanted]
    return out


def uploaded_months(every_month):
    """PURE: every month this prospect has rows for, oldest first.

    Only the MONTH-SCOPED feeds answer (`feed_is_period_sliced`): the fulfilment sheet is a price
    list read whole, so a month that carries nothing but handset orders is not a month the P&L can
    be taken for. One home, so the month-over-month table and the month picker cannot disagree
    about which months exist."""
    seen = {}
    for k in FEED_KEYS:
        if not feed_is_period_sliced(k):
            continue
        for r in ((every_month or {}).get(k) or []):
            p = _row_period(r)
            key = period_sort_key(p)
            if key != (0, 0):
                seen.setdefault(key, p)
    return [seen[k] for k in sorted(seen)]


def range_basis(months, expense_months):
    """PURE: the one sentence a P&L over several months prints about its own expense side.

    A range carries several months of carrier revenue, so it carries the same number of months of
    running cost — our average is a MONTHLY figure and the only honest arithmetic is to charge it
    once per month in the range (`expense_bookings(..., months=N)`). Said in one place, carried on
    the payload, printed on the screen: a reader of a three-month bottom line is never left to work
    out whether one month's rent or three came out of it."""
    n = max(1, int(expense_months or 1))
    if n == 1:
        return ("one month — our average monthly store cost is charged once, exactly as the "
                "single-month statement charges it")
    return (f"{n} months ({', '.join(months or ())}) — carrier revenue and device cost are the "
            f"window's own rows, and our average monthly store cost is charged {n} times, once for "
            f"each month in the window. A seller's declared rent is a monthly figure and is charged "
            f"the same {n} times.")


def months_in_range(periods, start, end=None):
    """PURE: which of `periods` a MONTH or a DATE RANGE selects, oldest first (owner ask 2026-10-16).

    Owner, verbatim: *"a filter which gives the p&l on the righ tside for a particuklar monmth
    selected or a data range selected"*. The platform's shared filter bar already carries both
    shapes in one value — `period` alone is a month, `period` + `periodTo` is a range (RULE FIVE,
    §3d) — so this function takes the same two strings and answers in MONTHS, because a P&L line is
    a month-grained figure and no row here carries a day.

    THE SEMANTICS, stated so they can be read off the screen:
      · `start` only, spelled as a month (`2026-04` / `April 2026`) → THAT month alone.
      · `start` and `end` → every uploaded month from `start`'s month to `end`'s month INCLUSIVE.
        A range is inclusive of the months its two ends fall in even when the dates sit mid-month:
        a P&L cannot be cut at the 14th, and silently dropping a half-covered month would understate
        the window. The screen says the range covers whole months.
      · `end` only → every uploaded month up to and including `end`'s month.
      · neither → EVERY uploaded month. An absent filter does not narrow.
    A month the prospect has no rows for is never invented: the answer is always a subset of
    `periods`.
    """
    keyed = sorted({(period_sort_key(p), str(p)) for p in (periods or ())
                    if period_sort_key(p) != (0, 0)})
    lo, hi = period_sort_key(start), period_sort_key(end)
    out = []
    for k, p in keyed:
        if lo != (0, 0) and k < lo:
            continue
        if hi != (0, 0) and k > hi:
            continue
        if lo != (0, 0) and hi == (0, 0) and k != lo:
            continue
        out.append(p)
    return out


def _activation_slice(rows, rules):
    """PURE: one month's rows → the activation picture for them, counted through `line_class`."""
    units = _lc.activation_units(rows, rules, txn_of=_activation_txn_of)
    na = _lc.new_activation_units(rows, rules, txn_of=_activation_txn_of, units=units)
    aal = _lc.add_a_line_units(rows, rules, txn_field=LINE_ID_COLUMN)
    placed = sum(1 for u in units if u)
    # A VALUE THE DECLARATION DOES NOT NAME IS REPORTED BY NAME. Silence here is how a feed's new
    # spelling becomes a silently uncounted month; the screen shows the value so declaring it is a
    # config edit rather than an investigation.
    unplaced = {}
    for i, r in enumerate(rows or []):
        if units[i]:
            continue
        label = " · ".join(str((r or {}).get(f) or "").strip() or "(blank)"
                           for f in ACTIVATION_RULES_FIELDS)
        unplaced[label] = unplaced.get(label, 0) + 1
    return {
        "lines": len(rows or []),
        "classified": placed,
        "new_activations": na["count"],
        "by_class": dict(na["by_class"]),
        "add_a_lines": aal["transactions"],
        "add_a_line_configured": aal["configured"],
        "excluded": na["excluded_count"],
        "unclassified": len(rows or []) - placed,
        "unclassified_values": [{"value": v, "lines": c}
                                for v, c in sorted(unplaced.items(), key=lambda x: (-x[1], x[0]))],
    }


def activations_by_month(comm_rows, cfg=None):
    """PURE: the commission feed across EVERY month → the activations per month, broken up.

    Returns `{"months", "periods", "by_month": [...], "totals", "classes", "class_labels",
              "classes_absent", "reading"}`. Each `by_month` row carries the activation picture AND
    the product breakup for the same rows, so "how many" and "of what" are one row rather than two
    reports a reader has to line up by eye.

    `classes_absent` names the classes this feed carries NO value for — reported as unanswerable
    rather than as zero sales."""
    rules = activation_rules(cfg)
    groups = rows_by_period(comm_rows)
    periods = sorted(groups, key=period_sort_key)
    by_month = []
    for p in periods:
        rows = groups[p]
        row = {"period": p}
        row.update(_activation_slice(rows, rules))
        pb = products_sold(rows, cfg)
        row["products"] = {c["key"]: c["lines"] for c in pb["classes"]}
        row["product_labels"] = dict(pb["labels"])
        by_month.append(row)
    totals = {"period": "All months"}
    totals.update(_activation_slice(list(comm_rows or []), rules))
    tot_pb = products_sold(list(comm_rows or []), cfg)
    totals["products"] = {c["key"]: c["lines"] for c in tot_pb["classes"]}
    totals["product_labels"] = dict(tot_pb["labels"])
    seen = {c for m in by_month for c, n in (m["by_class"] or {}).items() if n}
    return {
        "months": len(periods),
        "periods": periods,
        "by_month": by_month,
        "totals": totals,
        "classes": list(_lc.ACTIVATION_TYPE_CLASSES),
        "class_labels": dict(_lc.CLASS_LABELS),
        "classes_absent": [c for c in _lc.ACTIVATION_TYPE_CLASSES if c not in seen],
        "new_activation_sentence": _lc.NEW_ACTIVATION_SENTENCE,
        "reading": ("Every line is classified by the same rule the live stores are counted under, so "
                    "a count here and a count on a store report mean the same thing. An add-a-line "
                    "is counted as the activation it is AND reported again in its own column, so the "
                    "two columns deliberately overlap. A class this feed carries no value for is "
                    "named as unanswerable, never shown as zero sales."),
    }


def products_by_month(comm_rows, cfg=None):
    """PURE: what was sold, per month — the §COMMCALC-03.21 breakup sliced by `rows_by_period`.

    The whole-upload row is the SAME `products_sold` the month report prints, so the month on screen
    is one row of this table rather than a second derivation of it."""
    groups = rows_by_period(comm_rows)
    periods = sorted(groups, key=period_sort_key)
    return {
        "months": len(periods),
        "periods": periods,
        "by_month": [dict(products_sold(groups[p], cfg), period=p) for p in periods],
        "totals": products_sold(list(comm_rows or []), cfg),
    }


# ── THE AVERAGE EXPENSE MATRIX ────────────────────────────────────────────────────────────────────

def complete_months(periods, today_period):
    """PURE: the periods strictly before `today_period`, newest first.

    A COMPLETE month only: the month in progress has a partial expense sheet and averaging it in
    would drag every line down by however much of the month is left."""
    cur = period_sort_key(today_period)
    keyed = [(period_sort_key(p), p) for p in (periods or [])]
    keyed = [(k, p) for k, p in keyed if k != (0, 0) and (cur == (0, 0) or k < cur)]
    seen, out = set(), []
    for k, p in sorted(keyed, key=lambda kp: kp[0], reverse=True):
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out


# ── THE WINDOW OUR AVERAGE IS TAKEN OVER — THE NEAREST MONTHS, NOT ONLY THE EARLIER ONES ─────────
# OWNER REPORT 2026-10-16, verbatim: *"also expenses only appear int eh sept statementr i was check
# ing apr thre are not expenses showing up fir any store"*. He was right, and the data was never
# missing: `commcalc.store_expenses` holds every month this organisation has ever recorded, and the
# evaluation simply could not see any of them from April.
#
# MEASURED, live, 2026-10-16, before the fix:
#   · this organisation's own expense sheet carries TWO months — July 2026 (304 rows, $225,080.58)
#     and August 2026 (288 rows, $276,162.69). Nothing earlier was ever entered.
#   · the prospect carries EIGHT months of uploads, March to October 2026.
#   · the window was `complete_months(...)` — the months STRICTLY BEFORE the evaluated one — so:
#         September / October 2026 → [August, July]   → expenses charged
#         August 2026              → [July]           → expenses charged
#         July, June, May, April, March 2026 → []     → NO EXPENSE ON ANY STORE, AND NO WORD SAID
#     which is exactly the five months he could not find an expense in, and exactly the one he could.
#
# THE CLASS, NOT THE INSTANCE. The general fact that was wrong is not "April has no expenses". It is
# that **our own average store cost was treated as a time series read backwards from the evaluated
# month, when it is a BENCHMARK** — §COMMCALC-03.6's "the full average monthly expense of one of our
# own stores". A benchmark has no month of its own: the question it answers is "what does a store of
# ours cost to run", and the best available measurement of that is the months we actually recorded,
# whichever side of the evaluated month they fall on. A window that can only look backwards answers
# the question for recent months and silently answers NOTHING for every month before our first
# expense sheet — and it would do so for every tenant, every prospect and every month, not just this
# one. So:
#   1. PRIOR MONTHS STILL LEAD, unchanged. Where enough complete months sit before the evaluated one
#      the window is byte-identical to what it was (§Z2), because the nearest-in-time measurement of
#      our cost base is the right one and recency is a tie-breaker worth keeping.
#   2. A SHORTFALL IS FILLED FROM THE NEAREST MONTHS AFTER IT, nearest first, and only months that
#      have ENDED — a part-month's sheet drags every line down by however much of the month is left,
#      which is the same reason prior months must be complete (`month_has_ended`, dereferenced).
#   3. THE DIRECTION IS REPORTED, NEVER IMPLIED. `direction` and `basis` say in words which months
#      were averaged and whether any of them come after the month on screen, and the screen prints
#      it — an expense figure measured after the fact is still a measurement, but the reader has to
#      be told, exactly as §COMMCALC-03.17 names a part-month rather than hiding it.
#   4. AN UNMEASURED EXPENSE SIDE SAYS SO INSTEAD OF BOOKING ZERO. `measured` is False with a reason
#      naming what is missing, so a prospect whose organisation has entered no expenses at all reads
#      as "not measured" rather than as a store with no costs — the second half of the defect, and
#      the more dangerous half, because a zero expense line makes every takeover look profitable.

def expense_window(periods, period, want, today=None):
    """PURE: which of OUR OWN expense months the average is taken over, and in which direction.

    `periods` every period `store_expenses` carries for the org (any spelling, duplicates fine).
    `period`  the month being evaluated.  `want` how many months the average is taken over
    (`expense_basis_months`, declared per org — RULE TWO).  `today` the caller's date; this module
    reads no clock, and with it omitted no month counts as unfinished (`month_has_ended`).

    Returns `{months, before, after, requested, direction, measured, basis}` — `months` oldest-last
    (newest first, the order `complete_months` returns and `average_expense_matrix` is indifferent
    to), `direction` one of `before` / `after` / `both` / `none`, and `basis` the one sentence the
    screen prints. `months` is always a subset of `periods`: a month we never recorded is never
    invented, and nothing here reads or scales a dollar.
    """
    want = max(1, int(want or 1))
    before = complete_months(periods, period)[:want]
    after = []
    if len(before) < want:
        keyed = sorted({(period_sort_key(p), str(p)) for p in (periods or ())
                        if period_sort_key(p) != (0, 0)})
        cur = period_sort_key(period)
        seen = {period_sort_key(m) for m in before}
        for k, p in keyed:                      # oldest first = NEAREST first, for months after
            if cur != (0, 0) and k <= cur:
                continue
            if k in seen or not month_has_ended(p, today):
                continue
            seen.add(k)
            after.append(p)
            if len(before) + len(after) >= want:
                break
    months = list(before) + list(after)
    direction = ("none" if not months else "before" if not after
                 else "after" if not before else "both")
    if not months:
        basis = ("our own stores' expenses have no month this average could be taken over — nothing "
                 "has been entered on the Expenses sheet, so the expense side of this evaluation is "
                 "NOT MEASURED rather than zero")
    elif direction == "before":
        basis = ("our own stores' average monthly cost over " + ", ".join(before)
                 + " — the complete months before this one")
    elif direction == "after":
        basis = ("our own stores' average monthly cost over " + ", ".join(after)
                 + " — we had entered no expenses before " + str(period)
                 + ", so the NEAREST months we did record are the benchmark; they fall AFTER the "
                   "month on screen")
    else:
        basis = ("our own stores' average monthly cost over " + ", ".join(months)
                 + " — only " + str(len(before)) + " complete month(s) sit before "
                 + str(period) + ", so the nearest later month(s) make up the window")
    return {"months": months, "before": list(before), "after": list(after),
            "requested": want, "direction": direction, "measured": bool(months),
            "basis": basis}


def average_expense_matrix(rows_by_period, basis=HOUSE_AVERAGE_BASIS):
    """PURE: {period: [store_expenses rows]} → the per-store monthly average of each expense line.

    `rows_by_period` is what `expenses_effective.effective_series` returns for the chosen months
    (its `rows`, carry-forward already applied by the one home) — so the average is taken over the
    SAME figures the Expenses sheet and the live P&L book, not over the raw table. That is the whole
    reason this reads through that module instead of querying `store_expenses` itself.

    ══ THE ARITHMETIC, AND THE TWO WAYS IT GOES WRONG IF YOU DO IT IN ONE STEP ══════════════════

    The naive average — Σ amount ÷ every (store, month) cell in the window — was the first draft of
    this function, and running it over the live house matrix for May/June/July 2026 showed it fails
    twice, both times by UNDERSTATING a cost on a takeover decision:

      · **A month whose sheet omits a line.** May 2026 records NO salary line at all (Dm Salary,
        Employee Salaries, Owner / Mgmt Salaries, Employee Commission: 0 of 25 stores). Dividing a
        two-month total by three months of cells read Employee Salaries as $2,371.17/month against a
        real $4,329.96 — a $1,958 hole in the single largest controllable cost.
      · **A month with a wider store roster than the line.** July 2026 has expense rows for 34 store
        codes while every line is recorded on 25 of them. A window-wide denominator therefore
        divided EVERY July line by 34, understating all seventeen of them by 26%.

    So the fold is per (line, MONTH) first and only then across months:

      1. per (line, month):  Σ amount ÷ the stores carrying the line THAT month  →  the monthly cost
         of that line to a store that has it. Immune to both faults above: a month that does not
         record the line contributes no month, and a month's wider roster cannot dilute a line it
         does not cover.
      2. across months:      the mean of step 1 over the months the line APPEARS in, never over the
         window — and `months_present` / `months_in_window` are reported per line so a line the
         window only half-covers is visible rather than quietly smoothed.
      3. the coverage:       `store_coverage` = the mean share of that month's stores carrying the
         line. 1.0 for rent and electric; 0.48 for a line only 12 of 25 stores carry.

    Then the declared basis picks which figure BOOKS:
      · 'stores_with_the_line' (house default) — step 2 as it stands: what a store that HAS this line
        pays. The right default for a takeover, because the store being taken over will pay rent,
        electric and salaries; assuming otherwise understates the deal.
      · 'all_stores' — step 2 × step 3: what a TYPICAL store pays, counting the stores with no such
        line. The right read for a sparse line (live house: Garbage / Waste $100.00 on 12 of 25
        stores ⇒ $48.00 typical).
    BOTH are always returned on every line, whichever books, so neither reading is hidden.
    """
    basis = basis if basis in AVERAGE_BASES else HOUSE_AVERAGE_BASIS
    months = [p for p in (rows_by_period or {})]
    stores_in_month, lines = {}, {}
    for period, rows in (rows_by_period or {}).items():
        for r in rows or []:
            store = str((r or {}).get("store_code") or "").strip()
            name = str((r or {}).get("expense_name") or "").strip()
            if not store or not name:
                continue
            stores_in_month.setdefault(period, set()).add(store)
            e = lines.setdefault(name.lower(), {
                "expense_name": name, "expense_type": (r or {}).get("expense_type"),
                "source_key": (r or {}).get("source_key") or None,
                "months": {}, "stores": set(), "total": 0.0})
            m = e["months"].setdefault(period, {"total": 0.0, "stores": set()})
            amt = safe_float(r.get("amount"))
            m["total"] = round(m["total"] + amt, 2)
            m["stores"].add(store)
            e["total"] = round(e["total"] + amt, 2)
            e["stores"].add(store)
    out = {}
    for key, e in lines.items():
        per_month, coverage_shares = [], []
        for period, m in e["months"].items():
            n = len(m["stores"])
            if not n:
                continue
            per_month.append(m["total"] / n)
            roster = len(stores_in_month.get(period) or ())
            if roster:
                coverage_shares.append(n / roster)
        if_carried = round(sum(per_month) / len(per_month), 2) if per_month else None
        share = (sum(coverage_shares) / len(coverage_shares)) if coverage_shares else None
        all_stores = (round(if_carried * share, 2)
                      if (if_carried is not None and share is not None) else if_carried)
        out[key] = {
            "expense_name": e["expense_name"],
            "expense_type": e["expense_type"],
            "source_key": e["source_key"],
            "total": round(e["total"], 2),
            "months_present": len(per_month),
            "months_in_window": len(months),
            "months_missing": sorted(p for p in months if p not in e["months"]),
            "stores": len(e["stores"]),
            "store_coverage": round(share, 4) if share is not None else None,
            "amount_if_carried": if_carried,
            "amount_all_stores": all_stores,
            "amount": if_carried if basis == "stores_with_the_line" else all_stores,
            "basis": basis,
            "origin": "average",
        }
    return out


def expense_window_notes(matrix, months):
    """PURE: what a reader must be told about the window the average came from.

    A line the window only half-covers, and a month that records fewer lines than the others, are
    both REPORTED rather than smoothed — the two faults `average_expense_matrix` was rewritten to
    survive are still facts about the source data, and a figure derived from a half-empty month is
    worth less than one derived from three full ones."""
    n = len(months or ())
    partial = [{"expense_name": v["expense_name"], "months_present": v["months_present"],
                "months_in_window": v["months_in_window"], "months_missing": v["months_missing"]}
               for v in (matrix or {}).values() if 0 < v["months_present"] < n]
    sparse = [{"expense_name": v["expense_name"], "store_coverage": v["store_coverage"],
               "amount_if_carried": v["amount_if_carried"],
               "amount_all_stores": v["amount_all_stores"]}
              for v in (matrix or {}).values()
              if v.get("store_coverage") is not None and v["store_coverage"] < 0.9]
    return {
        "months": list(months or ()),
        "months_in_window": n,
        "lines": len(matrix or {}),
        "lines_absent_from_some_months": sorted(partial, key=lambda x: x["expense_name"]),
        "lines_not_on_every_store": sorted(sparse, key=lambda x: x["store_coverage"]),
        "note": ("Each line is averaged per month over the stores that carry it, then across the "
                 "months it appears in — so a month whose sheet omits a line does not halve it, and "
                 "a month with a wider store roster does not dilute it. A line absent from some "
                 "months, or missing from some stores, is listed here rather than smoothed away."),
    }


def apply_rent_override(matrix, rent_names, actual_rent):
    """PURE: replace the averaged rent with the prospect's ACTUAL rent.

    The owner's one explicit substitution. Every other line is the average of what our own stores
    spend; rent is the one cost that is a fact about THIS address and is known before the deal.

    · A declared rent line present in the matrix is replaced in place, keeping its label, and carries
      `origin='actual'` with the average it displaced on `replaced_average` — so the screen can show
      how the prospect's rent compares with ours, which is usually the first thing anybody asks.
    · The rent is split across the declared names when there is more than one (a tenant who books
      rent and CAM separately), in proportion to their averages, so the total is the entered figure.
    · No rent entered → the averages stand and the line is marked `origin='average'`, never silently
      zeroed.
    · A declared name the matrix has never seen is still added, so a prospect's rent reaches the P&L
      even on a tenant whose own stores record no rent line at all.
    """
    names = [str(n).strip().lower() for n in (rent_names or ()) if str(n).strip()]
    out = {k: dict(v) for k, v in (matrix or {}).items()}
    if not names:
        return out, {"applied": False, "reason": "no rent expense line is declared",
                     "rent_lines": [], "entered": None, "replaced_average": None}
    rent = safe_float(actual_rent)
    present = [n for n in names if n in out]
    replaced = round(sum(safe_float(out[n].get("amount")) for n in present), 2)
    if not rent:
        return out, {"applied": False, "reason": "no actual rent entered for the prospect",
                     "rent_lines": present, "entered": None, "replaced_average": replaced}
    weights = {n: max(0.0, safe_float(out[n].get("amount"))) for n in present}
    total_w = round(sum(weights.values()), 2)
    targets = present or names
    allocated, i = 0.0, 0
    for n in targets:
        i += 1
        if total_w and present:
            share = round(rent * (weights[n] / total_w), 2)
        else:
            share = round(rent / len(targets), 2)
        if i == len(targets):                      # the last line absorbs the rounding remainder
            share = round(rent - allocated, 2)
        allocated = round(allocated + share, 2)
        cell = out.get(n) or {"expense_name": n.title(), "expense_type": None, "source_key": None,
                              "store_months": 0, "stores": 0, "store_months_all": 0,
                              "amount_if_carried": None, "amount_all_stores": None}
        cell = dict(cell)
        cell["replaced_average"] = cell.get("amount")
        cell["amount"] = share
        cell["origin"] = "actual"
        out[n] = cell
    return out, {"applied": True, "reason": None, "rent_lines": targets,
                 "entered": round(rent, 2), "replaced_average": replaced}


def expense_bookings(matrix, route_expense_line, months=1):
    """PURE: the averaged matrix → P&L bookings, routed by coa's own router.

    `route_expense_line` is passed in rather than imported so this stays DB-free and the harness can
    prove the prospect books an expense to the SAME line the live P&L books it to (§D). A manual
    line (no `source_key`) takes coa's historical default exactly as a hand-entered expense does.

    `months` is how many MONTHS of running cost this statement covers, and it is 1 for every
    single-month statement — the default, so every existing caller is byte-identical (§Z8). A P&L
    over a DATE RANGE (owner ask 2026-10-16) carries several months of carrier revenue, so it has to
    carry the same number of months of running cost or the range would read as pure profit; the
    matrix is a MONTHLY figure, so the only honest arithmetic is to charge it once per month in the
    range. It multiplies AFTER the rent override, so a seller's declared rent — also a monthly
    figure — is charged for the same months as everything else."""
    n = max(1, int(months or 1))
    out = []
    for _key, e in sorted((matrix or {}).items()):
        amt = safe_float(e.get("amount"))
        if not amt:
            continue
        line, fallback = route_expense_line(e.get("source_key"))
        out.append((line, round(amt * n, 2), e.get("expense_name") or fallback))
    return out


# ── WHAT THE SELLER DECLARES, PER STORE (owner ask 2026-10-14, §COMMCALC-03.26) ──────────────────
# Verbatim: *"add a line for accessories sales as declared by seller to include in gorss profit and
# then calculate the net income, the cell sould be able to fill in manually and include the rent cell
# also for each store, if the rent is declared then the suystem will automaticalluy subtract our avg
# rent from the expenses if the rent is nto declared then it will keep our average as a part of the
# expense or the bettwe rway is to create a cell with ouer rent and if it is diff then it can vbe
# user overwritten"*. The owner named both designs and chose the second, so the rent cell OPENS on
# our own average and an entered figure replaces it.
#
# TWO FACTS THE UPLOADED FEEDS CANNOT CARRY, and they are different kinds of unknowable:
#   · ACCESSORY SALES — no carrier feed records them, because the carrier does not sell the case.
#     Until now the report could only say what accessory revenue the site WOULD NEED
#     (`accessory_break_even`); a seller's declaration is the only measurement that exists.
#   · THIS SITE'S RENT — a fact about the address, known before the deal, where every other expense
#     line is the average of what OUR stores spend (§COMMCALC-03.6).
#
# THE DUPLICATE CHECK, and it changed the design twice:
#   · `apply_rent_override` ALREADY replaces an averaged rent with an entered one, splits it across
#     several declared rent lines and reports the average it displaced. So the per-store cell is that
#     SAME function applied per column — not a second substitution. It is applied ONCE to the
#     averaged matrix per column, NEVER chained onto an already-overridden matrix, because a second
#     pass would record the first override as the "average it replaced" and the screen would then
#     claim our stores pay the prospect's rent.
#   · ACCESSORY COST was NOT invented here. `coa` has carried `accessory_rev` and `accessory_cost`
#     lines and an `accessory_cogs_pct` since mig 611 — a per-org rate the owner himself set to 20%
#     on 2026-06-20 — so a declared sale books its cost at the ORG'S OWN declared rate, read through
#     that one home and injected. Booking the declaration as revenue with no cost would have shown a
#     gross profit the store has never earned, and inventing a second rate here would have been the
#     divergence the index rules forbid.
#
# WHAT ADDS ACROSS THE COLUMNS AND WHAT DOES NOT, kept exactly as §COMMCALC-03.19 set it: a declared
# accessory sale is the STORE'S OWN money, so it sums to the whole-prospect column (revenue ties, and
# §N holds that). Rent is an EXPENSE, and every column already carries one whole store's expense on
# purpose, so a per-store rent changes that column alone.
# THE TWO CELLS, named once. The endpoint, the table and the harness all read this tuple, so a third
# cell is added in one place or not at all.
DECLARED_INPUT_COLUMNS = ("declared_accessory_sales", "declared_rent_monthly")
DECLARED_ACCESSORY_LINE = "accessory_rev"       # = coa.PL_SPEC's revenue line, never a new line
DECLARED_ACCESSORY_COST_LINE = "accessory_cost"
DECLARED_ACCESSORY_LABEL = "Accessory sales declared by the seller"
DECLARED_ACCESSORY_COST_LABEL = "Accessory cost on the declared sales"
# WHY A DECLARATION IS NOT A MEASUREMENT, said on the screen rather than implied by a number that
# looks like every other number on the row.
DECLARED_BASIS = (
    "a figure the SELLER states, not one any uploaded feed measures - no carrier feed records an "
    "accessory sale. It is entered per store and per month, its cost is taken at the rate this "
    "organisation itself declares for accessories, and both halves are shown so the declaration can "
    "be argued with rather than trusted")
RENT_CELL_BASIS = (
    "the cell opens on the average monthly rent OUR OWN stores pay, which is what this evaluation "
    "charges a site that has declared nothing. Typing this address's real rent replaces that average "
    "for this store only - and the average it displaced stays on the row, so the two can be compared")


def store_declared_inputs(rows):
    """PURE: `prospect_store_input` rows -> {store_key: {accessory_sales, rent}}.

    A blank cell is None (NOT 0.00) and stays None: "the seller declared nothing" and "the seller
    declared zero accessory sales" are different claims, and only the second may book a zero. The
    last row wins for a repeated key, which the table's own unique constraint already prevents."""
    out = {}
    for r in rows or []:
        r = r or {}
        key = str(r.get("store_key") or "").strip()
        if not key:
            continue
        acc = r.get("declared_accessory_sales")
        rent = r.get("declared_rent_monthly")
        out[key] = {
            "accessory_sales": (round(safe_float(acc), 2) if acc is not None else None),
            "rent": (round(safe_float(rent), 2) if rent is not None else None),
        }
    return out


def declared_accessory_bookings(sales, cogs_pct):
    """PURE: a declared monthly accessory sale -> [(line, amount, label), ...] for `add_booking`.

    `cogs_pct` is the ORG'S `coa` accessory rate, injected (house 0.20, owner-set 2026-06-20) - this
    function holds no rate of its own. None/blank declaration books NOTHING; a declared 0.00 also
    books nothing, because a zero line and an absent line read identically on a statement and the
    difference is carried by `declared` on the payload instead."""
    amt = round(safe_float(sales), 2)
    if not amt:
        return []
    pct = safe_float(cogs_pct)
    pct = pct if 0 <= pct <= 1 else 0.0
    cost = round(amt * pct, 2)
    out = [(DECLARED_ACCESSORY_LINE, amt, DECLARED_ACCESSORY_LABEL)]
    if cost:
        out.append((DECLARED_ACCESSORY_COST_LINE, cost, DECLARED_ACCESSORY_COST_LABEL))
    return out


def declared_meta(sales, cogs_pct, rent, rent_meta=None):
    """PURE: what the screen prints beside the two cells - the declaration, its cost, the gross
    profit it adds, and whether each cell is a declaration or our average."""
    amt = round(safe_float(sales), 2) if sales is not None else None
    pct = safe_float(cogs_pct)
    pct = pct if 0 <= pct <= 1 else 0.0
    cost = round((amt or 0.0) * pct, 2)
    rm = rent_meta or {}
    return {
        "accessory_sales": amt,
        "accessory_declared": sales is not None,
        "accessory_cogs_pct": pct,
        "accessory_cost": (cost if amt else None),
        "accessory_gross_profit": (round(amt - cost, 2) if amt else None),
        "accessory_basis": DECLARED_BASIS,
        "rent_entered": (round(safe_float(rent), 2) if rent is not None else None),
        # BOTH HALVES, deliberately: the flag says a rent was DECLARED for this store, so it needs
        # the entered figure as well as a meta that applied it. Reading the meta alone let a column
        # that declared nothing inherit the whole-prospect override's `applied` and claim a
        # declaration it never carried.
        "rent_declared": bool(rent is not None and rm.get("applied")),
        "rent_reason": rm.get("reason"),
        "rent_lines": list(rm.get("rent_lines") or []),
        "rent_our_average": rm.get("replaced_average"),
        "rent_basis": RENT_CELL_BASIS,
    }


# ── THE MONTH-OF-LIFE LEGS, M1 AND M2-M12 (owner report 2026-10-14, §COMMCALC-03.27) ─────────────
# Verbatim: *"also i cant see the m2-m12 commision line"*. The money was never missing - $39,704.49
# of it in the live prospect's September, measured - but all of it sat inside ONE `carrier_comm` line
# with the legs only in its drill-down detail, so nothing on the report named them.
#
# IT MATTERS MORE HERE THAN ANYWHERE ELSE ON THE PLATFORM: M2-M12 is money owed on lines ALREADY SOLD
# before the takeover. It keeps arriving for a while after the keys change hands and then stops. A
# buyer who reads the total as recurring revenue is overpaying, and 97% of that September figure
# ($38,451.50) is trailing-leg money.
#
# NOTHING IS DERIVED HERE. `ma_store_pnl.ma_earned_month_ladder` and `ma_received_month_ladder` are
# the platform's two ladder one-homes; they are injected and called with the PROSPECT's rows and the
# PROSPECT's org config, exactly as every other booking home in this file is. `commission_legs` owns
# which rung is the first month and which is trailing, and `commission_ledger.LEG_LABELS` owns what
# the two buckets are CALLED - this function asks all three and adds nothing of its own.
def commission_leg_ladder(earned, received, m1_month=None, leg_labels=None, ladder_key=None,
                          ladder_sort_key=None):
    """PURE: the two injected ladders -> the rungs and the M1 / M2-M12 split, ready to render.

    `earned` / `received` are `ma_*_month_ladder` payloads. Both are always reported: the sheet says
    what was EARNED in the activation month, the cash rows say what was RECEIVED, and for a takeover
    the two answer different questions (what the book is worth vs what is landing now). A basis with
    no rows is reported as having none, never folded into the other.

    The unknown rung keeps its own name. A commission sheet that states no month-of-life cannot be
    read as a first month, and reading it as one is what would make a trailing book look fresh."""
    m1 = int(m1_month or 1)
    labels = dict(leg_labels or {})
    out = {}
    for basis, lad in (("earned", earned), ("received", received)):
        lad = lad or {}
        months = {}
        for k, v in (lad.get("months") or {}).items():
            amt = round(safe_float(v), 2)
            if amt:
                months[str(k)] = amt
        buckets = {"m1": 0.0, "trailing": 0.0, "unsplit": 0.0}
        for k, amt in months.items():
            try:
                n = int(k)
            except (TypeError, ValueError):
                buckets["unsplit"] = round(buckets["unsplit"] + amt, 2)
                continue
            key = "m1" if n == m1 else ("trailing" if n > m1 else "unsplit")
            buckets[key] = round(buckets[key] + amt, 2)
        rungs = sorted(months, key=ladder_sort_key) if ladder_sort_key else sorted(months)
        out[basis] = {
            "basis": lad.get("basis"),
            "basis_label": lad.get("basis_label"),
            "months": months,
            "rungs": rungs,
            "buckets": buckets,
            "bucket_labels": {k: labels.get(k, k) for k in ("m1", "trailing", "unsplit")},
            "total": round(sum(months.values()), 2),
            "measured": bool(months),
        }
    tot = out["received"]["total"] or out["earned"]["total"]
    trail = (out["received"]["buckets"]["trailing"] if out["received"]["measured"]
             else out["earned"]["buckets"]["trailing"])
    out["trailing_total"] = round(trail, 2)
    out["trailing_share"] = (round(trail / tot, 4) if tot else None)
    out["reading"] = (
        "M2-M12 is commission owed on lines that were already sold before the takeover. It keeps "
        "arriving for a while after the keys change hands and then stops, so it is not recurring "
        "revenue and a price built on the total overpays for it."
        if trail else
        "No trailing-leg commission is measured in this month, so every commission dollar here is "
        "first-month money on lines sold in the month itself.")
    return out


# ── THE STATEMENT ─────────────────────────────────────────────────────────────────────────────────

def empty_inputs(spec):
    """PURE: coa's `build_inputs` shape, zeroed — the contract `engine._assemble` reads."""
    return {k: {"by_store": {}, "company_wide": 0.0, "detail": {},
                "detail_by_store": {}, "detail_company_wide": {}} for k, *_ in spec}


def add_booking(inputs, key, amount, detail_label=None, accrue_detail=None):
    """PURE: coa's `add()` for a prospect — company-wide, because a prospect HAS no store key.

    The drill-down dollar is accrued by DEREFERENCING `coa.accrue_detail` (passed in as
    `accrue_detail`, the one home for that accumulation — index §19.32), never by a second copy of
    its arithmetic. This file once carried that copy; harness_pl_drill_detail §G5 failed the build
    for it, correctly, because the identity `Σ detail_by_store + detail_company_wide == detail`
    holds only while ONE `+=` maintains every grain. A prospect has no canonical store key — it is
    one store nobody owns yet — so `store_key` is None and the detail lands company-wide, which is
    the same branch of the same function the live statement takes for an unattributable dollar.

    The money itself uses the same incremental 2-dp rounding coa uses, so a prospect's line and the
    same rows booked through the live P&L agree to the cent rather than to within a rounding drift.
    Passing no `accrue_detail` books the amount and skips the drill grain rather than deriving it."""
    amt = round(safe_float(amount), 2)
    if not amt or key not in inputs:
        return
    inputs[key]["company_wide"] = round(inputs[key]["company_wide"] + amt, 2)
    if detail_label and accrue_detail is not None:
        accrue_detail(inputs[key], None, detail_label, amt)


# WHICH feed answers which P&L line — the coverage vocabulary. DERIVED from the booking one-homes
# wherever it can be (the harness §E re-runs each one-home over a probe row and fails if a line it
# books is missing from this map), and declared only for the lines no prospect feed reaches.
FEED_FOR_LINE = {
    "carrier_comm": ("ma_commission", "ma_daily_tx"),
    "ma_device_margin": ("ma_commission",),
    "fee_income": ("ma_commission",),
    "financing_income": ("ma_commission",),
    "device_rebate": ("ma_commission",),
    "rebate_income": ("ma_commission",),
    "ma_merchant_discount": ("ma_daily_tx",),
    "atu_income": ("ma_daily_tx",),
    "mi_income": ("ma_daily_tx",),
    "mdf_income": ("ma_daily_tx",),
    "device_cost": ("ma_commission", "ma_marketplace_orders"),
}
# The lines a POS feed would answer and these three uploads cannot. Named, with the feed that would,
# so the gap reads as a gap rather than as a zero.
NEEDS_POS_FEED = (
    "accessory_rev", "device_rev", "service_income", "epay_fee_income", "accessory_cost",
    "billpay_collected", "billpay_offset", "service_sales", "shipping_sales",
    "merchandise_sales", "commission_income", "vip_reimb", "vip_device_pay", "vip_fees",
    "chargebacks", "rep_comm",
)
POS_REASON = ("no point-of-sale feed for a store we do not operate — the three MA uploads are the "
              "distributor's view, not the register's")
EXPENSE_LINES_REASON = "from our own stores' average monthly expenses"


def coverage(spec, pl_section, booked_lines, expense_lines, feeds_present):
    """PURE: every chart line → how this evaluation answered it, or why it could not.

    The point of this function is that the evaluation cannot quietly understate the prospect. Three
    verdicts and nothing else:
      · `measured`     — a feed or the expense average booked it (the amount stands)
      · `not_measured` — nothing here can answer it, with the feed that would (amount is None)
      · `uploaded_feed_missing` — a feed that WOULD answer it was not uploaded (amount is None)
    """
    booked, exp = set(booked_lines or ()), set(expense_lines or ())
    have = {k for k in (feeds_present or ()) if k in FEED_REGISTRY}
    out = []
    for key, label, section, _kind, _grain in spec:
        if pl_section.get(key) != section:          # BS lines are not part of this read
            pass
        if key in booked or key in exp:
            verdict, reason, feeds = "measured", (
                EXPENSE_LINES_REASON if key in exp else None), FEED_FOR_LINE.get(key, ())
        elif key in FEED_FOR_LINE:
            want = FEED_FOR_LINE[key]
            if have & set(want):
                verdict, reason, feeds = "measured", "the uploaded feed carries no dollars on this line", want
            else:
                verdict, reason, feeds = ("uploaded_feed_missing",
                                          "needs " + " or ".join(FEED_REGISTRY[f]["label"] for f in want),
                                          want)
        elif key in NEEDS_POS_FEED:
            verdict, reason, feeds = "not_measured", POS_REASON, ()
        else:
            verdict, reason, feeds = "not_measured", "nothing in a takeover evaluation books this line", ()
        out.append({"key": key, "label": label, "section": section,
                    "verdict": verdict, "reason": reason, "feeds": list(feeds)})
    return out


# ── WHOSE RULES BOOKED THIS — the switches that move money, and who declared them ────────────────
# OWNER REPORT 2026-10-14, verbatim: *"gross profit shoudl includea ll incoming income like reisduakl
# merchant discoutn ayto pay , all spiff etc, recheck and fix"*.
#
# He was right, and the cause was not a missing booking rule. The evaluation books through the
# company's own `ma_store_pnl` functions — and was handing them the HOUSE DEFAULT config instead of
# the org's own declared one. Those functions are config-driven by design, so the defaults quietly
# answered four questions differently from the way this tenant has answered them:
#
#   · `month_spiff_source`     house 'commission_sheet' vs the org's 'daily_tx' — so EVERY month-spiff
#                              cash row in the daily-transaction feed booked NOTHING
#   · `mdf_product_tokens`     house [] vs the org's own token — so MDF money booked nothing
#   · `store_attribution`      house False vs the org's True — so all airtime margin, residual and
#                              spiff money booked company-wide and NO store column could carry it,
#                              which is why a store column read as carrier commission alone
#   · `residual_order_types`   the mig-309 half of the same story
#
# MEASURED on the live prospect's own 65,491 daily-transaction rows (2026-10-14): the feed books
# **$435,121.34** of income over eight months under the org's declared rules and **$112,740.46**
# under the house defaults. Not a uniform increase — the spiff source also decides WHICH MONTH a
# spiff lands in (the cash row's month, not the activation's), so April falls ~$35k while August
# rises ~$74k. Both are coherent; only one is this tenant's declaration.
#
# THE CLASS: **an evaluation that books through the company's own code must book under the company's
# own declared config. Injecting a default is a second, invisible declaration** — and one nobody can
# see, because the figures it produces look exactly like figures.
#
# So the loaders are dereferenced (`ma_store_pnl.load_config`, `residual_subs.load_ma_pnl_config`),
# and this function says ON THE SCREEN which switch each answer came from. A switch whose column is
# absent from the org's row reads `house_default` with the migration that would add it — the §66
# lesson: a value without its basis invites the reader to treat a fallback as a decision.
def rule_basis(ma_cfg, ma_pnl_cfg=None, house_ma=None, house_pnl=None,
                  config_columns=None, migration_of=None):
    """PURE: the resolved booking switches that change what this statement says, each with its basis.

    `ma_cfg` / `ma_pnl_cfg` are the RESOLVED configs (`ma_store_pnl.load_config` /
    `residual_subs.load_ma_pnl_config` output); `house_ma` / `house_pnl` are those modules' OWN
    defaults, injected so the comparison is against the real house answer and not a copy of it.
    `config_columns` is the (column, migration) pair list the loaders own, and `migration_of` maps a
    column to the migration that adds it.

    THREE BASES, and the distinction is deliberate — a loader reports which COLUMNS the table has,
    never whether this org's row carried a value, so "declared" can only be claimed where the
    resolved value DIFFERS from the house answer:
      · `declared`      — differs from the house answer, so somebody set it
      · `house_default` — equals the house answer: declared as such, or never declared at all, and
                          these two are genuinely indistinguishable from here
      · `column_absent` — the table has no such column yet, so the house answer is the only possible
                          one until the named migration runs

    Returns `{switches, declared, house_default, column_absent, unknown_columns, columns_missing,
    migrations_missing, reading}`.
    """
    cfg, pnl = dict(ma_cfg or {}), dict(ma_pnl_cfg or {})
    hma, hpnl = dict(house_ma or {}), dict(house_pnl or {})
    missing = set(cfg.get("config_columns_missing") or []) | set(pnl.get("config_columns_missing") or [])
    mig = dict(migration_of or {})

    def _list(v):
        return list(v or ())

    # WHICH SWITCH ANSWERS WHICH QUESTION — the money consequence in the tenant's own words, because
    # a switch name tells a reader nothing about what it moves. `src` says which resolved config it
    # comes from, so the house comparison reads the matching default.
    spec = (
        ("month_spiff_source", "pl_ma_month_spiff_source", "ma", None,
         "which feed pays the month spiffs — the commission sheet's spiff columns, or the "
         "daily-transaction cash rows (which also decides WHICH MONTH a spiff lands in)"),
        ("spiff_order_types", "pl_ma_spiff_order_types", "ma", _list,
         "which daily-transaction order families ARE month spiffs"),
        ("mdf_product_tokens", "pl_mdf_product_tokens", "ma", _list,
         "which product names are market-development money rather than a cost"),
        ("store_attribution", "pl_ma_store_attribution", "ma", bool,
         "whether this money is attributed to the store that earned it, or booked company-wide — "
         "with it off, no store column can carry airtime margin, residual or spiff money"),
        ("rebate_presentation", "pl_rebate_presentation", "ma", None,
         "where a device purchase rebate presents — inside cost of goods, or as income"),
        ("device_margin_presentation", "pl_device_margin_presentation", "ma", None,
         "whether device margin gets its own block"),
        ("residual_order_types", "pl_ma_residual_order_types", "pnl", _list,
         "which order families ARE the airtime residual"),
        ("merchant_discount_own_line", "pl_merchant_discount_own_line", "pnl", bool,
         "whether airtime merchant discount gets its own P&L line"),
    )
    switches = []
    for key, column, src, cast, answers in spec:
        got = (cfg if src == "ma" else pnl).get(key)
        house = (hma if src == "ma" else hpnl).get(key)
        if cast is not None:
            got, house = cast(got), cast(house)
        if column in missing:
            basis = "column_absent"
        elif got != house:
            basis = "declared"
        else:
            basis = "house_default"
        switches.append({"key": key, "column": column, "value": got, "house_value": house,
                         "basis": basis, "migration": (mig.get(column) if column in missing else None),
                         "answers": answers})
    cols = [c for c, _m in (config_columns or ())]
    # A COLUMN THIS FUNCTION NAMES THAT THE LOADERS DO NOT IS A DRIFT, NOT A DETAIL: a renamed column
    # would never appear in `columns_missing`, so its switch could never read `column_absent` again.
    # Reported, and §T of the harness fails the build on a non-empty list against the real lists.
    unknown = sorted({sw["column"] for sw in switches} - set(cols)) if cols else []
    by = {}
    for sw in switches:
        by[sw["basis"]] = by.get(sw["basis"], 0) + 1
    return {
        "switches": switches,
        "unknown_columns": unknown,
        "declared": by.get("declared", 0),
        "house_default": by.get("house_default", 0),
        "column_absent": by.get("column_absent", 0),
        "columns": cols,
        "columns_missing": sorted(missing),
        "migrations_missing": sorted({m for m in (mig.get(c) for c in missing) if m}),
        "reading": ("These are the company's OWN booking switches, read from this organisation's "
                    "configuration row — not this report's defaults. A switch reading "
                    "'house_default' holds the house answer, which this organisation may have "
                    "chosen or may simply never have set; one reading 'column_absent' cannot be "
                    "set at all until this feature's setup is finished on this system."),
    }


def coverage_summary(cov):
    """PURE: the one-line honesty statement that goes at the top of the report."""
    by = {}
    for c in cov or ():
        by[c["verdict"]] = by.get(c["verdict"], 0) + 1
    return {
        "lines": len(cov or ()),
        "measured": by.get("measured", 0),
        "not_measured": by.get("not_measured", 0),
        "uploaded_feed_missing": by.get("uploaded_feed_missing", 0),
        "bottom_line_is": "carrier-side contribution",
        "bottom_line_note": (
            "Revenue here is EVERY dollar the carrier and the distributor pay that these three "
            "feeds carry: activation commission and month spiffs, the airtime merchant discount "
            "and the airtime residual, market-development money, fees and consumer financing — "
            "each booked by the company's own P&L code under this organisation's own declared "
            "rules (see the booking switches). It does NOT include retail device sales, bill-pay "
            "fees or any other counter income, because a store we do not operate gives us no "
            "point-of-sale feed — accessory sales appear ONLY where the seller has declared them in "
            "the per-store cell, and are marked as a declaration rather than a measurement. So read "
            "the result as the carrier side's contribution against a typical store's costs, plus "
            "whatever the seller has declared, not as the prospect's measured net income."),
    }


# ── THE EVALUATION ────────────────────────────────────────────────────────────────────────────────

def evaluate(feeds, cfg, homes, expense_matrix=None, rent_meta=None, period=None,
             exclusion_mode=HOUSE_EXCLUSION_MODE, store_inputs=None, prospect_rent=None,
             accessory_cogs_pct=None, expense_months=1, price_list=None):
    """PURE: one prospect month → the whole evaluation, under BOTH exclusion modes.

    `feeds`  {feed_key: [rows]} — prospect rows in the live tables' own column shape.
    `cfg`    `resolve_config()`'s output.
    `homes`  the dereferenced one-homes, injected so this function stays DB-free AND so the harness
             can prove the prospect books through the same code the live P&L books through:
                 ma_commission_bookings, ma_tx_bookings, ma_unit_price_from_rows,
                 ma_sold_cost_from_rows, route_expense_line, spec, pl_section,
                 ma_pnl_config, ma_cfg
    `expense_matrix` the AVERAGED expense matrix, BEFORE any rent override. The override is applied
             here, once per column (`apply_rent_override`), so a store's own declared rent replaces
             our average for that column alone and nothing is ever overridden twice — see the
             §COMMCALC-03.26 section comment for why a chained second pass misreports our average.
    `rent_meta` ignored, and kept only so an older caller does not break; the rent metadata this
             function RETURNS is the one the screen reads.
    `store_inputs` `store_declared_inputs()`'s output — what the seller declared, per store.
    `prospect_rent` the whole-prospect rent (`store_prospect.actual_rent_monthly`), used for the
             whole-prospect column, which has no single address.
    `accessory_cogs_pct` the ORG'S accessory cost rate from `coa` (mig 611), injected, never held
             here.
    `expense_months` how many months of running cost this statement covers — 1 for every single
             month, so a single-month evaluation is byte-identical (§Z8). A P&L over a date range
             (§COMMCALC-03.28) passes the number of months in the range, because a window carrying
             several months of carrier revenue must carry the same number of months of rent and
             salaries. Handed straight to `expense_bookings`, which is the only place it is used.
    `price_list` the handset price list, when the caller has already built it with the SAME home
             (`homes['ma_unit_price_from_rows']`). It is a pure performance injection and nothing
             else: the month-over-month trend evaluates every month off ONE price list instead of
             rebuilding the same 65,491-row list once per month. Omitted, it is built here exactly
             as before, and §Z9 proves injecting the home's own output changes no figure.

    BOTH modes are always computed. The owner asked for a sub-report that takes the flagged phones
    out "or leaves them", and a toggle that recomputes one of them would mean the two numbers never
    appear side by side — which is the only way to see what the flagged accounts are actually worth.
    `exclusion_mode` only names which one the screen opens on.
    """
    comm_rows = list(feeds.get("ma_commission") or [])
    tx_rows = list(feeds.get("ma_daily_tx") or [])
    ful_rows = list(feeds.get("ma_marketplace_orders") or [])
    feeds_present = tuple(k for k in FEED_KEYS if feeds.get(k))

    grouped = account_groups(comm_rows, cfg["account_key_columns"])
    # ONE resolution of the activation vocabulary for the whole evaluation, so the add-a-line column
    # on the flagged-account list and the add-a-line count on the month table cannot be answered by
    # two differently-resolved rule sets.
    act_rules = activation_rules(cfg)
    flagged = flag_multi_line_accounts(grouped, cfg["family_line_threshold"], act_rules)
    summary = multi_line_summary(grouped, flagged, cfg["family_line_threshold"])
    drop_ids, drop_imeis = flagged_line_ids(flagged), flagged_imeis(flagged)

    price = (price_list if price_list is not None
             else homes["ma_unit_price_from_rows"](ful_rows))
    # THE RENT, RESOLVED ONCE PER COLUMN off the AVERAGED matrix. `_exp_for` is the only place a
    # column's expense bookings are built, so the whole-prospect column and a store column cannot be
    # costed by two different routines, and neither can be overridden twice.
    avg_matrix = {k: dict(v) for k, v in (expense_matrix or {}).items()}
    rent_names = cfg.get("rent_expense_names") or ()

    def _exp_for(rent):
        mtx, meta = apply_rent_override(avg_matrix, rent_names, rent)
        return (expense_bookings(mtx, homes["route_expense_line"], expense_months),
                mtx, meta)

    exp_bookings, expense_matrix, rent_meta = _exp_for(prospect_rent)
    exp_lines = {line for line, _amt, _lbl in exp_bookings}
    declared = store_declared_inputs(store_inputs) if isinstance(store_inputs, (list, tuple)) \
        else dict(store_inputs or {})
    acc_pct = safe_float(accessory_cogs_pct)

    # ONE BOOKING PATH, used for the whole prospect and for each of its stores. Factored out when the
    # per-store columns were added (owner ask 2026-10-13): a second loop over a store's own rows is
    # how the columns would stop summing to the total, which is the only thing that makes a per-store
    # view trustworthy. `expenses` is passed in because our average expense matrix is a WHOLE-PROSPECT
    # figure — see `store_scenarios` for why a store's column carries no share of it.
    def _book(mode, c_rows, t_rows, f_rows, expenses, declared_sales=None):
        kept, dropped = split_rows(c_rows, drop_ids if mode == "exclude" else ())
        inputs = empty_inputs(homes["spec"])
        booked = set()
        for line, _acct, amt, detail in homes["ma_commission_bookings"](kept, homes.get("ma_cfg")):
            add_booking(inputs, line, amt, detail, homes.get("accrue_detail"))
            if round(safe_float(amt), 2):
                booked.add(line)
        for line, _acct, amt, detail in homes["ma_tx_bookings"](
                t_rows, homes.get("ma_pnl_config"), homes.get("ma_cfg")):
            add_booking(inputs, line, amt, detail, homes.get("accrue_detail"))
            if round(safe_float(amt), 2):
                booked.add(line)
        # The PRICE LIST stays the whole prospect's (`price`), deliberately: a handset ordered by one
        # store and activated in another must still be costed, exactly as the live device-costing home
        # reads the fulfilment sheet as a price list across periods.
        sold = homes["ma_sold_cost_from_rows"](
            kept, price, None, drop_imeis if mode == "exclude" else None)
        for sku, amt in (sold.get("detail") or {}).items():
            add_booking(inputs, "device_cost", amt, sku, homes.get("accrue_detail"))
        if round(safe_float(sold.get("cost")), 2):
            booked.add("device_cost")
        for line, amt, label in expenses:
            add_booking(inputs, line, amt, label, homes.get("accrue_detail"))
        # WHAT THE SELLER DECLARED. Booked LAST and through the same `add_booking` every other
        # dollar takes, so a declaration lands on coa's own accessory lines and flows into gross
        # profit and net income by the statement's own arithmetic rather than by an adjustment.
        for line, amt, label in declared_accessory_bookings(declared_sales, acc_pct):
            add_booking(inputs, line, amt, label, homes.get("accrue_detail"))
            booked.add(line)
        cov = coverage(homes["spec"], homes["pl_section"], booked,
                       {l for l, _a, _lb in expenses}, feeds_present)
        return {
            "mode": mode,
            "inputs": inputs,
            "device_cost": round(safe_float(sold.get("cost")), 2),
            "device_cost_meta": sold.get("meta") or {},
            "commission_lines_kept": len(kept),
            "commission_lines_dropped": len(dropped),
            "activations": activation_count(kept),
            "coverage": cov,
            "coverage_summary": coverage_summary(cov),
            "measured_lines": sorted(booked | {l for l, _a, _lb in expenses}),
        }

    # THE WHOLE-PROSPECT COLUMN carries the SUM of the per-store declarations, because a declared
    # accessory sale is the store's own money and revenue ties across the columns (§N holds that).
    # Only declarations for a store the uploads actually name are summed; a key naming no column is
    # REPORTED below rather than silently added to a total nobody can drill into.
    stores = store_index(feeds, homes["account_store_index"]) if homes.get(
        "account_store_index") else {}
    declared_known = {k: v for k, v in declared.items() if k in stores}
    declared_unknown = sorted(k for k in declared if k not in stores)
    declared_total = round(sum(safe_float(v.get("accessory_sales"))
                               for v in declared_known.values()
                               if v.get("accessory_sales") is not None), 2) or None
    scenarios = {mode: _book(mode, comm_rows, tx_rows, ful_rows, exp_bookings, declared_total)
                 for mode in EXCLUSION_MODES}

    # ── THE COLUMNS, one per store the uploads name ────────────────────────────────────────────────
    # EVERY COLUMN CARRIES THE FULL SINGLE-STORE EXPENSE (owner directive 2026-10-13: "add teh
    # expenses in teh expenses line and give ,e teh final p&l nunmber to see if teh store is
    # profitable aor loss"). The expense side of this evaluation is OUR OWN stores' average monthly
    # cost (§COMMCALC-03.6) — ALREADY one store's worth of rent, salaries and utilities. So there are
    # only two honest things to do with it, and the owner has chosen between them:
    #
    #   · SPLIT it across the prospect's sites — divides a per-store cost by seven and makes every
    #     column look profitable. Refused; it answers no question anyone asked.
    #   · CHARGE EACH COLUMN THE WHOLE of it — the column then answers "could THIS site stand on its
    #     own?", which is the takeover question, and it is the SAME basis the per-store break-even
    #     has used since §COMMCALC-03.14 (each store's accessory target is figured against the full
    #     single-store expense). This is what ships.
    #
    # THE CONSEQUENCE, STATED RATHER THAN HIDDEN: the expense and net-income rows therefore do NOT
    # add across to the whole-prospect column — seven columns carry seven copies of a one-store cost
    # on purpose. Revenue, device cost and activations still do add, because those are the store's
    # own money. `expenses_included` and `expenses_basis` say which it is on every column, the screen
    # prints it, and `harness_prospect_eval.py` §N holds BOTH halves: the lines that must tie still
    # tie, and the two that must not are asserted not to.
    split = rows_by_store(feeds, set(stores)) if stores else {}
    store_scenarios = {}
    for key, per in sorted(split.items()):
        dec = declared.get(key) or {}
        # THIS SITE'S OWN RENT, or our average when the seller has declared none. The override runs
        # off `avg_matrix` every time, never off the whole-prospect column's already-overridden one.
        st_rent = dec.get("rent")
        st_exp, st_matrix, st_rent_meta = (_exp_for(st_rent) if st_rent is not None
                                           else (exp_bookings, expense_matrix, rent_meta))
        store_scenarios[key] = {
            "store": stores.get(key) or {"account": None, "address": None, "name": None,
                                         "label": key, "has_address": False,
                                         "feeds": sorted(k for k, v in per.items() if v)},
            "expenses_included": True,
            "expenses_basis": STORE_EXPENSE_BASIS,
            "declared": declared_meta(dec.get("accessory_sales"), acc_pct, st_rent, st_rent_meta),
            "expenses": {"matrix": st_matrix or {}, "rent": st_rent_meta or {}},
            # THE LEGS FOR THIS SITE, through the same two injected ladder homes the whole prospect
            # uses — so a column's trailing share and the total's cannot be built two ways.
            "commission_legs": (commission_leg_ladder(
                homes["ma_earned_month_ladder"](per.get("ma_commission") or [],
                                                homes.get("ma_cfg")),
                homes["ma_received_month_ladder"](per.get("ma_daily_tx") or [],
                                                  homes.get("ma_pnl_config"),
                                                  homes.get("ma_cfg")),
                m1_month=homes.get("leg_m1_month"), leg_labels=homes.get("leg_labels"),
                ladder_sort_key=homes.get("ladder_sort_key"))
                if homes.get("ma_earned_month_ladder") and homes.get("ma_received_month_ladder")
                else None),
            "scenarios": {mode: _book(mode, per.get("ma_commission") or [],
                                      per.get("ma_daily_tx") or [],
                                      per.get("ma_marketplace_orders") or [], st_exp,
                                      dec.get("accessory_sales"))
                          for mode in EXCLUSION_MODES},
        }
    return {
        "period": period,
        "config": dict(cfg),
        "feeds_present": list(feeds_present),
        "feeds_missing": [k for k in FEED_KEYS if k not in feeds_present],
        "feed_registry": feed_registry_view(),
        "rows_by_feed": {k: len(feeds.get(k) or []) for k in FEED_KEYS},
        "price_list_models": len(price),
        "multi_line": {"summary": summary, "flagged": flagged,
                       "flagged_line_ids": sorted(drop_ids),
                       "flagged_imeis": sorted(drop_imeis)},
        "expenses": {"matrix": expense_matrix or {}, "rent": rent_meta or {},
                     "average_matrix": avg_matrix,
                     "bookings": [{"line": l, "amount": a, "label": lb}
                                  for l, a, lb in exp_bookings]},
        # WHAT THE SELLER DECLARED, and what nobody has declared yet — both named. A store with no
        # declaration is listed so the screen can show an empty cell that invites one, rather than
        # the reader having to notice an absence.
        "declared": {
            "by_store": {k: declared_meta(v.get("accessory_sales"), acc_pct, v.get("rent"))
                         for k, v in sorted(declared_known.items())},
            "accessory_total": declared_total,
            "accessory_cogs_pct": (acc_pct if 0 <= acc_pct <= 1 else 0.0),
            "stores_declaring": sorted(k for k, v in declared_known.items()
                                       if v.get("accessory_sales") is not None),
            "stores_without_declaration": sorted(k for k in stores
                                                 if (declared.get(k) or {}).get(
                                                     "accessory_sales") is None),
            "keys_naming_no_store": declared_unknown,
            "accessory_basis": DECLARED_BASIS,
            "rent_basis": RENT_CELL_BASIS,
        },
        # M1 vs M2-M12, through the two ladder one-homes (§COMMCALC-03.27). Absent homes degrade to
        # "not measured" rather than to a zero that reads like a measurement.
        "commission_legs": (commission_leg_ladder(
            homes["ma_earned_month_ladder"](comm_rows, homes.get("ma_cfg")),
            homes["ma_received_month_ladder"](tx_rows, homes.get("ma_pnl_config"),
                                              homes.get("ma_cfg")),
            m1_month=homes.get("leg_m1_month"), leg_labels=homes.get("leg_labels"),
            ladder_sort_key=homes.get("ladder_sort_key"))
            if homes.get("ma_earned_month_ladder") and homes.get("ma_received_month_ladder")
            else None),
        "default_mode": exclusion_mode if exclusion_mode in EXCLUSION_MODES else HOUSE_EXCLUSION_MODE,
        "scenarios": scenarios,
        "stores": stores,
        "store_scenarios": store_scenarios,
        "handset_orders": handset_orders(ful_rows, stores),
        # WHAT WAS SOLD, at the end of the report. Counted over the SAME commission rows the P&L books
        # from, so a line in this breakup is a line in the statement above it.
        "products": products_sold(comm_rows, cfg),
        "fraud": fraud_exposure(summary, flagged),      # the delta is folded in by the caller
    }


# ── THE BREAKUP OF WHAT WAS SOLD (owner ask 2026-10-13) ──────────────────────────────────────────
# "summarize at the end of the report, the breakup of Phones tablets, Fios VHI, and if any other item
# was sold". Every rule here lives in `product_mix` — the ONE home for reading a product description
# (`model_of` since §62, `product_class_of` and `product_breakup` since this ask) — so the prospect
# report and any live-store caller cannot disagree about what a tablet is. This function only names
# WHICH COLUMNS of the prospect's commission sheet answer, and it names them through the two one-homes
# above rather than spelling them.
def products_sold(comm_rows, cfg=None):
    """PURE: the prospect's commission lines → the breakup of what was sold, through `product_mix`."""
    c = cfg or {}
    return _pm.product_breakup(
        comm_rows or [],
        desc_of=lambda r: (r or {}).get(PRODUCT_DESC_COLUMN),
        declared_of=lambda r: (r or {}).get(NO_DEVICE_COLUMN),
        classes=c.get("product_classes"),
        no_device_tokens=c.get("no_device_tokens"),
        no_product_tokens=c.get("no_product_tokens"),
        promo_tokens=c.get("promo_tokens"))


# ── WHAT HANDSETS THIS STORE ORDERED ──────────────────────────────────────────────────────────────
# OWNER ASK 2026-10-13, verbatim: *"i just upladed handset infor monthwise and also all teptherg but
# it does nto show me any link for a report to asses teh handsets ordered"*. There was no such report:
# the fulfilment feed was read only as a PRICE LIST (`ma_unit_price_from_rows`), which is what it is
# for costing an activated line, and nothing showed the orders themselves.
#
# MEASURED FIRST, and it changes what the report can claim. The export lists **twenty order types**
# over 68,000 live rows and carries a ship-to, a cost and a date on the handset orders ALONE: 798 of
# those rows are branded-handset orders, 897 carry any store, and 3,103 carry no date. So this does
# not pretend the whole file is handset orders. It reports what carries a price, counts what does not
# beside it, and names the order types it set aside — an absence stated, never smoothed away.
#
# `number_ordered` is UNITS and `price` is the UNIT price: the live device-costing home multiplies the
# two the same way, so the extended cost here and the COGS on the statement cannot disagree.
def handset_orders(ful_rows, store_index_rows=None):
    """PURE: the prospect's handset orders → `{models, by_store, by_month, totals, set_aside}`.

    `ful_rows` the `ma_marketplace_orders` rows · `store_index_rows` `store_index`'s output, so a
    store reads as its address rather than an account number. Rows carrying no price are counted in
    `set_aside` with their order types, never folded into a total as zero."""
    col = _store_column("ma_marketplace_orders")
    idx = store_index_rows or {}
    models, by_store, by_month = {}, {}, {}
    set_aside, aside_types = 0, {}
    units = cost = 0.0
    priced = 0
    for r in ful_rows or []:
        r = r or {}
        price = safe_float(r.get("price"))
        qty = safe_float(r.get("number_ordered"))
        otype = str(r.get("order_type") or "").strip() or "(no order type)"
        if not price:
            set_aside += 1
            aside_types[otype] = aside_types.get(otype, 0) + 1
            continue
        priced += 1
        n = qty if qty else 1.0          # a priced row with no unit count is one unit, not none
        ext = round(price * n, 2)
        units += n
        cost += ext
        model = str(r.get("product_name") or "").strip() or "(no model named)"
        m = models.setdefault(model, {"model": model, "orders": 0, "units": 0.0, "cost": 0.0,
                                      "unit_price_low": price, "unit_price_high": price})
        m["orders"] += 1
        m["units"] += n
        m["cost"] = round(m["cost"] + ext, 2)
        m["unit_price_low"] = min(m["unit_price_low"], price)
        m["unit_price_high"] = max(m["unit_price_high"], price)
        acct = str(r.get(col) or "").strip() if col else ""
        label = (idx.get(acct) or {}).get("label") or (f"account {acct}" if acct
                                                       else UNATTRIBUTED_STORE)
        st = by_store.setdefault(label, {"store": label, "account": acct or None,
                                         "orders": 0, "units": 0.0, "cost": 0.0})
        st["orders"] += 1
        st["units"] += n
        st["cost"] = round(st["cost"] + ext, 2)
        month = _row_period(r) or "(no month on the row)"
        mo = by_month.setdefault(month, {"month": month, "orders": 0, "units": 0.0, "cost": 0.0})
        mo["orders"] += 1
        mo["units"] += n
        mo["cost"] = round(mo["cost"] + ext, 2)
    return {
        "models": sorted(models.values(), key=lambda x: (-x["cost"], x["model"])),
        "by_store": sorted(by_store.values(), key=lambda x: (-x["cost"], x["store"])),
        "by_month": sorted(by_month.values(),
                           key=lambda x: period_sort_key(x["month"]) if x["month"][0].isalpha()
                           or x["month"][0].isdigit() else (0, 0)),
        "totals": {"rows": len(ful_rows or ()), "priced_rows": priced, "orders": priced,
                   "units": round(units, 2), "cost": round(cost, 2),
                   "models": len(models), "stores": len(by_store)},
        "set_aside": {
            "rows": set_aside,
            "order_types": {k: aside_types[k] for k in sorted(aside_types,
                                                              key=lambda k: -aside_types[k])},
            "reason": ("rows carrying no unit price. The marketplace export lists every order type "
                       "the dealer's account saw — residuals, spiffs, promos, fees — and carries a "
                       "cost only on the physical handset orders. They are counted here rather than "
                       "folded into the totals as zero."),
        },
    }


# ── THE ACCOUNTS AT THE BOTTOM OF THE REPORT, AND THE NUMBER UNDERNEATH THEM ──────────────────────
# OWNER ASK 2026-10-13, verbatim: *"hightlight the 3 lines plus deemed fraud at the bottim of the
# report imn nunmber of those accounts and below that subtract the inform received from those
# accoutns to assess a real number of there is any fraud going on"*.
#
# Two figures, and the ORDER he asked for them in: how many accounts, then the same report with their
# money taken out. The second is why the evaluation already computes BOTH exclusion modes side by
# side (§COMMCALC-03.5) — this names the pair as the fraud read rather than computing a third answer.
#
# BANDED BY LINE COUNT, because "3 lines" and "nine lines on one account" are not the same finding
# and a single count hides the difference. The bands come from the DATA, never from a fixed list, so
# a file whose worst account carries sixteen lines shows a sixteen band.
#
# IT IS EVIDENCE, NOT A VERDICT, and that is not a style preference — it is the lesson the live
# multi-handset flag already paid for: on real data a legitimate household bundle and a four-handset
# $0.00 promo run look identical until a human opens the receipts. So `deemed_fraud` is **not** a
# field here. What is returned is a count, the money, and the report with the money removed; the word
# the owner used belongs to the person who reads it.
def fraud_exposure(summary, flagged, delta=None):
    """PURE: the accounts carrying more lines than the threshold allows, and what they are worth.

    `summary` `multi_line_summary`'s output · `flagged` `flag_multi_line_accounts`'s output ·
    `delta`   `scenario_delta`'s output, injected, so the money comes off the two statements already
              assembled rather than from a sum taken here.

    `bands` is `{lines_on_the_account: accounts}` over the flagged accounts only. `net_without_them`
    and `moved` are None when no delta is supplied — not measured is never shown as zero."""
    bands = {}
    for a in flagged or ():
        n = int(a.get("lines") or 0)
        bands[n] = bands.get(n, 0) + 1
    d = (delta or {}).get("net_income") or {}
    keep = d.get("keep")
    exc = d.get("exclude")
    return {
        "threshold": int((summary or {}).get("threshold") or 0),
        "accounts": int((summary or {}).get("flagged_accounts") or 0),
        "lines": int((summary or {}).get("flagged_lines") or 0),
        "accounts_in_file": int((summary or {}).get("accounts") or 0),
        "lines_in_file": int((summary or {}).get("lines") or 0),
        "bands": {str(k): bands[k] for k in sorted(bands)},
        "worst_account_lines": max(bands) if bands else 0,
        "net_with_them": (round(safe_float(keep), 2) if keep is not None else None),
        "net_without_them": (round(safe_float(exc), 2) if exc is not None else None),
        "moved": (round(safe_float(d.get("delta")), 2) if d.get("delta") is not None else None),
        "share_of_bottom_line": (delta or {}).get("share_of_bottom_line_at_risk"),
        "reading": ("A count and a number, not a verdict. An account carrying several lines in one "
                    "month is as often a family or a small business as it is a fraud, and on live "
                    "data the two are indistinguishable until someone opens the receipts. The "
                    "figure to act on is what the bottom line becomes without them."),
    }


# ── WHAT THE COUNTER HAS TO SELL FOR THIS STORE TO WORK ───────────────────────────────────────────
# OWNER ASK 2026-10-13, verbatim: *"Since no pos data is being loaded, the system should calculate
# e ow much accessories ahve to sold at teh botomt of teh sheet as teh stroe will be in loss with
# just commission income , that will be gross expnses less teh gross revenue = total loss = break
# even needed by selling accessories , then deivde teh number with the total actiavations done for
# tehat store in a particlua month to get teh APB needed to be profiavle"*.
#
# This TURNS THE EVALUATION'S OWN LIMIT INTO THE ANSWER. §COMMCALC-03.7 already says out loud that
# the three uploads are the distributor's view and carry no retail accessory revenue, so those lines
# read `not_measured` rather than zero. The owner's question is the right one to ask of exactly that
# gap: not "what did the counter take" — nothing here can know — but **"what would it have to take"**.
# So this is a REQUIREMENT, never a measurement, and every field name says so.
#
# THE ARITHMETIC IS NOT RE-DERIVED. Revenue, COGS and expenses come from `analysis.pl_totals` — the
# one home for a statement's totals, the same figures the dashboard and every finance surface read —
# injected, so this block can never disagree with the statement printed above it. Rounding is the
# platform's 2-dp `safe_float` + round, never a second convention.
#
# HIS CONJUNCTION, STATED AS HE GAVE IT: gross expenses less gross revenue. On this statement that is
# (expenses + COGS) − revenue, i.e. the negated net income, and taking it off `net_income` rather than
# re-summing the sections is what keeps it tied to the statement. Handset cost is INSIDE it (the
# default he was told about): a store that must buy phones to earn the commission is carrying that
# cost whether or not a phone is sold at a profit.
#
# A STORE ALREADY IN PROFIT IS NOT GIVEN A TARGET OF ZERO. `needed` is None, not 0.00, and
# `already_profitable` says so — a 0.00 target reads as "sell nothing", which is the opposite of true.
def accessory_break_even(totals, activations, pl_totals=None):
    """PURE: what accessory revenue this store must take to break even, and the per-box rate.

    `totals`      a `pl_totals` payload dict, OR an assembled statement when `pl_totals` is injected.
    `activations` the count of activated lines for the month — the denominator, the boxes the store
                  actually put out. 0 or None ⇒ `per_activation` is None, never a division.

    Returns `{revenue, cost_of_goods, expenses, net_income, needed, per_activation, activations,
    already_profitable, basis}`. `needed` is None when the store is already in profit."""
    t = pl_totals(totals) if pl_totals is not None else (totals or {})
    rev = round(safe_float(t.get("revenue")), 2)
    cogs = round(safe_float(t.get("cogs")), 2)
    exp = round(safe_float(t.get("expenses")), 2)
    net = round(safe_float(t.get("net_income")), 2)
    n = int(activations or 0)
    profitable = net >= 0
    needed = None if profitable else round(-net, 2)
    per = (round(needed / n, 2) if (needed is not None and n > 0) else None)
    return {
        "revenue": rev, "cost_of_goods": cogs, "expenses": exp, "net_income": net,
        "activations": n,
        "needed": needed,
        "per_activation": per,
        "already_profitable": profitable,
        "basis": ("gross expenses (operating expenses plus cost of goods) less gross revenue, taken "
                  "off the statement's own net income — what the counter must take in accessory "
                  "revenue, which no uploaded feed can measure, for this store to break even"),
        "per_activation_basis": ("that shortfall divided by the activated lines the commission feed "
                                 "records for the month — the accessory dollars per box this store "
                                 "has to hit" if n > 0 else
                                 "not divisible — the commission feed records no activated line for "
                                 "this month, so there is no box count to divide by"),
    }


def activation_count(comm_rows):
    """PURE: activated lines for the month — one per commission row, counted on the row identifier
    `line_id` already uses (`activation_order`, measured 1,248 distinct over 1,248 rows: one per
    line). A row with no identifier still counts as a line; it is a line the feed named badly, not a
    line that did not happen."""
    seen, blank = set(), 0
    for r in comm_rows or []:
        k = line_id(r or {})
        if k:
            seen.add(k)
        else:
            blank += 1
    return len(seen) + blank


def statement_line_amounts(statement):
    """PURE: an assembled statement → `{line key: amount}`, for a table whose COLUMNS are months.

    The month-over-month P&L (§COMMCALC-03.28) is a line down the side and a month across the top,
    so it needs a month's figures by line key. This is a PROJECTION of the statement the assembler
    built — it reads `sections[].lines[]` and invents no line, keeps no order of its own and sums
    nothing, so a cell of the trend and the same cell of that month's own statement are the same
    number by construction."""
    out = {}
    for sec in ((statement or {}).get("sections") or []):
        for ln in (sec.get("lines") or []):
            k = ln.get("key")
            if k is not None:
                out[k] = ln.get("amount")
    return out


def scenario_delta(statements, pl_totals):
    """PURE: {mode: assembled statement} → what excluding the flagged accounts is worth.

    The number the sub-report exists to produce: how much of the prospect's carrier-side
    contribution rests on accounts a human has not yet cleared.

    `pl_totals` is `account/analysis.pl_totals` injected — THE one home for a statement's four
    totals, so this delta and every finance surface read the same figures off the same payload
    rather than this module re-summing the sections into a fifth answer."""
    keep = pl_totals((statements or {}).get("keep") or {})
    exc = pl_totals((statements or {}).get("exclude") or {})
    out = {}
    for k in ("revenue", "cogs", "gross_profit", "opex", "expenses", "net_income"):
        a, b = round(safe_float(keep.get(k)), 2), round(safe_float(exc.get(k)), 2)
        out[k] = {"keep": a, "exclude": b, "delta": round(b - a, 2)}
    base = out["net_income"]["keep"]
    moved = out["net_income"]["delta"]
    out["share_of_bottom_line_at_risk"] = (
        round(abs(moved) / abs(base) * 100.0, 2) if base else None)
    return out


# ── COMPARE IT WITH A STORE WE ALREADY OWN (owner ask 2026-10-14) ─────────────────────────────────
# Owner, verbatim: *"Add one more column to it with to pick our existing store to compare the sales
# of the existing PNL with the new ones we might take over"*.
#
# WHAT MAKES THE TWO SIDES COMPARABLE, and it is not a classification. A takeover pack carries the
# target's CARRIER feeds and nothing else — no accessory sale, no bill payment, no counter revenue of
# any kind (§COMMCALC-03.7 says that out loud and this is the first surface where it would MISLEAD if
# it did not). Our own store's P&L carries all of it. So putting our store's whole revenue beside the
# target's would read as the target earning a fraction of what our store earns, when the real
# difference is which lines each side's data can speak to at all.
#
# The comparable figure is therefore "what OUR store earned on the SAME P&L lines the target's
# statement booked", and the line list comes from the target's OWN assembled statement rather than
# from any rule about which lines are carrier money. Three reasons it is derived and not declared:
#   · the two statements are assembled by ONE builder off ONE chart (`coa.PL_SPEC`), so a line key
#     means the same thing on both sides by construction;
#   · a pack that one day carries an accessory feed widens the comparison in the same upload, with
#     no code change and no config row — the comparison follows the data;
#   · a declared "these lines are carrier money" list would be a second answer to a question
#     `carrier_dollar_class` and the chart already answer, and the obvious candidate for it
#     (`GP_INCOME_COLUMNS`) files an UNRECOGNISED line into a carrier column, so asking it would
#     call every counter line carrier money — a last-resort placement read as evidence (§66).
#
# PURE. The stores and their figures are read by the endpoint through the ONE home
# (`statement_filter.store_pl_totals`); nothing here touches a database and nothing here re-sums a
# statement — `pl_totals` is injected exactly as `scenario_delta` and `accessory_break_even` take it.

def comparable_revenue_lines(statement):
    """PURE: the revenue line keys the TARGET's own statement actually booked, in chart order.

    "Actually booked" means the line carries a non-zero amount. A line the chart declares and this
    pack never filled is not a line the target can be compared on, and including it would quietly
    credit our own store's money to a comparison the target's data says nothing about.

    Returns a tuple, possibly empty — a pack that booked no revenue at all compares on nothing, and
    the caller reports that rather than falling back to the whole chart."""
    out = []
    for sec in (statement or {}).get("sections") or []:
        if sec.get("type") != "revenue":
            continue
        for ln in sec.get("lines") or []:
            if round(safe_float(ln.get("amount")), 2) and ln.get("key"):
                out.append(str(ln["key"]))
    return tuple(dict.fromkeys(out))


def peer_store_options(by_store, lines=(), computed_at=None, line_labels=None):
    """PURE: our own stores, as the screen's pick list — one row per store, newest figures first by
    name, each carrying what that store earned beside what it earned on the comparable lines.

    `by_store`   `statement_filter.store_pl_totals(...)["by_store"]` — {key: {label, address,
                 totals, revenue_on_lines}}. A store with no stored P&L for the month is already
                 ABSENT from it, and stays absent here: an un-computed month is never a $0 store.
    `lines`      the comparable line keys (`comparable_revenue_lines`), for the reading only.
    `computed_at` the label the read came back with — a stored snapshot does not recompute when the
                 screen loads, and a comparison that does not say when it was computed invites a
                 stale figure to be read as a current one.

    Returns `{stores: [...], lines: [...], line_labels: [...], computed_at, reading, comparable}`.
    `comparable` is False when the target booked no revenue line at all, which is the one case where
    the pick list cannot answer anything and the screen must say so instead of drawing columns."""
    rows = []
    for key, blk in (by_store or {}).items():
        t = (blk or {}).get("totals") or {}
        rows.append({
            "key": str(key),
            "label": str((blk or {}).get("label") or key),
            "address": str((blk or {}).get("address") or ""),
            "revenue": round(safe_float(t.get("revenue")), 2),
            "revenue_on_lines": (None if (blk or {}).get("revenue_on_lines") is None
                                 else round(safe_float(blk.get("revenue_on_lines")), 2)),
            "cogs": round(safe_float(t.get("cogs")), 2),
            "gross_profit": round(safe_float(t.get("gross_profit")), 2),
            "expenses": round(safe_float(t.get("expenses")), 2),
            "net_income": round(safe_float(t.get("net_income")), 2),
        })
    rows.sort(key=lambda r: r["label"].lower())
    labels = [str(l) for l in (line_labels or ())]
    reading = (
        "Our own store's figures come from its stored monthly P&L, not from this upload. The "
        "comparable column is what that store earned on the SAME revenue lines this takeover pack "
        "booked" + (" (" + ", ".join(labels) + ")" if labels else "") + " — its WHOLE revenue is "
        "shown beside it and is deliberately the larger number, because a store we own also sells "
        "accessories and takes bill payments and a takeover pack carries no feed for either. Read a "
        "gap on the whole-revenue column as the counter business this pack cannot show, never as the "
        "target earning less.")
    if not lines:
        reading = ("This takeover pack booked no revenue line at all for the month on screen, so "
                   "there is nothing to compare one of our stores against. Upload its carrier feeds, "
                   "or check the column mapping if one is loaded.")
    if not rows:
        reading = ("None of our own stores has a stored P&L for this month, so there is nothing to "
                   "compare against. Run the month's statements from the Finance hub and this list "
                   "fills itself — nothing here is a figure about your stores earning nothing.")
    return {"stores": rows, "lines": list(lines or ()), "line_labels": labels,
            "computed_at": computed_at, "reading": reading,
            "comparable": bool(lines) and bool(rows)}
