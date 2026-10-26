"""Canonical source-of-truth registry — the ONE place code names a data table.

WHY THIS EXISTS (owner 2026-08-30). The data-freshness banner read commcalc.raw_sales (the MONTHLY
reconciliation upload) when the LIVE feed is commcalc.daily_sales_feed, so it cried "stale since 8-09"
while the numbers were current. Nothing in code stopped it — the fact "which table is the live sales
feed" lived only in a human's head. This module makes that fact CODE, and the owner asked to extend the
same discipline to every module ("one by one … so we don't duplicate anything or miss anything"):

    • Read a data source, or add a metric/report? Reference the constants here — never hardcode a raw
      table name at the call site. If the source-of-truth table ever changes, it changes in ONE place.
    • The machine-readable dependency map is still commcalc.data_lineage (migrations 924/925) +
      docs/DATA_LINEAGE.md — query it for "what does X touch?". THIS module is the small runtime slice
      of that map that code dereferences, kept in sync with the SQL seed by
      harness_data_lineage_guard.py (which fails if they drift, an ingest table has no lineage edge, or
      freshness stops pointing at the live feed).

DON'T DUPLICATE, DON'T MISS. Before wiring a new feed or metric:
  1. Is its table already a value in INGEST_TABLES_BY_MODULE / an edge in 925_data_lineage_seed.sql?
     Then reuse it — do not stand up a second capture path for the same data (the "duplicate" risk).
  2. New EXTERNAL FEED (a file/API/scrape written to a table)? Add its table under the owning module in
     INGEST_TABLES_BY_MODULE AND add an `ingest` edge to the seed, then re-run the guard. The guard
     refuses a registered ingest table that has no lineage edge, so a new feed can't land undocumented
     (the "miss" risk).

SCOPE. This registry names EXTERNAL-FEED ingest tables (parsed from an uploaded/swept file, or pulled
from an external API/scrape) and the LIVE-vs-authoritative pairs behind freshness decisions — the two
places the 2026-08-30 class of bug lives. Purely in-app/derived/config tables (invoices, journal
entries, computed ledgers) are documented as lineage edges where they matter but are NOT feeds, so they
are not listed here.

Pure data + tiny pure helpers: no DB, no network, no heavy imports. Safe to import anywhere.
"""

# ── SALES (commcalc) ─────────────────────────────────────────────────────────────────────────────
# The hourly email/FTP sweep lands transactions here (clean ISO trans_date). THIS is the live feed:
# "is sales data flowing?" is answered by daily_sales_feed, not by the monthly upload.
LIVE_SALES_FEED = "daily_sales_feed"
# The MONTHLY 'sales' reconciliation upload. Moves only when a monthly file is loaded — so it is NOT a
# freshness signal on its own. Authoritative for month-close reconciliation; the daily feed is promoted
# into it. Displayed sales read the UNION of the two (see SALES_DISPLAY_SOURCES).
MONTHLY_SALES = "raw_sales"
# What the display aggregation (_sales_cell_agg) reads: the union, so a fresh daily feed keeps every
# report current between monthly uploads.
SALES_DISPLAY_SOURCES = (LIVE_SALES_FEED, MONTHLY_SALES)

# ── OTHER NAMED commcalc feeds referenced by name in code ─────────────────────────────────────────
CUSTOM_CAPTURE = "raw_custom_import"      # b2b custom sheets (Activation Details, Bill Payment, …) → JSONB
EPAY_DAILY = "raw_epay_daily_tx"          # Boost ePay settlement
MA_DAILY_TX = "raw_ma_daily_tx"           # VidaPay / Total MA daily transactions
DAILY_CLOSING = "daily_closing"           # employee cash + tender declaration (owned by the closing module)
# The five commission-money feeds that were only ever named as literals inside
# `COMMISSION_CALC_FEEDS` below. Named here because the §CORE-17 drill-downs have to say WHICH table a
# figure was read out of, and a declaration restating a table name at the call site is the copied fact
# this registry exists to prevent (§CORE-17.3k).
PAYMENT_DETAIL = "raw_payment_detail"     # ePay Commission Payment Detail (#50273)
MI_RESIDUAL = "raw_mi"                    # ePay MI / ATU residual report
COMP_REPORT = "raw_comp_report"           # ePay Comprehensive Compensation report (#100614)
MA_COMMISSION = "raw_ma_commission"       # VidaPay / master-agent MA Commission Details
CARRIER_STATEMENT = "carrier_commission"  # the direct-carrier statement landing (mig 065)
#: THE CARRIER'S OWN DAILY-ACTIVITY REPORTS, at the two grains it publishes them at. Both were
#: already spelled as LITERALS inside this very file — in `COMMISSION_CALC_FEEDS`, in
#: `INGEST_TABLES_BY_MODULE`, in `REQUIRED_CONTENT_COLUMNS`, in `DATA_DATE_COLUMN_BY_TABLE` and in
#: `FEED_LABEL_BY_TABLE`: five copies of one name in the registry whose whole job is to hold it once.
#: Named here and dereferenced at all five sites in the same commit (string-identical values, no
#: read and no label changed), because the feed-vs-transactions drill-down has to say which relation
#: a carrier's claim was read out of and a sixth copy at that call site is the divergence this file
#: exists to stop. `harness_batch19_drilldown.py` §A fails the build if a literal returns.
DLAR_REP = "raw_dlar_rep"                 # the carrier's daily activity report, per advocate
DLAR_STORE = "raw_dlar_store"             # the same report, per door
#: THE STORED DENIED-APPEAL RECOVERY RUN (`commcalc.appeal_recovery`, mig 098). Not an ingest feed:
#: the recovery engine delete-then-inserts its own snapshot into it, which is why a figure on the
#: Appeal Recovery report names THIS relation and names an evidence feed only where the snapshot
#: row recorded one (§CORE-17.3j, the `DISCREPANCY_SNAPSHOT` posture one report along).
RECOVERY_LEDGER = "appeal_recovery"
#: THE MULTI-MONTH INSTALLMENT LADDER (`commcalc.plan_installment_schedule` + its month lines, mig
#: 201). A DECLARED number rather than a measurement: what a month WOULD pay is config somebody
#: saved, so the provenance of an EXPECTED figure is the ladder and not any carrier feed
#: (§CORE-17.3m's posture, applied to a payout schedule rather than to a target).
INSTALLMENT_SCHEDULE = "plan_installment_schedule"
#: NO CONSTANT IS ADDED HERE FOR THE APPROVED EXPECTED-TO-EARNED MOVES, and that is deliberate.
#: `commcalc.installment_promote` (mig 258) ALREADY has one home — `expected_commission.TABLE`, the
#: module that owns the promote — so the drill-down declaration dereferences THAT by identity. A
#: constant here would be a second copy of a name this repo already keeps once, which is the defect
#: this file exists to prevent rather than an instance of the cure (§19.18).
#: The product reference snapshot a per-item COST is looked up in. It was already named FOUR times in
#: this file as a literal — in the module's ingest list, the no-month reason, the snapshot set and the
#: cadence note — and the accessory cost audit's drill-down had to name it a fifth time to say where
#: Option A's corrected cost came from (§CORE-17.3o). Named ONCE here and dereferenced instead:
#: naming a fact is only half the fix, and the other half is not making a second copy of it.
PRODUCT_CATALOG = "raw_catalog"           # the POS product catalogue (cost / SKU reference)
#: The report snapshot the Pay Discrepancy report is READ OUT OF (mig 312's canonical DDL). It is not
#: an ingest feed: two engines delete-then-insert their own `source` slice into it, which is why a
#: figure on that report names this table and names a FEED only where the writer recorded one.
DISCREPANCY_SNAPSHOT = "discrepancy_results"

# ── THE DEVICE-ASSET FAMILY'S OWN TABLES (§CORE-17.3l, batch 12) ─────────────────────────────────
# Named here for the same reason the five commission-money feeds were (§CORE-17.3k): the asset
# drill-downs have to say WHICH table a figure was read out of, and a declaration restating a table
# name at the call site is the copied fact this registry exists to prevent. `asset_ledger` already
# appeared as a literal in five maps below and now dereferences this constant in all of them —
# naming a fact is half the fix, deleting every copy in the same commit is the other half
# (harness_feed_watchdog §F8).
#
# ONLY `ASSET_LEDGER` IS A REGISTERED INGEST FEED. The other four are stored relations the platform
# writes itself — a POS inventory snapshot typed in on the recon page, two inter-store borrowing
# tables, and the distributor PayGo batches the portal sweep lands — so they are NAME constants, the
# `DISCREPANCY_SNAPSHOT` shape, not entries in `INGEST_TABLES_BY_MODULE`.
ASSET_LEDGER = "asset_ledger"             # the distributor's unit ledger (Asset_Lending.xlsx)
#: The per-store × device-type UNIT snapshot the inventory recon compares the ledger against. Typed
#: in (or uploaded) on `/commcalc/asset/inventory-recon`; the $ side of the SAME file lands in
#: `inventory_value` instead, which IS a registered feed. That asymmetry is reported in §CORE-17.3l.
POS_INVENTORY_SNAPSHOT = "b2b_inventory"
STORE_BORROWINGS = "store_borrowings"              # one row per inter-store loan (mig 018)
STORE_BORROWING_PAYMENTS = "store_borrowing_payments"   # its paybacks, by `borrowing_id`
PAYGO_BATCHES = "vip_paygo_payments"       # one row per weekly distributor lending batch
#: The distributor's INVOICE feed — one row per invoice raised against the dealer account, landed by
#: the portal sweep or by the workbook import on `/commcalc/vip`. Named here because the
#: device-financing drill-downs have to say which relation an invoice figure was read out of
#: (§CORE-17.3p, batch 16), and a declaration restating the name at the call site is the copied fact
#: this registry exists to prevent. Like the four constants above it is a NAME constant and not an
#: entry in `INGEST_TABLES_BY_MODULE`: this file held NO literal copy of it before, so there is
#: nothing here to re-key — the `.table(...)` reads that address it are PostgREST call sites, the
#: same posture `ASSET_LEDGER` keeps for its own 74.
DISTRIBUTOR_INVOICES = "vip_invoices"      # one row per distributor invoice (sweep / workbook)
#: THE CARRIER-AGNOSTIC PER-IMEI PAYABLE LEDGER (mig 095) — one row per device the platform owes
#: for, materialized from every carrier's own declared `payable_source_map`. Named here because the
#: aging report serves TWO provenances per row since §COMMCALC-28: a Boost/VIP device was read out of
#: `ASSET_LEDGER` and a Total device out of this relation, and a panel that named one for both would
#: be wrong on every row of the other tenant (§CORE-17.3y, batch 25). Like the constants above it is
#: a NAME constant, not an entry in `INGEST_TABLES_BY_MODULE`: the platform WRITES this relation from
#: feeds that are themselves registered, so registering it again would claim a second ingest edge
#: into the same money. This file held no literal copy of it, so there is nothing here to re-key.
DEVICE_PAYABLE_LEDGER = "device_payable_ledger"

# ── THE FIVE RELATIONS THE FINANCE DEVICE-BILLING AND INVENTORY FAMILIES HAVE TO NAME ────────────
# (§CORE-17.3w, batch 23.) Same reason as every block above it: a drill-down must tell the reader
# WHICH relation a figure was read out of, and a declaration restating a table name at its own call
# site is the copied fact this registry exists to prevent. None of the five is a new ingest edge, so
# all five are NAME constants — the `DISTRIBUTOR_INVOICES` / `DISCREPANCY_SNAPSHOT` shape — and not
# entries in `INGEST_TABLES_BY_MODULE`.
#
# WHAT WAS RE-KEYED, AND WHAT DELIBERATELY WAS NOT. Naming a fact is half a design fix and deleting
# every copy is the other half (§19.18), so every literal copy THIS FILE held is re-keyed onto these
# constants in the same commit: `inventory_value` appeared in SEVEN maps below and
# `vip_credit_memos` in one, and all eight now dereference (string-identical values, no map changed).
# The three invoice/device-level names held no copy here at all. What is NOT renamed is the
# PostgREST `.table("…")` / `_fetch_all(…)` reads across the finance module: a read is not a second
# statement about where a FIGURE came from, and re-pointing them is a finance-module change rather
# than a drill-down one — the posture `ASSET_LEDGER` keeps for its own 74 reads and
# `FINANCIAL_STATEMENTS` states for the statement engine's.
#: The distributor's invoice LINES — what was billed, with the money on it. The device-purchase
#: figure is read out of this relation and nothing else.
DISTRIBUTOR_INVOICE_LINES = "vip_invoice_lines"
#: The SERIALISED units on those invoices — one row per unit that actually arrived with a serial on
#: it. The unit COUNT comes from here and the money does not, which is why the two are two grains.
DISTRIBUTOR_INVOICE_UNITS = "vip_invoice_devices"
#: The distributor's scraped "Weekly Incentive Credit" memos — the money that ARRIVED, which the
#: credit-memo reconciliation measures the residual report against.
DISTRIBUTOR_CREDIT_MEMOS = "vip_credit_memos"
#: The emailed inventory-aging VALUE snapshot, per store: the swept total and any manual override a
#: human typed over it. Both the balance-sheet inventory line and its reconciliation read it.
INVENTORY_VALUE_SNAPSHOT = "inventory_value"
#: The same aging report at DEVICE grain — the unsold phones on the shelf, one row per device type
#: per store. The reconciliation's "devices" side; the UNIT sibling of it is
#: `POS_INVENTORY_SNAPSHOT` above.
INVENTORY_AGING_DEVICES = "inventory_aging_device"

# ── THE TWO STORED RELATIONS THE EXECUTIVE-MONTH FAMILY HAS TO NAME (§CORE-17.3q, batch 17) ──────
# Same reason as the four above: a drill-down must say WHICH relation a figure was read out of, and a
# declaration restating a table name at the call site is the copied fact this registry exists to
# prevent. Neither is an ingest feed — one is a penalty the platform records itself and the other is
# the store roster — so both are NAME constants, the `DISCREPANCY_SNAPSHOT` shape, not entries in
# `INGEST_TABLES_BY_MODULE`.
#
# `ops_chargeback` WAS ALREADY SPELLED TWICE, in `closing/ops_chargeback_drilldown.CHARGEBACK` and
# `closing/envelope_drilldown.CHARGEBACK`, which is the divergence this registry forbids. Both
# dereference this constant now (string-identical value, no read changed) — naming a fact here is
# half the fix and deleting every copy in the same commit is the other half.
OPS_CHARGEBACK = "ops_chargeback"          # commcalc.ops_chargeback (mig 504) — the recorded penalty
#: The STORE ROSTER (`storeops.stores`, mig 003). A figure that counts STORES — "how many of our
#: stores have a target a manager saved" — is read out of the roster, so the roster is that figure's
#: provenance and has to be nameable. Note the SCHEMA differs from every other constant in this
#: block, which is why a panel naming it carries its schema explicitly.
STORE_ROSTER = "stores"
#: THE SCHEDULE (`storeops.shifts`, mig 003). A figure whose rows are store-DAYS has to say which
#: days it even looked at, and for a trading-day question that is the schedule — so "which relation
#: records a store's scheduled day" is a fact a drill-down dereferences rather than spells.
#:
#: `shifts` WAS ALREADY SPELLED TWICE, in `account/liabilities_drilldown.SHIFTS` and
#: `commcalc/targets_drilldown.SHIFTS`, which is the divergence this registry forbids — the same
#: shape `OPS_CHARGEBACK` had one batch ago. Both dereference this constant now (string-identical
#: value, no read changed), and `harness_batch18_drilldown.py` §A fails the build if either literal
#: returns. Naming a fact here is half the fix; deleting every copy in the same commit is the other.
SCHEDULE = "shifts"
#: THE TIME CLOCK (`storeops.timelog`, mig 003). A figure that is output PER HOUR WORKED has to say
#: which relation the hours came out of, and for a worked hour that is the closed punch — never the
#: schedule, which records an intention (§CORE-17.3t, batch 20). Same schema caveat as the two above.
#:
#: `timelog` WAS ALREADY SPELLED as a literal in `account/liabilities_drilldown.TIMELOG`, which is
#: the divergence this registry forbids — the same shape `shifts` had one batch ago and
#: `ops_chargeback` the batch before that. That copy dereferences this constant now
#: (string-identical value, no read changed), and `harness_batch20_drilldown.py` §A fails the build
#: if the literal returns inside any drill-down declaration. Naming a fact here is half the fix;
#: deleting every copy in the same commit is the other half (§19.18).
#:
#: SCOPE, STATED SO THE NEXT READER DOES NOT RE-DERIVE IT — the same scope `STORE_EXPENSES` below
#: already carries. This constant is the one home for the PROVENANCE NAME: what a drill-down tells a
#: reader a figure was read out of. It is deliberately NOT a rename of the forty-odd PostgREST
#: `.table("timelog")` reads and writes across the time-clock, payroll and attendance paths, of
#: `storeops/payroll_evidence.PUNCH_TABLE` (a constant whose only use is such a read), of
#: `core/scope.WORKED_AT_SOURCES` (its own table/column/soft-delete registry, with its own lock), or
#: of the `source_table="timelog"` audit arguments: a read or a write is an I/O call, not a second
#: statement about where a figure came from, and re-pointing them is a payroll change rather than a
#: drill-down one — and payroll work is the payroll-workforce agent's (CLAUDE.md). `shifts` above
#: coexists with `WORKED_AT_SOURCES` on exactly these terms. What IS forbidden, and locked, is a
#: second copy of this name inside a drill-down DECLARATION, which is where the divergence this
#: registry exists to stop actually happens.
#:
#: THE REP-PAY SNAPSHOT IS DELIBERATELY *NOT* A CONSTANT HERE. "Which relation holds what the last
#: commission calculation wrote down" already has one home — `carrier_pay_drilldown.REP_COMMISSIONS`,
#: named there by batch 11 — and four declarations dereference THAT. A constant for it here would be
#: the second copy this registry exists to prevent, which is exactly the call batch 19 made for the
#: approved expected-to-earned moves. Written down so the next reader does not re-derive it.
TIME_CLOCK = "timelog"
#: THE ENTERED EXPENSE SHEET (`commcalc.store_expenses`, mig 002). The relation a store's monthly
#: expenses are typed into, and therefore the provenance of every "−Expenses" figure.
#:
#: SCOPE, STATED SO THE NEXT READER DOES NOT RE-DERIVE IT. This constant is the one home for the
#: PROVENANCE NAME — what a drill-down tells a reader a figure was read out of. It is deliberately
#: NOT a rename of the twenty-odd PostgREST `.table("store_expenses")` reads across the expense
#: writers and readers: a read is an I/O call, not a second statement about where a figure came
#: from, and re-pointing them is an expense-module change rather than a drill-down one. What IS
#: forbidden, and locked by `harness_batch18_drilldown.py` §A, is a second copy of this name inside
#: a drill-down DECLARATION — which is where the divergence this registry exists to stop happens.
STORE_EXPENSES = "store_expenses"
#: THE COMPUTED FINANCIAL STATEMENTS (`commcalc.account_statements`). The stored snapshot a
#: statement run WROTE DOWN for one (period, scope) — so a figure read out of it names the SNAPSHOT
#: and the trail stops there, which is the §CORE-17.3j ruling (batch 10) rather than a claim about
#: the feeds that run happened to read.
#:
#: `account_statements` WAS ALREADY SPELLED as a literal in `account/statement_drilldown.SNAPSHOT`,
#: which is the divergence this registry forbids — the same shape `timelog` had one batch ago and
#: `shifts` the batch before that. That copy dereferences this constant now (string-identical value,
#: no read changed), and `harness_batch21_drilldown.py` §A fails the build if the literal returns
#: inside any drill-down declaration or lineage registry. Naming a fact here is half the fix;
#: deleting every copy in the same commit is the other half (§19.18).
#:
#: SCOPE, STATED SO THE NEXT READER DOES NOT RE-DERIVE IT — the same scope `TIME_CLOCK` and
#: `STORE_EXPENSES` above already carry. This constant is the one home for the PROVENANCE NAME: what
#: a drill-down tells a reader a figure was read out of. It is deliberately NOT a rename of the
#: PostgREST `.table("account_statements")` reads and writes across the statement engine, the
#: statement filter, the P&L range and the finance router, nor of the `control_box_api` registry row
#: that names the same table as a thing to monitor: a read, a write or a monitoring row is not a
#: second statement about where a FIGURE came from, and re-pointing them is a finance-module change
#: rather than a drill-down one — and finance work is the finance agent's (CLAUDE.md).
FINANCIAL_STATEMENTS = "account_statements"
#: THE EMPLOYEE RECORD (`storeops.employees`, mig 003). Where a person's pay rate, their pay basis
#: and their identity live — so every MONEY figure folded out of hours names it beside whichever
#: relation answered for the hours, because a gross cannot be read without the two together.
#:
#: `employees` WAS ALREADY SPELLED as a literal in `account/liabilities_drilldown.EMPLOYEES`, which
#: is the divergence this registry forbids — the same shape `timelog`, `shifts`, `ops_chargeback` and
#: `account_statements` each had when they arrived here. That copy dereferences this constant now
#: (string-identical value, no read changed), and `harness_batch24_drilldown.py` §A fails the build if
#: the literal returns inside any drill-down declaration. Naming a fact here is half a design fix;
#: deleting every copy in the same commit is the other half (§19.18).
#:
#: SCOPE, STATED SO THE NEXT READER DOES NOT RE-DERIVE IT — the same scope `TIME_CLOCK` carries. This
#: is the one home for the PROVENANCE NAME, not a rename of the hundred-odd `.table("employees")`
#: reads across the roster, payroll, scheduling and onboarding paths: a read is an I/O call, not a
#: statement about where a figure came from.
EMPLOYEE_ROSTER = "employees"
#: HAND-ENTERED HOURS (`storeops.manual_hours`, mig 045). A correction somebody typed for a day the
#: clock did not answer for, and therefore the provenance of an hours figure that came from neither
#: the punch nor the plan.
#:
#: `manual_hours` WAS ALREADY SPELLED as a literal in `account/liabilities_drilldown.MANUAL_HOURS`,
#: and that copy dereferences this constant now — same posture, same commit, same lock as
#: `EMPLOYEE_ROSTER` above.
#:
#: REPORTED, NOT PAPERED OVER: `GET /storeops/payroll` does not read this table at all (§14y), so the
#: Payroll Report's own drill-down deliberately never names it — a provenance claim about a read that
#: did not happen would be worse than the gap. The gap is written down in
#: `storeops/payroll_hours_drilldown.py` and in §CORE-17.3x instead.
MANUAL_HOURS = "manual_hours"


# ── WHICH TABLE A BILL-PAYMENT PROCESSOR'S FEED LANDS IN ───────────────────────────────────────
# A drill-down has to tell the reader WHERE a figure came from (§CORE-17), and for the processor leg
# of the bill-payment recon that depends on which processor the tenant resolved. The resolution
# itself is `metric_source_of_truth` (config, never a branch); this maps its answer to the table the
# feed actually lands in, so a provenance line DEREFERENCES the fact instead of restating a table
# name at the call site — which is the whole point of this registry.
#
# Keyed by the processor value `metric_source_of_truth` stores, so a new processor is a ROW here plus
# its ingest edge, never a branch in a report.
BILLPAY_PROCESSOR_FEEDS = {
    "epay": EPAY_DAILY,
    "vidapay": MA_DAILY_TX,
}


def billpay_processor_feed(source_label):
    """The feed table behind a resolved bill-payment processor, from the label the closing resolvers
    already return (`"processor:<name>"`, or `"none"`).

    An UNKNOWN processor returns None, and a drill-down then reports an origin it could not trace
    rather than naming a table on a guess. A wrong provenance claim is worse than an admitted gap.
    """
    name = str(source_label or "").strip().lower()
    if name.startswith("processor:"):
        name = name.split(":", 1)[1].strip()
    return BILLPAY_PROCESSOR_FEEDS.get(name)


# ── WHICH FEEDS THE WRITER OF A DISCREPANCY ROW READ ───────────────────────────────────────────
# The Pay Discrepancy report is read out of `discrepancy_results`, a SNAPSHOT two engines write
# (mig 312's table comment is the one home for that fact): the legacy Boost engine
# (`source` 'boost', NULL on pre-312 rows) and the B2B ↔ MA recon (`source` 'ma'). §CORE-17.3j says a
# figure read out of a snapshot NAMES THE SNAPSHOT, and names a feed only where the writing code read
# that feed unconditionally — so this maps the STORED `source` value to the feeds its writer really
# reads for every row it writes.
#
# Keyed by that stored value, so a THIRD writer is a ROW here plus its derivation, never a branch in a
# report (RULE TWO). An unknown writer resolves to an EMPTY tuple and the drill names only the
# snapshot: a wrong provenance claim is worse than an admitted gap.
#
# ONLY THE WRITER THAT RECORDS ENOUGH TO BE NAMED IS LISTED, and that is deliberate. The legacy
# engine reads three feeds but WHICH of its two money feeds answered a given row depends on that row's
# comp type, and it records nothing about it — so it resolves to nothing here and the drill names the
# snapshot alone, which is the truth. Naming all three on every one of its rows would be a provenance
# claim nothing checked. The recon's rows carry their own `evidence` instead (see
# `commcalc/carrier_pay_drilldown.evidence_tables`), which is a stored fact and is read per row.
DISCREPANCY_WRITER_FEEDS = {
    # The key is `ma_recon.SOURCE_MA` — the value that engine stamps on every row it writes.
    "ma": (MA_COMMISSION, MA_DAILY_TX),
}


def discrepancy_writer_feeds(source):
    """The feeds the writer named by a `discrepancy_results.source` value reads unconditionally.

    An unrecognised (or absent) writer returns `()` — the drill then names the snapshot alone and the
    row says in words that its writer recorded no provenance. PURE."""
    return DISCREPANCY_WRITER_FEEDS.get(str(source or "").strip().lower(), ())


# ── THE COMMISSION-PER-DEVICE FEED — which table says "the carrier paid (or clawed back) on this IMEI" ─
# Owner 2026-09-24 (index §11b): inventory is "crashed against the sales report by invoice and the
# commission received reports". ONE fact, ONE home: the per-line vendor rebate / commission history
# (mig 1005) — one row per rebate COMPONENT per line, the device IMEI on every row, `earned_amount`
# SIGNED (a reversal / chargeback line is negative), the customer and invoice as the carrier states them.
# Every reader (the inventory-integrity engine, its router, the Inventory-vs-Sold report) DEREFERENCES
# these names; `harness_inventory_integrity_lock.py` fails the build on a copied table literal.
COMMISSION_PER_DEVICE_FEED = "raw_vendor_rebate"
# The columns a per-device reader takes from it, and what each MEANS (the header lies on two of them —
# §27.6: `imei` is the file's 'Related Tracking Number', `mdn` its 'Tracking Number'). `earned` is the ONLY
# money column summed (never `unit_amount`, which is unsigned — §27.7); `reversal_flag` is the feed's own
# chargeback cell, read as evidence beside the sign.
COMMISSION_PER_DEVICE_COLUMNS = {
    "device": "imei", "mobile": "mdn", "customer": "customer_name", "customer_ref": "customer_ref",
    "invoice": "invoice_no", "sold_on": "sold_on", "earned": "earned_amount", "reversal_flag": "charge_back",
    "device_name": "device_name", "device_sku": "device_sku", "rate_plan": "rate_plan", "store": "store",
}


def commission_per_device_select() -> str:
    """The select list a per-device commission reader uses — derived from COMMISSION_PER_DEVICE_COLUMNS."""
    return ",".join(dict.fromkeys(COMMISSION_PER_DEVICE_COLUMNS.values()))

# ── LIVE-vs-MONTHLY PAIRS — the freshness trap, per feed ──────────────────────────────────────────
# A freshness / "is data flowing?" check must read the LIVE (first) table, NEVER the monthly (second).
# Reading the monthly table is the 2026-08-30 false alarm this registry guards against. Each new pair a
# module introduces (e.g. the POS builtin stream) belongs here so the guard can hold the invariant.
LIVE_VS_MONTHLY_PAIRS = {
    "sales": (LIVE_SALES_FEED, MONTHLY_SALES),
    # POS builtin stream (pos/commcalc_feed.py MODE_TABLES): the in-house POS writes its OWN daily and
    # monthly tables, then promotes into the sales feed/raw_sales. Same trap — a freshness read here must
    # take the daily stream, never the monthly one.
    "pos_builtin": ("pos_builtin_daily_sales", "pos_builtin_sales"),
}

# ── WHAT THE COMMISSION RUN CALCULATION READS — the tables whose landing re-runs it (owner 2026-09-28) ─
# Owner: "when sept is uploaded the system should calculate automatically without manual intervention".
# ONE fact, ONE home: the period-keyed feed tables `router._run_calculation` reads — `_calc_inputs` (the sales
# basis via `_fetch_sales_unified`, payment detail, MI, the two DLAR grains), `_apply_new_engines` (the
# carrier statement) and `sale_installment_engine`'s paid-gate (the two MA feeds). A landing into one of these
# for org X, period P is what `auto_calc.landed` turns into ONE standard Run Calculation of (X, P). Index §6l.
# `harness_auto_calc_lock.py` fails the build if `_calc_inputs` starts reading a period-keyed table that is
# not listed here (the calculation would then read data whose arrival re-runs nothing).
COMMISSION_CALC_FEEDS = (
    LIVE_SALES_FEED, MONTHLY_SALES,
    PAYMENT_DETAIL, MI_RESIDUAL, DLAR_REP, DLAR_STORE,
    CARRIER_STATEMENT,
    MA_COMMISSION, MA_DAILY_TX,
)
# Sales tables that land BESIDE the pay basis but that the calculation does not read (index §30.13 / §32).
# Their landers call the same hook, which answers `not_a_calc_input` — so the day the calculation starts
# reading one of them, moving it into COMMISSION_CALC_FEEDS is the whole change; no lander is touched.
SALES_SIBLING_TABLES = ("raw_sales_invoice", "raw_sales_invoice_tender", "raw_sales_product")

# ── EXTERNAL-FEED INGEST TABLES, BY OWNING MODULE ─────────────────────────────────────────────────
# Every table here must have an `ingest` edge in 925_data_lineage_seed.sql (the guard enforces it), so a
# new feed cannot land undocumented. Extended one module at a time (owner 2026-08-30).
INGEST_TABLES_BY_MODULE = {
    # commcalc — the b2b / POS / MA report importers. Union of upload_file's TABLE_MAP and the special
    # handlers (x_report → pos_tender_summary, inventory_aging → inventory_value, ma_overview,
    # custom reports → raw_custom_import, ePay → raw_epay_daily_tx). Mirrors _TRACE_TARGET_TABLE.
    "commcalc": (
        "raw_sales", "daily_sales_feed", "raw_payment_detail", "raw_mi",
        DLAR_REP, DLAR_STORE, PRODUCT_CATALOG, "raw_categories",
        "raw_comp_report", "raw_ma_commission", "raw_ma_daily_tx", "raw_ma_fulfillment",
        "pos_tender_summary", INVENTORY_VALUE_SNAPSHOT, "ma_overview_upload",
        "raw_custom_import", "raw_epay_daily_tx",
        # Merchant-processor portal scrape (owner 2026-09-04, migs 955/956). The daily pull from the
        # three card-processor portals — PayAnywhere/Payments Hub (the EXTERNAL credit-card terminal
        # both current tenants run, the "white machine"), TransFirst TransLink and ClientLine/
        # BusinessTrack (the POS merchant providers). Settlement is the day-grain feed the closing
        # recon tallies against what employees declared; the batch table is the funding grain the
        # cash/deposit recon reads. Two tables because they are two GRAINS — summing them
        # double-counts, which is exactly the confusion a lineage edge exists to prevent.
        "merchant_settlement_day", "merchant_settlement_batch",
        # Back-office POS report portal (owner 2026-10-12, mig 1078). The portal's own account of what
        # each worker sold, per declared category, per day — pulled by the shared portal loop
        # (index §COMMCALC-07). It is the PORTAL's claim, landed beside what the reps declared at
        # closing (commcalc.closing_metric_line, mig 1072) and never merged into it: merging them
        # would destroy the comparison the feed exists for.
        "portal_metric_day",
        # Per-line vendor rebate/commission history (mig 1005), landed by the mapped ingest
        # (/commcalc/upload-mapped). A LANDING ZONE: earned_amount is what the carrier owes,
        # collected_amount what it has paid, and no P&L / GP / payout / balance-sheet path reads it
        # while "is an earned rebate a receivable?" is an open owner decision. Distinct from the
        # `pos` module's activation_rebate_ledger below, which is the AGGREGATE that books — two
        # edges because they answer two different questions, not two paths to one answer.
        # (Dereferenced — it is THE commission-per-device feed named above, not a second spelling.)
        COMMISSION_PER_DEVICE_FEED,
        # The POS by-product sales aggregate's OWN table (mig 1011, landing identity 2026-09-20) —
        # layout pos_product_sales through the mapped ingest / the intake's `pos` kind. Product-level
        # rows (SKU, cost, selling price); never summed beside raw_sales (double count). Read by the
        # onboarding verify + report links only; no money path.
        "raw_sales_product",
        # Sales by invoice with the tender types (mig 1012, owner 2026-09-21): ONE ROW PER INVOICE
        # (raw_sales_invoice) + one row per invoice × declared tender / tax-component column
        # (raw_sales_invoice_tender), landed by the intake's `invoice` kind through the mapped ingest.
        # Read by the onboarding Stage-4 verify (invoice totals, the tender split beside the X-report
        # per store-day, Σ tax, the report links); no money path reads either yet — the tax aggregator
        # and the closing basis are PROPOSED in index §30.13.
        "raw_sales_invoice", "raw_sales_invoice_tender",
    ),
    # pos — the in-house POS. Its builtin stream (commcalc.pos_builtin_daily_sales /
    # commcalc.pos_builtin_sales) promotes into the sales feed; receipt OCR and the carrier vendor-rebate
    # xlsx are its other external ingests. pos.* live in the `pos` schema (qualified keys); the two
    # commcalc-schema tables use bare keys, matching the seed's affected_key convention.
    "pos": (
        "pos_builtin_daily_sales", "pos_builtin_sales",
        "pos.sales", "pos.receipt_imports", "pos.customers", "pos.activations",
        "activation_rebate_ledger",
    ),
    # closing — employee daily cash + tender declaration and the per-org tender-map config sheet.
    "closing": (
        "daily_closing", "closing_tender_def", "closing_tender_map",
    ),
    # storeops — roster/identity template uploads, the external merchant-ID mapping, and the Google
    # Reviews API sweep. storeops.* are in the `storeops` schema (qualified keys); store_mapping is the
    # commcalc-schema mirror the roster upload writes (bare key, per the seed convention).
    "storeops": (
        "storeops.employees", "storeops.stores", "storeops.store_alias",
        "storeops.store_merchant_id", "store_mapping",
        "storeops.google_review_store", "storeops.google_review_snapshot", "storeops.google_review_item",
        # THE B2B CLOCK IN / CLOCK OUT REPORT (mig 1073, owner ask 2026-10-10, index §14z). Landed by
        # the EXISTING email sweep (upload_type 'clock_punch') through the EXISTING column_mapping
        # engine — no second sweep, no second mapper. It is a CLAIM about each store-day and is
        # deliberately NOT storeops.timelog: our own punches are a measurement, the report is somebody
        # else's assertion about the same day, and the VARIANCE between them is what a DM corrects.
        # Two tables answering two different questions, not two paths to one answer.
        "storeops.raw_clock_punch",
    ),
    # billing — the ONLY external feed here is the platform-cost connector (it pulls each store's
    # platform bill from an external source). Plans/invoices/pricing are in-app config, not feeds.
    "billing": (
        "storeops.platform_billing_connector",
    ),
    # asset — the Asset Lending ledger, parsed from an uploaded Asset_Lending.xlsx (staging → atomic swap).
    "asset": (
        ASSET_LEDGER,
    ),
    # supply (mig 1021, index §36) — the vendor catalog price snapshot: each supply vendor's products, prices,
    # pack sizes and stock, landed by ONE lander (supply/store.land_catalog) from two routes — the price-compare
    # kit's products.json/csv upload and the read-only portal read (live login or the data-sources scheduler).
    # Read by Price Compare and the cart optimizer only; no P&L / payout path reads it.
    "supply": (
        "vendor_catalog_price",
    ),
    # account — the franchise royalty report (mig 1022, index §37): the franchisor's monthly statement per center,
    # uploaded as a PDF / saved HTML / pasted text (or typed on the manual form) through /account/royalty/import |
    # /manual. Header + lines, one report per org × center × month (a re-import replaces it). The account module was
    # feed-less (a compute engine) until this; it now owns exactly these two tables and still derives everything else.
    "account": (
        "royalty_report", "royalty_report_line",
    ),
    # Other modules are added in subsequent PRs, one by one.
}


# ── FRESHNESS COLUMN per table — which timestamp reflects DATA ARRIVAL ────────────────────────────
# A "is data flowing?" probe must read the column that moves when new data lands. For most raw tables
# that is created_at (the DB stamps it on insert). daily_sales_feed is the exception: rows are re-inserted
# / promoted, so its true arrival stamp is `uploaded_at`, NOT created_at — probing created_at made a
# feed-only tenant (luxelink) read newest_ingest_at=None, so its P&L/Balance-Sheet never auto-computed
# (permanently empty) and the books-stale banner never fired. account/autocompute._PERIOD_SOURCES already
# lists daily_sales_feed with uploaded_at first (fix dcb0807); this registry makes that rule the ONE place
# it's written down, and the guard locks it so it can't silently regress.
#
# EVERY table whose arrival column is NOT created_at belongs here — that is the whole point of the map.
# `harness_ingest_freshness.py` §C fails the build if `account/autocompute._PERIOD_SOURCES` names a
# non-created_at arrival column for a table this dict does not declare, so the two copies of the fact
# cannot drift apart again (they already did once: the registry held daily_sales_feed and nothing
# dereferenced it, while autocompute carried its own copy).
FRESHNESS_COLUMN_BY_TABLE = {
    "daily_sales_feed": "uploaded_at",
    # VIP sweep landing tables: the sweep stamps `swept_at` when a row lands. `created_at` exists but
    # moves on re-write, so it is the same trap as daily_sales_feed — declared here rather than left
    # implicit in the one list that happened to know about it.
    PAYGO_BATCHES: "swept_at",
    DISTRIBUTOR_CREDIT_MEMOS: "swept_at",
    # THESE THREE TABLES HAVE NO `created_at` AT ALL (measured against the live schema 2026-10-03), so
    # the default left `last_ingest_at` None and the watchdog could only ever say "stopped arriving" —
    # the §19.18 discriminator dead again, on three more feeds. Found the moment the watchdog began
    # covering every registered feed instead of three: `asset_ledger` reported 16 days late with no
    # arrival time to explain whether the file had stopped or its content had frozen.
    ASSET_LEDGER: "uploaded_at",
    "pos_tender_summary": "updated_at",
    INVENTORY_VALUE_SNAPSHOT: "updated_at",
    # The B2B clock report (mig 1073): rows are replaced per slice on a re-send, so `created_at` moves
    # on a re-write — exactly the daily_sales_feed trap. `arrived_at` is stamped once, when the row
    # first lands, and is the only honest answer to "is the clock report still arriving?".
    "storeops.raw_clock_punch": "arrived_at",
}

# ── WHICH COLUMNS MUST CARRY A VALUE — the COMPLETENESS axis (owner defect 2026-09-26, index §19.28) ──
# §19.18 gave the platform two questions about a feed: is it still ARRIVING (`last_ingest_at`), and is its
# CONTENT moving (`latest_data_date`). Both can be green while a feed has quietly become USELESS, because
# there is a third question neither asks: **do the columns still carry values?**
#
# MEASURED, house org, `commcalc.raw_dlar_rep`, 2026-09-26 — rows kept arriving and the data date kept
# moving, and in JULY 2026 seven columns stopped carrying anything at all, together:
#
#   period      rows   store / door_*      ga_prepaid + ga_postpaid (sum)
#   March 2026   110   populated            1508 / 16
#   April 2026   103   populated            1071 /  2
#   May 2026      81   populated             403 /  3
#   June 2026     75   populated             621 /  2
#   July 2026     58   ALL BLANK               0 /  0     <-- the advocate report changed shape
#   August 2026   44   ALL BLANK               0 /  0
#   September     45   ALL BLANK               0 /  0
#
# One upstream change, seven columns: `normalize_rep` maps `store` and `door_address` from the record's
# `address`, `door_name/city/state/zip` from `name/city/state/zip`, and `ga_prepaid`/`ga_postpaid` from
# `prepaid_activations`/`postpaid_activations`. The portal stopped sending that whole block. Nothing
# failed: 147 rep rows since July divided a bounty by a denominator that was no longer being sent, and
# the `if ga_prepaid > 0 else 0` guard turned every one of them into a measured KPI failure at 0%.
#
# A column that STOPS ARRIVING must say so, and WHICH columns those are is declared here — once — so the
# sweep that sees the portal's response dereferences the fact instead of carrying its own list.
REQUIRED_CONTENT_COLUMNS = {
    # the advocate (rep) DLAR: the door identity the store roll-down joins on, and the prepaid split the
    # Boost App attach rate is measured against.
    DLAR_REP: ("store", "ga_prepaid", "gross_adds", "atu_pct"),
    # the store DLAR: the join key, the three KPIs that are ONLY published at store grain, and — added
    # 2026-10-16 — the UNIT COUNTS. Those three were not declared, so when the store report stopped
    # sending them in October 2026 the sweep's status line said nothing about the store grain at all:
    # 28 stores filed 0 activations, 0 upgrades and 0 total activity beside populated attach rates
    # under a green "OK". A count is as much a declared column as a rate (index §COMMCALC-16).
    DLAR_STORE: ("address", "family_plan_pct", "tmr3", "aal_conversion",
                 "gross_adds", "total_upgrades", "total_acts"),
}


def required_content_columns(table: str):
    """The columns a row of `table` must actually CARRY, or () when nothing is declared for it. The ONE
    home of that fact — a caller reads it, never repeats it (`harness_kpi_registry_lock.py` §(h))."""
    return tuple(REQUIRED_CONTENT_COLUMNS.get(table) or ())


def content_arrival(rows, columns):
    """PURE. Per declared column: how many of these rows carry a value, and which columns carry NONE.

    → `{"rows": n, "columns": {col: {"filled": n, "empty": n}}, "empty_columns": [col, ...]}`

    "Carries a value" means not None, not a blank/whitespace string, and — for a NUMERIC column — not a
    whole-pull zero: a column that is 0 in EVERY row of a pull that has activations is a column that
    stopped being sent, not a month in which nobody sold anything. That distinction is the whole point;
    it is what makes July's `ga_prepaid` visible while a genuine zero for one rep is not. With no rows,
    nothing is reported empty — an empty pull is a different failure and the load guards already own it.
    stdlib only, no DB."""
    rows = list(rows or [])
    cols = tuple(columns or ())
    out = {"rows": len(rows), "columns": {}, "empty_columns": []}
    if not rows or not cols:
        return out
    for col in cols:
        filled = 0
        for r in rows:
            v = (r or {}).get(col)
            if v is None:
                continue
            if isinstance(v, str):
                if v.strip():
                    filled += 1
                continue
            try:
                if float(v) != 0.0:
                    filled += 1
            except (TypeError, ValueError):
                filled += 1          # a non-numeric, non-string value is still a value
        out["columns"][col] = {"filled": filled, "empty": len(rows) - filled}
        if filled == 0:
            out["empty_columns"].append(col)
    return out


# ── WHICH UPLOAD TYPES REPLACE THEIR WHOLE PERIOD ─────────────────────────────────────────────────
# An upload type here lands by DELETING the (org, period) slice and inserting the file's rows. Two
# consequences follow, and the second is why this list exists:
#
#   1. Re-ingesting the same report is idempotent — it rewrites what it already wrote.
#   2. Given two files of the SAME report covering the SAME period, ingesting the newer one ALONE
#      leaves the database in exactly the state that ingesting both in order would. The older file's
#      entire contribution is erased by the newer one's delete. It is redundant work, not lost data.
#
# Property 2 is what lets a sweep collapse a BACKLOG. It is a consequence of the replace semantics,
# not an assumption about the report being cumulative — nothing here needs to know that.
#
# WHY IT IS A DECLARED LIST AND NOT A GUESS (Boost, 2026-09-20). While the house mailbox's login was
# rejected, ~336 hourly copies of the same b2bsoft sales report piled up. The sweep drained them in
# ARRIVAL ORDER, each one deleting September and re-inserting a slightly larger file: 336 full 1.1 MB
# imports to reach a state the newest file alone describes, ~30 per sweep, with the feed showing a
# part-month for hours while it crawled — and going BACKWARD whenever an older message landed after a
# newer one. §19.19.
#
# AN UPLOAD TYPE NOT LISTED HERE IS NEVER COLLAPSED. Anything incremental, append-only, or whose slice
# is narrower than the period (the INGEST_PARTITION tables in `ingest_slice.py`, where a file replaces
# only its own store/account slice) must process every message — dropping one there would lose rows.
# Silence means "process them all", which is the safe answer for a type nobody has reasoned about.
FULL_REPLACE_UPLOAD_TYPES = frozenset({
    # The b2b daily sales export -> daily_sales_feed. Not in INGEST_PARTITION, so its ingest takes the
    # legacy wide delete of (org, period) and re-inserts: the definition of a whole-period replace.
    "daily_sales",
    # POS X-report tender summary -> pos_tender_summary, and the sales-trend / key-stats summaries:
    # each is a per-period SNAPSHOT of the same numbers, rewritten whole on every ingest.
    "x_report",
    "sales_trend",
    # ⚠ DELIBERATELY ABSENT: 'clock_punch' (the B2B clock in / clock out report, mig 1073). A clock
    # report for a store is sent PER STORE and often per week, so a day's slice arrives in several
    # files and each file is a FRAGMENT of the period, never a snapshot of it. Collapsing older copies
    # would silently drop whole stores' punches, and a dropped punch is exactly the variance this feed
    # exists to surface. Its ingest replaces its OWN key (org, store string, person string, day,
    # clock-in instant — the unique index in mig 1073), so a re-sent file is idempotent without a
    # period-wide delete. Stated here rather than left implicit, because `replaces_whole_period`
    # returning False by default would otherwise read as nobody having thought about it.
})


def replaces_whole_period(upload_type: str) -> bool:
    """True when ingesting the NEWEST file of this report makes older copies of the SAME report, for the
    same period, redundant — because the ingest replaces the whole (org, period) slice. Unknown or
    unlisted types return False: never collapse a report nobody has reasoned about."""
    return (upload_type or "").strip() in FULL_REPLACE_UPLOAD_TYPES


# ── MODULES AUDITED TO HAVE NO EXTERNAL FEED (owner 2026-08-30 census) ─────────────────────────────
# These modules were checked and own NO external-feed ingest: either pure in-app CRUD, or a compute/
# derive engine that READS the feeds above and writes computed tables (not feeds). Listed so "every
# module" is explicitly accounted for — nothing was skipped silently. The guard asserts none of these
# is also in INGEST_TABLES_BY_MODULE (a module can't be both feed-owning and feed-less).
MODULES_WITHOUT_EXTERNAL_FEEDS = (
    # compute / derive engines (read feeds, write computed tables — not feeds):
    "payables",
    # core owns the freshness/feed REGISTRY infrastructure (core.import_feed), not an external feed itself:
    "core",
    # pure in-app feature modules (user-created data, no external file/API feed):
    "approvals", "chat", "crm", "helpdesk", "hr", "notify",
    "recovery", "referral", "remediation", "storevisit", "vision",
    # marketing (migs 986/987) — outside-store event management. Feed-LESS on purpose: every row it
    # owns is typed by a human (the event, its staff, the checklist, the giveaway counts) or captured
    # from the device at check-in. Its one derived number, event planned-vs-actual, is READ from
    # commcalc's shared sales pass (_sales_cell_agg via _compute_feed_actuals_py, §3/§23) rather than
    # ingested, so it introduces no external feed and owns no ingest table. The later creative-gallery
    # / marketing-portal-pull phase WILL bring an external feed; when it does it moves to
    # INGEST_TABLES_BY_MODULE above and seeds database/migrations/925_data_lineage_seed.sql.
    "marketing",
)


# ── WHICH ROUTE MAY DELIVER A FEED — pointer, not a second registry ──────────────────────────────
# This module names WHICH TABLE a feed lands in. WHICH ROUTE is allowed to deliver it (a portal login
# pull, the email sweep, FTP, a manual upload) is per-org CONFIG, not a fact of the lineage map:
# `commcalc.connector_route_policy` (migration 998) + `commcalc/connector_route_policy.py`, keyed on
# the connector slug and the `core.import_feed.source_type` route vocabulary.
#
# It is recorded here because the two questions get confused: a feed whose portal login has been closed
# still lands in exactly the same table, so nothing in THIS registry changes when a route is switched
# off — and a reader who assumes "no pull ⇒ no data" would wrongly hunt for a broken table.
#
# LIVE as of the owner directive 2026-09-09: the POS sales/inventory connector's `pull` route is CLOSED
# by the house default (the vendor instructed us not to use their 2FA/browser login), so
# `daily_sales_feed`, `raw_custom_import` and `inventory_value` arrive for it by `email_sweep` and by
# manual upload ONLY. The connector is named in the migration's seed rows, never here — RULE TWO.
ROUTE_POLICY_TABLE = "connector_route_policy"      # commcalc schema; migration 998


def freshness_column(table: str) -> str:
    """The timestamp column a freshness probe should read for `table` to detect new data — the mapped
    override (e.g. daily_sales_feed → uploaded_at) or 'created_at' by default."""
    return FRESHNESS_COLUMN_BY_TABLE.get(table, "created_at")


def all_ingest_tables() -> tuple:
    """Flattened, de-duplicated set of every registered external-feed ingest table across all modules."""
    seen, out = set(), []
    for tables in INGEST_TABLES_BY_MODULE.values():
        for t in tables:
            if t not in seen:
                seen.add(t); out.append(t)
    return tuple(out)


# Backwards-compatible alias (the guard and any earlier caller can keep using RAW_INGEST_TABLES).
RAW_INGEST_TABLES = all_ingest_tables()


def freshness_source(item: str = "sales") -> str:
    """The table a freshness / "is data flowing?" check must measure for a logical feed — always the LIVE
    side of its LIVE_VS_MONTHLY pair. For sales this is daily_sales_feed, never the monthly upload — the
    2026-08-30 false-alarm this registry guards against."""
    pair = LIVE_VS_MONTHLY_PAIRS.get(item)
    return pair[0] if pair else LIVE_SALES_FEED


def display_sources(item: str = "sales") -> tuple:
    """The table(s) a DISPLAY aggregation reads for a logical item (the union of a live/monthly pair)."""
    pair = LIVE_VS_MONTHLY_PAIRS.get(item)
    return tuple(pair) if pair else (freshness_source(item),)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# WHICH FEEDS ARE WATCHED, AND HOW OFTEN EACH IS DUE — the watchdog's one home (owner 2026-10-03)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Owner, after two feeds died unnoticed: *"need a root cause analysis why this fails and a fool proof
# system to avoid such fails"*.
#
# THE CLASS, named. The registry above already knows every external feed. The freshness monitor watched
# THREE of them — activation details, bill payments and sales — named by hand at the call site. So
# registering a feed did not get it watched, and a feed nobody listed could stop for seven weeks with no
# alarm: `raw_comp_report` last carried data on 2026-08-06 and `asset_ledger` on 2026-09-23, and BOTH
# were found by a human noticing a wrong number weeks later. That is not a monitor that missed a feed;
# it is a monitor that could not see it. The same shape as §19.18 three times over — the fact is written
# down here and the caller kept its own copy.
#
# ONE FACT, ONE HOME, DEREFERENCED. A feed's cadence and its data-date column live HERE and callers READ
# them. `watched_feeds()` is DERIVED from INGEST_TABLES_BY_MODULE, so a feed added to this registry is
# watched the same day — the list cannot fall behind the registry because it IS the registry.
#
# LOCKED SO IT CANNOT UN-WIRE. `harness_feed_watchdog.py` fails the build when a registered ingest table
# is neither watched nor explicitly excused here, when a MONTHLY archive is watched (the 2026-08-30 false
# alarm), when a watched table has no declared data-date column, or when a caller re-implements either
# map. An omission is therefore impossible; only a DECLARED exclusion, with its reason, compiles.

# How many days without new data makes a feed late. A feed's cadence is a property OF THE FEED, so it is
# declared, never inferred from how long the table has been quiet.
CADENCE_DAILY = 1
CADENCE_WEEKLY = 7
CADENCE_MONTHLY = 31

# ── WHICH COLUMN MEANS "THE DATE OF THE THING" ────────────────────────────────────────────────────
# NOT the same question as FRESHNESS_COLUMN_BY_TABLE above, and the whole diagnosis needs both: that one
# says when a row ARRIVED, this one says what day the DATA is about. An old arrival means the file
# stopped coming; a recent arrival carrying an old data date means the file still comes and its content
# is frozen — a different phone call, as `_table_feed_freshness` already documents.
#
# `None` means the table carries no single column naming its own day (it is period-keyed, or it is not
# yet populated on any org here so no column can be declared honestly). Such a feed is watched on
# ARRIVAL only. `None` is a DECLARATION — the lock requires an entry for every watched table, so a
# missing column is a stated fact rather than a silent gap.
DATA_DATE_COLUMN_BY_TABLE = {
    # sales — the live hourly feed and its monthly archive (the archive is not watched; see below)
    LIVE_SALES_FEED: "trans_date",
    MONTHLY_SALES: "trans_date",
    "raw_sales_invoice": "trans_date",
    "raw_sales_invoice_tender": "trans_date",
    # carrier / processor money feeds
    PAYMENT_DETAIL: "payment_date",
    COMP_REPORT: "begin_date",      # the statement's own coverage start
    MA_COMMISSION: "tx_date",
    MA_DAILY_TX: "tx_date",
    "raw_ma_fulfillment": "date_ordered",
    # period-keyed snapshots: one snapshot per month, replaced not appended, so no row names a day
    MI_RESIDUAL: None,
    COMMISSION_PER_DEVICE_FEED: None,
    # day-grain operational feeds
    # The B2B clock in / clock out report (mig 1073, §14z): one row per punch, and `business_date` is
    # the DAY THE PUNCH IS ABOUT — never `arrived_at`, which only says when the mailbox delivered it.
    # The distinction is load-bearing here: the file arrives at 08:00 for the PREVIOUS day, so the two
    # dates are never equal and a watchdog reading arrival would call a current feed a day stale.
    "storeops.raw_clock_punch": "business_date",
    DLAR_REP: "as_of_date",
    DLAR_STORE: "as_of_date",
    "pos_tender_summary": "close_date",
    INVENTORY_VALUE_SNAPSHOT: "as_of_date",
    ASSET_LEDGER: "acquired_date",          # when the distributor booked the unit to us
    # Unpopulated on every org in this deployment, so no data-date column is declared from a real row.
    # Watched on arrival; add the column here the day one lands (the lock makes that a conscious change).
    EPAY_DAILY: None,
    "merchant_settlement_day": None,
    # Day-keyed from the first row it ever lands: the report is about a business day, and the pull
    # deliberately ends YESTERDAY because a day that has not closed is a figure that still moves.
    "portal_metric_day": "business_date",
    "merchant_settlement_batch": None,
    "pos_builtin_daily_sales": None,
    "raw_sales_product": None,
    "royalty_report": None,
    "ma_overview_upload": None,
}

# ── DAY GRAIN vs PERIOD GRAIN — WHICH MONTH A ROW BELONGS TO, AND WHAT AN UPLOAD REPLACES ─────────
# THE DEFECT THIS CLOSES (house org, measured 2026-10-05). `commcalc.raw_payment_detail` is stamped
# with the period an operator PICKS at upload, and replaced per (org, period). The ePay portal serves
# that report over a rolling in-arrears window, so the 2026-10-04 pull covered 2026-09-05 … 2026-10-02
# and every one of its rows was filed under "October 2026": 18,231 rows / $415,862.56 of SEPTEMBER's
# commission landed a second time under October, which then read $431,323.25 against a true
# $15,460.69 — 28x. The 2026-08-24 pull did the same with July ($147,435.59 under August).
#
# THE CLASS, not the instance. The question "which day is this row about" already has ONE home —
# DATA_DATE_COLUMN_BY_TABLE above. `router.DATE_KEYED` kept its OWN copy of that map, listing four
# feeds by hand, and `raw_payment_detail` was simply not on it; so the per-day replace that makes a
# rolling re-pull idempotent, and the row-date period stamp that keeps a month honest, applied to the
# feeds somebody remembered. That is §19.18's shape for the fourth time, and it is why `raw_comp_report`
# got a multi-month upload guard in 2026-08 while its sibling feed never did.
#
# THE DESIGN FIX. A feed with a declared data-date column is DAY GRAIN unless it is declared
# period-grain here, with its reason. `day_keyed_date_columns()` DERIVES the map every caller needs, so
# a feed that declares a data-date column gets the day-grain treatment the same day. Omission does not
# compile: `harness_feed_day_grain.py` fails the build on a table that is in neither place, and on a
# caller that keeps a second copy of the map.
PERIOD_GRAIN_REASONS = {
    # The monthly ARCHIVE of the sales pair. It is uploaded as one whole month and replaced as one
    # whole month on purpose — the live feed (LIVE_SALES_FEED) is the day-grain side of the pair, and
    # it is already day-keyed. Making the archive day-keyed would let a partial monthly export replace
    # only the days it happened to carry and leave the rest of a stale month standing beside it.
    MONTHLY_SALES: "monthly archive of the sales pair — uploaded and replaced as one whole month",
    "pos_builtin_sales": "monthly archive of the pos_builtin pair — replaced as one whole month",
    # The distributor/vendor invoice feeds arrive as one statement per period, and their own day
    # columns describe lines INSIDE that statement rather than the pull window.
    "royalty_report": "one franchisor statement per period — the period IS the document",
}

# Tables whose data-date column is declared but which this upload path never reaches (no file_type
# maps to them), stated so the lock can tell "not an upload" apart from "forgotten".
NOT_UPLOADED_TABLES = (
    "raw_sales_invoice", "raw_sales_invoice_tender", "pos_tender_summary", INVENTORY_VALUE_SNAPSHOT,
    ASSET_LEDGER,
)


def day_keyed_date_columns() -> dict:
    """PURE: {table: data-date column} for every feed that is DAY GRAIN — i.e. declares a data-date
    column in DATA_DATE_COLUMN_BY_TABLE and is not declared period-grain in PERIOD_GRAIN_REASONS.

    THE one home for "a re-pull of these days replaces those days, and a row belongs to its own day's
    month". Callers DEREFERENCE this; a second literal copy fails the build."""
    return {t: c for t, c in DATA_DATE_COLUMN_BY_TABLE.items()
            if c and t not in PERIOD_GRAIN_REASONS}


def day_keyed_file_types(table_map) -> dict:
    """PURE: {upload file_type: data-date column} for every upload route whose LANDING TABLE is day
    grain. `table_map` is the caller's {file_type: table} (commcalc.column_mapping.TABLE_MAP).

    THE one derivation of "what does an upload of this report REPLACE". It existed as an inline
    comprehension in the upload handler, which meant the only thing that knew a one-day file replaces
    one DAY was the handler itself — so the Upload page went on warning that the whole PERIOD would be
    replaced, and the owner stopped uploading a day he needed (2026-10-08: *"also can i just upload
    1day of data, it says it will replace the whole period"*). Both the handler and every surface that
    TELLS a human what an upload does now dereference this; a hand-written list of feed names in a
    page is the §19.46 defect wearing a hat, and `harness_statement_month_coverage.py` fails the build
    on one."""
    dk = day_keyed_date_columns()
    return {ft: dk[tb] for ft, tb in (table_map or {}).items() if tb in dk}


def is_day_keyed(table: str) -> bool:
    """PURE: True when `table` is replaced by DAY and its rows carry their own month."""
    return table in day_keyed_date_columns()


def period_grain_reason(table: str):
    """PURE: why `table` is replaced per PERIOD rather than per day, or None when it is day grain."""
    return PERIOD_GRAIN_REASONS.get(table)


# ── WHERE A ROW'S MONTH IS STORED — one home, dereferenced (owner report 2026-10-12) ──────────────
# THE DEFECT THIS CLOSES. Every upload of the marketplace handset fulfilment report has been REFUSED
# since 2026-10-06 — "Could not find the 'period' column of 'raw_ma_fulfillment' in the schema cache"
# (6 refused attempts on one tenant, the newest 2026-10-12; the customer saw only the generic
# "this feature isn't switched on" sentence the API boundary rewrites such an error into). The
# day-grain fix of §19.46 (`feed_period.day_stamp`, 2026-10-05) re-stamps `period` / `period_month` /
# `period_year` on EVERY day-keyed feed's rows from the row's own date. `raw_ma_fulfillment` is day
# keyed — and is the one landing table that stores NO month columns at all (its upload mapper has
# always skipped them, and `report_pull`'s spec says `has_period: false`).
#
# THE CLASS, not the instance. The general fact that was wrong is not "one feed lost its uploads"; it
# is **"a feed row's month can always be written to period columns"**. Whether a landing table STORES
# a row's month is a schema fact that had no home: the upload handler carried a hand-written exclusion
# list (`file_type not in ['catalog', 'master_cats']`), which is §19.18's shape again — the stamp
# reached the feeds somebody remembered to exclude. It lives here now, next to the day-grain fact it
# travels with, and `harness_feed_day_grain.py` §M re-measures every declaration against the CREATE
# TABLE / ALTER TABLE statements in `database/migrations/`, so a drifting declaration fails the build.
# ── WHICH COLUMN NAMES THE STORE ──────────────────────────────────────────────────────────────────
# A distributor feed does not carry a store code. It carries the dealer's PROCESSOR ACCOUNT, and the
# three master-agent feeds each spell that account in a different column: the commission sheet says
# `merchant_account_id`, the daily-transaction sheet says `account_id`, the fulfilment sheet says
# `tspid`. They are ONE identifier space, verified on live house data (170074 / 170083 / 170084 all
# appear in both the commission and the fulfilment feed), which is what lets one key join all three.
#
# WHY IT LIVES HERE. Those three spellings were hand-written inline in `account/ma_store_pnl.py`, once
# per booking function — the §19.18 shape, three copies of one fact with nothing holding them
# together. The store-takeover evaluation needed the same answer to split a prospect's uploads by
# store, and a fourth copy is how the house ends up with two reports disagreeing about which store a
# dollar belongs to. So the fact is declared once and every reader dereferences it, including the
# three that were already there. Locked by `harness_prospect_eval.py` §L.
#
# `None` is a DECLARATION, not a gap: the table carries no column naming a dealer account, so money
# from it is booked company-wide rather than guessed onto a store.
STORE_ACCOUNT_COLUMN_BY_TABLE = {
    MA_COMMISSION: "merchant_account_id",
    MA_DAILY_TX: "account_id",
    "raw_ma_fulfillment": "tspid",
}


def store_account_column(table: str):
    """PURE: the column `table` names the dealer's processor account in, or None when it has none.

    THE one home for "which column is the store on this feed". A caller spelling one of these column
    names itself is a second copy of a fact that has already changed once."""
    return STORE_ACCOUNT_COLUMN_BY_TABLE.get(table)


MONTH_COLUMNS = ("period", "period_month", "period_year")

# A landing table that stores NO month columns, with the reason. Everything else stores all three.
NO_MONTH_COLUMN_REASONS = {
    "raw_ma_fulfillment": ("day-keyed order feed — `date_ordered` is the row's only month fact, and "
                           "the table was created without the period columns its two sibling "
                           "marketplace feeds carry"),
    PRODUCT_CATALOG: "a product snapshot, replaced whole — a catalogue row is not about a month",
    "raw_categories": "a payment-type → category map, replaced whole — not about a month",
    "pos_tender_summary": "one row per store close — `close_date` is the row's only month fact",
    INVENTORY_VALUE_SNAPSHOT: "a per-store value snapshot keyed by `as_of_date`",
    # FOUND BY THE LOCK, not by hand (2026-10-12): the §M re-measurement over the migrations named
    # these four the moment it was written, and each was confirmed against the live schema. None is
    # reached by the manual-upload handler today, which is exactly why nobody noticed — the same
    # silence raw_ma_fulfillment kept until the day the stamp was generalised to it.
    EPAY_DAILY: "day-keyed transaction stream — `settlement_date` is its month fact",
    "merchant_settlement_day": "one row per processor business day — `business_date` is its month fact",
    "portal_metric_day": ("one row per store-day-worker-category — `business_date` is its month fact; "
                          "a re-pull restates a day on the natural key, it never replaces a period"),
    "merchant_settlement_batch": "one row per processor batch — dated by its own batch day",
    ASSET_LEDGER: "the distributor's unit ledger — `acquired_date` is the row's only month fact",
}


def month_columns(table: str) -> tuple:
    """PURE: the columns `table` stores a row's month in — () when it stores none.

    THE one home for "may this row be stamped with a period". A caller that keeps its own list of
    exceptions fails the build (`harness_feed_day_grain.py` §M)."""
    return () if table in NO_MONTH_COLUMN_REASONS else MONTH_COLUMNS


def stores_row_month(table: str) -> bool:
    """PURE: True when `table` has the period columns a month-keyed read and replace need."""
    return bool(month_columns(table))


def no_month_column_reason(table: str):
    """PURE: why `table` stores no month, or None when it does."""
    return NO_MONTH_COLUMN_REASONS.get(table)


# A landing table an upload replaces WHOLE for the org (a snapshot of reference data, not a stream of
# events). Stated here because "no month columns" and "replaced whole" are two different facts, and
# reading one as the other is how a day-keyed feed would have its entire history deleted by one file.
SNAPSHOT_TABLES = frozenset({PRODUCT_CATALOG, "raw_categories"})


def is_snapshot(table: str) -> bool:
    """PURE: True when an upload into `table` replaces the org's whole set."""
    return table in SNAPSHOT_TABLES

# ── HOW OFTEN EACH WATCHED FEED IS DUE ────────────────────────────────────────────────────────────
# The default is daily, deliberately: a feed nobody thought about is better watched too keenly than not
# at all. A slower feed says so here.
FEED_CADENCE_BY_TABLE = {
    MI_RESIDUAL: CADENCE_MONTHLY,             # one monthly subscriber snapshot
    COMMISSION_PER_DEVICE_FEED: CADENCE_MONTHLY,
    "royalty_report": CADENCE_MONTHLY,        # the franchisor's monthly statement
    "ma_overview_upload": CADENCE_MONTHLY,
    "raw_ma_fulfillment": CADENCE_WEEKLY,
    INVENTORY_VALUE_SNAPSHOT: CADENCE_WEEKLY,  # an aging snapshot, not a transaction stream
}
DEFAULT_FEED_CADENCE = CADENCE_DAILY

# ── FEEDS DELIBERATELY NOT WATCHED, each with its reason ──────────────────────────────────────────
# A registered ingest table absent from this map AND absent from DATA_DATE_COLUMN_BY_TABLE fails the
# build. So this is the only way a feed goes unwatched, and it costs a sentence saying why.
NOT_WATCHED_REASONS = {
    # The monthly side of a LIVE_VS_MONTHLY pair. Watching it IS the 2026-08-30 false alarm: it moves
    # only at month close, so it reads "stale" while the live feed is current. Measured 2026-10-03 —
    # raw_sales' newest row was 2026-08-31 while daily_sales_feed carried that same day's trading.
    MONTHLY_SALES: "monthly archive of the sales pair — freshness reads the live feed",
    "pos_builtin_sales": "monthly archive of the pos_builtin pair — freshness reads the daily stream",
    # Already watched, per report_key, by the custom-import probe — watching the shared JSONB table
    # would answer for whichever report arrived last and hide the one that stopped.
    CUSTOM_CAPTURE: "watched per report_key by the custom-import probe, not as one table",
    # Reference data replaced wholesale when the catalog changes. It has no cadence to be late against.
    PRODUCT_CATALOG: "reference catalog, replaced on change — no arrival cadence",
    "raw_categories": "reference catalog, replaced on change — no arrival cadence",
    # Org setup / template uploads: they arrive when a human changes the org, not on a schedule.
    "store_mapping": "org setup upload — arrives when the org changes, not on a cadence",
    "storeops.employees": "org setup upload — arrives when the org changes, not on a cadence",
    "storeops.stores": "org setup upload — arrives when the org changes, not on a cadence",
    "storeops.store_alias": "org setup upload — arrives when the org changes, not on a cadence",
    "storeops.store_merchant_id": "org setup upload — arrives when the org changes, not on a cadence",
    "closing_tender_def": "org config sheet — arrives when the org changes it",
    "closing_tender_map": "org config sheet — arrives when the org changes it",
    "storeops.platform_billing_connector": "connector config row, not a data feed",
    # Entered by people in the app, so an absence is a staffing fact, not a feed fault. The closing
    # module raises its own exceptions for a missing declaration.
    DAILY_CLOSING: "entered by employees in-app — the closing module reports a missing declaration",
    # On-demand pulls with no promised cadence; a stale price list is a price question, not an outage.
    "vendor_catalog_price": "on-demand vendor price pull — no promised cadence",
    "storeops.google_review_store": "review sweep — a quiet week is not an outage",
    "storeops.google_review_snapshot": "review sweep — a quiet week is not an outage",
    "storeops.google_review_item": "review sweep — a quiet week is not an outage",
    # Line children of a watched parent: the parent's arrival is the feed's arrival, and watching both
    # would report one outage twice.
    "royalty_report_line": "line child of royalty_report, which is watched",
    # The `pos` schema is outside the commcalc-scoped probe's reach. Stated rather than omitted, so
    # extending the probe to that schema is a visible change to this line.
    "pos.sales": "pos schema — outside the commcalc-scoped probe; watch when the probe spans schemas",
    "pos.receipt_imports": "pos schema — outside the commcalc-scoped probe",
    "pos.customers": "pos schema — outside the commcalc-scoped probe",
    "pos.activations": "pos schema — outside the commcalc-scoped probe",
    "activation_rebate_ledger": "aggregate written by the pos module from its own feeds, not an ingest",
}


def data_date_column(table: str):
    """PURE: the column naming the DAY THE DATA IS ABOUT for `table`, or None when the table carries no
    such column (period-keyed, or unpopulated so none is declared). Distinct from freshness_column(),
    which names when a row ARRIVED — see the header above."""
    return DATA_DATE_COLUMN_BY_TABLE.get(table)


def feed_cadence_days(table: str) -> int:
    """PURE: how many days without new data makes `table` late. Declared per feed; daily by default."""
    return int(FEED_CADENCE_BY_TABLE.get(table, DEFAULT_FEED_CADENCE))


def is_monthly_archive(table: str) -> bool:
    """PURE: True when `table` is the MONTHLY side of a live/monthly pair — the side a freshness check
    must never measure."""
    return any(table == pair[1] for pair in LIVE_VS_MONTHLY_PAIRS.values())


def watched_feeds() -> tuple:
    """PURE: every registered ingest feed a freshness check must cover, DERIVED from
    INGEST_TABLES_BY_MODULE — so a feed registered today is watched today.

    Each entry: {table, module, data_date_column, cadence_days}. `data_date_column` None means watch
    ARRIVAL only. Tables named in NOT_WATCHED_REASONS are excluded, by declaration; the harness fails
    the build on any registered table that is in neither place, so the set cannot quietly shrink."""
    out = []
    for module, tables in INGEST_TABLES_BY_MODULE.items():
        for t in tables:
            if t in NOT_WATCHED_REASONS:
                continue
            out.append({"table": t, "module": module,
                        "data_date_column": data_date_column(t),
                        "cadence_days": feed_cadence_days(t)})
    return tuple(out)


def unwatched_reason(table: str):
    """PURE: why `table` is deliberately not watched, or None when it IS watched."""
    return NOT_WATCHED_REASONS.get(table)


# ── HOW A FEED IS NAMED TO A HUMAN ────────────────────────────────────────────────────────────────
# A watchdog line a tenant cannot read is a line nobody acts on, and "raw_comp_report is 7 days late"
# is not a sentence an owner should have to decode. RULE TWO holds: these are the names of OUR tables
# and the reports they carry, never a carrier, tenant or product branch — the carrier-vocabulary guard
# reads this file. Anything not named here derives a readable label from the table itself, so a new
# feed is never unnamed; naming it well is a one-line change.
FEED_LABEL_BY_TABLE = {
    LIVE_SALES_FEED: "Sales transactions (live feed)",
    MONTHLY_SALES: "Sales transactions (monthly reconciliation)",
    PAYMENT_DETAIL: "Payment detail",
    COMP_REPORT: "Compensation report (commission basis)",
    MI_RESIDUAL: "Subscriber base snapshot",
    DLAR_REP: "Daily activity by rep",
    DLAR_STORE: "Daily activity by store",
    MA_COMMISSION: "Master-agent commission",
    MA_DAILY_TX: "Master-agent daily transactions",
    "raw_ma_fulfillment": "Master-agent fulfilment orders",
    "pos_tender_summary": "Register tender summary",
    INVENTORY_VALUE_SNAPSHOT: "Inventory aging snapshot",
    ASSET_LEDGER: "Asset lending ledger (device cost)",
    COMMISSION_PER_DEVICE_FEED: "Per-device rebate history",
    EPAY_DAILY: "Settlement transactions",
    "merchant_settlement_day": "Card settlement by day",
    "portal_metric_day": "Portal report by category",
    "merchant_settlement_batch": "Card settlement by batch",
    "raw_sales_product": "Sales by product",
    "raw_sales_invoice": "Sales by invoice",
    "raw_sales_invoice_tender": "Sales by invoice tender",
    "pos_builtin_daily_sales": "In-house register sales (daily)",
    "royalty_report": "Royalty statement",
    "ma_overview_upload": "Master-agent overview",
}


def feed_label(table: str) -> str:
    """PURE: the human name for `table` — the declared label, else one derived from the table name, so
    every feed has a readable name and none is left as a bare identifier."""
    lbl = FEED_LABEL_BY_TABLE.get(table)
    if lbl:
        return lbl
    base = table.split(".")[-1]
    for prefix in ("raw_", "pos_builtin_"):
        if base.startswith(prefix):
            base = base[len(prefix):]
            break
    return base.replace("_", " ").strip().capitalize() or table
