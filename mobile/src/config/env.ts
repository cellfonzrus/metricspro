import Constants from 'expo-constants'

import { describeStartupFault, type StartupFault } from '@/lib/startup-fault'

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
//
// CORRECTED the same day, after the owner reported it crashing a SECOND time. Throwing here was
// still wrong — it crashed just as hard, one import earlier. A process that dies in its first
// second kills expo-updates' background download, so the app can never replace the bundle that is
// breaking it and the only way out is deleting and reinstalling. The fault is now a VALUE the UI
// renders (STARTUP_FAULT), and the app stays alive to repair itself.
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

// NOTHING ON THE STARTUP PATH MAY THROW. A fault is a value the UI renders, so the process stays
// alive and expo-updates can finish downloading the bundle that fixes it — see the account in
// src/lib/startup-fault.ts. The first version of this check threw here, which is the same mistake
// one import further up the chain: it made a bad bundle permanent.
export const STARTUP_FAULT: StartupFault | null = describeStartupFault(extra)

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
