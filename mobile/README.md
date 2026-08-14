# MetricsPro Mobile

Native iOS + Android app for MetricsPro, built with **Expo (React Native) + TypeScript** and shipped
to the **Apple App Store** and **Google Play Store**.

This first release delivers the frontline modules — **Time Clock**, **Point of Sale (POS)**, **CRM**,
and **Earnings** (commissions, targets & achievement) — on top of the existing platform backend, with a
module registry designed so the remaining back-office modules can be added incrementally without
re-architecting the app.

```
mobile/
  app/                       Expo Router routes (file-based navigation)
    _layout.tsx              Providers (Auth, React Query, App Lock) + root stack
    login.tsx                Email/password sign-in (Supabase)
    (app)/                   Authenticated area (guarded)
      _layout.tsx            Auth guard + stack for detail screens
      (tabs)/                Bottom tabs, filtered by the module registry + permissions
        index.tsx            Home dashboard (module grid, live status, roadmap)
        timeclock.tsx        Time Clock
        pos.tsx              POS catalog + cart
        crm.tsx              CRM leads + tasks
        earnings.tsx         Commissions, tier/KPIs, targets, history
        settings.tsx         Account, company switch, security, sync
      pos/checkout.tsx       Checkout (modal)
      crm/[leadId].tsx       Lead detail
      earnings/targets.tsx   Schedule-weighted target pace & achievement
  src/
    api/                     Supabase client, secure storage, HTTP client, per-module APIs
    auth/                    AuthContext, tokens, biometric app lock
    offline/                 Connectivity + durable offline mutation queue
    modules/                 Module registry (the extensibility backbone) + POS cart store
    components/              Shared UI kit + offline banner
    theme/                   Design tokens
    config/                  Runtime config (env)
```

## Why Expo / React Native

- **Genuine native binaries** for both stores (not a web wrapper — avoids App Store guideline 4.2
  rejections).
- **One TypeScript codebase** that reuses the platform's existing API contract and data shapes.
- **Secure-by-default primitives**: Keychain/Keystore session storage, biometrics, over-the-air
  updates via EAS.
- **EAS Build/Submit** to produce and ship `.ipa` / `.aab` artifacts.

## Prerequisites

- Node 20+, and the Expo tooling (`npm i -g eas-cli`)
- An [Expo account](https://expo.dev) (`eas login`) for cloud builds
- Apple Developer + Google Play Console accounts for store submission

## Setup

```bash
cd mobile
cp .env.example .env          # fill in PUBLIC values only (see below)
npm install                   # or: npx expo install  (reconciles native versions)
npm start                     # Expo dev server; press i / a for simulators
```

`.env` (never commit it) — only **public** values belong here; the Supabase **service-role** key must
never be in this app:

```
EXPO_PUBLIC_SUPABASE_URL=...
EXPO_PUBLIC_SUPABASE_ANON_KEY=...     # public/publishable key by design
EXPO_PUBLIC_API_URL=https://your-api.railway.app
```

## Building & submitting

Configure `eas.json` (fill the `submit` credentials), then:

```bash
eas login
eas build:configure
npm run build:ios:preview        # internal test build
npm run build:android:preview
npm run build:ios:prod           # store build (auto-increments)
npm run build:android:prod
npm run submit:ios               # upload to App Store Connect / TestFlight
npm run submit:android           # upload to Play internal track
```

See [`docs/MOBILE.md`](../docs/MOBILE.md) for the full architecture, security model, latency strategy,
and the store-submission checklist.

## Adding a new module (the provision for "everything eventually")

Every feature area is declared as **data** in `src/modules/registry.ts`. To bring a back-office module
over from the web platform:

1. Add an entry to `MODULES` in `src/modules/registry.ts` (key, title, route, icon, `visible`).
2. Add a typed API file under `src/api/<module>.ts` that calls the existing `/api/v1/...` endpoints
   through the shared `api` client (auth, org scoping, 2FA, error handling are already handled).
3. Create the screen(s) under `app/(app)/(tabs)/<module>.tsx` (and any detail screens under
   `app/(app)/<module>/...`).
4. When the backend module-entitlement gate lands (`core.module_catalog` / `roles.modules`), point the
   entry's `visible`/`entitlementKey` at it.

The tab bar, Home grid, and route guard all read the registry — nothing else needs to change.
