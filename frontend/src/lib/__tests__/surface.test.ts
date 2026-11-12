import { describe, it, expect } from 'vitest'
import { SURFACE, SURFACE2, rowStripe, rowStripeOr } from '../surface'

describe('surface — the one home for a themed background', () => {
  it('returns tokens, never literal colours', () => {
    for (const v of [SURFACE, SURFACE2, rowStripe(0), rowStripe(1), rowStripeOr(0, null)]) {
      expect(v.startsWith('var(--')).toBe(true)
    }
  })

  it('stripes odd rows and leaves even rows on the plain surface', () => {
    expect(rowStripe(0)).toBe(SURFACE)
    expect(rowStripe(1)).toBe(SURFACE2)
    expect(rowStripe(2)).toBe(SURFACE)
    expect(rowStripe(3)).toBe(SURFACE2)
  })

  it('never returns transparent — a sticky first column must not show the body through it', () => {
    for (let i = 0; i < 8; i++) expect(rowStripe(i)).not.toBe('transparent')
  })

  it('treats a broken index as row 0 instead of throwing', () => {
    expect(rowStripe(NaN)).toBe(SURFACE)
    expect(rowStripe(Infinity)).toBe(SURFACE)
    expect(rowStripe(-1)).toBe(SURFACE2)
    expect(rowStripe(1.7)).toBe(SURFACE2)
  })

  it('prefers an explicit tint over the stripe, and falls back when there is none', () => {
    expect(rowStripeOr(0, 'var(--c-bg-eff6ff)')).toBe('var(--c-bg-eff6ff)')
    expect(rowStripeOr(1, null)).toBe(SURFACE2)
    expect(rowStripeOr(1, '')).toBe(SURFACE2)
  })
})
