# Security — Open Questions for the Daily

Operational decisions raised by the Security Controls Spec Phase 1 work (PR #30). These are choices
that need an operator call, not code — parked here to be walked through in the daily review and checked
off as they're settled. See `docs/SECURITY_CONTROLS_SPEC.md` for the full plan.

Status legend: ⬜ open · ⏳ in discussion · ✅ decided (record the decision + date inline)

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

---

_Add new operational questions here as later phases land, so the daily has one running list._
