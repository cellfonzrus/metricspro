# MetricsPro — System Security Controls Specification

> **Scope.** Whole system, not just the CRM: FastAPI backend (Railway), Supabase
> Postgres, Next.js frontend (Vercel), the ingest/email pipelines, and the
> multi-tenant control plane. This document supersedes the CRM-only draft and
> covers every domain: identity & access, cryptography & data privacy,
> auditing/telemetry/DLP, API & integration, and governance/resilience/compliance.
>
> **Status legend.** ✅ Implemented · 🟡 Partial / config-gated · ❌ Missing
>
> **How to read this.** Each domain has a short posture summary, then a control
> matrix (control · status · evidence · gap · priority). Priorities: **P0** =
> close now (exploitable or compliance-blocking), **P1** = next, **P2** =
> hardening. The phased roadmap at the end sequences the P0/P1 work.

Last compiled: 2026-08-16. Derived from four independent domain audits of the
codebase at branch `claude/employee-commission-structure-3g5hva`.

---

## 0. Executive summary

MetricsPro has a **stronger-than-typical data-at-rest and tenant-isolation
foundation** for an early-stage SaaS, and a **weaker-than-typical session,
rate-limiting, and log-integrity posture**. The biggest real risks today are:

1. **No session controls.** No idle timeout, no absolute session lifetime, no
   concurrent-session limit, no server-side revocation. A leaked/again-used
   token is valid until Supabase's own JWT expiry.
2. **No inbound rate limiting.** Nothing throttles login attempts, exports, or
   scraping. This is exactly the gap that let the "strolling" incident go
   un-throttled while the system was ungated.
3. **Mutable, unbounded audit logs.** Only the impersonation log is
   trigger-enforced WORM. The new `core.access_log` and other audit tables have
   no retention, no prune, and no tamper-evidence.
4. **Exports are 100% client-side, un-audited, un-watermarked, unlimited.**
   A user who can see a table can export the whole thing and nothing records it.
5. **Fail-open defaults.** Field encryption returns plaintext when no key is set;
   server-side RBAC scope resolution falls back to `"all"`; tenant enforcement is
   gated behind an env flag. Each is individually defensible but collectively they
   mean "misconfigured" == "wide open" rather than "closed".

None of the four audits found a SQL-injection vector, a broken tenant filter in
the enforced path, or plaintext storage of SSN/bank fields — those are genuine
strengths and are recorded as ✅ below.

---

## 1. Identity & Access Management (IAM)

**Posture.** Authentication is delegated to Supabase Auth (JWT). Authorization is
a custom RBAC layer keyed on org membership + role, enforced in
`TenantScopeMiddleware` and per-router guards. Tenant isolation is correct **when
`MULTI_TENANT_ENFORCE` is on**; with it off, cross-org reads are possible. There
is a standing super-admin identity that bypasses both 2FA and tenant scoping.

| Control | Status | Evidence | Gap | Priority |
|---|---|---|---|---|
| Authentication (JWT via Supabase) | ✅ | `tenant_middleware.py` decodes/validates bearer token | — | — |
| Role-based authorization | ✅ | `_require_super_admin`, `_role_scope`, per-router guards | — | — |
| Tenant isolation (data scoping) | 🟡 | Enforced only when `MULTI_TENANT_ENFORCE=1`; off by default in some envs | Make enforce the default; treat "off" as a break-glass, logged state | **P0** |
| RBAC fails **closed** | ❌ | `_role_scope` → `"all"` fallback (`storeops/router.py:4958-4965`) | Unknown/missing role must resolve to **no** scope, not all | **P0** |
| Session idle timeout | ❌ | none | Add idle timeout (e.g. 30 min) enforced server-side | **P0** |
| Session absolute lifetime | ❌ | none | Cap absolute session age (e.g. 12h) regardless of activity | **P0** |
| Concurrent-session limit / device list | ❌ | none | Track sessions; allow revoke-all | **P1** |
| Server-side token revocation | ❌ | Relies on Supabase JWT expiry only | Maintain a revocation list / short-lived tokens + refresh | **P1** |
| Login lockout / brute-force protection | ❌ | none | Lock after N failed attempts; back-off | **P0** |
| 2FA / TOTP | ❌ | none (not even for super-admin) | Require TOTP for admin + impersonation | **P1** |
| SSO (SAML/OIDC) | ❌ | none | Enterprise readiness; defer | P2 |
| Standing super-admin bypass | 🟡 | Super-admin skips 2FA + tenant scope | Time-box + require step-up auth + log every use | **P0** |
| Impersonation controls | ✅ | Audit-before-grant, time-boxed, WORM impersonation log | — | — |

---

## 2. Cryptography & Data Privacy

**Posture.** Field-level encryption exists for the most sensitive fields (SSN,
bank) using Fernet (AES-128-CBC + HMAC). TLS is terminated by the platforms.
Secrets live in platform env vars. The two material issues: field encryption
**fails open** (no key ⇒ plaintext, silently), and the encryption key + other
secrets are **not documented in `.env.example`**, so a fresh/misconfigured deploy
can run with encryption effectively disabled and no one notices.

| Control | Status | Evidence | Gap | Priority |
|---|---|---|---|---|
| TLS in transit | ✅ | Railway/Vercel/Supabase terminate TLS | — | — |
| Field-level encryption (SSN, bank) | 🟡 | `crypto.py` Fernet `enc:v1:` envelope | Works, but see fail-open below | — |
| Encryption **fails closed** | ❌ | `crypto.py:70-73` returns plaintext when no key | In prod, refuse to store sensitive fields without a key (raise, don't pass through) | **P0** |
| Key documented / deploy-checked | ❌ | `FIELD_ENCRYPTION_KEY` absent from `backend/.env.example` | Add to env.example + startup assertion in prod | **P0** |
| Key rotation support | 🟡 | `_fernets()` supports multiple keys (rotation-capable) | Document + test rotation runbook | P1 |
| No plaintext SSN/bank at rest | ✅ | Audit found no plaintext writes of these fields | — | — |
| Customer PII masking (phone/email) | ❌ | Rendered in full in CRM/exports | Mask by default; reveal is an audited action | **P1** |
| DSAR / right-to-erasure | ❌ | none | Build subject-access + delete workflow | **P1** |
| Biometric data retention | ✅ | `docs/BIOMETRIC_RETENTION_POLICY.md` + pipeline | — | — |
| Secrets in platform vault, not repo | ✅ | env vars; no secrets committed | — | — |

---

## 3. Auditing, Telemetry & DLP

**Posture.** Coverage improved materially with the new `core.access_log`
(mig 856): every request now records actor, path, status, IP, and (best-effort)
GPS. But audit **integrity and retention** are the weak point — only the
impersonation log is WORM; everything else is a normal, mutable table with no
prune and no tamper-evidence. DLP is essentially absent: exports are unlimited,
un-watermarked, and unaudited.

| Control | Status | Evidence | Gap | Priority |
|---|---|---|---|---|
| Request-level access log | ✅ | `core/access_log.py` + mig 856 (actor/path/status/IP/GPS) | — | — |
| Impersonation log (WORM) | ✅ | trigger-enforced append-only | — | — |
| Access-log viewer (super-admin) | ✅ | `GET /core/access-log`, `admin/access-log` page | — | — |
| Audit-log retention / prune | ❌ | no retention on `access_log` or others | Add retention (e.g. 180–365d) + scheduled prune | **P0** |
| Audit-log tamper-evidence | ❌ | tables are mutable (except impersonation) | WORM triggers or hash-chain on security-relevant logs | **P1** |
| Export auditing | ❌ | exports are client-side; server never sees them | Route exports server-side (or log intent) — see §4 | **P0** |
| Export watermarking | ❌ | none | Stamp exports with user + timestamp | **P1** |
| Export volume limits | ❌ | unlimited rows client-side | Cap rows / rate-limit export endpoints | **P1** |
| Anomaly / scraper detection | 🟡 | access-log grouping surfaces high request/path counts manually | Alert on threshold (requests/min, distinct paths) | **P1** |
| Centralized log shipping / SIEM | ❌ | logs only in Postgres | Ship to external store for durability | P2 |
| GPS capture on requests | 🟡 | best-effort via browser geolocation header | Document limitation (one prompt/session; IP always captured) | — |

---

## 4. API & Integration Security

**Posture.** No SQL injection was found (PostgREST parameterization + dict-arg
RPC). A body-size cap and security headers/HSTS are in place. The two structural
gaps: **no inbound rate limiting anywhere**, and **exports run entirely in the
browser** (`XLSX.writeFile`, `jsPDF doc.save`) so the server has no chokepoint to
audit, throttle, or authorize them. The shared `NOTIFY_RUN_SECRET` is long-lived,
reused across ~30 endpoints, and compared non-constant-time.

| Control | Status | Evidence | Gap | Priority |
|---|---|---|---|---|
| No SQL injection | ✅ | PostgREST `.schema().table()` params; dict-arg `.rpc()` | — | — |
| Request body-size cap | ✅ | middleware caps payload | — | — |
| Security headers + HSTS | ✅ | set at app layer | Add CSP (see below) | — |
| Content-Security-Policy | ❌ | not set | Add CSP + frontend security headers | **P1** |
| Inbound rate limiting | ❌ | none (login, exports, all endpoints) | Add per-IP + per-actor rate limits | **P0** |
| Server-side export chokepoint | ❌ | `export.tsx` `XLSX.writeFile:84`, `jsPDF doc.save:156` — 100% client-side | Move exports server-side to enable audit/limit/authorize | **P0** |
| Input validation (typed schemas) | ❌ | 433 raw `dict` request bodies, zero Pydantic models | Introduce Pydantic models on write endpoints incrementally | **P1** |
| Internal secret for run/notify | 🟡 | `NOTIFY_RUN_SECRET` static, reused ~30 endpoints | Rotate; per-purpose secrets; **constant-time compare** | **P1** |
| Constant-time secret compare | ❌ | plain `==` on shared secret | `hmac.compare_digest` | **P1** |
| Webhook/inbound auth (email ingest) | 🟡 | secret-gated | Fold into secret-rotation + constant-time work | P1 |
| CORS scoping | 🟡 | configured | Verify allowlist is tight in prod | P2 |

---

## 5. Governance, Resilience & Compliance

**Posture.** Product-level governance (audit trails for business actions, role
approvals) is reasonable, but **engineering governance is thin**: no CI security
gates, no SAST/DAST, no dependency scanning, and no documented RPO/RTO or DR
runbook. Supabase provides backups; there is no tested restore.

| Control | Status | Evidence | Gap | Priority |
|---|---|---|---|---|
| Business-action audit trails | ✅ | per-module audit tables | — | — |
| CI security gates (SAST) | ❌ | none | Add SAST (e.g. semgrep/bandit) to CI | **P1** |
| Dependency / SCA scanning | ❌ | none | Add dependency audit to CI | **P1** |
| DAST / dynamic scanning | ❌ | none | Add post-deploy scan | P2 |
| Secret scanning in CI | ❌ | none | Enable secret scanning | **P1** |
| Documented RPO / RTO | ❌ | none | Define + document targets | **P1** |
| Tested backup restore / DR runbook | 🟡 | Supabase backups exist; restore untested | Write + test restore runbook | **P1** |
| Incident response runbook | ❌ | none | Write IR + break-glass procedures | **P1** |
| Change management / migration review | 🟡 | numbered migrations, PR review | Formalize security review on schema/auth changes | P2 |
| Compliance mapping (SOC2/GDPR/CCPA) | ❌ | none | Map controls to a framework when pursuing certification | P2 |

---

## 6. Phased roadmap

Sequenced by risk-reduction-per-effort. Each phase is independently shippable.

### Phase 1 — Close the open doors (P0)
1. **Session controls** — idle timeout + absolute lifetime, enforced server-side.
   🟡 **Built (gated OFF).** `app/core/session_guard.py` + `core.session_activity`
   (mig 858), keyed on the JWT `session_id`. Enable with `SESSION_ENFORCE=1`
   (`SESSION_IDLE_MINUTES`, `SESSION_ABSOLUTE_HOURS`).
2. **Inbound rate limiting** — per-IP and per-actor, applied first to auth +
   export + write endpoints. Directly addresses the "strolling/scraping" gap.
   ✅ **Built (ON).** `app/core/rate_limit.py` — per-IP fixed window, strict on
   auth paths, generous elsewhere. `RATE_LIMIT_ENFORCE=0` to disable.
3. **Audit-log retention + prune** — retention window + scheduled prune job on
   `core.access_log` and peer audit tables. ✅ **Built.** `core.prune_audit_logs`
   (mig 857) + daily pg_cron + `POST /core/audit/prune/run-due`. Impersonation
   log / crm_lookup_audit deliberately untouched.
4. **Fail-closed hardening** — (a) field encryption raises in prod when no key;
   (b) `_role_scope` unknown-role → no scope; (c) `MULTI_TENANT_ENFORCE` on by
   default, "off" is a logged break-glass state.
   🟡 **Built.** (a) `crypto.py` raises `EncryptionKeyMissing` in prod when
   `FIELD_ENCRYPTION_STRICT=1` and no key (default OFF — enable once key set).
   (b) ✅ `_role_scope` unresolved-role → `self`, break-glass
   `RBAC_SCOPE_FAILCLOSED=0`. (c) Default **not** flipped in code (outage risk
   without the isolation test) — startup now flags the off state loudly; the flip
   stays an operator go-live (daily item).
5. **Secrets documentation + startup assertion** — add `FIELD_ENCRYPTION_KEY`,
   `NOTIFY_RUN_SECRET`, `MULTI_TENANT_ENFORCE` to `.env.example`; assert presence
   on prod boot. ✅ **Built.** `.env.example` documents them + every switch;
   `security_posture.check_and_log()` warns/records at boot; `STARTUP_STRICT=1`
   fails the boot on prod findings.
6. **Login lockout** — back-off / lock after N failed attempts.
   🟡 **Built (ledger + soft lockout).** `core.login_attempt` (mig 859) +
   `login_guard.py` + `/core/auth/login-precheck|login-record`, enforced by the
   login page. **Note:** sign-in goes browser → Supabase directly, so this is
   defense-in-depth + visibility; the *authoritative* lockout is Supabase Auth's
   own rate limits (operator config — daily item).

### Phase 2 — Data protection & DLP (P1)
7. **Export governance** — audit, watermark, volume-cap. ✅ **Built.** Every
   user-initiated export flows through `src/lib/export.tsx` → `POST
   /core/export-event`: recorded in `core.export_event` (mig 862, who/what/rows/
   format), stamped with a server-derived **watermark** (Excel trailing row, PDF
   page footer, Print footer), and refused over `EXPORT_MAX_ROWS` (super-admins
   exempt). `GET /core/export-event` lets a super-admin review exports. Generation
   stays client-side (the data is already gated per-API); this adds the DLP layer
   on top rather than a full server-render rewrite.
8. **Customer PII masking** — mask phone/email by default; reveal is audited.
   ✅ **Built (Customer 360).** `mask_pii()` masks phone/email in every 360 section
   by default; `?reveal=true` returns them unmasked and stamps `pii_revealed` on
   the `crm_lookup_audit` row (mig 861). Frontend shows a masked view with a
   "Reveal contact info" action. _(Other CRM surfaces — leads list, agencies — can
   follow the same pattern.)_
9. **Constant-time secret compare + per-purpose secrets + rotation** for
   `NOTIFY_RUN_SECRET` and inbound webhook auth. ✅ **Built.** Shared
   `run_secret.verify_notify_secret()` — `hmac.compare_digest` across all ~24
   `run-due`/cron sites, plus rotation via `NOTIFY_RUN_SECRET_NEXT`. Fixed a
   fail-open site in `recovery/router.py` (unset secret used to allow the sweep).
10. **2FA/TOTP for admin + impersonation**; time-box the standing super-admin and
    require step-up. 🟡 **Built (gated).** `ADMIN_2FA_ENFORCE` makes super-admins
    present a valid 2FA marker (middleware, any-org check) — the marker's 12h/30d
    expiry **time-boxes** their standing access. Starting an impersonation session
    requires the actor's 2FA. `/me` reports `required` for super-admins so the
    existing prompt fires. Default OFF (enroll super-admins first); startup warns
    while off. _(Reuses the existing OTP/marker infra — no new 2FA mechanism.)_
11. **CSP + frontend security headers.** ✅ **Built.** `next.config.ts` sets
    nosniff / frame-DENY / Referrer-Policy / Permissions-Policy (geolocation
    allowed for self) / HSTS on every route, and a **Report-Only** CSP (safe
    rollout — promote to enforcing after the console is clean).

### Phase 3 — Assurance & resilience (P1/P2)
12. **CI security gates** — SAST, dependency/SCA, secret scanning. ✅ **Built
    (report-only).** `.github/workflows/security.yml` — bandit + semgrep (SAST),
    pip-audit + npm audit (SCA), trufflehog (secrets). Non-blocking to start;
    promote to required after the baseline is clean.
13. **Audit-log tamper-evidence** — WORM triggers / hash-chain on security logs.
    ✅ **Built.** `core.worm_guard()` (mig 863) makes access_log / login_attempt /
    export_event / crm_lookup_audit append-only: UPDATE always blocked, DELETE
    allowed only from inside the retention job (transaction-local flag). Blocks
    tampering even with the service role.
14. **RPO/RTO + tested restore runbook + incident-response runbook.** ✅ **Built.**
    `docs/INCIDENT_RESPONSE_PLAN.md` + `docs/BACKUP_DR_PLAN.md` (RPO ≤5 min w/
    PITR, RTO ≤2 h, restore procedure + quarterly drill). _Remaining: run the
    first drill; confirm PITR is on._
15. **Typed request schemas (Pydantic)** rolled out across write endpoints.
    🟡 **In progress (incremental).** `app/core/schemas.py` — `StrictModel`
    (extra=forbid → rejects unknown fields) / `LaxModel` (ignores unknown, for
    legacy). **Part 1:** containment + auth endpoints authored this cycle
    (ip-block ×2, sessions/revoke, export-event, login-precheck/record → strict;
    forgot/reset-password → lax). **Part 2:** auth self-service (me/set-password,
    me/2fa/start·verify·settings → lax). **Part 3:** admin/RBAC writes
    (auth-config, tenants create/patch, super-admins create → lax; PATCH keeps
    "only-sent-keys" semantics via `model_fields_set`). **Part 4:** CRM writes
    (tasks create, agency-response, dedupe-check → lax). **Part 5:** asset
    borrowings (create/patch/payment → lax; `amount` kept as `Any` so the handler's
    own 400 validation stands, not a Pydantic 422). **Part 6:** account company
    mgmt (companies create/patch, companies/assign → lax). **Part 7a:** storeops
    org-structure CRUD (levels/units create·update, unit managers, store/employee
    unit-assign → lax; null-to-unassign + PUT presence preserved). **Part 7b:**
    storeops approvals — shift-extension / timeclock-permission / budget-override
    decisions, payroll-chargeback decision, request-extra-time → lax (note kept
    null-when-empty). **Part 7c:** storeops config/requests (timeoff-conflict-mode,
    hours-budget set/clear, budget-override request → lax). **Part 7d:** storeops
    payroll writes (payroll-settings, manual-hours add, salary-advance record → lax;
    amount/hours typed Any so handler 400s stand). **Part 7e:** storeops
    payroll-config (payroll-tax-config, expense-item create/patch → lax;
    presence-loops via model_fields_set). **Part 7f:** storeops action-plans
    (submit/push-back/approve/dm-confirm) + google-reviews resolve-place → lax
    (optional bodies via Optional[Model] + `body or Model()`). **Part 7g:** storeops
    google-reviews config/sweep-config/run-now → lax (presence via model_fields_set;
    write-only api_key). **Part 7h:** storeops schedule-templates
    (save-week/apply-templates), shift-extension request, face-retention
    request-deletion (optional body), google-reviews store-config PUT → lax
    (presence via model_fields_set; place_id/target handler-validated typed Any).
    **Part 7i:** storeops bulk + timeclock writes (bulk-create employees/stores,
    employee merge, bulk-payscale, clock-in / override / clock-out,
    face-retention run, face descriptor save → lax; selfie/gps/descriptor/entry_id
    handler-validated typed Any). **storeops now fully swept** — the 7 remaining
    `dict` handlers thread `body` into shared config helpers (deferred by rule).
    **Part 8:** helpdesk support-console writes (case reply/note/assign/status,
    canned-response upsert, SLA-policy put, failures bulk-review, fix-request
    status → lax; ids/hours/mark_reviewed handler-validated typed Any). Config
    CRUD + create-fix-request thread `body` into `_cfg_*`/`new_fix_row` helpers
    (deferred by rule). **Part 9:** helpdesk ticket core (create/update/comment,
    team-member add, settings put, AI-assist, escalate → lax; presence loops via
    model_fields_set, passthrough ids typed Any to keep None-when-absent). Helpdesk
    now fully swept bar the `_clean`/`new_fix_row` threaders. **Part 10:** commcalc
    config CRUD (carrier create/update, connector create/update, report-definition
    create/update, nav-label + nav-layout → lax; presence loops via
    model_fields_set, handler-validated int/url fields typed Any). commcalc is the
    large module (~124 dict bodies) — being taken in careful money-sensitive
    batches. **Part 11:** commcalc mapping/registry upserts (column-mapping,
    commission-field create, target-field create, commission-category-map → lax;
    presence via model_fields_set, int/enum-validated fields typed Any).
    **Part 12:** commcalc MA class wiring (mode flip, class→leg map) + MA
    product-class (upsert one, confirm proposals) → lax; normalize/enum-validated
    fields typed Any. **Part 13:** commcalc accessory-definition (upsert mapping,
    confirm proposals, propose-from-data optional body) → lax; the field-rule PUT
    threads `body` into `normalize_field_rule` (deferred). **Part 14:** commcalc
    item/config (upload-duty config, item-category put, item-mapping upsert + bulk,
    device-model add) → lax; presence via model_fields_set, int-coerced fields
    typed Any. **Part 15:** commcalc registry/schedule (custom-import-type create,
    connector-schedule patch, accessory-flags push) → lax; presence-and-not-None
    loop via model_fields_set. **Part 16:** commcalc money-settings (commission-
    settings put, activation-matcher put, plan-line-matcher put) → lax; all-Any
    presence fields, handler enum/float validation preserved. save-plan-installment
    threads `body` into `_write_installment_schedule` (deferred). **Part 17:**
    commcalc category-rule + expected-commission money-writes (save-category-rule,
    promote, revoke) → lax; the qualification/payout/expected-config PUTs use `body`
    itself as a freeform category map (the `else body` fallback) and are deferred as
    freeform-map bodies. **Part 18:** commcalc MRC/payout config (mrc-mapping
    confirm + bulk-classify, payout-schedule save, product-mrc save) → lax; int/
    float-validated + presence fields typed Any, per-line loops kept local.
    update_plan_installment threads `body` into `_write_installment_schedule`
    (deferred). **Part 19:** commcalc distributor/template (carrier-template clone,
    distributor save, distributor-payment add) → lax; int/float-coerced fields
    typed Any. **Part 20:** commcalc save-commission-plan (plan header + rules +
    tiers + assignments delete-then-insert) → lax; tier presence via
    model_fields_set, child-list dicts read locally. setup-fee/pay-gate configs
    (freeform `else body` map) + payout-exclusion (`{**body}` spread) deferred.
    **Part 21:** commcalc coverage/assign/store (coverage-excluded-sellers put,
    bulk-assign commission plan, store update, store-alias add) → lax; the
    `body.items()` allow-list filter becomes a model_fields_set loop.
    **Part 22:** commcalc ingest-guard (config put, queue-item decide) + reporting
    config (gp-category-map, commission-leg label + config) → lax; presence loops
    (`'k' in body`) via model_fields_set, int/regex-validated fields typed Any.
    **Part 23:** commcalc chargeback-update + classification config (accessory-config
    — 14 presence-gated list/map fields via model_fields_set — and catalog-override)
    → lax. **Part 24:** commcalc targets + KPI (save-target, roll-forward optional
    body, carrier-kpi-metric save, kpi-actuals upsert, paramount MTD import) → lax;
    safe_float/int-coerced fields typed Any. **Part 25:** commcalc exec-metric config,
    productivity-item config (mixed is-not-None + model_fields_set presence loops),
    rep-aliases merge → lax. **Part 26:** commcalc store-expenses group (put/matrix
    replace, bulk-apply cells, system-line receiver, apply-config tokens, apply-to-
    months) → lax; list-of-cell payloads typed Any, read locally. **Part 27:**
    commcalc FTP + email sweep config/test (ftp config+test, email config+test) →
    lax; write-only password field stays Any and is only persisted when non-empty
    (unchanged), presence-merge loops via model_fields_set. **Part 28:** commcalc
    sales-derive window, POS profile, data-source login (SSRF-guarded portal/proxy
    URLs, password-keep-on-blank), proxy test → lax; `_SOURCE_FIELDS` allow-list
    filter becomes a model_fields_set loop, defaults preserved via model_fields_set.
    **Part 29:** commcalc report-pull-map save, manual-upload save/reset mapping,
    data-source login verify + live-login submit/click/input → lax; `_RPM_FIELDS`
    allow-list via model_fields_set, float-validated click coords typed Any.
    **Part 30:** commcalc ma-overview recon (tile-config put — 17-field TILE_FIELDS
    allow-list via model_fields_set — and carrier rate-plan put) → lax.
    custom-report-defs save threads `body` into `validate_definition` (deferred).
    **Part 31:** commcalc agency set-consent, set-carriers, generate-invoice
    (optional body) → lax; the six agency `upsert_*` / add-transfer handlers thread
    `body` into `_agency.*` helpers (deferred). **Part 32:** commcalc portal sweep
    configs (vip / dlar / epay sweep put-config) → lax; presence-and-not-None loops
    via model_fields_set, write-only portal_pass + SSRF portal_url guard preserved.
    **Part 33:** commcalc financing vendor-carrier assign + store-target save → lax
    (financing vendor/detection-rule saves thread `body` into `_finreg.normalize_*`
    — deferred). **Part 34:** commcalc management-incentive (mi save-plan +
    header/children, compute, payout-decision, resolve) → lax; the `_actor` internal
    key uses a Pydantic alias (`Field(alias="_actor")`) since leading-underscore
    field names are reserved. **Part 35:** commcalc atu-config set, flag-rules put,
    carrier-category-rule upsert → lax (presence loops via model_fields_set /
    is-not-None; float-clamp preserved). payout-record + payout-accrual-config
    thread `body` into `payout_accrual.*` (deferred). **Part 36:** commcalc whatif
    source-config, pay-simulator (read-only, optional body), MA-class-wiring
    rule-proposals apply, commission-rule-impact (read-only blast radius) → lax.
    **Part 37:** commcalc seed endpoints (ma-product-class seed-proposals,
    accessory-definition seed-classes; optional bodies) → lax. **commcalc is now
    fully swept** — all 20 remaining `body: dict` handlers are legitimately deferred
    (body-threaders into module helpers, freeform `else body` category maps, or
    `{**body}` spreads). ~246 `dict` bodies remain across other modules (hr next;
    body-threading endpoints get a dedicated pass; POS skipped — incomplete/no
    data). **Part 38:** hr onboarding config CRUD (category save/update, task
    save/update — TASK_FIELDS allow-list via model_fields_set) → lax; hr
    create/update-employee are EMP_FIELDS-driven freeform records (deferred).
    **Part 39:** hr intake-field save/update (INTAKE_FIELD_COLS allow-list),
    accounting-forward settings, onboarding-attention config → lax. **Part 40:**
    hr onboarding-approve + onboarding-advance (workflow transitions, compliance
    override) → lax. **Part 41 (hr fully swept):** onboarding provision (docs +
    separately-audited compliance override gates), set-profile, update-status,
    reattach-orphan, mint-token, return-task, send-documents, forward-accounting,
    invite one/bulk, reconcile, plus the token-gated portal handlers
    (me/public state, view, dd-disclaimer, sign — each keeps its `value` identity
    gate as a typed field and `form_data` as an opaque `Any`) → lax. **hr is now
    fully swept** — 4 `body: dict` handlers remain deferred: create/update-employee
    (EMP_FIELDS freeform records) and me/public onboarding-intake (freeform intake
    map into `_apply_intake`). **Part 42 (crm leads/tasks fully swept):** create_lead
    (26-field), update_lead, move_stage, dispose_lead, assign_lead, bulk_assign,
    bulk_dispose, convert_lead, intake_lead, add_activity, complete_task, snooze_task
    → lax. Sibling body-threading solved with shared/inherited models
    (`BulkDisposeIn(DisposeLeadIn)` / `BulkAssignIn(AssignLeadIn)` pass the same typed
    body straight to the single-lead handler; `IntakeLeadIn(CreateLeadIn)` forwards
    to create_lead). **Also fixed a latent bug** — `agency_response` was typed but
    still called `body.get("reason")` (would AttributeError on every agency decline);
    added the field + attribute access. crm config CRUD deferred (loud unknown-key
    rejection — a lax model drops silently, a strict model changes the 400→422
    contract). **Part 43 (referral fully swept):** create_referral, redeem_submit
    (public token), and the staff lifecycle — send_qr, log_sale, activate, submit,
    approve (float-validated commission stays `Any`), pay, reject, void, flag →
    lax. Optional bodies typed `Model = None` + `body or Model()`; shared
    `ReferralNoteIn`/`ReferralReasonIn` reused. referral put_config deferred (same
    loud unknown-key rejection). ~195 `dict` bodies remain across other modules
    (core, closing, notify, billing, storeops helpers, storevisit, …; POS skipped —
    incomplete/no data).
16. **DSAR / erasure workflow.** 🟡 **DSAR export built; erasure deferred.**
    `GET /crm/customer-360/dsar` (admin-only, audited) packages the full unmasked
    record for a data-subject request, reusing Customer-360; the lookup page shows
    a "DSAR export" download to admins. **Erasure/anonymization deliberately
    deferred** (no SSN yet, 1st month, no retention policy; collides with WORM
    audit) — approach documented for when it's needed.

### Phase 4 — Enterprise & compliance (P2)
17. SSO (SAML/OIDC), SIEM shipping, DAST, compliance-framework mapping.

---

## 7. Confirmed strengths (do not regress)

- No SQL injection anywhere in the audited surface.
- SSN/bank fields never stored in plaintext; Fernet envelope with rotation-capable
  multi-key support.
- Impersonation is audit-before-grant, time-boxed, and WORM-logged.
- Request body-size cap and security headers/HSTS in place.
- Tenant isolation is correct in the enforced path (the gap is the default, not
  the logic).
- Biometric data has a written retention policy and pipeline.
- Request-level access logging (actor/path/status/IP/GPS) is now live.
