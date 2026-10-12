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
    },
    "ma_daily_tx": {
        "label": "MA Daily Transactions",
        "grain": "one row per airtime / top-up / order transaction",
        "books_through": ("account.ma_store_pnl", "ma_tx_bookings"),
        "carries_account_identity": False,
        "carries_device": False,
        "answers": "airtime merchant discount, residual, MDF market spiffs, month-n spiff cash",
    },
    "ma_marketplace_orders": {
        "label": "MA Marketplace Handset Fulfillment Orders",
        "grain": "one row per handset order line",
        "books_through": ("account.device_cogs", "ma_unit_price_from_rows"),
        "carries_account_identity": False,
        "carries_device": True,
        "answers": "what each handset model cost — the price list the activated devices are costed at",
    },
}
FEED_KEYS = tuple(FEED_REGISTRY)


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
)


def default_config():
    """PURE: the house defaults, as a resolved config dict."""
    return {
        "account_key_columns": list(HOUSE_ACCOUNT_KEY_COLUMNS),
        "family_line_threshold": HOUSE_FAMILY_LINE_THRESHOLD,
        "expense_basis_months": HOUSE_EXPENSE_BASIS_MONTHS,
        "rent_expense_names": list(HOUSE_RENT_EXPENSE_NAMES),
        "expense_average_basis": HOUSE_AVERAGE_BASIS,
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
        period = str((r or {}).get("period") or "").strip()
        g = groups.setdefault((period, key), {"period": period, "account_key": key,
                                              "account_column": col, "lines": []})
        g["lines"].append(r)
    return {"groups": groups, "unidentified": unidentified, "columns_used": used}


def device_of(row):
    """PURE: the device sold on one line, as (sku, imei). `sku` may be the feed's own
    'not available' placeholder — reported verbatim rather than cleaned, because the placeholder is
    itself the finding when it lands on a flagged account."""
    r = row or {}
    return (str(r.get("sku") or "").strip(), str(r.get("imei") or "").strip())


def flag_multi_line_accounts(grouped, threshold):
    """PURE: the accounts carrying MORE THAN `threshold` lines in one month, with their devices.

    This is a WHO-TO-LOOK-AT report, and the wording matters: nothing here concludes that fraud
    happened. A family of four buying four phones on one account on one day is a good sale and reads
    identically in this feed to the thing the owner is worried about. So every flagged account
    carries the evidence a human needs to tell them apart — the lines, the devices, the day span, the
    store account, how many are add-a-lines — and no verdict.

    Returns a list sorted by line count descending, then account key, so the output is stable."""
    thr = max(0, int(threshold or 0))
    out = []
    for (_period, _key), g in (grouped.get("groups") or {}).items():
        lines = g["lines"]
        if len(lines) <= thr:
            continue
        devices, days, stores, adds = {}, set(), set(), 0
        for r in lines:
            sku, imei = device_of(r)
            label = sku or "(no model on the row)"
            d = devices.setdefault(label, {"sku": sku, "lines": 0, "imeis": []})
            d["lines"] += 1
            if imei:
                d["imeis"].append(imei)
            day = str((r or {}).get("tx_date") or "").strip()
            if day:
                days.add(day)
            acct = str((r or {}).get("merchant_account_id") or "").strip()
            if acct:
                stores.add(acct)
            if str((r or {}).get("activation_type") or "").strip().lower() == "add":
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


def expense_bookings(matrix, route_expense_line):
    """PURE: the averaged matrix → P&L bookings, routed by coa's own router.

    `route_expense_line` is passed in rather than imported so this stays DB-free and the harness can
    prove the prospect books an expense to the SAME line the live P&L books it to (§D). A manual
    line (no `source_key`) takes coa's historical default exactly as a hand-entered expense does."""
    out = []
    for _key, e in sorted((matrix or {}).items()):
        amt = safe_float(e.get("amount"))
        if not amt:
            continue
        line, fallback = route_expense_line(e.get("source_key"))
        out.append((line, round(amt, 2), e.get("expense_name") or fallback))
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
            "Revenue here is what the carrier and the distributor pay, plus the airtime margin. It "
            "does NOT include retail device or accessory sales, bill-pay fees or any other counter "
            "income, because a store we do not operate gives us no point-of-sale feed. Read the "
            "result as the contribution the carrier side makes against a typical store's costs, not "
            "as the prospect's net income."),
    }


# ── THE EVALUATION ────────────────────────────────────────────────────────────────────────────────

def evaluate(feeds, cfg, homes, expense_matrix=None, rent_meta=None, period=None,
             exclusion_mode=HOUSE_EXCLUSION_MODE):
    """PURE: one prospect month → the whole evaluation, under BOTH exclusion modes.

    `feeds`  {feed_key: [rows]} — prospect rows in the live tables' own column shape.
    `cfg`    `resolve_config()`'s output.
    `homes`  the dereferenced one-homes, injected so this function stays DB-free AND so the harness
             can prove the prospect books through the same code the live P&L books through:
                 ma_commission_bookings, ma_tx_bookings, ma_unit_price_from_rows,
                 ma_sold_cost_from_rows, route_expense_line, spec, pl_section,
                 ma_pnl_config, ma_cfg
    `expense_matrix` the rent-overridden average matrix (`apply_rent_override`'s first return).

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
    flagged = flag_multi_line_accounts(grouped, cfg["family_line_threshold"])
    summary = multi_line_summary(grouped, flagged, cfg["family_line_threshold"])
    drop_ids, drop_imeis = flagged_line_ids(flagged), flagged_imeis(flagged)

    price = homes["ma_unit_price_from_rows"](ful_rows)
    exp_bookings = expense_bookings(expense_matrix or {}, homes["route_expense_line"])
    exp_lines = {line for line, _amt, _lbl in exp_bookings}

    scenarios = {}
    for mode in EXCLUSION_MODES:
        kept, dropped = split_rows(comm_rows, drop_ids if mode == "exclude" else ())
        inputs = empty_inputs(homes["spec"])
        booked = set()
        for line, _acct, amt, detail in homes["ma_commission_bookings"](kept, homes.get("ma_cfg")):
            add_booking(inputs, line, amt, detail, homes.get("accrue_detail"))
            if round(safe_float(amt), 2):
                booked.add(line)
        for line, _acct, amt, detail in homes["ma_tx_bookings"](
                tx_rows, homes.get("ma_pnl_config"), homes.get("ma_cfg")):
            add_booking(inputs, line, amt, detail, homes.get("accrue_detail"))
            if round(safe_float(amt), 2):
                booked.add(line)
        sold = homes["ma_sold_cost_from_rows"](
            kept, price, None, drop_imeis if mode == "exclude" else None)
        for sku, amt in (sold.get("detail") or {}).items():
            add_booking(inputs, "device_cost", amt, sku, homes.get("accrue_detail"))
        if round(safe_float(sold.get("cost")), 2):
            booked.add("device_cost")
        for line, amt, label in exp_bookings:
            add_booking(inputs, line, amt, label, homes.get("accrue_detail"))
        cov = coverage(homes["spec"], homes["pl_section"], booked, exp_lines, feeds_present)
        scenarios[mode] = {
            "mode": mode,
            "inputs": inputs,
            "device_cost": round(safe_float(sold.get("cost")), 2),
            "device_cost_meta": sold.get("meta") or {},
            "commission_lines_kept": len(kept),
            "commission_lines_dropped": len(dropped),
            "coverage": cov,
            "coverage_summary": coverage_summary(cov),
            "measured_lines": sorted(booked | exp_lines),
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
                     "bookings": [{"line": l, "amount": a, "label": lb}
                                  for l, a, lb in exp_bookings]},
        "default_mode": exclusion_mode if exclusion_mode in EXCLUSION_MODES else HOUSE_EXCLUSION_MODE,
        "scenarios": scenarios,
    }


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
