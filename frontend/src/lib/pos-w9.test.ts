/**
 * Unit: the browser's W-9 reader (index §POS-16).
 *
 * The server decides whether a certificate is current, whether a sale is blocked and whether it is
 * tax-free — and asks again at checkout. What is tested here is what the BROWSER is allowed to
 * decide: the empty state it assumes before it has asked, the rate an exempt sale is taxed at, and
 * the words the panel shows.
 */
import { describe, it, expect } from 'vitest'
import { W9_UNKNOWN, loadW9, taxRateFor, panelWords, requestLabel, awaitingReview } from './pos-w9'

const CURRENT = { ...W9_UNKNOWN, wholesale: true, w9_status: 'current' as const, tax_exempt: true, received_at: '2026-05-01' }
const MISSING = { ...W9_UNKNOWN, wholesale: true, w9_status: 'missing' as const, blocked: true, refusal: 'No signed W-9 is on file for A Shop. Send the form.', needs_w9_request: true }
const EXPIRED = { ...W9_UNKNOWN, wholesale: true, w9_status: 'expired' as const, blocked: true, refusal: 'The W-9 on file for A Shop is out of date. Send a new one.' }

describe('the state before anything is known', () => {
  it('does not block and does not exempt', () => {
    expect(W9_UNKNOWN.blocked).toBe(false)
    expect(W9_UNKNOWN.tax_exempt).toBe(false)
  })
  it('a sale with no customer never asks the server', async () => {
    let called = false
    const r = await loadW9(async () => { called = true; return {} }, null, 'B-1115')
    expect(called).toBe(false)
    expect(r).toEqual(W9_UNKNOWN)
  })
  it('a partial answer is filled out rather than left undefined', async () => {
    const r = await loadW9(async () => ({ wholesale: true, blocked: true }), 'c-1', 'B-1115')
    expect(r.wholesale).toBe(true)
    expect(r.tax_exempt).toBe(false)
    expect(r.accepted_formats).toEqual([])
  })
})

describe('the rate a line is taxed at', () => {
  it('is zero on an exempt sale', () => {
    expect(taxRateFor(0.08875, CURRENT)).toBe(0)
  })
  it('is the store rate otherwise, including for a blocked wholesale sale', () => {
    expect(taxRateFor(0.08875, MISSING)).toBe(0.08875)
    expect(taxRateFor(0.08875, W9_UNKNOWN)).toBe(0.08875)
  })
  it('is zero when the store has no rate declared', () => {
    expect(taxRateFor(null, W9_UNKNOWN)).toBe(0)
  })
})

describe('what the panel says', () => {
  it('says nothing at all for a retail customer', () => {
    expect(panelWords(W9_UNKNOWN)).toEqual({ tone: 'none', text: '' })
  })
  it('confirms a current certificate and that the sale is tax-free', () => {
    const w = panelWords(CURRENT)
    expect(w.tone).toBe('ok')
    expect(w.text).toContain('2026-05-01')
    expect(w.text).toMatch(/tax-free/)
  })
  it('says tax still applies when the company has opted out of the exemption', () => {
    const w = panelWords({ ...CURRENT, tax_exempt: false })
    expect(w.tone).toBe('ok')
    expect(w.text).toMatch(/tax still applies/i)
  })
  it('shows the server’s own sentence when the sale is blocked, never its own wording', () => {
    expect(panelWords(MISSING).text).toBe(MISSING.refusal)
    expect(panelWords(EXPIRED).text).toBe(EXPIRED.refusal)
    expect(panelWords(MISSING).tone).toBe('block')
  })
  it('warns without blocking when the company does not block on a missing W-9', () => {
    const w = panelWords({ ...MISSING, blocked: false, refusal: null })
    expect(w.tone).toBe('warn')
    expect(w.text).toMatch(/tax applies/i)
  })
  it('passes a setup note through instead of blocking, when the store is not set up yet', () => {
    // The note's WORDING is the platform's (lib/setupNotice.tsx, §19.36) and never written here —
    // what this pins is that the panel relays it as a warning rather than refusing the sale.
    const note = 'This store is not set up for wholesale yet.'
    const w = panelWords({ ...MISSING, setup_note: note })
    expect(w.tone).toBe('warn')
    expect(w.text).toContain(note)
  })
})

describe('the buttons', () => {
  it('offers to email the form, and to resend once a link is out', () => {
    expect(requestLabel(MISSING)).toMatch(/Email the W-9/)
    expect(requestLabel({ ...MISSING, link_live: true })).toMatch(/again/)
  })
  it('tells the cashier when something has come back that nobody has checked', () => {
    expect(awaitingReview({ ...MISSING, submitted_at: '2026-10-29T10:00:00Z' })).toMatch(/needs checking/)
    expect(awaitingReview(CURRENT)).toBeNull()
    expect(awaitingReview(MISSING)).toBeNull()
  })
})
