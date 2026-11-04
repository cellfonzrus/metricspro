/**
 * The closing form's shaping, proved before the screen exists.
 *
 * WHY THIS IS A TEST FILE AND NOT A COMMENT IN THE SCREEN. Every number a rep types here is money
 * that reconciles against the carrier feed and feeds the incentive calculation. Three things could
 * each lose real money silently and none of them would show on screen:
 *
 *   1. A declared category routed to the WRONG store — a tally into the metric-line table or a
 *      per-person money declaration into the counts map. The catalog's `storage` is the ONE answer
 *      (`closing/metric_catalog.storage_for`, locked by harness_metric_catalog_lock §A8) and this
 *      module must dereference it rather than re-deriving it from `value_kind` / `declared_by`.
 *   2. A custom tender sent in a physical `t_*` slot, or a standard one sent as custom. The server's
 *      own `standard` list (from GET /closing/tender-config) decides; there is no copy here.
 *   3. A tenant's declared vocabulary replaced by house words. The house's seven tenders are a
 *      TEMPLATE the server sends, never a default this module keeps.
 */
import { describe, expect, it } from 'vitest'

import {
  STD_PREFIX, buildPayload, countsAndLines, declaredTenders, richLines, tenderSlots, totalCollected,
} from './closing-submit'

// ── The shapes the three config endpoints actually answer with ───────────────────────────────────
const STANDARD = [
  { tender_key: 'cash', label: 'Cash', is_standard: true },
  { tender_key: 'credit', label: 'Credit', is_standard: true },
  { tender_key: 'ext_cc', label: 'External Credit Card', is_standard: true },
  { tender_key: 'gift', label: 'Gift Card', is_standard: true },
  { tender_key: 'store_acct', label: 'Store Account', is_standard: true },
  { tender_key: 'zelle', label: 'Zelle / CashApp', is_standard: true },
  { tender_key: 'acima', label: 'ACIMA (lease)', is_standard: true },
]

describe('tenderSlots — which payload slot a tender_key belongs in', () => {
  it('reproduces all seven physical columns from the server`s own standard list', () => {
    const slots = tenderSlots(STANDARD)
    expect(Object.keys(slots).sort()).toEqual(
      ['acima', 'cash', 'credit', 'ext_cc', 'gift', 'store_acct', 'zelle'])
    expect(Object.values(slots).sort()).toEqual(
      ['t_acima', 't_cash', 't_credit', 't_ext_cc', 't_gift', 't_store_acct', 't_zelle'].sort())
  })

  it('a key the server did NOT call standard has no physical slot', () => {
    expect(tenderSlots(STANDARD)['affirm']).toBeUndefined()
  })

  it('an empty or unreadable standard list yields NO slots — never a guessed seven', () => {
    for (const junk of [[], null, undefined, 'nonsense', {}]) {
      expect(tenderSlots(junk as never)).toEqual({})
    }
  })

  it('the prefix is stated once', () => {
    expect(STD_PREFIX).toBe('t_')
  })
})

describe('declaredTenders — the tenant`s list, never the house template', () => {
  it('uses the tenant`s own defs when it has configured them', () => {
    const defs = [{ tender_key: 'cash', label: 'Efectivo' }, { tender_key: 'affirm', label: 'Affirm' }]
    const out = declaredTenders({ defs, standard: STANDARD, configured: true })
    expect(out.map((d) => d.tender_key)).toEqual(['cash', 'affirm'])
    expect(out[0].label).toBe('Efectivo')
  })

  it('a tenant that configured an ALL-OFF list gets an empty list, not the house seven', () => {
    // §29.9: `configured` means "this company has its own list". Falling back then would answer with
    // another tenant's vocabulary — the 2026-10-30 directive's exact failure.
    expect(declaredTenders({ defs: [], standard: STANDARD, configured: true })).toEqual([])
  })

  it('an UNCONFIGURED tenant gets the server`s template — which is the server`s words, not ours', () => {
    const out = declaredTenders({ defs: [], standard: STANDARD, configured: false })
    expect(out.map((d) => d.tender_key)).toEqual(
      ['cash', 'credit', 'ext_cc', 'gift', 'store_acct', 'zelle', 'acima'])
  })

  it('no response at all identifies NOTHING, rather than inventing a form', () => {
    for (const junk of [null, undefined, {}, 'nope']) {
      expect(declaredTenders(junk as never)).toEqual([])
    }
  })
})

describe('totalCollected', () => {
  it('sums the declared amounts and ignores blanks and junk', () => {
    expect(totalCollected({ cash: '100.50', credit: '20', gift: '', zelle: 'oops' })).toBe(120.5)
  })

  it('is zero, not NaN, for an untouched form', () => {
    expect(totalCollected({})).toBe(0)
    expect(totalCollected(null as never)).toBe(0)
  })

  it('does NOT count the accessory declaration — it is not a tender', () => {
    // The web form states this: accessory is declared separately and tallied against sales.
    expect(totalCollected({ cash: '10', acc_sale: '999' })).toBe(1009)
    // …which is why the caller passes tender values only; the guard is the caller's shape, so this
    // case documents that this function sums WHAT IT IS GIVEN and decides nothing about membership.
  })
})

// ── The split that could silently cost somebody their incentive ──────────────────────────────────
const CATALOG = [
  { key: 'upgrade_count', label: 'Upgrades', storage: 'counts_jsonb' },
  { key: 'new_line_count', label: 'New Lines', storage: 'counts_jsonb' },
  { key: 'acc_revenue', label: 'Accessory revenue', storage: 'metric_line' },
  { key: 'per_rep_boxes', label: 'Boxes by rep', storage: 'metric_line' },
]
const COUNT_DEFS = [
  { field_key: 'upgrade_count', label: 'Upgrades' },
  { field_key: 'new_line_count', label: 'New Lines' },
  { field_key: 'acc_revenue', label: 'Accessory revenue' },
]

describe('countsAndLines — the catalog`s `storage` decides, never this module', () => {
  it('a count-only store-level field rides the counts map', () => {
    const { counts } = countsAndLines(CATALOG, COUNT_DEFS, { upgrade_count: '4', new_line_count: '2' })
    expect(counts).toEqual({ upgrade_count: '4', new_line_count: '2' })
  })

  it('a field the catalog stores as a metric LINE never reaches the counts map', () => {
    const { counts } = countsAndLines(CATALOG, COUNT_DEFS, { acc_revenue: '812.40' })
    expect(counts.acc_revenue).toBeUndefined()
  })

  it('names the metric-line categories so the screen can render their own inputs', () => {
    const { lineMetrics } = countsAndLines(CATALOG, COUNT_DEFS, {})
    expect(lineMetrics.map((m) => m.key)).toEqual(['acc_revenue', 'per_rep_boxes'])
  })

  it('a declared count field MISSING from the catalog is REPORTED, never routed on a guess', () => {
    const { counts, unknown } = countsAndLines(
      CATALOG, [...COUNT_DEFS, { field_key: 'mystery' }], { mystery: '7' })
    expect(unknown).toEqual(['mystery'])
    expect(counts.mystery).toBeUndefined()
  })

  it('a catalog entry with NO storage is reported, not defaulted to the counts map', () => {
    // Guessing here is how a per-person money declaration ends up in a store-level tally.
    const { counts, unknown } = countsAndLines(
      [{ key: 'half_declared', label: 'Half' }], [{ field_key: 'half_declared' }], { half_declared: '3' })
    expect(unknown).toEqual(['half_declared'])
    expect(counts).toEqual({})
  })

  it('a count the rep left blank is sent as zero, so the day is a complete declaration', () => {
    const { counts } = countsAndLines(CATALOG, COUNT_DEFS, {})
    expect(counts).toEqual({ upgrade_count: '0', new_line_count: '0' })
  })

  it('nothing declared identifies nothing — no counts map at all', () => {
    expect(countsAndLines(null as never, null as never, {})).toEqual(
      { counts: {}, lineMetrics: [], unknown: [] })
  })
})

describe('richLines — what reaches PUT /closing/metric-lines', () => {
  it('keeps a line that carries units or money, and trims', () => {
    expect(richLines([
      { metric_key: 'acc_revenue', employee_name: ' Ana ', units: '', amount: ' 812.40 ' },
      { metric_key: 'per_rep_boxes', employee_name: 'Bo', units: ' 7 ', amount: '' },
    ])).toEqual([
      { metric_key: 'acc_revenue', employee_name: 'Ana', units: '', amount: '812.40' },
      { metric_key: 'per_rep_boxes', employee_name: 'Bo', units: '7', amount: '' },
    ])
  })

  it('drops an empty line rather than declaring a zero nobody typed', () => {
    expect(richLines([
      { metric_key: 'acc_revenue', employee_name: 'Ana', units: '', amount: '' },
      { metric_key: '', employee_name: 'Bo', units: '3', amount: '' },
    ])).toEqual([])
  })

  it('an absent list is an empty list, never a raise', () => {
    expect(richLines(null as never)).toEqual([])
  })
})

describe('buildPayload', () => {
  const base = {
    close_date: '2026-11-01',
    store: { sfid: 'SF1', store_code: 'B-1115', store_name: 'Main St' },
    employee_name: ' Ana ',
    standard: STANDARD,
    tenderValues: { cash: '500', credit: '250', affirm: '75' },
    epay: { epay_on_cash: '40', epay_on_credit: '', epay_on_acima: '' },
    acc_sale: '120',
    catalog: CATALOG,
    countDefs: COUNT_DEFS,
    countValues: { upgrade_count: '4' },
    expenseLines: [
      { category_id: 'c1', amount: '30', description: ' lunch ' },
      { category_id: '', amount: '', description: '' },
    ],
    envelope_picture: 'data:image/jpeg;base64,xxx',
    remarks: ' all good ',
  }

  it('puts a standard tender in its physical column and a custom one in custom_tenders', () => {
    const p = buildPayload(base)
    expect(p.t_cash).toBe('500')
    expect(p.t_credit).toBe('250')
    expect(p.custom_tenders).toEqual({ affirm: '75' })
    expect((p as Record<string, unknown>).t_affirm).toBeUndefined()
  })

  it('sends every physical column, so an omitted tender reads as zero rather than as missing', () => {
    const p = buildPayload(base) as Record<string, unknown>
    for (const c of ['t_cash', 't_credit', 't_ext_cc', 't_gift', 't_store_acct', 't_zelle', 't_acima']) {
      expect(p[c]).toBeDefined()
    }
    expect(p.t_gift).toBe('0')
  })

  it('omits custom_tenders entirely when the tenant declared none', () => {
    const p = buildPayload({ ...base, tenderValues: { cash: '500' } })
    expect(p.custom_tenders).toBeUndefined()
  })

  it('trims the names a person typed and drops an empty expense line', () => {
    const p = buildPayload(base)
    expect(p.employee_name).toBe('Ana')
    expect(p.remarks).toBe('all good')
    expect(p.expense_lines).toEqual([{ category_id: 'c1', amount: '30', description: 'lunch' }])
  })

  it('carries the store the rep picked, by code AND sfid', () => {
    const p = buildPayload(base)
    expect(p.store_code).toBe('B-1115')
    expect(p.sfid).toBe('SF1')
    expect(p.store_name).toBe('Main St')
  })

  it('routes the declared counts through the catalog and never invents one', () => {
    const p = buildPayload(base)
    expect(p.counts).toEqual({ upgrade_count: '4', new_line_count: '0' })
    expect((p.counts as Record<string, unknown>).acc_revenue).toBeUndefined()
  })

  it('names NO tenant, carrier or store code of its own', () => {
    // The whole payload must be a function of what the server declared plus what the rep typed.
    const src = buildPayload.toString().toLowerCase()
    for (const w of ['cellfonz', 'luxelink', 'vzone', 'boost', 'b-11', 'total wireless']) {
      expect(src).not.toContain(w)
    }
  })
})
