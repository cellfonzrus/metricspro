/**
 * Unit: the browser's reader of the register's tender list (index §POS-15).
 *
 * The register used to CARRY the tender buttons as a literal array, and the rule for swapping the
 * integrated card pair for the external-terminal one lived beside it. Both facts now come from the
 * server's one home (`pos/tender_vocabulary.py`) through `GET /pos/tender-types`, and this file
 * holds what the browser is still allowed to decide: keeping a selected tender valid when the list
 * changes, and what a Terms sale needs typed before the green button may be pressed.
 */
import { describe, it, expect } from 'vitest'
import {
  TENDER_FALLBACK, keepSelectionValid, termsBlockers, dueDateFor, paymentLineFor,
} from './pos-tenders'

const CASH = { key: 'cash', label: 'Cash', icon: '💵', settles_now: true, needs_amount_tendered: true }
const CREDIT = { key: 'credit_card', label: 'Credit', icon: '💳', settles_now: true, needs_amount_tendered: false }
const TERMS = { key: 'terms', label: 'Terms (on account)', icon: '🧾', settles_now: false, needs_amount_tendered: false, day_options: [15, 30, 60] }

describe('the list the screen draws', () => {
  it('falls back to cash alone when the server has not answered yet, never to a guessed list', () => {
    expect(TENDER_FALLBACK.map(t => t.key)).toEqual(['cash'])
  })
})

describe('keeping the selected tender valid', () => {
  it('leaves a selection that is still offered alone', () => {
    expect(keepSelectionValid('credit_card', [CASH, CREDIT])).toBe('credit_card')
  })
  it('moves to the first offered tender when the selected one disappears', () => {
    expect(keepSelectionValid('credit_card', [CASH, TERMS])).toBe('cash')
  })
  it('answers cash when nothing is offered at all, so the screen is never left with no tender', () => {
    expect(keepSelectionValid('terms', [])).toBe('cash')
  })
})

describe('what a Terms sale needs before it may be rung', () => {
  it('needs a customer', () => {
    expect(termsBlockers(TERMS, { customerId: null, termsDays: 30 })[0]).toMatch(/customer/i)
  })
  it('needs a day count that is on offer', () => {
    expect(termsBlockers(TERMS, { customerId: 'c-1', termsDays: null })).toHaveLength(1)
    expect(termsBlockers(TERMS, { customerId: 'c-1', termsDays: 45 })[0]).toMatch(/15|30|60/)
  })
  it('blocks nothing once both are there', () => {
    expect(termsBlockers(TERMS, { customerId: 'c-1', termsDays: 30 })).toEqual([])
  })
  it('blocks nothing at all for a tender that settles at the till', () => {
    expect(termsBlockers(CASH, { customerId: null, termsDays: null })).toEqual([])
  })
})

describe('what the cashier is shown', () => {
  it('counts the due date from the sale date', () => {
    expect(dueDateFor(new Date('2026-10-30T12:00:00Z'), 30)).toBe('2026-11-29')
  })
  it('labels an unpaid sale a balance, not a payment', () => {
    expect(paymentLineFor(TERMS, 108.88, '2026-11-29').label).toMatch(/due/i)
    expect(paymentLineFor(TERMS, 108.88, '2026-11-29').label).toContain('2026-11-29')
  })
  it('labels a settled sale a payment, as it always did', () => {
    expect(paymentLineFor(CASH, 108.88, null).label).toMatch(/^Paid/)
  })
})
