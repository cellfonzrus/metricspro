// THE BROWSER LOOKUP AGREES WITH THE BACKEND RESOLVER — on LIVE spellings, for EVERY tenant.
//
// A second implementation of a shared rule is the duplicate-derivation defect the index rules forbid,
// and `store-market.ts` IS a second implementation: the canonical answer lives in
// `backend/app/core/scope.py` (§13a). It is a deliberate one, because the pages hold a SPAN-SCOPED
// roster and must not fetch the whole org to answer a filter — so what keeps it honest is this
// equivalence pin rather than an argument.
//
// `store-market.live.json` was GENERATED, not typed: on 2026-11-12 the real
// `core.scope.store_market_resolver` was run against production for all four tenants over every store
// spelling their shifts, time clock and roster actually carry since 2026-08-01, plus the UPPER and
// lower variant of every roster code (the drift class itself). Regenerate it the same way — never by
// hand — when a tenant's roster changes:
//
//   cd backend && python3.11 - <<'PY'   # see the PR body for the exact script
//   from app.core import scope as SC; from app.core.database import get_supabase
//   resolve, _ = SC.store_market_resolver(get_supabase(), ORG)
//   PY
//
// THE ONE KNOWN DIVERGENCE, named rather than smoothed over: `Cellular Services` is a house-org
// roster row whose `market` is BLANK, and it is a company rather than a store (owner, 2026-10-02).
// The canonical union answers `NYC` for it because `commcalc.store_mapping` — the SALES vocabulary,
// which the browser is not served — carries a market there. No shift, punch or payroll row in
// production names it, so no workforce surface is affected, and the lookup's answer ('' → it shows
// under "(no market)") is what every one of the thirteen private maps already answered. A NEW
// divergence fails this test, which is the point.
import { describe, it, expect } from 'vitest'
import { buildStoreMarket, type StoreRosterRow } from '../store-market'
import live from './store-market.live.json'

/** spelling -> why the browser lookup is allowed to differ from the canonical resolver. */
const ALLOWED_DIVERGENCE: Record<string, Record<string, string>> = {
  house: {
    'CELLULAR SERVICES': 'a company, not a store; its market lives only in commcalc.store_mapping',
  },
}

type Fixture = { roster: StoreRosterRow[]; expected: Record<string, string> }
const FIXTURE = live as unknown as Record<string, Fixture>

describe.each(Object.keys(FIXTURE))('%s — every live store spelling', (org) => {
  const { roster, expected } = FIXTURE[org]
  const lk = buildStoreMarket(roster)
  const excused = ALLOWED_DIVERGENCE[org] || {}

  it('resolves to the same market the backend resolver gives', () => {
    const disagreements = Object.entries(expected)
      .filter(([spelling, want]) => lk.marketOf(spelling) !== want)
      .filter(([spelling]) => !(spelling.trim().toUpperCase() in excused))
      .map(([spelling, want]) => `${spelling}: backend=${want || '(none)'} browser=${lk.marketOf(spelling) || '(none)'}`)
    expect(disagreements).toEqual([])
  })

  it('resolves a market for every spelling the roster itself declares', () => {
    const unresolved = roster
      .filter(s => (s.market || '').trim())
      .flatMap(s => [s.store_code, s.address].filter(Boolean) as string[])
      .filter(sp => !lk.marketOf(sp))
    expect(unresolved).toEqual([])
  })

  it('answers no store spelling belonging to another tenant', () => {
    const mine = new Set(roster.flatMap(s => [s.store_code, s.address].filter(Boolean) as string[])
      .map(v => v.trim().toUpperCase()))
    const foreign = Object.keys(FIXTURE)
      .filter(o => o !== org)
      .flatMap(o => FIXTURE[o].roster.map(s => (s.store_code || '').trim()).filter(Boolean))
      .filter(code => !mine.has(code.toUpperCase()))
    const leaked = foreign.filter(code => lk.marketOf(code))
    expect(leaked).toEqual([])
  })
})

describe('the excused divergences are real rows, not a growing escape hatch', () => {
  it('each one is a spelling the fixture actually holds', () => {
    for (const [org, rows] of Object.entries(ALLOWED_DIVERGENCE)) {
      const have = new Set(Object.keys(FIXTURE[org].expected).map(s => s.trim().toUpperCase()))
      for (const sp of Object.keys(rows)) expect(have.has(sp)).toBe(true)
    }
  })
  it('stays a short list — a long one means the lookup is wrong, not the data', () => {
    const n = Object.values(ALLOWED_DIVERGENCE).reduce((a, r) => a + Object.keys(r).length, 0)
    expect(n).toBeLessThanOrEqual(1)
  })
})
