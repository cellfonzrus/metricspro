// Unit proof for `lib/target-axis` — the browser's dereference of the ONE home for "what can have a
// target" (`targets_engine.target_axis`, index §COMMCALC-02).
//
// THE DEFECT THESE EXIST FOR (owner report 2026-11-14, index §CORE-44). The axis was asked for at
// `/commcalc/targets/{period}/axis`, with no `/api/v1` prefix. `apiUrl()` prefixes NOTHING — in the
// browser the site proxies the path as given — so the request reached the Next app, which has no
// page of that name, and 404'd. The catch then answered with the four built-in categories. Every
// target screen therefore showed exactly four columns and looked correct, while every category the
// tenant had declared was invisible: LuxeLink's thirteen, the house's four. No target for one could
// be typed, so `commcalc.target_line` held ZERO rows in the whole database, for every tenant, for
// every month.
//
// So two things are pinned here, and the second is the one that mattered:
//   1. the address — the path asked for must be one the site actually proxies;
//   2. that a FAILED read is REPORTED and not indistinguishable from the tenant's real answer. The
//      fall back to the built-ins stays (a blank target screen would be a regression); what changes
//      is that `failed` says it is a fall back, so a screen whose job is setting targets can too.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const calls: string[] = []
let impl: (path: string) => Promise<any> = async () => ({ axis: [] })

vi.mock('@/lib/client', () => ({
  ORG_ID: 'org-1',
  api: (path: string) => { calls.push(path); return impl(path) },
}))

const DECLARED = {
  axis: [
    { key: 'activations', label: 'Activations', unit: 'count', store: 'column', source: 'sales_feed' },
    { key: 'new_line_count', label: 'New Lines', unit: 'count', store: 'line', source: 'declared' },
    { key: 'tender:cash', label: 'Cash', unit: 'dollars', store: 'line', source: 'declared' },
  ],
}

// The module caches per period, so each test imports it fresh and uses its own period label.
async function freshModule() {
  vi.resetModules()
  return import('@/lib/target-axis')
}

beforeEach(() => { calls.length = 0; impl = async () => DECLARED })
afterEach(() => { vi.restoreAllMocks() })

describe('the address it asks for', () => {
  it('asks a path the site proxies — the prefix IS the address', async () => {
    const m = await freshModule()
    await m.fetchTargetAxisResult('November 2026')
    expect(calls).toHaveLength(1)
    expect(calls[0].startsWith('/api/v1/')).toBe(true)
    expect(calls[0]).toContain('/commcalc/targets/November%202026/axis')
  })

  it('the shipped defect shape is NOT what it asks (armed negative control)', async () => {
    const m = await freshModule()
    await m.fetchTargetAxisResult('November 2026')
    expect(calls[0].startsWith('/commcalc/')).toBe(false)
  })
})

describe('a failed read is not an answer', () => {
  it('reports failed, and still returns the built-ins so no screen goes blank', async () => {
    const m = await freshModule()
    impl = async () => { throw Object.assign(new Error('Not Found'), { status: 404 }) }
    const r = await m.fetchTargetAxisResult('November 2026')
    expect(r.failed).toBe(true)
    // Compared against the home's own floor, never a list restated here — a second copy in a test
    // is still a second copy, and `harness_target_axis_lock.py` §D2 is right to say so.
    expect(r.cats).toEqual([...m.BUILTIN_CATS])
    // ...and NOTHING declared is claimed, which is the honest answer when nothing was read.
    expect(m.declaredCats(r.cats)).toEqual([])
  })

  it('never caches a failure — a blip does not become a session of wrong answers', async () => {
    const m = await freshModule()
    impl = async () => { throw new Error('boom') }
    expect((await m.fetchTargetAxisResult('November 2026')).failed).toBe(true)
    impl = async () => DECLARED
    const good = await m.fetchTargetAxisResult('November 2026')
    expect(good.failed).toBe(false)
    expect(good.cats.map((c) => c.key)).toContain('new_line_count')
    expect(calls).toHaveLength(2)
  })

  it('a real answer is NOT reported as failed, and is cached once per period', async () => {
    const m = await freshModule()
    const a = await m.fetchTargetAxisResult('November 2026')
    const b = await m.fetchTargetAxisResult('November 2026')
    expect([a.failed, b.failed]).toEqual([false, false])
    expect(calls).toHaveLength(1)
    await m.fetchTargetAxisResult('October 2026')
    expect(calls).toHaveLength(2)   // a tenant can declare a category mid-month
  })
})

describe('the declared half', () => {
  it('names exactly the categories whose target is a target_line row', async () => {
    const m = await freshModule()
    const r = await m.fetchTargetAxisResult('November 2026')
    expect(m.declaredCats(r.cats).map((c) => c.key)).toEqual(['new_line_count', 'tender:cash'])
  })

  it('an axis that comes back EMPTY falls back rather than rendering no categories', async () => {
    const m = await freshModule()
    impl = async () => ({ axis: [] })
    const r = await m.fetchTargetAxisResult('November 2026')
    expect(r.cats).toEqual([...m.BUILTIN_CATS])
    // An empty-but-successful read is not a failure: the server answered.
    expect(r.failed).toBe(false)
  })
})
