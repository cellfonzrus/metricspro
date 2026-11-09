import 'react-native-url-polyfill/auto'
import { createClient } from '@supabase/supabase-js'
import { AppState } from 'react-native'

import { ENV, STARTUP_FAULT } from '@/config/env'
import { chunkedSecureStore } from './secure-storage'

// ── Supabase auth client ─────────────────────────────────────────────────────────────────────────
// Same project the web app uses. Differences from the web client, all mobile-specific:
//   • Session persisted in the Keychain / Keystore (chunkedSecureStore), never plaintext storage.
//   • detectSessionInUrl: false — there is no browser URL to parse a magic-link out of on native.
//   • Token auto-refresh is driven by AppState (below) so a backgrounded app doesn't burn a timer,
//     and refreshes immediately when the user returns instead of surfacing a stale-token 401.
//
// CONSTRUCTED ONLY WHEN THE BUNDLE IS CONFIGURED. `createClient('')` throws `supabaseUrl is
// required.`, and this module sits on the startup path — so on 2026-11-06 a bundle published with
// no configuration died here while it was still being evaluated, about a second after launch. That
// also kills expo-updates' background download, so the app could never replace the bundle that was
// breaking it. The fault is reported by `STARTUP_FAULT` and rendered by the root layout instead;
// nothing here may throw (index §CORE-38.7).
//
// When faulted, the export is a stand-in that names the real problem if anything reaches for it.
// It never points at a wrong or fake server: a client that silently talks to nowhere is worse than
// one that says why it cannot.
const unavailable = (): never => {
  throw new Error(
    `Supabase is unavailable: ${STARTUP_FAULT?.detail ?? 'this bundle is not configured.'}`,
  )
}

export const supabase = STARTUP_FAULT
  ? (new Proxy({} as Record<string, never>, {
      get: unavailable,
      apply: unavailable,
    }) as unknown as ReturnType<typeof createClient>)
  : createClient(ENV.supabaseUrl, ENV.supabaseAnonKey, {
      auth: {
        storage: chunkedSecureStore,
        autoRefreshToken: true,
        persistSession: true,
        detectSessionInUrl: false,
      },
    })

// Supabase recommends gating the refresh loop on foreground state in React Native. Not wired when
// the bundle is faulted: there is no client to refresh, and an AppState listener that throws on
// every foreground is the crash again by another route.
if (!STARTUP_FAULT) {
  AppState.addEventListener('change', (state) => {
    if (state === 'active') supabase.auth.startAutoRefresh()
    else supabase.auth.stopAutoRefresh()
  })
}

/** The current access token, or null. Awaits the in-memory session (fast; no network). */
export async function currentAccessToken(): Promise<string | null> {
  const { data } = await supabase.auth.getSession()
  return data.session?.access_token ?? null
}
