# Onboarding / Setup Wizard — Architecture

Senior-architecture design for the platform-wide onboarding wizard (owner 2026-08-26). The wizard is a
**navigator + state ledger, not a config store**: every answer writes through the *existing* config endpoint
its settings page already uses; the wizard's own table stores only meta (step, status, free-text notes);
completion is **derived** from the same readiness probes the pages rely on. Single-source, like migs 208/923.

## Data-first lens (owner refinement)
Lead with **what data is actually ingested**, then reverse-engineer — for each ingested dataset, which
reports/menus it powers, and which reports are blank because their feed is missing. Powered by
`GET /commcalc/data-readiness` (data_lineage reachability + per-tenant presence). This is the entry view;
the config phases below are the "complete the missing pieces" layer.

## Phases (dependency-ordered)
- **A. Foundation** — company/entities, carriers, users & roles.
- **B. Physical network** — stores, store↔processor merchant IDs, employee roster. (needs A)
- **C. Data ingestion** — column mapping, email/FTP imports, the data feeds, Dealer Code → Store. (needs A/B)
- **D. Classification & source-of-truth** — accessory departments, activation/bill-pay basis, exec metric defs. (needs C)
- **E. Pay & goals** — commission plans, plan assignment, setup-fee economics, installments, targets. (needs A/B/D)

Each step: a plain-English question, the existing endpoint it writes through, a completion check (an existing
readiness probe), and prerequisites. Optional steps (email/FTP/installments) are skippable so 100% is reachable.

## Data model
`commcalc.onboarding_state (org_id, step_key, status, answers jsonb, reviewed_by, reviewed_at, updated_at,
unique(org_id, step_key))` — meta only. `status`: not_started | in_progress | skipped | reviewed. `done` is
DERIVED from readiness, never stored. Additive + RLS + service_role (mig 923 template). Missing table →
readiness-only, never 500.

## Backend (composition layer; reuse, don't shadow)
- `GET /commcalc/onboarding` — phase/step catalog decorated with question, `powers` (from data_lineage),
  deep-link, prereqs, and a computed `{status, ready, unlocked}` (left-joins onboarding_state).
- `PUT /commcalc/onboarding/{step_key}` — record status/answers (skipped / in_progress / notes). Never accepts config payloads.
- `POST /commcalc/onboarding/{step_key}/review` — stamp reviewed_by/at for config-only steps.
- Reuse `_onboarding_checklist` / `_oc_*` probes and cross-module readers (core users/roles, storeops
  stores/employees/merchant-ids, account companies) — call their functions, never copy their tables.

## Frontend
Phase-grouped, dependency-gated stepper under `commcalc/onboarding`: progress header, `PhaseSection`
(locks steps whose prereqs aren't done), `StepCard` (status pill, "Powers:" line, CTA deep-link, Skip / Mark
reviewed). Drag-drop only where many-opaque-tokens → known-targets: **dealer_code → store**, **POS dept →
accessory**, **rep → plan**, **merchant id → store**. Everything else is a form/deep-link.

## Reuse vs new
Reuse: the presence engine (`_ONBOARDING_ITEMS`, `_oc_count/_oc_custom_present/_oc_config_present`,
`_onboarding_checklist`), `data_lineage` + `get_data_lineage`, every config endpoint/page as a deep-link,
existing readiness endpoints (column-mapping, coverage, dealer-code-map, merchant-ids coverage).
New (small): `onboarding_state` migration, the `_ONBOARDING_STEPS` catalog, the 3 endpoints, the phase-grouped
frontend components, and the data-first `data-readiness` reverse map.

## Top risk
Single-source violation — never let `onboarding_state.answers` hold carrier/store/plan data; hard config
always round-trips its owning endpoint (the mig-208 failure mode). Cross-module checks stay defensive
(try/except, degrade to 0/`review`), never 500 the wizard.
