// WHICH STORE IS THIS SALE FOR, AND IS THAT CONFIRMED? — the client's ONE home (index §POS-14).
//
// THE INSTANCE (owner, 2026-10-30). *"since we have multiple stores under 1 tenant, the user if
// assigned to multiple stores should confirm which store they are working in so the receipt and the
// sale are logged for that store."*
//
// THE CLASS. The question had no home, and four screens — the register, activations, inventory and
// special orders — each spelled the SAME ladder: the terminal's saved pick, else the login's own
// `store_code`, else `stores[0].store_code`. A guess was therefore indistinguishable from an
// answer, and the saved pick carried no user, so the next cashier to sign in at that till inherited
// the last one's store.
//
// So this module holds the fact and its BASIS, and there is deliberately no positional fallback in
// it. `stores[0]` was the defect; a sole-store span is the only case where an unpicked store is
// still a known fact. The server owns the same rule in `pos/working_store.py` and is what actually
// REFUSES a sale — this half is the screen, not the gate.
//
// IT HOLDS NO TAX RULE. The register used to derive its own rate (store, then org-wide), skipping
// the market rung the server's rule has, so a rate set on a market was charged by nobody. The rate
// arrives from `GET /pos/working-store` with the span; there is nothing here to disagree with it.

/** The per-user key prefix. A terminal is shared; a store choice is not. */
export const STORE_KEY_PREFIX = 'pos_active_store:'
/** The pre-§POS-14 shared key, read once so a signed-in cashier does not lose their pick on
 *  deploy. Never written back — see `saveStore`. */
export const LEGACY_STORE_KEY = 'pos_active_store'

export const BASIS = {
  /** The cashier picked this store, under this login, on this terminal. */
  CONFIRMED: 'confirmed',
  /** Exactly one store is ringable, so there is nothing to confirm — and no guess either. */
  SOLE: 'sole_store',
  /** Nobody has chosen, or the pick is not theirs to make. NEVER carries a store code. */
  UNCONFIRMED: 'unconfirmed',
} as const

export type Basis = typeof BASIS[keyof typeof BASIS]

/** Said to a cashier. The SAME two sentences the server owns, so one refusal is worded once —
 *  `harness_pos_working_store.py` §F compares these strings to the Python ones. */
export const ASK_SENTENCE =
  'Confirm which store you are working in before you ring up a sale — the receipt and the sale are logged against it.'
export const NO_STORE_SENTENCE =
  'Your login is not granted any store that can ring up a sale. Ask an admin to add your stores in Roles & Access.'

/** What `GET /pos/working-store` answers about the caller. `null` = not loaded yet. */
export interface WorkingSpan {
  stores: string[]
  login_store: string | null
  must_confirm: boolean
}

export interface WorkingStore {
  /** The store a sale may be written at — null whenever the basis is `unconfirmed`. */
  storeCode: string | null
  basis: Basis
  /** What to show the cashier; null when nothing is owed. */
  refusal: string | null
  /** What to PRE-SELECT in the dialog (the login's pin). A hint, never an answer. */
  suggested: string | null
}

function up(v: unknown): string {
  return String(v ?? '').trim().toUpperCase()
}

/** This login's saved pick on this terminal, or null. Falls back to the legacy shared key once. */
export function savedStore(userId: string | null | undefined): string | null {
  if (typeof window === 'undefined') return null
  try {
    const own = userId ? window.localStorage.getItem(STORE_KEY_PREFIX + userId) : null
    return own || window.localStorage.getItem(LEGACY_STORE_KEY) || null
  } catch { return null }
}

/** Record an EXPLICIT pick. Also retires the legacy shared key, so the value cannot be inherited by
 *  the next login once anybody has chosen under the new one. */
export function saveStore(userId: string | null | undefined, code: string | null): void {
  if (typeof window === 'undefined') return
  try {
    const key = STORE_KEY_PREFIX + (userId || 'anon')
    if (code) window.localStorage.setItem(key, code)
    else window.localStorage.removeItem(key)
    window.localStorage.removeItem(LEGACY_STORE_KEY)
  } catch { /* localStorage unavailable */ }
}

export function clearSavedStore(userId: string | null | undefined): void {
  saveStore(userId, null)
}

/** The working store and WHY — the client's mirror of `working_store.resolve`.
 *
 *  `saved` is what the terminal says was picked and is the ONLY thing consulted for an answer.
 *  While `span` is null (not loaded) the verdict is `unconfirmed`: an optimistic answer here would
 *  let a cart be started against a store the server is about to refuse. */
export function workingStoreFrom(saved: string | null | undefined, span: WorkingSpan | null): WorkingStore {
  const suggested = span?.login_store ?? null
  if (!span) return { storeCode: null, basis: BASIS.UNCONFIRMED, refusal: null, suggested }
  const codes = span.stores || []
  const asked = String(saved ?? '').trim()
  if (asked) {
    const hit = codes.find(c => up(c) === up(asked))
    if (hit) return { storeCode: hit, basis: BASIS.CONFIRMED, refusal: null, suggested }
    return {
      storeCode: null, basis: BASIS.UNCONFIRMED, suggested,
      refusal: `${asked} is not a store you can ring up a sale at. Pick one of your own stores.`,
    }
  }
  if (codes.length === 0) {
    return { storeCode: null, basis: BASIS.UNCONFIRMED, refusal: NO_STORE_SENTENCE, suggested }
  }
  if (codes.length === 1) {
    return { storeCode: codes[0], basis: BASIS.SOLE, refusal: null, suggested }
  }
  return { storeCode: null, basis: BASIS.UNCONFIRMED, refusal: ASK_SENTENCE, suggested }
}

/** Does the till have to ask before anything can be rung up? */
export function needsConfirmation(w: WorkingStore): boolean {
  return w.basis === BASIS.UNCONFIRMED
}
