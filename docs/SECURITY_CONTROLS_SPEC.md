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
    hours-budget set/clear, budget-override request → lax). ~393 `dict` bodies
    remain (body-threading endpoints get a dedicated pass; POS skipped —
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
