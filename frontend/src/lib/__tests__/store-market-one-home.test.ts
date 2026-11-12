// THE LOCK: the browser's store→market answer has ONE home, and this fails the build if it grows a
// second one (index §CORE-43; the house rule is "lock it so it cannot un-wire", owner 2026-09-20).
//
// Without this test the fix is temporary by construction. The private map it bans —
//
//     const m: Record<string, string> = {}
//     for (const s of stores) if (s.store_code) m[s.store_code] = s.market || ''
//
// — was copied into TWENTY-FOUR screens over four months, each time by someone doing the obvious
// thing, and each copy compared a roster code to a free-text store string letter for letter. It cost
// LuxeLink's October payroll report $44,403.09 of market attribution (owner, 2026-11-12). Writing the
// one home without a check that callers keep dereferencing it is the failure mode this repo has
// already hit three times (§19.18).
//
// IT SCANS SOURCE TEXT, deliberately: a type signature cannot stop someone building a plain object,
// and this is the cheapest check that runs on every PR (the `frontend-tests` job, .github/workflows/
// tests.yml). Both rules below carry a NEGATIVE CONTROL proving they can go red — a scan that cannot
// fail reports a clean codebase forever.
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

// `process.cwd()` is vitest's root, i.e. `frontend/` (vitest.config.ts) — `import.meta.url` is not a
// file URL under the jsdom environment this suite runs in.
const SRC = join(process.cwd(), 'src')
const THIS_MODULE = 'lib/store-market.ts'

function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) { sources(p, out); continue }
    if (/\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name)) out.push(p)
  }
  return out
}

const FILES = sources(SRC).map(p => ({ path: relative(SRC, p), text: readFileSync(p, 'utf8') }))

/** A private store→market map: a `[something.store_code]` (or `[s.store]`) assignment whose value is
 *  a market. Written as one regex over the whole file so a reformatted copy is caught too. */
const PRIVATE_MAP = /\[\s*\w+\.(store_code|store|home_store)\s*\]\s*=\s*\w*\.?market\b/

/** The ladder this module replaced: an inline leading-street-number fallback for a market. */
const PRIVATE_NUMBER_LADDER = /mktByNum|marketByNum/

describe('no screen builds its own store → market map', () => {
  it('every market answer dereferences lib/store-market', () => {
    const offenders = FILES
      .filter(f => f.path !== THIS_MODULE)
      .filter(f => PRIVATE_MAP.test(f.text))
      .map(f => f.path)
    expect(offenders).toEqual([])
  })

  it('and no screen carries its own street-number market ladder', () => {
    const offenders = FILES
      .filter(f => f.path !== THIS_MODULE)
      .filter(f => PRIVATE_NUMBER_LADDER.test(f.text))
      .map(f => f.path)
    expect(offenders).toEqual([])
  })

  it('negative control — the banned shapes really do match', () => {
    expect(PRIVATE_MAP.test("for (const s of stores) if (s.store_code) m[s.store_code] = s.market || ''")).toBe(true)
    expect(PRIVATE_MAP.test("stores.forEach(s => { m[s.store_code] = s.market })")).toBe(true)
    expect(PRIVATE_MAP.test("m[row.home_store] = row.market")).toBe(true)
    expect(PRIVATE_MAP.test("m[ s.store ] = s.market")).toBe(true)
    expect(PRIVATE_NUMBER_LADDER.test('const mktByNum: Record<string, string> = {}')).toBe(true)
    // and do NOT match the legitimate shapes the app still uses
    expect(PRIVATE_MAP.test("m[s.store_code] = s.address || ''")).toBe(false)
    expect(PRIVATE_MAP.test("const market = storeMarket.marketOf(r.store)")).toBe(false)
  })
})

describe('the screens that answer "which market" actually call the one home', () => {
  // Named rather than counted: a page dropped from this list is a page that stopped dereferencing.
  const MUST_DEREFERENCE = [
    'app/(platform)/storeops/payroll/page.tsx',
    'app/(platform)/storeops/payroll-tax/page.tsx',
    'app/(platform)/storeops/payroll-change-log/page.tsx',
    'app/(platform)/storeops/salary-advances/page.tsx',
    'app/(platform)/storeops/employees/page.tsx',
    'app/(platform)/storeops/schedule/page.tsx',
    'app/(platform)/storeops/timeoff/page.tsx',
    'app/(platform)/storeops/shift-extensions/page.tsx',
    'app/(platform)/storeops/hours-budget/page.tsx',
    'app/(platform)/storeops/reports/page.tsx',
    'app/(platform)/storeops/attendance/page.tsx',
    'app/(platform)/storeops/timeclock/page.tsx',
    'app/(platform)/storeops/accountability/page.tsx',
    'app/(platform)/hr/employee-database/page.tsx',
    'app/(platform)/closing/accessory-recon/page.tsx',
    'app/(platform)/closing/bank-deposit/page.tsx',
    'app/(platform)/closing/deposit-recon/page.tsx',
    'app/(platform)/closing/epay-recon/page.tsx',
    'app/(platform)/closing/expenses-report/page.tsx',
    'app/(platform)/closing/tender-recon-3way/page.tsx',
    'app/(platform)/accounts/pl/page.tsx',
    'app/(platform)/accounts/journal/page.tsx',
    'app/(platform)/accounts/balance-sheet/page.tsx',
  ]
  it.each(MUST_DEREFERENCE)('%s imports buildStoreMarket', (rel) => {
    const f = FILES.find(x => x.path === rel)
    expect(f, `${rel} is gone — move it off this list in the same PR`).toBeTruthy()
    expect(f!.text).toContain("from '@/lib/store-market'")
    expect(f!.text).toMatch(/buildStoreMarket\s*\(/)
  })
})
