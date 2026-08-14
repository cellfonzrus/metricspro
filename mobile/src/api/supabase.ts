import 'react-native-url-polyfill/auto'
import { createClient } from '@supabase/supabase-js'
import { AppState } from 'react-native'

import { ENV } from '@/config/env'
import { chunkedSecureStore } from './secure-storage'

// ── Supabase auth client ─────────────────────────────────────────────────────────────────────────
// Same project the web app uses. Differences from the web client, all mobile-specific:
//   • Session persisted in the Keychain / Keystore (chunkedSecureStore), never plaintext storage.
//   • detectSessionInUrl: false — there is no browser URL to parse a magic-link out of on native.
//   • Token auto-refresh is driven by AppState (below) so a backgrounded app doesn't burn a timer,
//     and refreshes immediately when the user returns instead of surfacing a stale-token 401.
export const supabase = createClient(ENV.supabaseUrl, ENV.supabaseAnonKey, {
  auth: {
    storage: chunkedSecureStore,
    autoRefreshToken: true,
    persistSession: true,
    detectSessionInUrl: false,
  },
})

// Supabase recommends gating the refresh loop on foreground state in React Native.
AppState.addEventListener('change', (state) => {
  if (state === 'active') supabase.auth.startAutoRefresh()
  else supabase.auth.stopAutoRefresh()
})

/** The current access token, or null. Awaits the in-memory session (fast; no network). */
export async function currentAccessToken(): Promise<string | null> {
  const { data } = await supabase.auth.getSession()
  return data.session?.access_token ?? null
}
