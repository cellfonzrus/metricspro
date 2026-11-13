// ── THE TREND VERDICT AND THE OUTLIER COLOUR, proved before anything renders them ────────────────
//
// Owner, 2026-11-12, on the action plan's team total: "it shoudl also show trending and which stores
// are not trending towards the accessories goals … the outloers shoudl be highlighted in a different
// color".
//
// `targetState` already owns "how is this scope doing against its goal" and exists BECAUSE six
// screens each carried their own copy and drifted (the 2026-09-10 "0 achieved with on target" bug).
// A trend verdict is the same kind of fact about the same goal, so it lives in the same home rather
// than starting the same drift again — and the colour a highlighted outlier gets is a constant here
// for exactly the same reason: "a different colour" has to mean ONE colour.
//
// The arithmetic is NOT here. The projection, the verdict and which stores are outliers are all
// decided by the server (`commcalc/month_trend`, §COMMCALC-72) so the screen and the mailed copy
// cannot disagree. This module only turns a served verdict into words and a colour.
import { describe, it, expect } from 'vitest'
import { trendState, OUTLIER_COLOR, targetState } from '../target-state'

describe('a trend that reaches the target reads as reaching it', () => {
  it('on_track is passing, and says so in words a manager can act on', () => {
    const s = trendState('on_track', 0)
    expect(s.passing).toBe(true)
    expect(s.label.toLowerCase()).toContain('trending')
    expect(s.kind).toBe('on_track')
  })
})

describe('a trend that falls short is NOT the same state as being behind today', () => {
  it('short is not passing and carries no invented phrase — the caller prints the gap', () => {
    const s = trendState('short', 95309)
    expect(s.passing).toBe(false)
    expect(s.kind).toBe('short')
  })

  it('and it is a DIFFERENT colour from the ordinary behind-today amber', () => {
    // "Behind right now" and "will not finish the month" are different facts and a manager acts on
    // them differently. One colour for both would collapse them.
    expect(trendState('short', 1).color).not.toBe(targetState(100, 50).color)
  })
})

describe('the two states that mean nobody measured this', () => {
  it('not_measured never reads as behind — a shortfall nobody measured is not a shortfall', () => {
    const s = trendState('not_measured', null)
    expect(s.passing).toBe(false)
    expect(s.kind).toBe('not_measured')
    expect(s.label).toMatch(/not measured/i)
    // the same neutral grey the other unmeasured states use, so the screen has one vocabulary
    expect(s.color).toBe(targetState(null, null).color)
  })

  it('no_target never reads as on track — you cannot trend towards nothing', () => {
    const s = trendState('no_target', null)
    expect(s.passing).toBe(false)
    expect(s.label).toMatch(/no target/i)
    expect(s.color).toBe(targetState(0, 0).color)
  })

  it('an unknown kind from a server that has moved on is treated as not measured, never as fine', () => {
    const s = trendState('something-new' as never, null)
    expect(s.passing).toBe(false)
    expect(s.kind).toBe('not_measured')
  })
})

describe('the outlier colour is one constant', () => {
  it('and it is neither the pass colour nor the behind colour', () => {
    // An outlier is not a verdict. The store furthest AHEAD of the pack is an outlier too, so
    // colouring it with the behind-amber or the met-green would say something untrue about it.
    expect(OUTLIER_COLOR).toBeTruthy()
    expect(OUTLIER_COLOR).not.toBe(targetState(100, 50).color)
    expect(OUTLIER_COLOR).not.toBe(targetState(100, 0).color)
    expect(OUTLIER_COLOR).not.toBe(targetState(null, null).color)
  })
})
