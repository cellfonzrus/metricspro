# MetricsPro Mobile — Architecture, Security & Latency

The mobile app (`mobile/`) is a native iOS + Android client built with Expo (React Native) +
TypeScript. It consumes the **same FastAPI backend** the web app uses — no new server surface was
added — so identity, tenancy, RBAC and the business logic all remain in one place.

```
┌────────────────────┐        HTTPS (Bearer JWT)        ┌───────────────────────┐
│  Mobile app (Expo)  │  ───────────────────────────▶   │  FastAPI backend       │
│  iOS / Android      │   x-active-org, x-2fa-token      │  (Railway)             │
│                     │                                   │                       │
│  Supabase JS  ──────┼──── auth (JWT) ─────────────────▶│  Supabase (Postgres)  │
└────────────────────┘                                   └───────────────────────┘
```

## Scope of the first release

| Module | Screens | Backend |
|--------|---------|---------|
| **Time Clock** | status, clock in/out, store picker, GPS | `storeops /timeclock/*` |
| **POS** | catalog search, cart, checkout, register | `pos /*` |
| **CRM** | leads list/detail, tasks, stage moves, activity | `crm /*` |
| **Earnings** (Commissions & Targets) | commission/tier/KPI dashboard, history, accessory attainment, schedule-weighted target pace & achievement by category, conversion | `core /employee-dashboard`, `commcalc /targets/{period}/calendar`, `commcalc /coaching/{period}` |

### Earnings module notes

The Earnings module is the employee-facing view of the commission engine. It reuses the exact
self-service bundle the web portal uses (`GET /core/employee-dashboard`) and pins `employee_id` to the
**signed-in** rep from `/core/me` — a rep only ever sees their own numbers. Achievement/pace comes
from the schedule-weighted target calendar (`/targets/{period}/calendar?scope=rep`), scoped by the
rep's sales-data name and home store returned in the dashboard bundle.

> Note: `GET /core/employee-dashboard` currently takes `employee_id` as a query param without a
> server-side "is this you?" check (same contract the web portal relies on). The mobile client always
> sends the caller's own id, but hardening that endpoint to derive identity from the token (like
> `/timeclock/*` does) is a recommended backend follow-up before this data is considered
> defense-in-depth safe.

Back-office/admin modules (Commission Intelligence, Scheduling & HR, Assets, Closing, …) are shown as
**"coming soon"** on Home and are added later via the module registry (see below). This is the
"provision to add all items eventually" the app was scoped around.

## Extensibility — the module registry

`src/modules/registry.ts` declares every feature area as data. The bottom tab bar, the Home grid and
the route guard all derive from this one list, so adding a module is: add a registry entry → add a
typed API file → add screen(s). No navigation or shell rewiring. Each entry has a `visible(me)`
predicate and an `entitlementKey` reserved for the backend's `core.module_catalog` / `roles.modules`
gate, so server-side entitlements can be enforced the moment they ship without touching UI code.

## Security

The app handles payroll, sales money and customer PII, so the security posture is deliberate:

| Concern | How it's handled |
|--------|------------------|
| **Session storage** | Supabase session (access + refresh JWT) is stored in the iOS **Keychain** / Android **Keystore** via `expo-secure-store`, never in plaintext AsyncStorage. Large sessions are transparently chunked (`src/api/secure-storage.ts`). Keychain accessibility is `AFTER_FIRST_UNLOCK`. |
| **App lock** | Optional Face ID / Touch ID / passcode gate (`expo-local-authentication`) that re-locks after backgrounding past a grace period and on cold start (`src/auth/AppLockGate.tsx`). A local gate on top of the server auth — stops casual device theft / shoulder-surfing. |
| **No secrets in the bundle** | Only the **public** Supabase anon key + API URL ship in the app (via `EXPO_PUBLIC_*`). The service-role key is never present. RLS + backend token verification are the real trust boundary. |
| **Token handling** | The access token is attached per-request from SecureStore and never logged; it is never stored in the offline queue (attached fresh at replay time). Auto-refresh is driven by app foreground state. |
| **Tenant isolation** | Mirrors the web client: `x-active-org` (a hint the server re-verifies), house-org substitution / append for super-admins acting as a tenant. A normal user's org is always overridden server-side from their verified membership. |
| **2FA** | `x-2fa-token` (the signed OTP proof) is stored in SecureStore and sent on every request; inert unless the tenant requires 2FA. |
| **Dead-session handling** | A live token rejected with the middleware's exact `authentication required` string forces a clean sign-out instead of a screen of errors. |
| **Transport** | HTTPS only. iOS App Transport Security is left on (the app only talks to your own backend + Supabase). `ITSAppUsesNonExemptEncryption:false` is declared (standard HTTPS only). |
| **Permissions** | Location (clock-in verification), Camera (clock-in selfie), Biometrics — each with a purpose string, requested lazily at point of use, and non-blocking when denied. |

### Recommended follow-ups (not in this release)
- **TLS certificate pinning** to the backend/Supabase hosts (e.g. `expo-build-properties` + native
  network config) for high-value tenants.
- **Jailbreak/root detection** to disable the app lock bypass surface.
- Move the PII-reveal (`pos_view_pii`) flows behind a re-auth prompt on mobile, mirroring the web's
  fine-grained gates.

## Latency

Store networks are frequently slow or intermittent, so the app is built cache- and offline-first:

| Technique | Where |
|----------|-------|
| **Stale-while-revalidate reads** | React Query (`src/api/query.ts`): screens paint instantly from cache and refresh in the background; 30s stale window; retry only on network errors, never on 4xx/auth. |
| **Durable offline mutations** | Clock punches, sales and activity logs are enqueued to disk and replayed FIFO on reconnect (`src/offline/queue.ts`). Terminal business errors move to a reviewable "failed" list; network/auth errors keep the item for the next attempt. |
| **Atomic writes** | POS checkout is a single `pos.checkout` RPC (sale + items + payments in one transaction), which is what makes a queued replay safe — it fully lands or fully fails. |
| **Per-request timeouts** | `AbortController`-based timeout (20s) so a dead network fails fast into the offline/retry paths instead of hanging the UI. |
| **Compression** | The backend already gzips responses > 1KB; the client benefits for free. |
| **Connectivity awareness** | `@react-native-community/netinfo` drives an offline banner and auto-flush; reads fall back to cache, writes fall into the queue. |

## Store submission checklist

1. Replace placeholder assets in `mobile/assets/` (icon, adaptive icon, splash) with real branding.
2. Set production `EXPO_PUBLIC_*` values in `eas.json` build profiles.
3. Fill `eas.json` `submit` credentials (Apple ID / ASC app id / team id; Play service account json).
4. Bump `version` in `app.config.ts`; `production` build auto-increments the native build number.
5. Prepare store listings: privacy policy URL (the platform already serves `/privacy`), data-safety /
   App Privacy answers (auth token, coarse/precise location for attendance, no ad tracking), screenshots.
6. `eas build --profile production` for each platform, then `eas submit`.
7. iOS: submit for review from App Store Connect; Android: promote from the internal track.

## Local development against the backend

Point `EXPO_PUBLIC_API_URL` at your running backend. On a physical device, `localhost` is the device,
not your machine — use your machine's LAN IP (e.g. `http://192.168.x.x:8000`) and ensure the backend
`CORS_ORIGINS` / network allows it. Simulators can use `http://localhost:8000`.
