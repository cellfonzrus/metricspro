'use client'
// "Print each store" on the P&L page (owner 2026-10-01: "i need to print the report for each stores p&l
// individually after selecting from the p&l menu"). Index §4d.
//
// Which stores: the ones picked in the page's Store filter — or, when none is picked, every store the page's
// filter + company scope covers (a market pick, or the whole company). The SERVER resolves that list (the
// same canonical store/market matcher + company scope the page's own read uses — `filtered_stores`), so the
// printout can never name a store the page would not show.
// Each store's numbers: ONE call to the single-month P&L read with ONLY that store in the filter — exactly
// what the page shows when that store alone is selected (`plQuery`, the page's own query builder). This
// component reads the page's ONE month; it never loops months and adds up nothing.
import { useMemo, useState } from 'react'
import { api, ORG_ID } from '@/lib/client'
import ReportExportBar from '@/components/ReportExportBar'
import { perStorePayload, plQuery, type StoreResult } from './plStatement'

const PARALLEL = 4            // a few reads at a time — polite to the API, quick for a 30-store company

export default function PLPerStorePrint({ period, scope, companyLabel, stores, markets, allStores, storeMarket }: {
  period: string
  scope: string                         // the company / store scope dropdown
  companyLabel: string
  stores: string[]                      // the Store filter (empty = no store picked)
  markets: string[]                     // the Market filter
  allStores: string[]                   // every store with a P&L this period (the Store filter's options)
  storeMarket: Record<string, string>   // store → market, for the page header
}) {
  const [open, setOpen] = useState(false)
  const key = `${period}|${scope}|${stores.join('|')}|${markets.join('|')}`
  // results / error are tagged with the selection they were prepared for: change the month, company or
  // store filter and they no longer apply (the panel goes back to "Prepare") — no stale printout.
  const [prepared, setPrepared] = useState<{ key: string; results: StoreResult[] } | null>(null)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState('')
  const [error, setError] = useState<{ key: string; msg: string } | null>(null)
  const results = prepared?.key === key ? prepared.results : null
  const err = error?.key === key ? error.msg : ''
  const setErr = (msg: string) => setError({ key, msg })

  async function prepare() {
    setBusy(true); setError(null); setPrepared(null)
    try {
      // 1. which stores — resolved by the server exactly as the page's filter resolves them
      const pick = stores.length > 0 ? stores : allStores
      if (pick.length === 0) { setErr('No store has a P&L for this period.'); return }
      const scopeRead: any = await api(plQuery(period, scope, pick, markets, ORG_ID))
      if (!scopeRead?.computed) { setErr('The P&L is not computed for this period — compute statements first.'); return }
      const list: string[] = scopeRead.filtered_stores || []
      if (list.length === 0) { setErr('None of the selected stores is in this company for this period.'); return }
      // 2. each store alone, a few at a time, kept in the list's order
      const out: StoreResult[] = list.map(s => ({ store: s, market: storeMarket[s] }))
      let next = 0, done = 0
      setProgress(`0 / ${list.length}`)
      const worker = async () => {
        while (next < list.length) {
          const i = next++
          try {
            out[i].data = await api(plQuery(period, scope, [list[i]], [], ORG_ID))
          } catch (e: any) {
            out[i].error = String(e?.message || e)
          }
          done++
          setProgress(`${done} / ${list.length}`)
        }
      }
      await Promise.all(Array.from({ length: Math.min(PARALLEL, list.length) }, worker))
      setPrepared({ key, results: out })
    } catch (e: any) {
      setErr(String(e?.message || e))
    } finally { setBusy(false) }
  }

  const payload = useMemo(() => (results ? perStorePayload(results, period, companyLabel) : null),
    [results, period, companyLabel])
  const who = stores.length > 0 ? `the ${stores.length} store(s) picked in the Store filter`
    : markets.length > 0 ? `every store in ${markets.join(', ')}` : `every store in ${companyLabel}`

  if (!open) {
    return (
      <button className="btn btn-secondary" style={{ fontSize: 12, padding: '5px 10px' }} onClick={() => setOpen(true)}
        title="Print (or save as PDF) each store's P&L on its own page">
        🖨️ Print each store
      </button>
    )
  }
  return (
    <div style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', padding: '4px 8px',
      border: '1px solid var(--border)', borderRadius: 8 }}>
      <span style={{ fontSize: 12, color: 'var(--text2)' }}>🖨️ One page per store — {who}</span>
      {!results && (
        <button className="btn btn-primary" style={{ fontSize: 12, padding: '5px 10px' }} disabled={busy} onClick={prepare}>
          {busy ? `⏳ Loading ${progress}` : 'Prepare'}
        </button>
      )}
      {results && payload && (
        <>
          <span style={{ fontSize: 12, color: 'var(--text2)' }}>
            {results.length} store(s) ready{results.some(r => r.error) ? ` · ${results.filter(r => r.error).length} could not load (their page says so)` : ''}
          </span>
          <ReportExportBar title={payload.title} subtitle={payload.subtitle} filename={payload.filename}
            sheets={payload.sheets} />
        </>
      )}
      <button className="btn btn-secondary" style={{ fontSize: 12, padding: '5px 8px' }} onClick={() => setOpen(false)}
        aria-label="Close print each store">✕</button>
      {err && <div style={{ flexBasis: '100%', fontSize: 12, color: '#dc2626' }}>{err}</div>}
    </div>
  )
}
