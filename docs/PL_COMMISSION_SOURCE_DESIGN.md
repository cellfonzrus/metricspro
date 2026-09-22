# The P&L books commission from the Commission Ledger — design (owner 2026-09-21, mig 1013)

Owner, verbatim: *"p&l is not showing the commission received, it shows in the commission ledger but
not populating the p&l - check platform wide not bandaid"*.

## 1. The class, not the instance

**Instance.** Org `f4f1c16e…` (a POS/carrier onboarded through the intake) holds 973 lines in
`commcalc.commission_ledger` for July 2026 — net **$86,970.34** after the declared sign convention,
bucketed through the mig-1009 registry — and its P&L line "Carrier commissions & incentives" reads
nothing from them. Index §30.7 recorded the gap in so many words: *"RECORDED, NOT BOOKED — today NO
P&L line reads commission_ledger"*.

**Class.** `account/coa.build_inputs` derived the P&L's commission lines from **per-feed tables**,
one booking loop per feed shape: `raw_mi` (MI residual), `raw_ma_commission` (the master-agent
sheet, `ma_store_pnl.ma_commission_bookings`), `raw_ma_daily_tx` (month spiffs / residual / MDF /
merchant discount, `ma_store_pnl.ma_tx_bookings`), `raw_comp_report` (the carrier-category drill-down),
`activation_rebate_ledger` (mig 867). The canonical, bucketed, sign-conventioned ledger — the ONE
place every carrier onboarded through the intake lands, whatever its file looks like — was not a
P&L source. So a tenant whose statements exist only in the ledger showed **$0 commission on the P&L,
silently**. Fixing "the Vzone tenant" by teaching coa its feed would have been the fourth per-feed
loop: the same defect wearing a hat.

## 2. The design — config, one home, dereferenced, byte-identical by default

| | |
|---|---|
| **The switch is config** | `commcalc.commission_org_config.pl_commission_source` ∈ `feeds` (house default) \| `ledger` \| `ledger_else_feeds` — mig `1013_pl_commission_source.sql`, **written, NOT applied**. It sits on THE per-org money-policy row beside the other P&L source-of-truth switches (`pl_ma_month_spiff_source`, `pl_rebate_presentation`, `pl_device_margin_presentation`) and is read by the same reader, `ma_store_pnl.load_config` (column set falls back 1013 → 996 → 934 → 314 → defaults, so a database without the column reads `feeds`). The vocabulary lives once: `ma_store_pnl.COMMISSION_SOURCES`. No sibling config table. |
| **One resolver** | `account/ledger_pnl.resolve_source(configured, ledger_has_lines)` → `feeds` \| `ledger`. `ledger_else_feeds` is decided **per period** on the one fact it turns on (does the ledger hold lines for the period). Every consumer of the switch calls it; the lock fails the build on a second resolver or on coa comparing a source word of its own. |
| **The booking dereferences the registry** | `ledger_pnl.ledger_bookings(rows, buckets, PL_SECTION, cfg)` groups the period's ledger rows by store and hands each group to `commission_ledger.summarize` — **the ledger's own summarizer** (the finance boundary in CLAUDE.md: the AMOUNTS are the commission module's; finance never sums a `payout_total`). Each bucket's total books to `categories[bucket]["pl_line_key"]` (the mig-1009 registry row, house default + tenant override per key via `load_buckets_meta`, the one reader). No bucket → line map exists in finance code. |
| **The sign is the chart's** | `ledger_pnl.route_line`: a revenue line takes the bucket's signed total as-is (earned +, a deduction a tenant nets into revenue −); a COGS / opex line takes the NEGATED total — a deduction bucket of −7,396.27 chargebacks lands as **+7,396.27 of expense**, an earned rebate bucket lands as contra-COGS (ruling K1). The rebate family goes through `ma_store_pnl.rebate_route` — the ONE route both feed rebate sources already use — so `pl_rebate_presentation='income'` flips the ledger's rebate to `rebate_income` exactly as it flips the feeds'. |
| **Store grain** | the ledger line's `store` runs through coa's `add` → `store_resolver` (exact address, alias, store code, unambiguous leading number); unresolvable → the P&L's existing convention (the cleaned string, never a guessed store); no store → company-wide. |
| **The suppression set is derived** | `ledger_pnl.covered_lines(buckets, PL_SECTION, cfg)` = the `pl_line_key`s of the org's ACTIVE buckets, each ALSO routed through `route_line` (so under `income` presentation `rebate_income` is covered too — the guard found exactly this on its first run). Under `ledger`, coa's **guarded adder `add_comm`** — the ONLY adder the five commission-feed paths call — drops a feed booking whose line is covered (tallied as suppressed for the drill-down, never silent). Bookings from non-commission sources (rent on `store_opex`, `chargeback_items` on `chargebacks`, distributor shipping on `vip_fees`) go through the plain `add` and are untouched. Under the house default `add_comm` IS `add`. |
| **The guard** | `ledger_pnl.double_booked(feed_booked, ledger_booked)` — the lines booked from both. coa **raises** on a non-empty answer (an honest failure beats a doubled statement); the proof forces a ledger booking onto a feed-booked line and asserts RED. |
| **The divergence** | `ledger_pnl.divergence` attaches, per line, `{source, configured, feeds, ledger, difference, suppressed, words, unbooked, by_source_report}` as `inputs[line]["commission_source"]`; `engine._assemble` copies it onto the row like `note`. Under `feeds` a line the ledger also holds says *"Booked from the feed tables ($F). The Commission Ledger holds $L for this line this month — a difference of $D. Switch the P&L commission source to the ledger to book it from there."* — the gap a migrating tenant reads BEFORE flipping. Under `ledger`: *"Booked from the Commission Ledger ($L). The feed tables would have booked $F … the feed booking is switched off on this line so nothing is counted twice."* An org with an empty ledger carries NO new key (payload byte-identical). |
| **Unbooked money is reported** | an unmapped payout (`other`), a line under a bucket key the registry no longer lists (`unlisted`), a bucket with no `pl_line_key` — each with its amount and a reason on the same passthrough (the mig-312 posture: silence made visible). Identity proven: Σ booked buckets (signed) + Σ unbooked = the ledger's own `net_total`. |
| **Where it shows up** | `landing_identity.CONSUMERS["commission_ledger"]` now names the P&L Statement; `consumers_for_table(table, pl_link)` / `shows_in(row, …, pl_link)` decorate that entry with `lines` (from `ledger_pnl.pl_link` — the registry's routed lines, deduped, labelled by `coa.PL_LABEL` with the org's `pl_line_labels`), `source` and `active`. Rendered by the ONE component `ShowsIn` ("P&L Statement → Carrier commissions & incentives, Residual, …", "(not the P&L source yet)") on the intake's 3.9 commit card (`shows_in` on the commit payload), the Stage-5 runbook (`rail(..., pl_link)`) and the Commission Ledger page. The P&L line links back through ScreenLink `commission_ledger`. No second link map. |
| **Onboarding asks** | `GET /commcalc/pl-commission-source` → the saved value, the three options in layman words, the **suggestion** (`ledger_pnl.suggest_source`: offered only when the ledger has lines and NO feed table holds a row; both present → "compare on the P&L first"; nothing → nothing) with its evidence (`load_source_evidence`: which feed tables have rows, ledger lines per period), the lines, `shows_in`, and `ready` (the column exists). ONE writer: `PUT /commcalc/commission-settings {pl_commission_source}` (admin-gated, validated against the one vocabulary, its own statement, **read back** — a missing column refuses naming mig 1013, never a silent non-save). `components/PlCommissionSourcePanel.tsx` is the one panel (spells no source word; mounted on the Commission Ledger page and the intake's 3.9 card). |
| **The lock (CI)** | `backend/harness_pl_commission_source_lock.py` in `carrier-vocab-guard.yml`: one resolver; the booking dereferences `pl_line_key` and names no chart key; `_lp_covered` only from `covered_lines(`; every commission-feed site on `add_comm(` and the un-guarded spellings gone; no `payout_total` outside the commission module except `ledger_pnl` (never summed); the passthrough, the P&L page, ShowsIn, the one panel with one writer, both mounts, CONSUMERS, the endpoint; registration; **eleven negative controls**. |

## 3. Every feed booking path — fixed or excused by name

| Path in `coa.build_inputs` | Under `feeds` | Under `ledger` |
|---|---|---|
| `raw_mi` → `mi_income` / `atu_income` | unchanged | suppressed when the line is covered (house: `mi_income` is) |
| `raw_ma_commission` → `ma_store_pnl.ma_commission_bookings` (spiffs → `carrier_comm`, rebate → route, device margin / fees / financing / clearing) | unchanged | covered lines suppressed; `ma_device_margin`, `fee_income`, `financing_income`, `distributor_clearing` keep booking from the sheet (no bucket names them) |
| `raw_ma_daily_tx` → `ma_store_pnl.ma_tx_bookings` (month spiffs, residual, MDF, merchant discount) and the mig-309 fallback | unchanged | covered lines suppressed; `ma_merchant_discount`, `mdf_income` keep booking |
| `raw_comp_report` → `carrier_comm` (carrier_category_map drill-down) | unchanged | suppressed |
| `activation_rebate_ledger` → `carrier_comm` (commission) + rebate route | unchanged | suppressed; its **device cost** stays on the plain adder (the device leg, not commission) |
| `asset_ledger` / `vip_invoices` → `vip_fees`; `chargeback_items` / `vip_invoice_lines` → `chargebacks`; `store_expenses` → `store_opex` | unchanged | **excused: not commission** — untouched under every source, and the deduction buckets ADD to these lines |

## 4. Compatibility pin (money) and the live tenants

`backend/harness_pl_commission_source.py` (73 checks) replays the REAL `build_inputs` over an in-memory
client that filters: a pre-1013 database (no column, no ledger table) == explicit `feeds` with ledger
lines present == `feeds` with an empty ledger, on every line's `by_store` / `company_wide` / `detail`;
every feed shape equals the oracle from the same pure booking functions; a `raw_mi`-shaped org is
untouched; an org with an empty ledger carries no new key. Under `ledger`: the eight house buckets incl.
the three deduction buckets + a tenant bucket + a bucket with no line — each on its registry line with
the chart's sign, per store, feeds suppressed, non-commission bookings untouched, unbooked reported,
identity to the ledger's net, other org / other period never leak; `ledger_else_feeds` per period; the
guard goes RED; the divergence under both sources; shows-in; the suggestion; the resolver table; the
real endpoints' save / read-back / refusals.

| Live tenant | After this PR | What changes when a person flips it |
|---|---|---|
| house org (`raw_mi` / comp report / asset ledger) | `feeds` — byte-identical | nothing until switched |
| `854f6d7b…` (master-agent feeds, plan mode) | `feeds` — byte-identical; its ledger (MA-sync) lines, where present, show as the divergence in words on each commission line | under `ledger` the MA feed bookings on covered lines are suppressed and the ledger's buckets book instead |
| `f4f1c16e…` (statements in the ledger only) | `feeds` — still $0 commission on the P&L, but each covered line now SAYS the ledger holds $86,970.34 net for July 2026 and how to switch; the panel SUGGESTS `ledger` (no feed table holds a row) | under `ledger`: July 2026 books $94,366.61 earned across the registry's revenue lines and $7,396.27 of deductions on their expense lines, per store where the statement names one |

**Migration 1013 is surfaced, not applied. No org is switched by this PR.**

## 5. Duplicate check (build gate)

Searched index §4 / §4a (the P&L booking paths, `ma_store_pnl` as the ONE MA home), §15 / §16
`commission_ledger` / `commission_bucket` / `commission_org_config` (the mig-207 house-default +
tenant-override pattern, `load_buckets_meta`, `summarize`), §17 `/commission-ledger/*` /
`/commission-settings` / `/report-kinds` `shows_in`, §18 "Commission received", §30.7 (the recorded
link), §32 (`landing_identity.CONSUMERS` / `shows_in` / ShowsIn / ScreenLink), the
`metric_source_of_truth` / `pl_ma_month_spiff_source` pattern for where a source switch lives.
REUSED: `commission_ledger.summarize` / `load_buckets_meta` / `active_buckets` (the amounts and the
registry), `ma_store_pnl.load_config` (the reader) / `rebate_route` / `REBATE_LINES`,
`coa.store_resolver` via `add`, `coa.PL_SPEC` / `PL_LABEL` (+ new `PL_SECTION` beside them),
`engine._assemble`'s passthrough pattern (`note`), `landing_identity.CONSUMERS` / `shows_in` /
`consumers_for_table`, `ShowsIn` / `ScreenLink`, `PUT /commission-settings` (the one writer),
`_period.period_keys` (both period spellings). NEW: `account/ledger_pnl.py`, one column (mig 1013),
one read endpoint, one panel, two harnesses. Nothing else.

## 6. 2026-09-22 — the reader reads ANY subset of its columns (index §4b.1)

**What happened live.** The owner applied mig 1013 and chose the ledger; the row reads
`pl_commission_source='ledger'`. `ma_store_pnl.load_config` returned `feeds`: the row lacks the mig-996 column
`pl_device_margin_presentation`, so the 1013 block and the 996 block both failed and the ladder fell to the
934 block, which predates the switch. `build_inputs` booked $0 commission and nothing said why.

**The class, not the instance.** A reader that selects column SETS as blocks with a fallback to a smaller block
lets one missing OLDER column hide every NEWER one. Nineteen such ladders existed. They are all gone, replaced by
ONE reading rule in `backend/app/core/column_tolerant.py`:

- a per-org CONFIG row is read with `select("*")` and the reader takes the keys present (`read_row`) —
  byte-identical when every column exists;
- a wide, hot DATA table keeps its explicit column list and PROBES each optional column on its own
  (`present_columns` + `select_list`) before the one real select;
- what is missing is RETURNED (`missing`) and SURFACED: `load_config` → `config_columns_missing` /
  `config_migrations_missing` (`PL_CONFIG_COLUMNS`, the column ↔ migration map, one home); the panel's
  `load_source_meta` now DEREFERENCES `load_config` (one reader for the panel and the statement, `ready` = the
  1013 column present); `divergence` puts `switch_ready` and the sentence *"the P&L commission-source switch
  column is not applied on this database yet — apply migration 1013_pl_commission_source.sql"* on the line —
  the read side says what the save side already refused.

**Lock.** `harness_any_columns_lock.py` (CI): an AST scan fails the build on any for-ladder, try/except
block-then-subset fallback or tier table under `backend/app`; the P&L reader must read through `read_row`;
`load_source_meta` must dereference `load_config`; the three helper functions have one home; every named reader
is on the rule; the two credential-holding rows (`_connector_status`, `data_source_pull_diagnostic`) are
projected. **Proof** `harness_any_columns.py`: the live shape reads `ledger` and books the ledger while the
REMOVED ladder, replayed over the same rows, reads `feeds`; the inverse shape reads `feeds`, reports
`['pl_commission_source']` and names 1013 on the P&L and the panel; byte-identical under complete columns for
every reader that changed.

**Unapplied on live.** `996_pl_device_margin_presentation.sql` — the only `commission_org_config` column from
migrations 210 → 1013 absent on the owner's `select *` row. Both the panel and the P&L now report it.

**Money.** No figure changes where every column exists. On the live tenant the ledger books once the reader
sees the switch — and the ledger holds DUPLICATE July 2026 copies (task #36, another agent), so the July
figure is not to be judged until that lands. Ledger identity is untouched here.
