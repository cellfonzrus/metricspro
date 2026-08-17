# Security Workstream — Handoff

Living handoff for the security hardening effort. Any session can resume from here. Kept current as
work proceeds. Companion docs: `SECURITY_CONTROLS_SPEC.md` (the plan + control matrix),
`SECURITY_DAILY_QUESTIONS.md` (operator go-lives), `INCIDENT_RESPONSE_PLAN.md`, `BACKUP_DR_PLAN.md`.

**Last updated:** 2026-08-17 (item 15 pt55 — core/router signup/purge/bulk-assign) · **Branch:** `claude/employee-commission-structure-3g5hva` · **PR:** #30 (draft, CI green)

---

## 1. Status at a glance

| Phase | State |
|---|---|
| **Phase 1 (P0)** — sessions, rate-limit, retention, fail-closed, startup posture, login ledger | ✅ complete |
| **Phase 2 (P1)** — export governance, PII masking, constant-time secrets, 2FA admin, CSP | ✅ complete |
| **Phase 3 (P1/P2)** — CI gates (12), WORM (13), RPO/RTO+IRP (14), DSAR export (16) | ✅ done; **erasure deferred** |
| **Item 15 — Pydantic rollout** | 🟡 incremental (parts 1–55 = 357 endpoints; ~76 remain, ALL DEFERRED-by-rule or POS). pt55: signup, purge_employee, bulk_assign. **core/router convertible surface COMPLETE.** The remaining raw `body: dict` handlers are all deferred by the established rules: **create_login / assign_role** (7 in-process dict callers across hr onboarding_provision + assign_role threads into `_normalize_grant_write` — convert would need 7 coordinated auth-critical edits; deferred), create_fix_request + support_docs_upsert/import (helper-threaders), whats_new+training (clean_* threaders), impersonation put_policy (else body), commcalc-20 (threaders/freeform/spreads), helpdesk `_cfg_*`/create_fix_request, hr-3 (EMP_FIELDS + intake freeform), crm-3 (loud-reject config), payables source-map spread, notify send_file, notify/render helper, closing create_row (dynamic axes), **pos-40 (skipped per owner — incomplete/no data).** | Fully swept: commcalc, hr(+letters), crm-leads, referral, notify, storevisit, billing, recovery, remediation, asset(PO+router), closing(27/28), account, storeops/payroll_approval, core small files, core/fix_pipeline. core/router: failures cluster + portal-reports + widget-overrides done; **~24 raw remain = auth/tenant-credential handlers** (signup, create_login, assign_role, bulk_assign, connect_tenant, admin_set_password, reset_password, set/verify_phone, put_tenant_settings, put_security_settings, create/update_role, resend_invite, reveal_code, bulk_provision, delete/deactivate/purge_user, disable_and_switch, reinstate_login, reset_tenant_admin_password) — security-critical, MANY thread body into auth helpers → read each carefully, expect a mix of convert + defer. Deferred: whats_new+training (clean_* threaders), impersonation put_policy + core support_docs + create_fix_request (else body / helper-threaders), commcalc-20/helpdesk/hr-3/crm-3 (classified earlier), pos (40, skipped). |
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
  live_login_submit/click/input. **Part 30:** ma_overview_put_tile, ma_overview_put_rate;
  custom_report_defs_save threads body into validate_definition → deferred. **Part 31:** agency
  set_consent, set_carriers, generate_invoice; the six agency upsert_*/add_transfer thread body into
  `_agency.*` → deferred. **Part 32:** vip/dlar/epay sweep put-config. **commcalc still has ~15
  convertible handlers** (38 `body: dict` total, minus known threaders/freeform-map). Remaining commcalc
  convertible candidates: pay_simulator_simulate, whatif_put_source_config, apply_ma_class_wiring_rule_
  proposals, upsert_category_rule, put_flag_rules, commission_rule_impact, payout_record,
  put_payout_accrual_config, save_financing_vendor, add_financing_vendor_carrier/detection_rule,
  seed_ma_product_class, seed_accessory_definition_classes (optional bodies) — read each first
  (payout_record + put_payout_accrual_config thread body → deferred). **Part 33:** add_financing_vendor_carrier,
  save_financing_target (save_financing_vendor + add_financing_detection_rule thread body into
  `_finreg.normalize_*` → deferred). **Part 34:** mi_save_plan (note: `_actor` internal key needs a
  Pydantic `Field(alias="_actor")` — leading-underscore names are reserved), mi_compute,
  mi_payout_decision, mi_resolve. **Part 35:** atu_config_set, put_flag_rules, upsert_category_rule.
  **Part 36:** whatif_put_source_config, pay_simulator_simulate, apply_ma_class_wiring_rule_proposals,
  commission_rule_impact. **Part 37:** seed_ma_product_class, seed_accessory_definition_classes.
  **✅ commcalc convertible surface COMPLETE** — the 20 remaining `body: dict` handlers are all
  deferred: body-threaders (save_plan_installment/update_plan_installment→_write_installment_schedule,
  put_accessory_definition_field_rule→normalize_field_rule, custom_report_defs_save→validate_definition,
  6× agency→_agency.*, payout_record→record_payout, put_payout_accrual_config→save_config,
  save_financing_vendor→normalize_vendor, add_financing_detection_rule→normalize_matcher); freeform
  `else body` maps (put_category_qualification, put_category_payout, put_expected_commission_config,
  save_setup_fee_config, save_pay_gate); `{**body}` spread (save_payout_exclusion).
  **Part 38 (hr started):** onboarding_save_category, onboarding_update_category, onboarding_save_task,
  onboarding_update_task (TASK_FIELDS allow-list via model_fields_set). **Parts 39–40:** intake_field_save/
  update (INTAKE_FIELD_COLS), onboarding_set_accounting_settings, put_onboarding_attention_config,
  onboarding_approve, onboarding_advance. **Part 41 (hr FULLY swept):** onboarding_provision
  (docs+compliance override gates), onboarding_set_profile (work_state via model_fields_set),
  onboarding_update_status (note presence via model_fields_set), onboarding_reattach_orphan,
  onboarding_mint_token, onboarding_return_task, onboarding_send_documents, onboarding_forward_accounting,
  onboarding_invite_one/bulk, onboarding_reconcile, onboarding_me_state, public_onboarding_state,
  public_onboarding_view, onboarding_me_dd_disclaimer, public_onboarding_dd_disclaimer,
  public_onboarding_sign, onboarding_me_sign (token-gated handlers still keep their `value` identity
  gate as a `value: Any` field + `form_data: Any` opaque). **✅ hr convertible surface COMPLETE** — 4
  `body: dict` handlers remain deferred: hr_create_employee / hr_update_employee (EMP_FIELDS-driven
  freeform employee records, like storeops's own create_employee); onboarding_me_intake /
  public_onboarding_intake (freeform intake map threaded/spread into `_apply_intake`).
  **Part 42 (crm leads/tasks — fully swept):** create_lead (26-field), update_lead (allow-list via
  model_fields_set), move_stage, dispose_lead, assign_lead, bulk_assign, bulk_dispose, convert_lead,
  intake_lead, add_activity, complete_task, snooze_task. **New pattern — sibling body-threading solved
  by shared/inherited models:** dispose_lead/assign_lead take `DisposeLeadIn`/`AssignLeadIn`;
  `BulkDisposeIn(DisposeLeadIn)` / `BulkAssignIn(AssignLeadIn)` add `lead_ids` and pass the SAME typed
  body straight through to the single-lead handler (attributes already present); move_stage constructs
  a `DisposeLeadIn(...)` for its internal dispose call; complete_task types its optional body as
  `DisposeLeadIn = None` and sets `body.task_id` before threading; intake_lead types body as
  `IntakeLeadIn(CreateLeadIn)` and forwards the model to create_lead. **Also fixed a latent bug:**
  `agency_response` was already typed `AgencyResponseIn(accepted)` but still called `body.get("reason")`
  → would AttributeError on every agency *decline*; added `reason` field + attribute access. crm
  config CRUD (put_config/create_config/update_config) DEFERRED — they iterate `body.keys()` and
  LOUDLY reject unknown keys with a custom 400 ("Nothing was saved"); a LaxModel would silently drop
  them (the exact documented bug) and a StrictModel would change the 400→422 contract.
  **Part 43 (referral — fully swept):** create_referral, redeem_submit (public token), and the whole
  staff lifecycle — send_qr, log_sale, activate, submit_for_approval, approve (float-validated
  commission stays `Any`), mark_paid, reject, void, flag_fraud. Optional-body handlers typed as
  `Model = None` + `body or Model()`; shared models `ReferralNoteIn` / `ReferralReasonIn` reused across
  the note-only and reason-or-note transitions. referral put_config DEFERRED (same loud unknown-key
  rejection as crm).
  **Part 44 (notify — swept):** create_recipient, update_recipient (allow-list via model_fields_set),
  put_report_config, send_to_designated, put_settings, send_now, send_email_plain,
  create_subscription, update_subscription. **Cross-module in-process caller fixed** — commcalc
  sales-recon calls `N.send_to_designated({...dict...})`; since the handler is now typed, the call site
  now builds `N.SendToDesignatedIn(...)`. `_sub_with_next` switched from `body.get(k)` to
  `getattr(body, k)` so both subscription writers pass the typed `SubscriptionIn` (None-drop filter
  preserves PATCH semantics). `send_now`/`send_to_designated` pass an explicit
  `{emails,phones,recipient_ids}` dict to the shared `_resolve_targets` helper (stays dict-based).
  notify `send_file` DEFERRED — threads `body` into `_resolve_targets` AND carries a nested freeform
  `files:[{filename,mime,content_b64}]` payload.
  **Part 45 (storevisit + billing — both fully swept):** storevisit — put_storevisit_config,
  create_checklist_item, update_checklist_item (allow-list via model_fields_set), create_visit,
  update_visit (header allow-list + `responses`/`accessories` full-replace via model_fields_set,
  nested rows stay `Any`), save_action_items, save_action_plan, signoff (note: a Pydantic v2 field
  literally named `items` is fine — v2 BaseModel has no `.items()` method). billing — upsert_plan,
  generate_invoice, update_invoice (presence via model_fields_set), mark_paid (optional),
  upsert_platform_connector (write-only `credential` persisted only when non-masked — stays `Any`),
  refresh_platform_costs (optional).
  **Part 46 (payables/recovery/remediation/asset — swept):** payables put_settings + upsert_phone_map
  (upsert_source_map DEFERRED — `dict(body)` full spread); recovery put_config (11-field allow-list),
  update_claim, send_claim; remediation upsert_playbook, propose (nested `params`/`assignee` stay
  `Any`), decide; asset PO create/update_vendor, put_po_settings, create_po (nested `lines` Any),
  update_po (status-transition validated), receive_po_line (nested `units` Any); asset router
  upload_b2b_inventory, set_investigation.
  **Part 47 (closing STARTED — 5/28):** verify_store, approve_expense, upload_envelope_photo,
  put_tender_config (nested defs/maps Any + recon_mode/custom presence), put_deposit_config (toggle
  presence via model_fields_set). `create_row` DEFERRED (huge money-handler with dynamic
  `counts`/`custom_tenders` config-driven axes + nested `expense_lines`, like hr_create_employee).
  closing remaining ~23 (mostly simple named-field/allow-list config setters + action toggles):
  put_count_config, bank_deposit, put_deposit_categories, put_deposit_adjustment_types,
  create_deposit_adjustment, update_bank_deposit_meta, put_cash_config, set_store_closer,
  upsert_alert_recipient, put_ops_chargeback_policy, decide_missed_dm_verify, record_deposit,
  confirm_pickup, undo_pickup, put_pickup_config, closing_sweep_put_config, put_expense_categories,
  create_expense_line, decide_expense_line, put_envelope_config, record_envelope_withdrawal,
  release_closing_row.
  **Part 48 (closing FULLY swept — 27/28):** all the above config setters + action toggles converted.
  Notables: `update_bank_deposit_meta` keeps its forbidden-money-field rejection by declaring those
  fields on the model so `model_fields_set` still detects them; `bank_deposit` `include_*` toggles
  fall back to the org config default via `X if "X" in model_fields_set else cfg[...]`, and
  `will_deposit_more` preserves its 3-state None/absent semantics the same way; `create_expense_line`
  hands `_validate_expense_line` (shared dict-based helper, also fed nested rows from create_row) an
  explicit dict built from the model. ONLY `create_row` stays DEFERRED (huge money-handler with
  dynamic `counts`/`custom_tenders` config-driven axes + nested `expense_lines`).
  **Part 49 (account + storeops/payroll_approval + hr/letters — all swept):** account put_journal /
  put_inventory_values (nested `rows`) / put_config (5 finance knobs, presence via model_fields_set);
  payroll_approval decide, set_payer, override, create_payer, update_payer (allow-list),
  set_store_payers, dispatch; hr/letters update_template, send_letter, approve_letter (opt),
  reject_letter (opt), put_letters_config (all with a Pydantic field literally named `body` where the
  endpoint's own param is also `body` — `body.body` works in v2).
  **Part 50 (core small files):** import_health create/update_import_feed; onboarding set_task_state,
  import_apply (opt); impersonation start/stop/reauth (auth-sensitive — target/session_id/token/reason
  fields). DEFERRED: whats_new save_note+ingest and training save_tour (thread `body` into
  `clean_entry`/`clean_tour` freeform helpers, and ingest uses the `else [body]` freeform pattern);
  impersonation put_policy (`normalize_policy(body.get("policy") if dict else body)` — freeform `else
  body`).
  **Part 51 (core/fix_pipeline — fully swept):** create_pipeline_request (16-field),
  patch_pipeline_request (18-field `_PATCHABLE` allow-list via model_fields_set + user_actions/status/
  note), patch_pipeline_request_action (status), upsert_token_rate (float-validated rate fields stay
  `Any`, output_share default 0.20).
  **Part 52 (core/router — failures cluster + config setters):** record_failure, update_failure,
  put_failures_config, upsert_failure_kind_doc (allow-list over `_KIND_DOC_FIELDS`), failures_bulk_review,
  set_portal_report, set_employee_widget_overrides. DEFERRED in core/router: create_fix_request (threads
  `body` into `_new_fix_request_row`), support_docs_upsert/import (thread `body` into `_clean_doc`).
  **~24 raw core/router handlers remain — all auth/tenant-credential** (signup, create_login,
  assign_role, bulk_assign, connect_tenant, admin_set_password, reset_password, set/verify_phone,
  put_tenant_settings, put_security_settings, create/update_role, resend_invite, reveal_code,
  bulk_provision, delete/deactivate/purge_user, disable_and_switch, reinstate_login,
  reset_tenant_admin_password). These are the most security-sensitive endpoints and many thread `body`
  into auth helpers (`assign_role`→`_normalize_grant_write`, `create_login`→core auth) — read each
  span first; expect a mix of convert + defer. Then storeops/router misc (7, mostly config
  setters that thread body — likely defer). Next: run
  `grep -rn 'body: dict' app/modules` for remaining modules (pos still skipped — incomplete/no data).
  **Rules:** skip endpoints that thread the raw
  `body` dict into shared helpers (unless the callee is also being typed — then share/inherit a model),
  use `body` itself as a freeform map (`else body`), spread `{**body}`, or loudly reject unknown keys;
  preserve None-vs-empty + downstream `.get()`; type handler-validated fields (`float(amount)` → 400)
  as `Any` so Pydantic doesn't pre-empt with a 422; for PATCH/presence use `model_fields_set`; a
  leading-underscore body key needs `Field(alias="_x")`.
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
