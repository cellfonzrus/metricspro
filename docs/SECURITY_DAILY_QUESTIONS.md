# Security — Open Questions for the Daily

Operational decisions raised by the Security Controls Spec Phase 1 work (PR #30). These are choices
that need an operator call, not code — parked here to be walked through in the daily review and checked
off as they're settled. See `docs/SECURITY_CONTROLS_SPEC.md` for the full plan.

Status legend: ⬜ open · ⏳ in discussion · ✅ decided (record the decision + date inline)

**Migrations status:** 857 ✅ · 858 ✅ · 859 ✅ · **860 ⬜** (ip_block — IRP containment) ·
**861 ⬜** (crm_lookup_audit.pii_revealed — Customer-360 reveal marker). Both idempotent; backend
tolerates them being un-run.

---

### 1. ✅ Prune fallback if pg_cron is unavailable — RESOLVED 2026-08-16
pg_cron is enabled on the project and the daily job registered cleanly:
`prune_audit_logs_daily` → `10 4 * * *` → `SELECT core.prune_audit_logs();`. No fallback needed; the
backend `POST /api/v1/core/audit/prune/run-due` endpoint remains available as a manual trigger.
- Original decision (enable pg_cron vs. wire into notify `/run-due`): moot — pg_cron present.
- Re-check anytime: `SELECT jobname, schedule FROM cron.job WHERE jobname = 'prune_audit_logs_daily';`

### 2. ⬜ Retention windows
Defaults: `access_log` = **365 days**, `failure_log` = **180 days** (30-day floor enforced in-function).
- **Decide:** confirm these match the compliance posture. Override per-call, e.g.
  `SELECT core.prune_audit_logs(180, 90);`, or pass `access_days` / `failure_days` to the run-due
  endpoint.
- The WORM impersonation log and `crm_lookup_audit` (audit-of-record) are **never** pruned — confirm
  that's the intended policy.

### 3. ⬜ Session enforcement go-live (`SESSION_ENFORCE`)
Built and shipped **gated OFF** (mig 858 + `session_guard`). Turning it on can end active sessions, so
it's a deliberate flip.
- **Defaults when enabled:** `SESSION_IDLE_MINUTES=30`, `SESSION_ABSOLUTE_HOURS=12`.
- **Decide:** when to set `SESSION_ENFORCE=1` in Railway, and with what windows. For a retail floor
  where a device stays signed in all shift, consider **idle 4–8 h / absolute 16–24 h** instead of the
  security defaults.
- **Suggested sequence:** run with rate limiting live for a bit first, then enable session controls.

### 4. ⬜ Rate-limit ceilings (shared-IP / NAT)
Live now: **300/min** general and **20/min** auth, **per IP**. Break-glass `RATE_LIMIT_ENFORCE=0`.
- **Watch:** if several staff in one store share a single public IP (NAT), the general limit is shared
  across all of them.
- **Decide:** confirm 300/min is comfortable for the busiest store, or raise `RATE_LIMIT_PER_MIN`.

### 5. ⬜ `FIELD_ENCRYPTION_KEY` set in production — HIGHEST PRIORITY
Field encryption **fails open**: with no key configured, SSN / bank fields are stored in **plaintext**
and nothing warns.
- **Decide/verify:** confirm `FIELD_ENCRYPTION_KEY` is actually set in Railway prod. If it isn't,
  sensitive fields written since launch are plaintext at rest and need re-encryption after the key is
  set.
- Fail-**closed** hardening for this (refuse to store sensitive fields without a key in prod) is Phase 1
  item 4 — tracked in the spec, not yet built.

### 6. ⬜ Supabase Auth rate limits — the authoritative login lockout
Primary sign-in is browser → **Supabase Auth directly**, so our backend can't enforce a hard login
lockout. The in-app ledger + soft lockout (mig 859) is defense-in-depth and visibility only.
- **Decide/verify:** confirm Supabase Auth's built-in rate limits are configured in the Supabase
  dashboard (Auth → Rate Limits) — this is the control that actually throttles brute-force at the
  sign-in endpoint. Our per-IP limiter only covers our own API, not the Supabase auth host.
- Soft-lockout tuning (advisory): `LOGIN_MAX_FAILURES=8`, `LOGIN_WINDOW_MIN=15`, `LOGIN_LOCK_MIN=15`;
  `LOGIN_LOCKOUT_ENFORCE=0` disables the lockout (attempts are still recorded). Tradeoff: per-email
  lockout is DoS-able by design — kept short to limit that.

### 7. ⬜ Enable the new fail-closed switches once verified
Built this phase, defaulted conservatively so nothing breaks on deploy:
- `FIELD_ENCRYPTION_STRICT=1` — **after** confirming `FIELD_ENCRYPTION_KEY` is set (ties to item 5).
  Then a missing key refuses to store plaintext instead of failing open.
- `RBAC_SCOPE_FAILCLOSED` — already **ON** by default (unresolved role → `self`, not `all`). Break-glass
  `=0` if it ever over-restricts a real role.
- `STARTUP_STRICT=1` — optional: make the app refuse to boot in prod when a posture finding exists.
- `MULTI_TENANT_ENFORCE=1` — the deliberate tenant-isolation go-live (see item 3-adjacent). Startup now
  warns while it's off. Needs the isolation test before flipping.

### 12. ⬜ Enable admin 2FA (`ADMIN_2FA_ENFORCE`)
Built this phase (item 10), default OFF. When on, super-admins must present a valid 2FA marker on every
request (its 12h/30d expiry also time-boxes their standing access), and starting an impersonation
session requires the actor's 2FA.
- **Before enabling:** have every super-admin enroll 2FA (`/admin/security` → 2FA, or the `/me/2fa`
  flow). The enroll/verify endpoints are always reachable, but enrolling first avoids a scramble.
- **Then:** set `ADMIN_2FA_ENFORCE=1` in Railway. Startup warns while it's off. Break-glass `=0`.

### 8. ⬜ Promote CSP from Report-Only → enforcing
`next.config.ts` ships a **Content-Security-Policy-Report-Only** (Phase 2 item 11). It blocks nothing
yet — it surfaces violations so we can confirm the policy fits the app (inline styles, Next inline
scripts, Supabase + Railway origins).
- **Do:** after deploy, browse the app with devtools open and watch for `[Report Only]` CSP violations.
  When clean, rename the header `Content-Security-Policy-Report-Only` → `Content-Security-Policy` to
  enforce.
- **Watch:** `connect-src` currently allows `*.supabase.co` + `*.up.railway.app`; if the API/host
  changes, update it before enforcing or calls will be blocked.

### 9. ⬜ Optional: rotate NOTIFY_RUN_SECRET
Rotation is now zero-downtime (Phase 2 item 9): set `NOTIFY_RUN_SECRET_NEXT` to the new value, update
the schedulers/cron to send it, then move it to `NOTIFY_RUN_SECRET` and clear `_NEXT`. No forced
rotation needed — available when wanted.

### 10. ⬜ Edge / infra controls from the External Threat Defense Plan
These close the plan's remaining items but live at a layer we don't own in code (see
`docs/INCIDENT_RESPONSE_PLAN.md` §6). Operator decisions:
- **Managed WAF/CDN** (Cloudflare/AWS WAF) in front of Vercel + Railway — OWASP rules in BLOCK mode, bot
  management, L3/L4 + L7 DDoS, geofencing, Tor/IP-reputation blocking.
- **CAPTCHA/Turnstile** on login + signup + reset (needs a provider key).
- **Supabase Auth rate limits** (already item 6) — the authoritative sign-in throttle.
- **ZTNA / private admin surface**, **CI security gates** (SAST/SCA/secret scan, EASM, ≤24h CVE SLA),
  **tested backup restore + RPO/RTO**.

### 11. ⬜ Try the incident-response containment tools
Now available (mig 860) — worth a dry run so they're familiar before you need them:
- `/admin/access-log` → **Block** an IP (or the API `POST /core/ip-block`), and unblock from the
  Blocked-IPs panel.
- **Revoke all sessions** button (enforced once `SESSION_ENFORCE=1`).
- Full playbook in `docs/INCIDENT_RESPONSE_PLAN.md`.

---

_Add new operational questions here as later phases land, so the daily has one running list._
