import AsyncStorage from '@react-native-async-storage/async-storage'
import * as SecureStore from 'expo-secure-store'

// ── Auxiliary auth material ──────────────────────────────────────────────────────────────────────
// Mirrors the web client's non-Supabase headers (frontend/src/lib/client.ts):
//   • x-active-org : which tenant a multi-tenant login is acting in. NOT secret — the server
//     re-verifies membership on every request — so AsyncStorage is fine. A hint, not authority.
//   • x-2fa-token  : an HMAC-signed proof the sign-in OTP was passed. A bearer-like proof, so it
//     lives in SecureStore. Inert for tenants that don't require 2FA (default off).
const ACTIVE_ORG_KEY = 'mp_active_org'
const TWOFA_KEY = 'mp_2fa_token'

// ── active org ──
let _activeOrg: string | null = null

export async function loadActiveOrg(): Promise<string | null> {
  try {
    _activeOrg = await AsyncStorage.getItem(ACTIVE_ORG_KEY)
  } catch {
    _activeOrg = null
  }
  return _activeOrg
}
export function getActiveOrg(): string | null {
  return _activeOrg
}
export async function setActiveOrg(id: string | null): Promise<void> {
  _activeOrg = id || null
  try {
    if (id) await AsyncStorage.setItem(ACTIVE_ORG_KEY, id)
    else await AsyncStorage.removeItem(ACTIVE_ORG_KEY)
  } catch {
    /* ignore */
  }
}

// ── 2FA proof ──
let _twofa: string | null = null

export async function load2faToken(): Promise<string | null> {
  try {
    _twofa = await SecureStore.getItemAsync(TWOFA_KEY)
  } catch {
    _twofa = null
  }
  return _twofa
}
export function get2faToken(): string | null {
  return _twofa
}
export async function set2faToken(tok: string | null): Promise<void> {
  _twofa = tok || null
  try {
    if (tok) await SecureStore.setItemAsync(TWOFA_KEY, tok)
    else await SecureStore.deleteItemAsync(TWOFA_KEY)
  } catch {
    /* ignore */
  }
}

/** Clear everything auxiliary on sign-out. The Supabase session itself is cleared by supabase.auth. */
export async function clearAuxAuth(): Promise<void> {
  await Promise.all([setActiveOrg(null), set2faToken(null)])
}
