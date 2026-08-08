# MetricsPro — SAP/SaaS Framework for the Prepaid Retail Industry

_Status: foundational design (v0.1). This is the spine the product is being refactored onto. Read
with [ARCHITECTURE.md](ARCHITECTURE.md) (how it works today) and [USER_GUIDE.md](USER_GUIDE.md)
(plain-English, for operators)._

---

## 1. The vision in one paragraph

Every prepaid dealer — whether they sell **Boost, Cricket, Metro, or Total Wireless** — runs the
**same business**: sell phones at a discount set by the carrier's **hotsheet** for a **promo
period**, get **reimbursed** the difference between device cost and the discounted sale price, and
get **paid for activations** plus whatever **incentives (SPIFFs)** that carrier defines that month.
The carriers use different portals, different report names, and different category labels — but the
**economics are identical**. MetricsPro becomes the **"SAP of prepaid retail"**: one engine, one P&L,
one set of dashboards, where each carrier/vendor is **configured and mapped**, never hardcoded. Add a
new customer on a new carrier → an onboarding wizard captures their portals + logins + reports and
maps their categories onto the canonical model → every existing report, P&L line, and dashboard
works for them on day one.

---

## 2. The canonical payout model (the heart of the system)

No matter the carrier, dealer compensation rolls up to **four canonical components**. Everything we
compute, report, and put on the P&L is one of these four:

| Canonical component | What it is | Boost source today | Cricket / Metro / Total (examples) |
|---|---|---|---|
| **RESIDUAL** | Recurring monthly income tied to **active subscribers** (paid every month the line stays active). | **MI + ATU** (`raw_mi`) | "Residual", "Recurring Commission", "Base Comp" |
| **COMMISSION** | One-time pay for an **activation / upgrade / port-in**, usually promo-rate by hotsheet. | Comprehensive Comp "**Promo / Offer**" lines (`raw_comp_report`) — ~76% of comp | "Activation Commission", "Promo Payout" |
| **SPIFF** | Special performance incentives / **bounties** (often paid over months 1–6, retention-based). | Comp "**Bounty / SPIFF**" lines — ~20% of comp | "SPIFF", "Bonus", "Accelerator" |
| **REIMBURSEMENT** | Device **cost-vs-discounted-sale** make-whole, per the hotsheet promo for that device + period. | **Asset reimbursement** (VIP invoices / `asset_ledger`) + Comp "Reimbursement" lines | "Equipment Reimbursement", "Subsidy Recovery" |

> **This is why the "Residual Trend" was wrong.** The old report summed the **entire**
> Comprehensive Comp report and called it "residual." But that report is **~76% Promo (Commission) +
> ~20% Bounty (SPIFF) + a little Reimbursement** — and **$0 actual residual**. Per the corrected
> definition, **RESIDUAL = MI + ATU** (`raw_mi`). The comp report is **COMMISSION + SPIFF**. See the
> rename in ARCHITECTURE.md → "Total Compensation Trend".

### Canonical entities (not just payouts)
The same map-don't-hardcode rule applies to every dimension:
- **Store / Location** — carrier SFID / address → canonical `store` (the store-match UI already does this).
- **Rep / Advocate** — carrier rep name/id → canonical employee (rep-alias map exists).
- **Product / Device** — carrier SKU string → canonical device model (the hotsheet normalizer, pending).
- **Category** — carrier compensation-type string → canonical payout component (the **new** map below).

---

## 3. The mapping layer

A small set of **per-tenant, per-carrier config tables** turns raw carrier data into canonical data.
Nothing carrier-specific lives in code; it lives in these tables and is edited through admin UIs.

```
carrier                     (tenant's carriers: Boost, Cricket, …)
carrier_category_map        (carrier raw category string  → canonical component + subtype)
store_aliases / store_mapping  (raw store string         → canonical store)   [exists]
rep_aliases                 (raw rep name/id             → canonical employee) [exists]
product_aliases / hotsheet  (raw SKU                     → canonical device)   [partial]
```

**`carrier_category_map`** is the new keystone. One row per (carrier, raw_category) → canonical
component. Example seed for Boost (derived from the live comp data):

| carrier | raw_category (matches on prefix/regex) | component | subtype |
|---|---|---|---|
| Boost | `MI`, `ATU` | RESIDUAL | base |
| Boost | `* Promo Upgrade`, `Promo PIC Offer`, `Promo New Act Offer` | COMMISSION | promo |
| Boost | `* Bounty - Month *`, `* SPIFF *` | SPIFF | bounty |
| Boost | `* Reimbursement`, `Ramp Up Subsidy` | REIMBURSEMENT | subsidy |

To onboard Cricket, an implementer adds Cricket's rows — **no code change**. Reports that group by
`component` (P&L, comp trend, dashboards) immediately work for Cricket.

**Matching rules**: exact → prefix → regex, evaluated most-specific-first; an **unmapped** raw
category is surfaced in a "Categories needing mapping" panel (same pattern as the store-match
"unmatched" view) so it's never silently dropped or miscounted.

---

## 4. Tenant / carrier / vendor model

```
tenant (org)                          -- one customer company  (today: single org_id; multi-tenant = drop the hardcode)
  └── carrier (Boost, Cricket, …)     -- which carriers this tenant sells
        └── vendor / connector        -- a portal the data comes from (a carrier can have MANY vendors)
              • ePay owner portal      (MI/ATU, comp, payment detail)
              • VIP Wireless           (invoices, paygo, credit memos)   ← one of Boost's vendors
              • Elevate Go (DLAR)      (KPIs)
              • Yoobic                 (hotsheet)
              • B2B Soft               (sales, inventory, closing)
```

**Key insight the user called out:** a carrier has **multiple vendors**, each with its **own website,
login, and 2FA**. VIP Wireless is just *one* of Boost's vendors. So the unit of configuration is the
**connector** (a vendor portal), not the carrier.

### Connector config (per vendor portal) — the schema the onboarding wizard fills
```
connector_config
  id, tenant_id, carrier_id, vendor_name, label
  portal_url
  auth_type            -- form | oauth | api_key
  username             -- stored; password in a secret store, never in logs or API responses
  twofa_method         -- none | sms | totp | email | biometric/device-trust
  twofa_status         -- ok | needs_setup | BLOCKED (e.g. VIP/b2bsoft datacenter-IP block)
  automatable          -- true | false  (false ⇒ manual-upload fallback + 9:30am reminder)
  reports[]            -- list of report definitions (below)
  schedule             -- frequency, hour, timezone, next_run_at
  last_status, last_run_at, last_detail
```
```
report_definition (per connector)
  report_key           -- e.g. mi, comp_report, payment_detail, dlar_rep, inventory_aging
  source_name          -- the exact report name in the portal (shown in the Upload Wizard)
  report_id            -- portal's internal id (if applicable)
  period_mode          -- data | report_month | current   (see epay_sweep period derivation)
  target_table         -- raw_mi, raw_comp_report, …
  category_field       -- which column feeds carrier_category_map
  refresh_months       -- 1 = current only; >1 = current + N-1 closed months (comp in-arrears)
```

This generalizes exactly what `epay_sweep.REPORTS` + `epay_sweep_config` already do for Boost/ePay —
the refactor is to make those **rows in a table** instead of a Python dict, keyed by connector.

---

## 5. The onboarding wizard (no-human-interaction implementation tool)

Goal: a non-engineer implementer onboards a new customer/carrier end-to-end, and **never hits the
VIP/b2bsoft surprise** (login that silently fails post-2FA from a datacenter IP).

**Wizard steps**
1. **Tenant** — company name, org, contact, billing.
2. **Carriers** — pick carriers (Boost/Cricket/Metro/Total) or "new carrier".
3. **Vendors / connectors** — for each carrier, add each vendor portal:
   - portal URL, username, password (→ secret store).
   - **2FA probe**: the wizard attempts a *server-side* login (from the Railway egress IP that
     production scrapes from) and **detects the auth outcome**: success / wrong-creds / 2FA-required
     / device-trust-or-biometric-gate / IP-blocked. This is the critical step — it surfaces the
     VIP/b2bsoft class of problem **at onboarding**, not months later.
   - If `automatable=false`, the wizard records a **manual-upload fallback** (which report, which
     page, who gets the 9:30am reminder).
4. **Reports** — for each connector, define the reports to pull (name, id, period mode, target
   table). The wizard's "discover reports" (already built for ePay) can enumerate a portal's menu.
5. **Category mapping** — run a sample pull, show every distinct raw category, let the implementer
   map each → canonical component (with smart prefix defaults). Unmapped = blocked until resolved.
6. **Store / rep / product mapping** — reuse the existing store-match + rep-alias UIs, seeded from
   the sample pull.
7. **Schedule + go-live** — set frequencies; the wizard runs one full sweep, verifies row counts and
   that the P&L/dashboards populate, and flips the tenant live.

**2FA strategy (so we don't repeat VIP):**
- Classify each connector's `twofa_method` up front.
- `totp` → store the TOTP secret, generate codes server-side (fully automatable).
- `sms`/`email` → needs a human or an inbox/relay integration → mark semi-automated.
- `biometric / device-trust` (Yoobic) or `datacenter-IP-blocked finalize` (VIP/b2bsoft) → mark
  `BLOCKED`, fall back to **manual upload + reminder**, and log the exact reason so it's visible.

---

## 6. What exists vs. what to build (gap map)

| Capability | Today | To reach the framework |
|---|---|---|
| Canonical components | Implicit/scattered; comp mislabeled "residual" | Define the 4 components; map comp + MI/ATU + asset onto them (this doc + the rename) |
| Category mapping | None (categories hardcoded in places) | `carrier_category_map` table + admin UI + "unmapped" panel |
| Store/rep mapping | **Built** (store-match UI, rep aliases) | Generalize per-carrier |
| Product/SKU mapping | Partial (hotsheet) | SKU→model normalizer (pending hotsheet upload) |
| Connector config | Python dict (`epay_sweep.REPORTS`) + per-portal `*_sweep_config` tables | One `connector_config` + `report_definition` table set, carrier/vendor-keyed |
| 2FA handling | Ad-hoc; VIP/b2bsoft discovered late | 2FA probe at onboarding; explicit `twofa_method`/`automatable` |
| Multi-tenant | Single hardcoded `org_id` | Drop the hardcode; scope every query by tenant; RBAC already roughed in |
| Onboarding | Manual, engineer-driven | The wizard above |

---

## 7. Phased rollout

- **Phase 0 (now):** bug-test all existing systems; fix the residual mislabel (RESIDUAL = MI+ATU,
  comp = Total Compensation); write this framework + the docs. *(in progress)*
- **Phase 1 — Canonical model:** add `carrier_category_map`; route comp/MI/ATU/asset through the 4
  components; every payout report/P&L groups by component. Boost seed mapping shipped.
- **Phase 2 — Connector config:** move `epay_sweep.REPORTS` and the per-portal sweep configs into
  `connector_config` + `report_definition`; the existing sweeps read from rows.
- **Phase 3 — Mapping admin UIs:** category-map editor + "unmapped categories" panel (mirrors
  store-match); generalize store/rep/product maps per carrier.
- **Phase 4 — Onboarding wizard:** the 7-step wizard incl. the server-side 2FA probe.
- **Phase 5 — Multi-tenant:** remove the hardcoded org_id, tenant-scope all queries, enforce RBAC
  server-side, per-tenant data isolation.

**Guiding rule (the standing directive):** _everything user-mappable + user-configurable — config
tables + admin UIs, never hardcoded or one-off SQL._

---

## 8. Dual-POS rule — streams never merge (owner directive 2026-08-07)

A tenant may run the **built-in POS module** (`pos.*` schema) and/or an **external POS**
(b2bsoft et al., via the existing sales-feed pipeline). Which, and in what roles, is a
tenant-setup decision stored in `core.tenant_pos_setup` (mig 727): `builtin_role` /
`external_role` ∈ {off, primary, secondary}, `secondary_mode` ∈ {add, parallel},
`separate_registers` boolean.

**THE RULE: numbers from the primary POS and a secondary POS are NEVER merged.** Every
category of reporting shows them separately — "sales under POS 1 and POS 2".

Consequences, in force everywhere:

1. **The external feed pipeline is untouchable.** Mailbox sweeps, parsers, uploads,
   `raw_sales` / `daily_sales_feed`, and their consumers keep working byte-identically for
   external-primary tenants.
2. **The built-in POS writes only its own stream** (`commcalc.pos_builtin_daily_sales`,
   `commcalc.pos_builtin_sales`, same grain as the external tables). Promotion into
   `daily_sales_feed` / `raw_sales` (column-for-column, delete-by-period + empty-abort)
   happens exclusively when `builtin_role = 'primary'` — that is the tenant's POS-1 ledger
   landing where every existing consumer already looks, exactly like an external feed does.
3. **Secondary in `add` mode:** the secondary stream counts toward end-of-day sales totals
   and flows into the discrepancy report, P&L and other reporting **as its own separately
   labeled stream** — and its qualifying sales DO pay commission ("the rep did their job"),
   computed on the secondary stream, never blended into primary figures.
4. **Secondary in `parallel` mode:** comparison-only figures; excluded from EOD totals,
   discrepancy, P&L and commissions.
5. **Reconciliation for dual-POS tenants:** both POS totals are shown and reconciled
   against the **combined** end-of-day total — unless `separate_registers` is set, in which
   case each POS reconciles independently as its own register. Per-tenant, dynamic, never a
   static behavior.

Status: config table + built-in stream + primary promotion are implemented
(`pos/commcalc_feed.py`, mig 727). The secondary-stream consumers (EOD/discrepancy/P&L
labeling, the POS-2 commission pass, dual-register recon UI) are the follow-up wave — the
commission piece is gated on the still-missing commissions requirements doc.
