# Data Lineage & System Schematic

**Purpose.** The system is large; a change to one ingested item (bill payments, activations, upgrades, accessories, cash) touches many downstream fields. This document + the `commcalc.data_lineage` table (migration 924/925) are the map of *what feeds what*, so a future change can see everything it affects and nothing is missed.

- **Machine-readable:** `commcalc.data_lineage` — query it, or browse via `GET /api/v1/commcalc/data-lineage` (grouped by source).
- **Human-readable:** this file. Both are seeded from the same audit; keep them in sync.

Each edge is *source → affected* with: the **entry point**, a **code reference** (file:function), a plain-English **effect**, the **kind** (`ingest`/`display`/`pay`/`target`/`recon`), and **`auto_updated`** — `false` marks a place where a change does **not** propagate on its own (the wiring gaps).

---

## 1. Ingestion — where data enters (raw capture)

| Item | Entry point | Table | Notes |
|---|---|---|---|
| Sales Transaction Details | `POST /upload/sales`, email/FTP sweep | `commcalc.raw_sales` | replace-by-period |
| Daily sales feed | `POST /upload/daily_sales`, email sweep | `commcalc.daily_sales_feed` | replace-by-date; promoted → `raw_sales` |
| Activation Details (b2b) | Custom import (sweep/upload) | `commcalc.raw_custom_import` | JSONB; detected by signature `Serial# + Contract Type` |
| Bill Payment Transactions (b2b) | Custom import | `commcalc.raw_custom_import` | signature `Discounts + Bill Pay System` |
| Sales by Product (b2b) | Custom import | `commcalc.raw_custom_import` | signature `Product GP + Total Exp Comm` |
| ePay Daily Tx (Boost) | `POST /epay/upload`, Boost sweep | `commcalc.raw_epay_daily_tx` | terminal→store via `storeops.merchant_ids` |
| VidaPay MA Daily Tx (Total) | `POST /upload/ma_daily_tx`, VidaPay sweep | `commcalc.raw_ma_daily_tx` | keyed `account_id + tx_date` |
| Employee daily cash | `POST /closing/row`, `/closing/upload`, GSheet sweep | `commcalc.daily_closing` | `t_cash` = cash; `epay_on_cash` = bill-payment cash portion |
| POS X-report tenders | `POST /upload/x_report` | `commcalc.pos_tender_summary` | per store-day tender matrix |
| MA commission / fulfillment, DLAR, MI, comp, catalog, VIP, hotsheet, inventory | see registry | respective `raw_*` tables | every ingest also writes `upload_log` + `upload_trace` |

Sweeps (email, FTP, ePay, VidaPay, DLAR, VIP) do **not** add tables — they funnel into the tables above via `upload_file`.

---

## 2. The single shared aggregation

`_sales_cell_agg` (router.py) is **the one** per-(store, rep, day) display aggregation. **Change it (or its config) and all six consumers move together:**

```
raw_sales / daily_sales_feed
        │
        ▼
   _sales_cell_agg  ──►  Sales Report
   (activations,    ──►  Executive MTD (by location / employee, trending, conv, APB)
    byod, upgrade,  ──►  Daily Targets (attainment, pace, DM roll-up, conversion)
    port, bill_qty, ──►  Productivity / Stack Ranking / Review
    accessory_rev,  ──►  Reconciliation (sales side)
    setup_fee, gp)  ──►  (box counts)
```

Shared config that fans out to all six: `contract_type_map` (mig 213), `activation_rules` (mig 224), `accessory_config` (mig 208), `billpay_products` (mig 214), `box_departments` (mig 218), `box_count_buckets` (mig 231), and the canonical store key.

> **Critical:** `_sales_cell_agg` is **display only**. The commission **PAY** path (`calculator.py`, `commission_engine.py`) re-derives activations/accessories/upgrades from its **own** classifiers over `raw_sales`. So a display change does **not** move pay (`auto_updated = false` on the pay edges), and pay changes when the classifier/config changes. Moving pay onto a new basis is always an explicit opt-in.

---

## 3. Activations — basis of truth

```
Activation Details report (raw_custom_import, distinct Serial#, Upgrade excluded)
   │  _cr_resolve_activation_details  (Dealer Code → store name via _dealer_to_store_map)
   ├─► /activation-counts (per store + market)
   ├─► Executive MTD activations         ┐ when the basis = activation_details
   ├─► Sales Report activations          ┘ (auto-on when AD data present; total-safe merge)
   └─► metric_recon (primary side)  ◄──► _sales_cell_agg activations (secondary side)
                                         → match = ingest proven good; mismatch = flag + remediation
```

The basis is chosen by `metric_source_of_truth` (`_metric_source`). It **auto-activates** for a tenant that has Activation Details ingested, and falls back to the sales feed otherwise (byte-identical). One place decides the basis; Exec MTD, Sales Report and the Activations report all read it.

---

## 4. Bill payments — three-way + daily cash

```
Bill Payment Transactions report (basis of truth)
        ├─► metric_recon.reconcile_bill_payments  ◄── sales feed (bill_qty/bill_amt)
        │                                          ◄── processor (ePay / VidaPay, by carrier)
        └─► (tender = cash) ─► reconcile_billpay_cash ◄── daily_closing.epay_on_cash (declared)
                                                          → per-store over/short
```

- Processor is chosen from `metric_source_of_truth.processor` or `commcalc.data_source` (ePay=Boost, VidaPay=Total).
- The **daily-cash** link (`/metric-recon?metric=bill_payments` → `daily_cash` block) reconciles actual bill-payment cash against what employees declared at closing — join grain `(store, period)`.
- Declared cash also reconciles against `bank_deposit` via `deposit_recon.py`.

---

## 5. Accessories

`accessory_config` (per-org, single source) → `_is_accessory` → `_sales_cell_agg.accessory_rev` → Sales Report, Exec MTD (Acc Sales / APB / Acc+Setup), Accessory Targets (incl. set-up fee), Productivity. The **Sales-by-Product** accessory flag reads the *same* `accessory_config` departments, selectable from the report's observed departments. **Pay** uses `calculator.py`'s own accessory dept/keyword config — separate from display.

---

## 6. Store identity — the join key everything depends on

`_canonical_store_key_fn` collapses store spelling/ID variants to one key so per-store rows merge. Activation Details identifies stores by numeric **Dealer Code**; `_dealer_to_store_map` resolves it to the store name (via `store_mapping` / storeops stores / merchant IDs). **If a Dealer Code is not linked to a store record, its activations show as a numeric ID instead of merging** — an `auto_updated = false` gap; fix by mapping the code in Store Management.

---

## Maintaining this map

When you add an ingested item, a derived metric, or a new report:
1. Add the edge(s) to `925_data_lineage_seed.sql` (and re-run it), and
2. Add the row(s) here.

Query "what does X touch?": `select * from commcalc.data_lineage where source_key = 'X'` — or `GET /api/v1/commcalc/data-lineage?source_key=X`.
