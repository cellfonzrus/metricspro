# Integrations hub — one page for every data connection

Owner design (2026-08-27): *"All integrations should be on one page … each one should have a clear purpose
without carrier name unless it is carrier specific … clear steps as part of a wizard even if it is a 2-step
job … make setting up this highly efficient system very user friendly — best in class."*

## What it is
`/commcalc/integrations` — a single **navigator + status board** over every connection/import surface. It is
**composition only, never a config store**: each card deep-links to the page that already owns that config
(single-source, migs 208/923). Same posture as the Setup Wizard.

## Why it exists
Integration surfaces were spread across three places — the NAV "Integrations & Imports" group, the
`/configurations` "Auto-Imports & Data" section, and standalone routes (two, `ma-upload` and `report-mappings`,
weren't in the nav at all). No single page answered "what can I connect, and what's connected?"

## The page
- **Cards**, grouped into four categories: Automatic data feeds · Manual imports · Make the data make sense ·
  Guided setup. Each card = icon, **carrier-neutral title**, one-line **purpose**, a **live status pill**, and
  a **Set up / Manage** button plus a direct **Open** link.
- **Status** (`connected` / `set up · paused` / `not set up` / unknown) is a best-effort probe of each
  integration's own config table — never 500s the page.
- **Carrier-neutral naming**: titles never carry a carrier/processor name. A small `badge`
  (Processor / Carrier / Distributor) marks the few genuinely bound to an external-system category; only the
  distributor sweep is `carrier_specific`.
- **2-step wizard** on every card (a right-side drawer), uniform even for a 2-step job: **Step 1 Connect**
  (what it does + a button into its config page) → **Step 2 Turn it on & verify** (save, Test, Run now →
  status flips to Connected; a "Recheck status" re-probes). The wizard guides; the config still round-trips
  its owning page.

## Backend
`GET /commcalc/integrations` — returns the catalog (`_INTEGRATIONS_CATALOG`) decorated with a live
`status` per item and a summary count. `_integration_probe` reads the item's config table
(`email_sweep_config`, `ftp_sweep_config`, `data_source`, `connector_instances`, `epay_sweep_config`,
`dlar_sweep_config`, `vip_sweep_config`, `closing_sweep_config`, `column_mapping`, `store_aliases`,
`report_pull_map`, `report_definitions`) with an optional `enabled_col`; any error → `unknown`.

## Registration
NAV: added as the **front door** of "Integrations & Imports" (`rbac.ts`); the individual pages stay reachable
and are what the hub deep-links to. Also listed in `reports.ts` for discoverability. Not in `REPORT_TREES`, so
no report-area gate — same gating as Connectors / Column Mapping.

## Coverage vs. the raw inventory
The catalog folds near-duplicates into one clear purpose (Upload Files + Upload Wizard + MA upload →
"Upload a Report File"; the mapping screens surface under "Make the data make sense") and **surfaces the two
orphaned routes** so nothing is missable. POS Import and Camera Setup live in their own modules and keep their
own setup wizards; they can be added as cards later if the hub should span every module.
