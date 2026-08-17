# Security Workstream — Handoff

Living handoff for the security hardening effort. Any session can resume from here. Kept current as
work proceeds. Companion docs: `SECURITY_CONTROLS_SPEC.md` (the plan + control matrix),
`SECURITY_DAILY_QUESTIONS.md` (operator go-lives), `INCIDENT_RESPONSE_PLAN.md`, `BACKUP_DR_PLAN.md`.

**Last updated:** 2026-08-17 (item 15 pt5) · **Branch:** `claude/employee-commission-structure-3g5hva` · **PR:** #30 (draft, CI green)

---

## 1. Status at a glance

| Phase | State |
|---|---|
| **Phase 1 (P0)** — sessions, rate-limit, retention, fail-closed, startup posture, login ledger | ✅ complete |
| **Phase 2 (P1)** — export governance, PII masking, constant-time secrets, 2FA admin, CSP | ✅ complete |
| **Phase 3 (P1/P2)** — CI gates (12), WORM (13), RPO/RTO+IRP (14), DSAR export (16) | ✅ done; **erasure deferred** |
| **Item 15 — Pydantic rollout** | 🟡 in progress, incremental (parts 1–5 = 22 endpoints; ~411 remain) |
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

- **Item 15 (Pydantic), continue incrementally.** Done: parts 1–5 (containment/export/auth-ledger
  strict; auth self-service, admin/RBAC, CRM writes, asset borrowings lax). Next candidates by size:
  commcalc (125), storeops (50), pos (43), hr (32), helpdesk (29). **Rules:** skip endpoints that
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
