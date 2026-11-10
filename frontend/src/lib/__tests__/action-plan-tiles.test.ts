// Unit proof for `lib/action-plan-tiles` — the ONE home for "how the Daily Action Plan is grouped
// into tiles" (owner ask 2026-11-10: *"Make it tiled with focused metrics … and it shows who needs
// improvement and so forth with the details at the bottom, the tiles should be switched between
// kpi / stores / sales reps"*).
//
// WHAT THESE TESTS GUARD, beyond the arithmetic:
//
//  · THE VOCABULARY IS THE TENANT'S (owner directive 2026-10-30). There is no built-in metric list:
//    `planMetrics` reads what the endpoint declared for THIS org, and `buildTiles` takes those
//    declarations as a REQUIRED argument. Two tenants with different POS words are run through every
//    assertion, plus the crossing case — one tenant's declarations against the other's plan must
//    produce NOTHING, never a tile with the wrong tenant's label over the wrong tenant's rows.
//  · NOT MEASURED IS NOT ZERO. A rep KPI that no feed landed a value for arrives in
//    `commission.kpis_no_data` (the backend's own honesty, `kpi_failing.evaluate`). A tile for it
//    must say so — "0 reps behind" would read as a clean sheet. This is live today: the DLAR gross
//    adds / activations / upgrades columns stopped arriving with the Elevate Go renames.
//  · NO SECOND FOLD. A tile's headline figure is the CARDINALITY of the list it prints (who needs
//    improvement), never a re-summed target, achieved or need — those are folded once on the server
//    (`_targets_dd.team_totals`) and the page reads that fold.
import { describe, it, expect } from 'vitest'
import {
  buildTiles, planMetrics, filterPlan, tilesToSnapshot, metricKey,
  type PlanStore, type TileView,
} from '@/lib/action-plan-tiles'
import { emptyStandardFilter } from '@/lib/standard-filters'

// ── TENANT A — the house org's words (wireless boxes, dollar accessories) ────────────────────────
const A_LEFFERTS: PlanStore = {
  store_code: 'B-1598', address: '104-08 Lefferts Blvd', market: 'Queens',
  conversion: { boxes: 40, billpays: 200, rate: 20, target: 25, meets_target: false },
  metrics: [
    { cat: 'boxes', label: 'Boxes', unit: 'count', target: 100, achieved: 40, need: 60, pace: 6, today_target: 4 },
    { cat: 'accessories', label: 'Accessories', unit: 'dollars', target: 5000, achieved: 4900, need: 100, pace: 10, today_target: 160 },
  ],
  items: [
    { severity: 'critical', metric: 'boxes', title: 'Behind on Boxes', detail: '60 to go; must do 6/day.' },
    { severity: 'good', metric: 'accessories', title: 'Accessories on track', detail: '$100 to go.' },
  ],
  reps: [
    {
      rep: 'Khan, Abid', below_store: true,
      conversion: { boxes: 10, billpays: 90, rate: 11, target: 25, meets_target: false },
      items: [{ severity: 'warning', metric: 'conversion', title: 'Below store conversion', detail: '11% vs 20%.' }],
      commission: {
        tier: 0.75, kpis_met: 1, total_kpis: 2, subtotal: 4000, total_payout: 3000, at_risk: 1000,
        short_kpis: ['Family Plan'], t100: 2, t75: 1,
        kpis: [
          { kpi: 'familyplan', label: 'Family Plan', target: 45, actual: 20, met: false },
          { kpi: 'atu', label: 'ATU', target: 55, actual: 60, met: true },
        ],
        kpis_no_data: [],
      },
    },
  ],
  commission_at_risk: 1000, counts: { critical: 1, warning: 1 },
}
const A_UTICA: PlanStore = {
  store_code: 'B-1115', address: '1020 Utica Ave', market: 'Brooklyn',
  conversion: { boxes: 30, billpays: 100, rate: 30, target: 25, meets_target: true },
  metrics: [
    { cat: 'boxes', label: 'Boxes', unit: 'count', target: 80, achieved: 80, need: 0, pace: 0, today_target: 0 },
    { cat: 'accessories', label: 'Accessories', unit: 'dollars', target: 4000, achieved: 1000, need: 3000, pace: 300, today_target: 130 },
  ],
  items: [
    { severity: 'good', metric: 'boxes', title: 'Boxes target met', detail: '80 of 80 — done.' },
    { severity: 'warning', metric: 'accessories', title: 'Behind on Accessories', detail: '$3,000 to go.' },
  ],
  reps: [
    {
      rep: 'Sohail, Hajra', below_store: false,
      conversion: { boxes: 20, billpays: 60, rate: 33, target: 25, meets_target: true },
      items: [],
      commission: {
        tier: 1.0, kpis_met: 2, total_kpis: 2, subtotal: 2000, total_payout: 2000, at_risk: 0,
        short_kpis: [], t100: 2, t75: 1,
        kpis: [
          { kpi: 'familyplan', label: 'Family Plan', target: 45, actual: 50, met: true },
          { kpi: 'atu', label: 'ATU', target: 55, actual: 70, met: true },
        ],
        kpis_no_data: [],
      },
    },
  ],
  commission_at_risk: 0, counts: { critical: 0, warning: 1 },
}
const TENANT_A: PlanStore[] = [A_LEFFERTS, A_UTICA]

// ── TENANT B — a different tenant's OWN declared words, and a KPI no feed landed ─────────────────
// `boostapp` sits only in `kpis_no_data`: nothing fed it this period, which is exactly the live
// Elevate Go situation. A tile for it must say "not measured", never "nobody is behind".
const B_HARBOR: PlanStore = {
  store_code: 'L-900', address: '9 Harbor Rd', market: 'Nassau',
  conversion: { boxes: 15, billpays: 50, rate: 30, target: 25, meets_target: true },
  metrics: [
    { cat: 'handsets', label: 'Handsets', unit: 'count', target: 60, achieved: 10, need: 50, pace: 5, today_target: 2 },
    { cat: 'tablets', label: 'Tablets', unit: 'count', target: 20, achieved: 20, need: 0, pace: 0, today_target: 0 },
  ],
  items: [
    { severity: 'critical', metric: 'handsets', title: 'Behind on Handsets', detail: '50 to go.' },
    { severity: 'good', metric: 'tablets', title: 'Tablets target met', detail: '20 of 20.' },
  ],
  reps: [
    {
      rep: 'Novak, Imre', below_store: false,
      conversion: { boxes: 15, billpays: 50, rate: 30, target: 25, meets_target: true },
      items: [],
      commission: {
        tier: 0.75, kpis_met: 0, total_kpis: 1, subtotal: 1000, total_payout: 750, at_risk: 250,
        short_kpis: ['Protect'], t100: 1, t75: 1,
        kpis: [{ kpi: 'protect', label: 'Protect', target: 80, actual: 10, met: false }],
        kpis_no_data: [{ kpi: 'boostapp', label: 'Carrier App', target: 65 }],
      },
    },
  ],
  commission_at_risk: 250, counts: { critical: 1, warning: 0 },
}
const TENANT_B: PlanStore[] = [B_HARBOR]

const ALL: string[] = []                       // no narrowing — every declared metric
const view = (v: TileView) => v

describe('planMetrics — the vocabulary is the tenant\'s, read off their own plan', () => {
  it('declares nothing of its own for an empty plan', () => {
    expect(planMetrics([])).toEqual([])
  })

  it('reads tenant A\'s store categories and rep KPIs, with A\'s labels', () => {
    const decls = planMetrics(TENANT_A)
    expect(decls.map(d => d.key)).toEqual(['store:boxes', 'store:accessories', 'kpi:familyplan', 'kpi:atu'])
    expect(decls.map(d => d.label)).toEqual(['Boxes', 'Accessories', 'Family Plan', 'ATU'])
    expect(decls.filter(d => d.grain === 'store').map(d => d.unit)).toEqual(['count', 'dollars'])
  })

  it('reads tenant B\'s own words — and a KPI that only arrived as "no data" is still declared', () => {
    const decls = planMetrics(TENANT_B)
    expect(decls.map(d => d.key)).toEqual(['store:handsets', 'store:tablets', 'kpi:protect', 'kpi:boostapp'])
    // and none of tenant A's vocabulary leaked in
    expect(decls.some(d => /box|accessor|family/i.test(d.label))).toBe(false)
  })

  it('keys a store category and a rep KPI apart, so a shared word cannot collide', () => {
    expect(metricKey('store', 'familyplan')).not.toEqual(metricKey('kpi', 'familyplan'))
  })
})

describe('buildTiles — KPI view: one tile per focused metric, naming who needs improvement', () => {
  it('counts the stores behind a store-grain metric and names the worst first', () => {
    const tiles = buildTiles(TENANT_A, planMetrics(TENANT_A), view('kpi'), ALL)
    const boxes = tiles.find(t => t.key === 'store:boxes')!
    expect(boxes.label).toBe('Boxes')
    expect(boxes.behind).toBe(1)              // Lefferts needs 60; Utica is done
    expect(boxes.total).toBe(2)
    expect(boxes.measured).toBe(true)
    expect(boxes.severity).toBe('critical')   // the server called Lefferts critical
    expect(boxes.needsWork.map(w => w.name)).toEqual(['104-08 Lefferts Blvd'])
    expect(boxes.needsWork[0].detail).toContain('60')

    const acc = tiles.find(t => t.key === 'store:accessories')!
    expect(acc.behind).toBe(2)                // both are short: 3,000 and 100
    expect(acc.severity).toBe('warning')
    expect(acc.needsWork.map(w => w.name)).toEqual(['1020 Utica Ave', '104-08 Lefferts Blvd'])
  })

  it('counts the reps behind a rep-grain KPI and leaves a met KPI good', () => {
    const tiles = buildTiles(TENANT_A, planMetrics(TENANT_A), view('kpi'), ALL)
    const fp = tiles.find(t => t.key === 'kpi:familyplan')!
    expect(fp.behind).toBe(1)
    expect(fp.total).toBe(2)
    expect(fp.measured).toBe(true)
    expect(fp.needsWork.map(w => w.name)).toEqual(['Khan, Abid'])
    expect(fp.needsWork[0].detail).toContain('20')
    expect(fp.needsWork[0].detail).toContain('45')

    const atu = tiles.find(t => t.key === 'kpi:atu')!
    expect(atu.behind).toBe(0)
    expect(atu.severity).toBe('good')
    expect(atu.needsWork).toEqual([])
  })

  it('A KPI NOBODY MEASURED IS NOT A CLEAN SHEET — it says not measured, and why', () => {
    const tiles = buildTiles(TENANT_B, planMetrics(TENANT_B), view('kpi'), ALL)
    const app = tiles.find(t => t.key === 'kpi:boostapp')!
    expect(app.measured).toBe(false)
    expect(app.behind).toBe(0)
    expect(app.total).toBe(0)
    expect(app.severity).toBe('none')
    expect(app.note).toMatch(/no recorded value|not measured/i)
    expect(app.figure).toBeNull()
    // the measured sibling in the same tenant is unaffected
    expect(tiles.find(t => t.key === 'kpi:protect')!.measured).toBe(true)
  })

  it('orders the tiles worst first — critical, then warning, then nothing to do', () => {
    const sev = buildTiles(TENANT_A, planMetrics(TENANT_A), view('kpi'), ALL).map(t => t.severity)
    expect(sev.indexOf('critical')).toBeLessThan(sev.indexOf('warning'))
    expect(sev.indexOf('warning')).toBeLessThan(sev.indexOf('good'))
  })
})

describe('buildTiles — the metric picker narrows, and never falls back to somebody else\'s list', () => {
  it('shows only the picked metrics', () => {
    const tiles = buildTiles(TENANT_A, planMetrics(TENANT_A), view('kpi'), ['store:boxes'])
    expect(tiles.map(t => t.key)).toEqual(['store:boxes'])
  })

  it('ignores a metric this tenant never declared — and does NOT widen to all when only that was picked', () => {
    expect(buildTiles(TENANT_A, planMetrics(TENANT_A), view('kpi'), ['kpi:protect'])).toEqual([])
  })

  it('ONE TENANT\'S DECLARATIONS CANNOT ANSWER FOR ANOTHER\'S PLAN', () => {
    // tenant A's words over tenant B's rows: nothing is identified, and nothing is invented.
    expect(buildTiles(TENANT_B, planMetrics(TENANT_A), view('kpi'), ALL)).toEqual([])
    expect(buildTiles(TENANT_A, planMetrics(TENANT_B), view('kpi'), ALL)).toEqual([])
  })

  it('a plan with no declarations yields no tiles rather than house words', () => {
    expect(buildTiles(TENANT_A, [], view('kpi'), ALL)).toEqual([])
    expect(buildTiles([], planMetrics(TENANT_A), view('store'), ALL)).toEqual([])
  })
})

describe('buildTiles — store view: one tile per store, worst first', () => {
  it('counts how many of the focused metrics each store is behind on', () => {
    const tiles = buildTiles(TENANT_A, planMetrics(TENANT_A), view('store'), ALL)
    expect(tiles.map(t => t.label)).toEqual(['104-08 Lefferts Blvd', '1020 Utica Ave'])
    const lef = tiles[0]
    // boxes + accessories short, and its rep is under Family Plan; ATU is met → 3 of 4 focused
    expect(lef.behind).toBe(3)
    expect(lef.total).toBe(4)
    expect(lef.severity).toBe('critical')
    expect(lef.needsWork.map(w => w.name)).toEqual(['Boxes', 'Accessories', 'Family Plan'])
    expect(tiles[1].behind).toBe(1)            // Utica: accessories only
  })

  it('honours the metric picker in the store count', () => {
    const tiles = buildTiles(TENANT_A, planMetrics(TENANT_A), view('store'), ['store:boxes'])
    expect(tiles.find(t => t.label === '1020 Utica Ave')!.behind).toBe(0)
    expect(tiles.find(t => t.label === '104-08 Lefferts Blvd')!.behind).toBe(1)
  })

  it('counts a focused KPI at store grain through the reps working there — never a second score', () => {
    const tiles = buildTiles(TENANT_A, planMetrics(TENANT_A), view('store'), ['kpi:familyplan'])
    const lef = tiles.find(x => x.label === '104-08 Lefferts Blvd')!
    expect(lef.total).toBe(1)
    expect(lef.behind).toBe(1)
    expect(lef.needsWork[0].detail).toMatch(/1 of 1 rep/)
    expect(tiles.find(x => x.label === '1020 Utica Ave')!.behind).toBe(0)
  })

  it('a store carrying none of the focused metrics is not measured, not a zero', () => {
    // THE LIVE SHAPE: the store has the target but no feed landed a figure for it, and its rep's
    // KPI arrived as no-data. "0 behind" here would read as a store with nothing to fix.
    const unfed: PlanStore[] = [{
      ...A_UTICA,
      reps: [{
        ...A_UTICA.reps[0],
        commission: { ...A_UTICA.reps[0].commission!, kpis: [], kpis_no_data: [{ kpi: 'familyplan', label: 'Family Plan', target: 45 }] },
      }],
    }]
    const t = buildTiles(unfed, planMetrics(TENANT_A), view('store'), ['kpi:familyplan'])[0]
    expect(t.measured).toBe(false)
    expect(t.figure).toBeNull()
    expect(t.note).toMatch(/not measured|no recorded value/i)
  })
})

describe('buildTiles — sales-rep view: one tile per rep, folded across their stores', () => {
  it('names the failing KPIs and carries the at-risk money', () => {
    const tiles = buildTiles(TENANT_A, planMetrics(TENANT_A), view('rep'), ALL)
    expect(tiles.map(t => t.label)).toEqual(['Khan, Abid', 'Sohail, Hajra'])
    const abid = tiles[0]
    expect(abid.behind).toBe(1)
    expect(abid.money).toBe(true)
    expect(abid.figure).toBe(1000)
    expect(abid.needsWork.map(w => w.name)).toEqual(['Family Plan'])
    expect(tiles[1].behind).toBe(0)
    expect(tiles[1].figure).toBe(0)
  })

  it('A REP WORKING TWO STORES IS ONE ROW AND ONE AT-RISK FIGURE, never doubled', () => {
    // The endpoint keys commissions by REP, so the same rep in two stores carries the SAME
    // commission row on both — summing it would invent money that is not at risk.
    const twice: PlanStore[] = [A_LEFFERTS, { ...A_UTICA, reps: [...A_UTICA.reps, A_LEFFERTS.reps[0]] }]
    const tiles = buildTiles(twice, planMetrics(TENANT_A), view('rep'), ALL)
    const abid = tiles.filter(t => t.label === 'Khan, Abid')
    expect(abid).toHaveLength(1)
    expect(abid[0].figure).toBe(1000)
    expect(abid[0].note).toMatch(/2 stores/)
  })

  it('a rep whose commission was never calculated is unmeasured, not $0 at risk', () => {
    const noComm: PlanStore[] = [{ ...A_LEFFERTS, reps: [{ ...A_LEFFERTS.reps[0], commission: null }] }]
    const t = buildTiles(noComm, planMetrics(TENANT_A), view('rep'), ALL)[0]
    expect(t.figure).toBeNull()
    expect(t.note).toMatch(/not been calculated|not calculated/i)
  })
})

describe('filterPlan — the standard filters, over the rows the page already loaded', () => {
  const sel = (patch: Partial<ReturnType<typeof emptyStandardFilter>>) => ({ ...emptyStandardFilter(), ...patch })

  it('passes everything through when nothing is narrowed', () => {
    expect(filterPlan(TENANT_A, sel({})).map(s => s.store_code)).toEqual(['B-1598', 'B-1115'])
  })

  it('narrows by store, case-insensitively, on the label the rows carry', () => {
    expect(filterPlan(TENANT_A, sel({ stores: ['1020 utica ave'] })).map(s => s.store_code)).toEqual(['B-1115'])
  })

  it('narrows by market', () => {
    expect(filterPlan(TENANT_A, sel({ markets: ['Queens'] })).map(s => s.store_code)).toEqual(['B-1598'])
  })

  it('narrows to the stores a picked rep works, and keeps only that rep', () => {
    const out = filterPlan(TENANT_A, sel({ reps: ['Khan, Abid'] }))
    expect(out.map(s => s.store_code)).toEqual(['B-1598'])
    expect(out[0].reps.map(r => r.rep)).toEqual(['Khan, Abid'])
  })

  it('a cross-store rep view keeps the rep\'s own items and drops the store-wide ones', () => {
    // A store-wide figure is a cross-rep figure: it tells one rep what the rest of the shop is
    // leaving on the table. The peer-gap item compares STORES and names no colleague, so it stays.
    const withPeer: PlanStore[] = [{
      ...A_LEFFERTS,
      items: [...A_LEFFERTS.items,
        { severity: 'warning', metric: 'Peer gap', title: 'Behind comparable stores', detail: 'see Peer Sales Comparison.' }],
    }]
    const out = filterPlan(withPeer, sel({ reps: ['Khan, Abid'] }))
    expect(out[0].items.map(i => i.title)).toEqual(['Behind comparable stores'])
    expect(out[0].reps[0].items).toHaveLength(1)
  })

  it('a rep picked together with a store keeps that store\'s own items', () => {
    const out = filterPlan(TENANT_A, sel({ stores: ['104-08 Lefferts Blvd'], reps: ['Khan, Abid'] }))
    expect(out[0].items.length).toBe(A_LEFFERTS.items.length)
  })
})

describe('tilesToSnapshot — what the Send bar mails', () => {
  it('mails the tile row with its own words, and an unmeasured tile blank rather than 0', () => {
    const tiles = buildTiles(TENANT_B, planMetrics(TENANT_B), view('kpi'), ALL)
    const rows = tilesToSnapshot(tiles)
    const app = rows.find(r => r.label.startsWith('Carrier App'))!
    expect(app.value).toBeNull()
    expect(app.measured).toBe(false)
    expect(app.note).toMatch(/no recorded value|not measured/i)
    const hs = rows.find(r => r.label.startsWith('Handsets'))!
    expect(hs.value).toBe('1 of 1 behind')
    expect(hs.measured).toBe(false)            // this report compares no two periods
    expect(hs.note).toContain('9 Harbor Rd')
  })
})
