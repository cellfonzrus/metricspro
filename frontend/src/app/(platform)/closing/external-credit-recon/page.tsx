'use client'
import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { api, fmt, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import type { EntityOption } from '@/components/EntityPicker'
import type { StandardFilterValue } from '@/lib/standard-filters'

// Card Settlement Recon — OWNER DIRECTIVE 2026-09-04, verbatim: "we need to pull in data from the
// merchants from both pos merchant provider and the external credit card provider … need to scrape
// the reports on a daily basis and tally with our platform as entered by the employees."
//
// One row per (store, day, processor role): what the employee DECLARED at closing vs what the
// processor actually SETTLED, the variance, and a short/over/match verdict. Reads
// GET /closing/external-credit-recon (RULE FIVE standard filters, span-scoped and
// market-manager-gated server-side); the verdict is the mig-936 envelope truth table reused
// server-side, so this page renders it and never re-computes a status of its own.
//
// RULE TWO: no processor BRAND appears here. The external field's name and each leg's title come
// from the payload (`role_titles`, resolved through the mig-960 label preset), so a tenant that
// calls the terminal something else sees its own word on screen and in the export.
const NO_MARKET = '(no market)'
const csv = (a: string[]) => (a.length ? a.join(',') : undefined)

// Status → how it reads. The three verdicts, then the HONEST GAPS — a gap is deliberately styled
// as "no evidence", never as a balanced day.
const STATUS_BADGE: Record<string, { label: string; color: string }> = {
  short: { label: '🔻 Short', color: '#c0392b' },
  over: { label: '🔺 Over', color: '#b7791f' },
  match: { label: '✅ Match', color: '#237a4b' },
  no_processor_data: { label: '— No processor data', color: 'var(--text3)' },
  no_declared_data: { label: '— Nothing declared', color: 'var(--text3)' },
  dm_merged: { label: '⚠️ DM total not split', color: '#b7791f' },
}
const BASIS_NOTE: Record<string, string> = {
  rep: 'as entered by the employee',
  dm: 'DM-corrected split',
  dm_merged: 'the DM corrected the card total without stating the external portion',
}

export default function CardSettlementReconPage() {
  const today = localToday()
  const [filt, setFilt] = useState<StandardFilterValue>({ period: today.slice(0, 8) + '01', periodTo: today, stores: [], markets: [], reps: [] })
  const [status, setStatus] = useState('')
  const [role, setRole] = useState('')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const reqRef = useRef(0)

  // Store options: the canonical org-scoped roster (pick-don't-type) — the same source DM Verify
  // and the Envelope Report use, never a second roster read.
  const [pStores, setPStores] = useState<any[]>([])
  useEffect(() => {
    apiCached('/api/v1/closing/stores', LOOKUP).then((s: any) => setPStores(Array.isArray(s) ? s : [])).catch(() => {})
  }, [])
  const storeOptions: EntityOption[] = useMemo(
    () => pStores.filter((s: any) => s.store_code).map((s: any) => ({ id: s.store_code, label: s.store_address || s.store_code, sublabel: s.market || undefined })),
    [pStores])

  const load = useCallback(() => {
    const myReq = ++reqRef.current
    setLoading(true); setErr('')
    const qs = new URLSearchParams()
    const from = filt.period || localToday()
    qs.set('date_from', from)
    qs.set('date_to', filt.periodTo || from)
    const s = csv(filt.stores); if (s) qs.set('stores', s)
    const m = csv(filt.markets); if (m) qs.set('markets', m)
    if (status) qs.set('status', status)
    if (role) qs.set('role', role)
    api(`/api/v1/closing/external-credit-recon?${qs.toString()}`)
      .then(d => { if (reqRef.current === myReq) setData(d) })
      .catch(e => { if (reqRef.current === myReq) { setErr(e?.message || String(e)); setData(null) } })
      .finally(() => { if (reqRef.current === myReq) setLoading(false) })
  }, [filt, status, role])
  useEffect(() => { load() }, [load])

  const rows: any[] = data?.rows || []
  const t = data?.totals || {}
  // §13c ENUMERATION doctrine: the market list comes from the SERVER's canonical composition
  // (core.scope.org_market_options — the org's whole vocabulary ∪ this report's own stamps), NOT
  // from the loaded store roster. A market that lives on only one vocabulary, or that belongs to a
  // settlement-only store with no roster row, is therefore still selectable here. The "(no market)"
  // sentinel is appended by the page, per the doctrine. Falls back to the roster only if the
  // payload has not arrived yet, so the filter is never empty on first paint.
  const marketOptions: EntityOption[] = useMemo(() => {
    const canonical: string[] = data?.market_options
      || Array.from(new Set(pStores.map((s: any) => s.market).filter(Boolean))).sort()
    return [...canonical.map((m: string) => ({ id: m, label: m })), { id: NO_MARKET, label: NO_MARKET }]
  }, [data, pStores])
  const titles: Record<string, string> = data?.role_titles || {}
  const roleTitle = (r: string) => titles[r] || r
  const feeds: Record<string, any> = data?.feeds || {}
  const gaps = (t.no_processor_data || 0) + (t.no_declared_data || 0) + (t.dm_merged || 0)

  const columns: ExportColumn[] = useMemo(() => [
    { header: 'Date', field: 'close_date', type: 'date', role: 'date', get: (r: any) => r.close_date },
    { header: 'Store', field: 'store_address', role: 'store', get: (r: any) => r.store_address || r.store_code },
    { header: 'Market', field: 'market', get: (r: any) => r.market },
    { header: 'Processor', field: 'processor_role', get: (r: any) => roleTitle(r.processor_role) },
    { header: 'Declared at closing $', field: 'declared_amount', money: true, get: (r: any) => r.declared_amount },
    { header: 'Declared basis', field: 'declared_basis', get: (r: any) => BASIS_NOTE[r.declared_basis] || r.declared_basis },
    { header: 'Processor settled $', field: 'settled_amount', money: true, get: (r: any) => r.settled_amount },
    { header: 'Variance $', field: 'variance', money: true, get: (r: any) => r.variance },
    { header: 'Status', field: 'status', get: (r: any) => STATUS_BADGE[r.status]?.label || r.status },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [titles])

  const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
  const tile: React.CSSProperties = { padding: '10px 14px', borderRadius: 10, border: '1px solid var(--border)', background: 'var(--surface)', minWidth: 140 }
  const th: React.CSSProperties = { textAlign: 'left', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }
  const td: React.CSSProperties = { padding: '7px 10px', borderBottom: '1px solid var(--border)' }
  const num: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>💳 Card Settlement Recon</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            What the store declared at closing, tallied against what each card processor actually settled — per store, per day.
          </p>
        </div>
        {!loading && rows.length > 0 && (
          <ReportExportBar
            title="Card Settlement Recon"
            subtitle={`${filt.period} → ${filt.periodTo || filt.period}${status ? ` · ${status}` : ''}`}
            filename={`card-settlement-recon_${filt.period}_${filt.periodTo || filt.period}`}
            sheets={[{ name: 'Settlement Recon', columns, rows }]}
          />
        )}
      </div>

      <StandardFilterBar
        value={filt} onChange={setFilt}
        periodMode="range"
        storeOptions={storeOptions} marketOptions={marketOptions}
        storeLabel="Stores…" marketLabel="Markets…"
        right={(
          <>
            <select style={sel} value={role} onChange={e => setRole(e.target.value)}>
              <option value="">Both processors</option>
              {(data?.roles || ['external_cc', 'pos_merchant']).map((r: string) => (
                <option key={r} value={r}>{roleTitle(r)}</option>
              ))}
            </select>
            <select style={sel} value={status} onChange={e => setStatus(e.target.value)}>
              <option value="">All store-days</option>
              <option value="variance">Variances (short + over)</option>
              <option value="short">Short only</option>
              <option value="over">Over only</option>
              <option value="match">Match only</option>
              <option value="gap">Without evidence (gaps)</option>
              <option value="no_processor_data">No processor data</option>
              <option value="dm_merged">DM total not split</option>
            </select>
          </>
        )}
      />

      {/* Summary tiles. Gaps are counted separately and contribute NO dollars — a missing scrape
          must never read as a balanced day. */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', margin: '14px 0' }}>
        <div style={tile}><div style={{ fontSize: 12, color: 'var(--text3)' }}>Store-days</div><div style={{ fontSize: 20, fontWeight: 700 }}>{t.cells ?? 0}</div></div>
        <div style={tile}><div style={{ fontSize: 12, color: 'var(--text3)' }}>Declared</div><div style={{ fontSize: 20, fontWeight: 700 }}>{fmt(t.declared_total || 0)}</div></div>
        <div style={tile}><div style={{ fontSize: 12, color: 'var(--text3)' }}>Processor settled</div><div style={{ fontSize: 20, fontWeight: 700 }}>{fmt(t.settled_total || 0)}</div></div>
        <div style={tile}><div style={{ fontSize: 12, color: 'var(--text3)' }}>Short</div><div style={{ fontSize: 20, fontWeight: 700, color: '#c0392b' }}>{t.short ?? 0} · {fmt(t.short_total || 0)}</div></div>
        <div style={tile}><div style={{ fontSize: 12, color: 'var(--text3)' }}>Over</div><div style={{ fontSize: 20, fontWeight: 700, color: '#b7791f' }}>{t.over ?? 0} · {fmt(t.over_total || 0)}</div></div>
        <div style={tile}><div style={{ fontSize: 12, color: 'var(--text3)' }}>Matched</div><div style={{ fontSize: 20, fontWeight: 700, color: '#237a4b' }}>{t.match ?? 0}</div></div>
        <div style={tile}><div style={{ fontSize: 12, color: 'var(--text3)' }}>Without evidence</div><div style={{ fontSize: 20, fontWeight: 700 }}>{gaps}</div></div>
      </div>

      {/* Feed honesty: say plainly when a processor's daily pull has not registered or has not
          landed for the window, instead of rendering zeros that look reconciled. */}
      {!loading && Object.entries(feeds).some(([, f]: any) => !f.registered) && (
        <div className="card" style={{ padding: '8px 12px', marginBottom: 12, fontSize: 12, background: '#fff8e6', border: '1px solid #f3d98b' }}>
          ⚠️ No daily settlement feed is registered yet for{' '}
          {Object.entries(feeds).filter(([, f]: any) => !f.registered).map(([r]) => roleTitle(r)).join(' · ')}
          {' '}— those store-days show “no processor data” rather than a zero.
        </div>
      )}
      {!loading && (data?.unmapped_count || 0) > 0 && (
        <div className="card" style={{ padding: '8px 12px', marginBottom: 12, fontSize: 12, background: '#fff8e6', border: '1px solid #f3d98b' }}>
          ⚠️ {data.unmapped_count} settled row(s) could not be matched to a store — add the terminal’s merchant ID to that store in Store Management so its money is counted.
        </div>
      )}
      {data?.market_filter_skipped && (
        <div className="card" style={{ padding: '8px 12px', marginBottom: 12, fontSize: 12, background: '#fff8e6', border: '1px solid #f3d98b' }}>
          ⚠️ Your market filter could not be applied (store roster unavailable) — showing all markets rather than silently dropping stores.
        </div>
      )}
      {err && <div className="card" style={{ padding: 12, marginBottom: 12, color: '#c0392b' }}>⚠️ {err}</div>}
      {loading && <div style={{ padding: 24, color: 'var(--text3)' }}>Loading…</div>}

      {!loading && !err && (
        <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Date', 'Store', 'Market', 'Processor', 'Declared', 'Basis', 'Settled', 'Variance', 'Status'].map(h => (
                <th key={h} style={th}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {rows.length === 0 && (
                <tr><td colSpan={9} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No store-days in this range.</td></tr>
              )}
              {rows.map((r: any, i: number) => {
                const badge = STATUS_BADGE[r.status] || { label: r.status, color: 'var(--text2)' }
                return (
                  <tr key={`${r.store_code}-${r.close_date}-${r.processor_role}-${i}`}>
                    <td style={td}>{r.close_date}</td>
                    <td style={td}>{r.store_address || r.store_code}</td>
                    <td style={td}>{r.market || NO_MARKET}</td>
                    <td style={td}>{roleTitle(r.processor_role)}</td>
                    <td style={num}>{r.declared_amount === null ? '—' : fmt(r.declared_amount)}</td>
                    <td style={{ ...td, fontSize: 11, color: 'var(--text3)' }}>{BASIS_NOTE[r.declared_basis] || ''}</td>
                    <td style={num}>{r.settled_amount === null ? '—' : fmt(r.settled_amount)}</td>
                    <td style={{ ...num, color: r.variance === null ? 'var(--text3)' : r.variance < 0 ? '#c0392b' : r.variance > 0 ? '#b7791f' : 'inherit' }}>
                      {r.variance === null ? '—' : fmt(r.variance)}
                    </td>
                    <td style={{ ...td, color: badge.color, whiteSpace: 'nowrap' }}>{badge.label}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
