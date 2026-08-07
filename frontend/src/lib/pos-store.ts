// Active POS store — a property of the physical terminal (like the register number), so it
// lives in localStorage, not in the user profile. Pages fall back to the login's own
// store_code grant when nothing is chosen yet.

const KEY = 'pos_active_store'

export function getActiveStore(): string | null {
  if (typeof window === 'undefined') return null
  try { return window.localStorage.getItem(KEY) || null } catch { return null }
}

export function setActiveStore(code: string | null): void {
  if (typeof window === 'undefined') return
  try {
    if (code) window.localStorage.setItem(KEY, code)
    else window.localStorage.removeItem(KEY)
  } catch { /* localStorage unavailable */ }
}
