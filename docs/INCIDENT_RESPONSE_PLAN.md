# MetricsPro — Incident Response Plan (IRP)

Adapted from the External Threat Defense Plan to **our actual stack**: Next.js on Vercel, FastAPI on
Railway, Supabase Postgres. It maps each response phase to the concrete levers this codebase gives you
— kill switches, containment endpoints, the access log, secret rotation. Pair with
`docs/SECURITY_CONTROLS_SPEC.md` (what's built) and `docs/SECURITY_DAILY_QUESTIONS.md` (operator go-lives).

> **Reality check on the edge.** Several plan controls live at a layer we don't own in code — a managed
> WAF/CDN (Cloudflare/AWS WAF), L3/L4 DDoS, geofencing, ZTNA/mTLS, CAPTCHA, EASM scanning. Those are
> **operator/infra** actions (see §6). This runbook covers what the application can do itself.

---

## 1. Severity matrix

| Severity | Definition | Response | Notify |
|---|---|---|---|
| **SEV-1** | Confirmed data exfiltration, active breach, or system compromise | Immediate; contain within 1h | Owner + legal; start §5 disclosure clock |
| **SEV-2** | Sustained DDoS, credential-stuffing outbreak, unpatched critical CVE | < 30 min; contain within 4h | Owner + admin |
| **SEV-3** | Isolated brute-force from botnets, single contained ATO, minor alerts | < 2h; contain within 24h | Admin on call |

---

## 2. Detection & analysis

Where the signal comes from in our stack:
- **Access log** (`/admin/access-log`) — group by IP or user to spot a scraper (high request count /
  many distinct paths), filter anonymous-only, see IP + GPS. This is the primary triage surface.
- **Login ledger** (`core.login_attempt`) + soft-lockout alerts in `core.failure_log`
  (`category = security_auth`) — credential-stuffing signal.
- **Failure log** (`core.failure_log`) — `security_posture` (missing secrets), `system_error`, sweep
  failures.
- **Startup posture line** — the app logs its enforcement posture and warns on missing secrets at boot.

Scope it: which IPs/emails/paths, which tenant(s), what was reached (status codes in the access log),
and whether any export or sensitive-field read occurred.

---

## 3. Containment — the levers we have

All of these are application-level and take effect without a redeploy.

### Block a malicious IP (instant)
- UI: `/admin/access-log` → **Block** on the offending row. API: `POST /api/v1/core/ip-block`
  `{ip, reason, minutes?}` (super-admin). Omit `minutes` for a permanent block; unblock via
  `POST /api/v1/core/ip-block/remove`. Effective fleet-wide within ~30s (mig 860).

### Purge sessions (evict an active attacker)
- `POST /api/v1/core/sessions/revoke` `{auth_id?}` — all sessions, or one user's. **Enforced only when
  `SESSION_ENFORCE=1`** — if it's off, flip it on in Railway as your first containment step, then revoke.

### Throttle / lock down auth
- Rate limiting is on by default (per-IP; auth paths strict, password-reset 3/hr). Tighten in Railway
  without a deploy: `RATE_LIMIT_PER_MIN`, `RATE_LIMIT_AUTH_PER_MIN`, `RATE_LIMIT_RESET_PER_HOUR`.
- Login soft-lockout: `LOGIN_MAX_FAILURES` (default 5), `LOGIN_LOCK_MIN`.

### Kill switches (Railway env — no code rollback)
- `REQUIRE_AUTH=1` — reject any unauthenticated hit to a non-public route (default on).
- `MULTI_TENANT_ENFORCE=1` — enforce tenant isolation from the verified token.
- `TWOFA_ENFORCE`, `STRICT_MEMBERSHIP`, `RBAC_SCOPE_FAILCLOSED`, `FIELD_ENCRYPTION_STRICT`,
  `SESSION_ENFORCE` — see the spec. In an incident, prefer **more** enforcement.
- Break-glass **off** switches exist for each (e.g. `RATE_LIMIT_ENFORCE=0`) — use only to recover from a
  self-inflicted outage, never as a response to an attack.

### Isolate
- Roll the Railway/Vercel deployment back or take a node out of rotation if a specific instance is
  suspected. Capture logs first.

---

## 4. Eradication

1. **Patch** the exploited path; deploy the fix (branch → PR → merge, or hotfix).
2. **Rotate secrets** — the ones this app holds:
   - `NOTIFY_RUN_SECRET`: zero-downtime via `NOTIFY_RUN_SECRET_NEXT` (set new, cut over schedulers,
     retire old).
   - `SUPABASE_SERVICE_KEY`, `SUPABASE_KEY`: rotate in Supabase, update Railway.
   - `FIELD_ENCRYPTION_KEY`: rotate via `FIELD_ENCRYPTION_KEYS` (new,old) then re-encrypt; **never drop
     the old key until data is re-encrypted** or ciphertext becomes unreadable.
   - `RESEND_API_KEY`, `WHATSAPP_*`, `FIX_PIPELINE_SECRET` as applicable.
3. **Revoke sessions** again after rotation so nothing rides an old token.
4. Confirm the access log shows the source is no longer reaching protected routes.

---

## 5. Recovery & post-incident

1. Restore from Supabase backups if data integrity is in doubt (see §6 — restore is currently
   **untested**; that's a tracked gap).
2. Bring services back with enforcement dialed up; watch the access log + failure log.
3. **Disclosure:** if customer PII/financial data was accessed, notify the owner/legal immediately and
   prepare statutory notifications (GDPR 72h, CCPA) — MetricsPro stores SSN/bank (encrypted) and customer
   contact data, so a confirmed exfiltration is likely reportable.
4. **Blameless post-mortem** within 5 business days; fold structural fixes into the spec roadmap.

---

## 6. Operator / infra actions the app can't do itself

These close the remaining External Threat Defense Plan items and belong at the edge or in Supabase/CI.
Tracked as open questions in `docs/SECURITY_DAILY_QUESTIONS.md`.

- **Managed WAF/CDN** (Cloudflare/AWS WAF) in front of Vercel + Railway: OWASP managed rules in BLOCK
  mode, bot management, L3/L4 + L7 DDoS, geofencing, IP-reputation/Tor blocking.
- **CAPTCHA/Turnstile** on the login + signup + reset forms (needs a provider key).
- **Supabase Auth rate limits** — the authoritative sign-in brute-force control (sign-in bypasses our
  backend).
- **ZTNA/private admin** — restrict admin surfaces to a VPN/ZTNA broker where feasible.
- **CI security gates** — SAST/dependency/secret scanning; EASM; a ≤24h critical-CVE patch SLA.
- **Tested backup restore + RPO/RTO** — validate a Supabase restore end-to-end.

---

## 7. Quick reference — during an incident

```
Spot it:     /admin/access-log  (group by IP/user; anonymous-only)
Block IP:    POST /api/v1/core/ip-block {ip, reason, minutes?}
Purge users: SESSION_ENFORCE=1  then  POST /api/v1/core/sessions/revoke {auth_id?}
Throttle:    lower RATE_LIMIT_* envs in Railway
Rotate:      NOTIFY_RUN_SECRET_NEXT; Supabase keys; FIELD_ENCRYPTION_KEYS
Lock down:   MULTI_TENANT_ENFORCE=1, TWOFA_ENFORCE=1, FIELD_ENCRYPTION_STRICT=1
```
