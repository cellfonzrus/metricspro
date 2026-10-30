// THE working store, and the rate the till charges — proved (index §POS-14).
//
// The defect this starts from (owner, 2026-10-30): *"since we have multiple stores under 1 tenant,
// the user if assigned to multiple stores should confirm which store they are working in so the
// receipt and the sale are logged for that store."*
//
// Four POS screens each spelled the same ladder — `getActiveStore() || user.store_code ||
// stores[0].store_code`. So a cashier granted three stores rang up at whichever store sorted
// first, nothing on the screen said nobody had chosen it, and the terminal's saved pick carried no
// user, so the next cashier to sign in inherited the last one's store.
//
// And the tax half: the register derived its own rate as store-then-org-wide, skipping the MARKET
// rung the server's rule has. A rate set on a market showed as `effective` in the Settings grid and
// charged the org-wide rate — or nothing — at the till. The register asks the server now; what is
// proved here is that the client holds NO rule of its own to disagree with.
import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  BASIS, STORE_KEY_PREFIX, LEGACY_STORE_KEY, ASK_SENTENCE, NO_STORE_SENTENCE,
  savedStore, saveStore, clearSavedStore, workingStoreFrom, needsConfirmation, type WorkingSpan,
} from '../pos-working-store'

const SPAN3: WorkingSpan = { stores: ['B-1115', 'B-1598', 'B-2001'], login_store: null, must_confirm: true }
const SPAN1: WorkingSpan = { stores: ['B-1598'], login_store: 'B-1598', must_confirm: false }
const SPAN0: WorkingSpan = { stores: [], login_store: null, must_confirm: true }

beforeEach(() => { window.localStorage.clear() })

describe('the saved pick is per user, not per terminal alone', () => {
  it('keys the saved store by the login', () => {
    saveStore('u-1', 'B-1115')
    expect(savedStore('u-1')).toBe('B-1115')
    expect(savedStore('u-2')).toBeNull()   // the next cashier inherits nothing
  })

  it('writes under a key that names the user', () => {
    saveStore('u-1', 'B-1115')
    expect(window.localStorage.getItem(`${STORE_KEY_PREFIX}u-1`)).toBe('B-1115')
  })

  it('reads the old shared key once, for the cashier already signed in', () => {
    // Back-compat, deliberately one-way: an existing terminal does not lose its pick on deploy,
    // but the value is never written back to the shared key.
    window.localStorage.setItem(LEGACY_STORE_KEY, 'B-2001')
    expect(savedStore('u-1')).toBe('B-2001')
    saveStore('u-1', 'B-1115')
    expect(window.localStorage.getItem(LEGACY_STORE_KEY)).toBeNull()
  })

  it('clearing one user does not clear another', () => {
    saveStore('u-1', 'B-1115'); saveStore('u-2', 'B-1598')
    clearSavedStore('u-1')
    expect(savedStore('u-1')).toBeNull()
    expect(savedStore('u-2')).toBe('B-1598')
  })

  it('survives localStorage being unavailable', () => {
    const spy = vi.spyOn(window.localStorage, 'getItem').mockImplementation(() => { throw new Error('denied') })
    expect(savedStore('u-1')).toBeNull()
    spy.mockRestore()
  })
})

describe('the basis — a guess is never an answer', () => {
  it('an explicit pick inside the span is confirmed', () => {
    const w = workingStoreFrom('B-1598', SPAN3)
    expect(w).toMatchObject({ storeCode: 'B-1598', basis: BASIS.CONFIRMED, refusal: null })
  })

  it('a sole store needs no confirmation', () => {
    expect(workingStoreFrom(null, SPAN1)).toMatchObject({ storeCode: 'B-1598', basis: BASIS.SOLE })
  })

  it('nothing picked with several stores names NO store', () => {
    const w = workingStoreFrom(null, SPAN3)
    expect(w.storeCode).toBeNull()
    expect(w.basis).toBe(BASIS.UNCONFIRMED)
    expect(w.refusal).toBe(ASK_SENTENCE)
  })

  it('never falls back to the first store in the list', () => {
    // THE defect, pinned on its own so no later change can reintroduce a positional fallback.
    for (const stores of [SPAN3.stores, [...SPAN3.stores].reverse()]) {
      expect(workingStoreFrom(null, { ...SPAN3, stores }).storeCode).toBeNull()
    }
  })

  it('never falls back to the login pin when several stores are possible', () => {
    // A pin is what somebody typed on a form once. It PRE-SELECTS the dialog; it does not answer it.
    const w = workingStoreFrom(null, { ...SPAN3, login_store: 'B-2001' })
    expect(w.storeCode).toBeNull()
    expect(w.suggested).toBe('B-2001')
  })

  it('refuses a stale saved pick the caller is no longer granted', () => {
    // A cashier moved off a store keeps the old code in their browser. It must not ring there.
    const w = workingStoreFrom('B-2001', SPAN1)
    expect(w.storeCode).toBeNull()
    expect(w.refusal).toContain('B-2001')
  })

  it('tells a cashier with no store at all, in its own sentence', () => {
    expect(workingStoreFrom(null, SPAN0).refusal).toBe(NO_STORE_SENTENCE)
  })

  it('resolves to the span’s own spelling, not the one in the browser', () => {
    expect(workingStoreFrom('b-1115', SPAN3).storeCode).toBe('B-1115')
  })

  it('is unconfirmed while the span has not loaded, rather than optimistic', () => {
    expect(workingStoreFrom('B-1115', null).basis).toBe(BASIS.UNCONFIRMED)
    expect(workingStoreFrom('B-1115', null).storeCode).toBeNull()
  })
})

describe('needsConfirmation — what blocks the till', () => {
  it('blocks while nothing is confirmed and several stores are possible', () => {
    expect(needsConfirmation(workingStoreFrom(null, SPAN3))).toBe(true)
  })
  it('does not block a confirmed pick or a sole store', () => {
    expect(needsConfirmation(workingStoreFrom('B-1598', SPAN3))).toBe(false)
    expect(needsConfirmation(workingStoreFrom(null, SPAN1))).toBe(false)
  })
})

describe('the client holds no tax rule of its own', () => {
  it('exports no rate resolver', async () => {
    // The register asked itself for the rate with a two-rung copy of the server's three-rung rule.
    // There is deliberately nothing here to ask: the rate arrives from GET /pos/working-store.
    const mod = await import('../pos-working-store')
    expect(Object.keys(mod).some(k => /rate|tax/i.test(k))).toBe(false)
  })
})
