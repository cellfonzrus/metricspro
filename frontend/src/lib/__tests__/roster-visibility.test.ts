// THE ONE FRONTEND ANSWER to "is this store / this person still active" (index §CORE-44) — the twin
// of `backend/app/modules/core/roster_visibility.py`, proved to give the SAME answers.
//
// Owner directive 2026-11-14: an inactive store or employee must not show in any module unless
// "Show inactive" is ticked. Before this there were SEVENTY hand-written `is_active !== false` tests
// across the app, so nothing stopped the next screen spelling it differently — and some already did
// (`is_active === true`, which hides every row whose flag was never set).
import { describe, expect, it } from 'vitest'
import {
  INACTIVE_LABEL, inactiveCount, rosterIsActive, splitRoster, visibleRoster, withIncludeInactive,
} from '@/lib/roster-visibility'

describe('rosterIsActive', () => {
  it('reads a missing or null flag as ACTIVE, because the column defaults to true', () => {
    expect(rosterIsActive({})).toBe(true)
    expect(rosterIsActive({ is_active: null })).toBe(true)
    expect(rosterIsActive({ is_active: undefined })).toBe(true)
  })

  it('treats only an explicit false as inactive', () => {
    expect(rosterIsActive({ is_active: false })).toBe(false)
    expect(rosterIsActive({ is_active: true })).toBe(true)
  })

  it('counts the wire spellings of false, which a bare truthiness test gets wrong', () => {
    // A JSON/CSV/form round-trip hands back strings and 0/1, and the STRING 'false' is truthy in JS.
    for (const v of ['false', 'False', 'FALSE', 'f', '0', 'no', 0]) {
      expect(rosterIsActive({ is_active: v })).toBe(false)
    }
    for (const v of ['true', 't', '1', 'yes', 1]) {
      expect(rosterIsActive({ is_active: v })).toBe(true)
    }
  })

  it('never crashes on a row that is not an object', () => {
    expect(rosterIsActive(null)).toBe(true)
    expect(rosterIsActive(undefined)).toBe(true)
  })

  it('reads a differently named flag only when the caller names it', () => {
    expect(rosterIsActive({ active: false })).toBe(true)
    expect(rosterIsActive({ active: false }, 'active')).toBe(false)
  })
})

describe('visibleRoster', () => {
  const rows = [
    { code: 'A' }, { code: 'B', is_active: false }, { code: 'C', is_active: null },
    { code: 'D', is_active: 'false' },
  ]

  it('hides inactive rows by default', () => {
    expect(visibleRoster(rows, false).map(r => r.code)).toEqual(['A', 'C'])
  })

  it('shows every row when Show inactive is ticked', () => {
    expect(visibleRoster(rows, true).map(r => r.code)).toEqual(['A', 'B', 'C', 'D'])
  })

  it('returns the same array reference when nothing is hidden, so a report keeps its own order cheaply', () => {
    const allActive = [{ code: 'A' }, { code: 'C', is_active: true }]
    expect(visibleRoster(allActive, false)).toBe(allActive)
  })

  it('survives a null row list', () => {
    expect(visibleRoster(null as any, false)).toEqual([])
  })

  it('can say how many it hid, so hiding is never silent', () => {
    expect(inactiveCount(rows)).toBe(2)
    expect(inactiveCount([])).toBe(0)
  })

  it('can hand the inactive rows back separately, for a surface that greys them instead of dropping them', () => {
    const [active, inactive] = splitRoster(rows)
    expect(active.map(r => r.code)).toEqual(['A', 'C'])
    expect(inactive.map(r => r.code)).toEqual(['B', 'D'])
  })

  it('reads the flag through a getter when the row spells it some other way', () => {
    const people = [{ name: 'Ali', status: 'terminated' }, { name: 'Zoe', status: 'employed' }]
    const got = visibleRoster(people, false, p => p.status !== 'terminated')
    expect(got.map(p => p.name)).toEqual(['Zoe'])
  })
})

describe('the label the user reads', () => {
  it('is one phrase, in one place, so two screens cannot word the same checkbox differently', () => {
    expect(INACTIVE_LABEL).toBe('Show inactive')
  })
})

describe('withIncludeInactive', () => {
  it('leaves a roster url alone when inactive rows are not wanted, keeping its cache key', () => {
    expect(withIncludeInactive('/api/v1/core/filter-options', false))
      .toBe('/api/v1/core/filter-options')
  })

  it('adds the one spelling of the opt-in, with the right separator', () => {
    expect(withIncludeInactive('/api/v1/core/filter-options', true))
      .toBe('/api/v1/core/filter-options?include_inactive=1')
    expect(withIncludeInactive('/api/v1/core/filter-options?org_id=abc', true))
      .toBe('/api/v1/core/filter-options?org_id=abc&include_inactive=1')
  })

  it('never adds it twice to a url that already asks', () => {
    const u = '/api/v1/storeops/stores?include_inactive=true'
    expect(withIncludeInactive(u, true)).toBe(u)
  })
})
