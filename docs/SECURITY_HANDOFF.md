# Security Workstream — Handoff

Living handoff for the security hardening effort. Any session can resume from here. Kept current as
work proceeds. Companion docs: `SECURITY_CONTROLS_SPEC.md` (the plan + control matrix),
`SECURITY_DAILY_QUESTIONS.md` (operator go-lives), `INCIDENT_RESPONSE_PLAN.md`, `BACKUP_DR_PLAN.md`.

**Last updated:** 2026-08-17 (item 15 pt29) · **Branch:** `claude/employee-commission-structure-3g5hva` · **PR:** #30 (draft, CI green)

---

## 1. Status at a glance

| Phase | State |
|---|---|
| **Phase 1 (P0)** — sessions, rate-limit, retention, fail-closed, startup posture, login ledger | ✅ complete |
| **Phase 2 (P1)** — export governance, PII masking, constant-time secrets, 2FA admin, CSP | ✅ complete |
| **Phase 3 (P1/P2)** — CI gates (12), WORM (13), RPO/RTO+IRP (14), DSAR export (16) | ✅ done; **erasure deferred** |
| **Item 15 — Pydantic rollout** | 🟡 in progress, incremental (parts 1–29 = 164 endpoints; ~269 remain) |
| External Threat Defense Plan | ✅ code-tractable parts done (IP blocklist, session purge, IRP) |

**Migrations 857–863: ALL APPLIED.** No SQL pending.

---

## 2. What ships ON vs OFF (enablement posture)

Nothing behaviour-changing enforces on deploy. Toggle via Railway env; break-glass documented per switch.

**ON by default (safe):** rate limiting (`RATE_LIMIT_ENFORCE`), RBAC scope fail-closed
(`RBAC_SCOPE_FAILCLOSED`), PII masking, constant-time secret verify, WORM triggers, export governance,
CSP (report-only).

**OFF — deliberate operator go-lives (tracked in SECURITY_DAILY_QUESTIONS):**
- `SESSION_ENFORCE` (idle/absolute session timeout)
- `ADMIN_2FA_ENFORCE` (super-admin 2FA + impersonation 2FA; enroll admins first)
- `FIELD_ENCRYPTION_STRICT` (refuse plaintext PII in prod — after confirming `FIELD_ENCRYPTION_KEY` set)
- `MULTI_TENANT_ENFORCE` (tenant isolation — after isolation test)
- `STARTUP_STRICT` (fail-to-boot on posture findings)

**Non-code operator actions still open:** Vercel Deployment Protection on previews; confirm Supabase
PITR + run first restore drill; promote CSP + CI gates from report-only to blocking; escrow
`FIELD_ENCRYPTION_KEY` offline; configure Supabase Auth rate limits (authoritative login lockout).

---

## 3. Where things live (code map)

**Backend (`backend/app/`):**
- `core/rate_limit.py` — per-IP limiter (auth/reset tiers) + IP-block check.
- `core/session_guard.py` — idle/absolute session timeout + `revoke()`; table `core.session_activity`.
- `core/login_guard.py` — login attempt ledger + soft lockout + alert; table `core.login_attempt`.
- `core/ip_block.py` — IP blocklist (cached); table `core.ip_block`.
- `core/security_posture.py` — startup posture check/warnings.
- `core/run_secret.py` — constant-time `verify_notify_secret` + rotation (`NOTIFY_RUN_SECRET_NEXT`).
- `core/crypto.py` — field encryption (fail-closed under `FIELD_ENCRYPTION_STRICT`).
- `core/schemas.py` — `StrictModel` / `LaxModel` (Pydantic rollout base).
- `core/tenant_middleware.py` — session guard + admin-2FA gate wired into the auth path.
- `core/access_log.py` — request access log; table `core.access_log`.
- `modules/core/router.py` — endpoints: `/core/audit/prune/run-due`, `/ip-block*`, `/sessions/revoke`,
  `/export-event`, plus the typed auth/admin endpoints.
- `modules/crm/customer360.py` — `mask_pii` + DSAR via `build_360(reveal=True)`;
  `modules/crm/router.py` — `/customer-360/dsar`, `can_export_dsar`.
- `main.py` — middleware order + `@app.on_event("startup")` posture check.

**Frontend (`frontend/`):**
- `next.config.ts` — security headers + CSP (report-only).
- `src/lib/export.tsx` — export governance (audit/watermark/cap chokepoint) + `governExport`.
- `src/lib/client.ts` — dead-session codes (`session_idle`/`session_expired`) → sign out.
- `src/app/(platform)/admin/access-log/page.tsx` — Block IP / Revoke sessions.
- `src/app/(platform)/crm/lookup/page.tsx` — Reveal contact + DSAR export.

**Migrations:** `database/migrations/857`–`863` (retention, session_activity, login_attempt, ip_block,
crm_lookup_audit.pii_revealed, export_event, WORM).

---

## 4. In flight / next steps

- **Item 15 (Pydantic), continue incrementally.** Done: parts 1–7c (containment/export/auth-ledger
  strict; auth self-service, admin/RBAC, CRM writes, asset borrowings, account company mgmt, storeops
  full storeops sweep — org-structure + approvals + config + payroll + payroll-config + action-plans
  + google-reviews + schedule-templates + shift-extension + face-deletion + store-config + bulk
  employee/store + merge + bulk-payscale + clock-in/override/out + face-retention-run + face-save lax).
  **storeops done** except 7 body-threading config helpers (deferred by rule). **Part 8:** helpdesk
  support-console writes (case reply/note/assign/status, canned upsert, SLA put, failures bulk-review,
  fix-request status → lax). **Part 9:** helpdesk ticket core (create/update/comment, team-member add,
  settings put, AI-assist, escalate → lax). **helpdesk done** except `_clean`/`new_fix_row` threaders
  (deferred). **Part 10:** commcalc config CRUD (carrier/connector/report-def create+update, nav-label,
  nav-layout → lax). commcalc is the big one (~124 dict bodies, money-sensitive) — take in careful
  batches, reading each function's exact span first. **Part 11:** commcalc mapping/registry upserts
  (column-mapping, commission-field, target-field, commission-category-map). **Part 12:** MA class wiring
  (mode/leg) + MA product-class (upsert/confirm). **Part 13:** accessory-definition (upsert/confirm/
  propose-from-data; field-rule PUT threads body → deferred). **Part 14:** item/config (upload-duty,
  item-category, item-mapping upsert+bulk, device-model add). **Part 15:** custom-import-type,
  connector-schedule, accessory-flags-push. **Part 16:** money-settings (commission-settings,
  activation-matcher, plan-line-matcher); save_plan_installment threads body → deferred. **Part 17:**
  save-category-rule + expected-commission promote/revoke; **new skip pattern** — put_category_qualification,
  put_category_payout, put_expected_commission_config use `body` ITSELF as a freeform category map
  (`raw = body.get("config") if isinstance(...) else body`), so LaxModel would drop the top-level keys →
  treat like body-threading, SKIP. **Part 18:** mrc-mapping confirm+bulk-classify, payout-schedule,
  product-mrc; update_plan_installment threads body → deferred. **Part 19:** carrier-template clone,
  distributor save, distributor-payment add. **Part 20:** save_commission_plan (large: header+rules+
  tiers+assignments); setup-fee/pay-gate use freeform `else body` maps and payout-exclusion uses
  `{**body}` spread → all three deferred (freeform-map family). **Part 21:** coverage-excluded-sellers,
  bulk-assign commission plan, update_store (body.items() allow-list → model_fields_set loop),
  add_store_alias. **Part 22:** ingest-guard config+decide, gp-category-map, commission-leg label+config.
  **Part 23:** update_chargeback, put_accessory_config (14 presence fields), put_catalog_override.
  **Part 24:** save_target, roll_forward_targets, save_carrier_kpi_metric, save_kpi_actuals, import_paramount_mtd.
  **Part 25:** put_exec_metric_config, put_productivity_config, post_rep_aliases.
  **Part 26:** put_expenses, bulk_apply_expenses, upsert_expense_system_line, put_expense_apply_config,
  apply_expenses_to_months. **Part 27:** put_ftp_config, test_ftp, put_email_config, test_email.
  **Part 28:** put_sales_derive_config, put_pos_profile, save_data_source, test_proxy.
  **Part 29:** save_report_pull_map, manual_upload_save/reset_mapping, data_source_login_verify,
  live_login_submit/click/input. Next commcalc candidates (grep 'body: dict'): ma_overview_put_tile/rate,
  custom_report_defs_save, agency_* (~9). The
  group (put_expenses/bulk_apply/upsert_expense_system_line/put_expense_apply_config/apply_expenses),
  ftp/email/data-source/proxy config, report-pull/manual-upload maps, live-login, ma-overview,
  custom-report-defs, and the agency_* group (~9). Read each first, watch for body-threading AND
  freeform-map fallbacks. hr (~32,
  many freeform-intake / public-token endpoints — treat like body-threading). **POS skipped** — module
  incomplete / no data. **Rules:** skip
  endpoints that
  thread the raw `body` dict into shared helpers; preserve None-vs-empty + downstream `.get()`; for a
  field the handler validates itself (e.g. `float(amount)` → 400), type it `Any` so Pydantic doesn't
  pre-empt with a 422; for PATCH use `model_fields_set` to keep "only-sent-keys". `create_lead`
  (25 fields) + `bulk-assign/dispose` need a dedicated pass.
- **Item 16 erasure** — deferred by owner context (no SSN yet, 1st month, WORM-vs-erasure tension).
  When needed: scoped anonymization of `pos.customers`/`crm_lead` leaving WORM audit intact;
  crypto-shred once SSN/bank exists. Owner to confirm scope.
- **Operator go-lives** — see §2 and SECURITY_DAILY_QUESTIONS.

---

## 5. Conventions (so a resume matches the house style)

- Every new enforcement has a break-glass env kill switch, documented; behaviour-changing ones default OFF.
- Pure helpers with `if __name__ == "__main__"` self-tests; verify with `python -m app.core.<mod>`,
  `python -c "import app.main"`, and frontend `npx tsc --noEmit -p tsconfig.json`.
- Best-effort audit writes never break a request (wrapped, swallowed).
- Migrations numbered; the prune function is redefined by later migs (drop old arity first).
- Commits: descriptive body + `Co-Authored-By` / `Claude-Session` footer. PR #30 stays draft;
  auto-subscribed; hourly self check-in via send_later.
