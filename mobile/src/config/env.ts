import Constants from 'expo-constants'

// ── Runtime configuration ──────────────────────────────────────────────────────────────────────
// Resolved from app.config.ts `extra` (populated at build time from EXPO_PUBLIC_* env vars). Reading
// through one module means a missing value fails loudly in ONE place at startup, not with a cryptic
// "undefined" deep inside a fetch.
type Extra = {
  supabaseUrl?: string
  supabaseAnonKey?: string
  apiUrl?: string
  eas?: { projectId?: string }
}

const extra = (Constants.expoConfig?.extra ?? {}) as Extra

function required(value: string | undefined, name: string): string {
  if (!value) {
    // In production these are set via EAS build env; in dev via .env. A clear message beats a
    // silent misconfiguration that only surfaces as 401s.
    console.warn(`[config] Missing ${name}. Set it in your .env / EAS build env.`)
    return ''
  }
  return value
}

export const ENV = {
  supabaseUrl: required(extra.supabaseUrl, 'EXPO_PUBLIC_SUPABASE_URL'),
  supabaseAnonKey: required(extra.supabaseAnonKey, 'EXPO_PUBLIC_SUPABASE_ANON_KEY'),
  apiUrl: required(extra.apiUrl, 'EXPO_PUBLIC_API_URL'),
  easProjectId: extra.eas?.projectId ?? '',
}

// The house org constant, mirrored from the web client (frontend/src/lib/client.ts ORG_ID). The
// backend tenant middleware overrides this for a normal user from their verified membership, so it
// is only a default for org-less URLs. Multi-tenant logins set the active org after /core/me.
export const HOUSE_ORG_ID = '00000000-0000-0000-0000-000000000001'
