import Constants from 'expo-constants'

import REQUIRED from '../../config/required-extra.json'

// ── Runtime configuration ──────────────────────────────────────────────────────────────────────
// Resolved from app.config.ts `extra` (populated at build time from EXPO_PUBLIC_* env vars). Reading
// through one module means a missing value fails loudly in ONE place at startup, not with a cryptic
// "undefined" deep inside a fetch.
//
// WHAT IT USED TO DO, and why that was the defect: a missing value was `console.warn`ed and read as
// `''`. Nothing then stopped the app — until `src/api/supabase.ts` called `createClient('')` three
// imports later, which throws `supabaseUrl is required.` at module scope. A bundle published with
// no environment therefore crashed every phone it reached in the first second, with a message that
// named Supabase rather than the missing variable (TestFlight build 14, owner report 2026-11-09).
// A warning nobody can see on a phone is not a check. It now throws here, naming every missing
// variable at once, so a crash log says what to set.
//
// The authority on "what this app cannot run without" is config/required-extra.json — ONE
// declaration, dereferenced here and by scripts/delivery-env.js, which is what every delivery path
// checks before anything reaches a phone. Never list these keys again anywhere else.
type Extra = {
  supabaseUrl?: string
  supabaseAnonKey?: string
  apiUrl?: string
  eas?: { projectId?: string }
}

const extra = (Constants.expoConfig?.extra ?? {}) as Extra

const missing = Object.entries(REQUIRED.required)
  .filter(([key]) => {
    const value = (extra as Record<string, unknown>)[key]
    return typeof value !== 'string' || value.trim() === ''
  })
  .map(([, envVar]) => envVar)

if (missing.length > 0) {
  throw new Error(
    `[config] This build carries no ${missing.join(', ')}. ` +
      'They are set in mobile/eas.json under `build.<profile>.env` for a build, and reach an ' +
      'over-the-air publish through scripts/with-profile-env.mjs. Locally, set them in .env.',
  )
}

export const ENV = {
  supabaseUrl: extra.supabaseUrl as string,
  supabaseAnonKey: extra.supabaseAnonKey as string,
  apiUrl: extra.apiUrl as string,
  easProjectId: extra.eas?.projectId ?? '',
}

// The house org constant, mirrored from the web client (frontend/src/lib/client.ts ORG_ID). The
// backend tenant middleware overrides this for a normal user from their verified membership, so it
// is only a default for org-less URLs. Multi-tenant logins set the active org after /core/me.
export const HOUSE_ORG_ID = '00000000-0000-0000-0000-000000000001'
