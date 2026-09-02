'use client'
import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { api, fmt, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import type { EntityOption } from '@/components/EntityPicker'
import type { StandardFilterValue } from '@/lib/standard-filters'

// Envelope Report — OWNER DIRECTIVE 2026-09-02, verbatim: "a new report when all the envelopes can
// be filtered by using the standard filters... user can put their comments after counting the
// actual cash marking it short or over and if it is short then checkmark for assigning it to the
// sales rep as a chargeback if the cash is coming back as short - all comments chargebacks or any
// discrepancy over or short must be filterable with the date range with all our filters."
//
// One row per envelope (= one daily_closing rep-day row). Reads GET /closing/envelope-report
// (RULE FIVE standard filters, span-scoped server-side); the count/comment/chargeback save goes to
// POST /closing/envelope-count, which computes short/over server-side (pure envelope_report.py)
// and — for a short envelope with the checkbox ticked — creates a PENDING chargeback on the
// EXISTING ops_chargeback machinery (reason 'envelope_short'); post/waive decisions ride
// POST /closing/envelope-chargeback/decide (management-gated, same as missed-DM-verify).
const NO_MARKET = '(no market)'
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const csv = (a: string[]) => (a.length ? a.join(',') : undefined)
const STATUS_BADGE: Record<string, string> = {
  short: '🔻 Short', over: '🔺 Over', match: '✅ Match', uncounted: '— Uncounted',
}

export default function EnvelopeReportPage() {
  const today = localToday()
  const [filt, setFilt] = useState<StandardFilterValue>({ period: today.slice(0, 8) + '01', periodTo: today, stores: [], markets: [], reps: [] })
  const [status, setStatus] = useState('')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [savingId, setSavingId] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, { counted: string; comment: string; chargeback: boolean }>>({})
  const reqRef = useRef(0)

  // Canonical org-scoped option sources (pick-don't-type) — same sources as DM Verify.
  const [pStores, setPStores] = useState<any[]>([])
  const [pEmps, setPEmps] = useState<any[]>([])
  useEffect(() => {
    apiCached('/api/v1/closing/stores', LOOKUP).then((s: any) => setPStores(Array.isArray(s) ? s : [])).catch(() => {})
    apiCached('/api/v1/storeops/employees?all_company=true', LOOKUP).then((r: any) => setPEmps(Array.isArray(r) ? r : (r?.employees || []))).catch(() => {})
  }, [])
  const storeOptions: EntityOption[] = useMemo(
    () => pStores.filter((s: any) => s.store_code).map((s: any) => ({ id: s.store_code, label: s.store_address || s.store_code, sublabel: s.market || undefined })),
    [pStores])
  const marketOptions: EntityOption[] = useMemo(() => {
    const real = Array.from(new Set(pStores.map((s: any) => s.market).filter(Boolean))).sort()
    return [...real.map((m: string) => ({ id: m, label: m })), { id: NO_MARKET, label: NO_MARKET }]
  }, [pStores])
  const repOptions: EntityOption[] = useMemo(
    () => pEmps.filter((e: any) => (e.name || '').trim()).map((e: any) => ({ id: e.name, label: e.name })),
    [pEmps])

  const load = useCallback(() => {
    const myReq = ++reqRef.current
    setLoading(true); setErr('')
    const qs = new URLSearchParams()
    const from = filt.period || localToday()
    qs.set('date_from', from)
    qs.set('date_to', filt.periodTo || from)
    const s = csv(filt.stores); if (s) qs.set('stores', s)
    const m = csv(filt.markets); if (m) qs.set('markets', m)
    const r = csv(filt.reps); if (r) qs.set('reps', r)
    if (status) qs.set('status', status)
    api(`/api/v1/closing/envelope-report?${qs.toString()}`)
      .then(d => { if (reqRef.current === myReq) setData(d) })
      .catch(e => { if (reqRef.current === myReq) { setErr(e?.message || String(e)); setData(null) } })
      .finally(() => { if (reqRef.current === myReq) setLoading(false) })
  }, [filt, status])
  useEffect(() => { load() }, [load])

  const rows: any[] = data?.rows || []
  const t = data?.totals || {}
  const canDecide = !!data?.can_decide

  const draftFor = (r: any) => drafts[r.closing_row_id] || {
    counted: r.counted_amount != null ? String(r.counted_amount) : '',
    comment: r.comment || '',
    chargeback: !!r.chargeback_id,
  }
  // Every call site passes the FULL draft (seeded from the row's current values via draftFor), so
  // this is a plain replace — a first edit starts from the row's saved count, never from blanks.
  const setDraft = (id: string, full: { counted: string; comment: string; chargeback: boolean }) =>
    setDrafts(d => ({ ...d, [id]: full }))

  async function saveCount(r: any) {
    const d = draftFor(r)
    if (d.counted === '') { alert('Enter the counted cash amount first.'); return }
    setSavingId(r.closing_row_id)
    try {
      await api('/api/v1/closing/envelope-count', {
        method: 'POST',
        body: JSON.stringify({
          closing_row_id: r.closing_row_id,
          counted_amount: d.counted,
          comment: d.comment,
          assign_chargeback: d.chargeback,
        }),
      })
      setDrafts(x => { const y = { ...x }; delete y[r.closing_row_id]; return y })
      load()
    } catch (e: any) {
      alert(e?.message || String(e))
    } finally {
      setSavingId(null)
    }
  }

  async function decide(r: any, decision: 'posted' | 'waived') {
    if (!r.chargeback_id) return
    try {
      await api('/api/v1/closing/envelope-chargeback/decide', {
        method: 'POST', body: JSON.stringify({ id: r.chargeback_id, decision }),
      })
      load()
    } catch (e: any) { alert(e?.message || String(e)) }
  }

  const columns: ExportColumn[] = useMemo(() => [
    { header: 'Date', field: 'close_date', type: 'date', role: 'date', get: (r: any) => r.close_date },
    { header: 'Store', field: 'store_address', role: 'store', get: (r: any) => r.store_address },
    { header: 'Market', field: 'market', get: (r: any) => r.market },
    { header: 'Employee', field: 'employee_name', role: 'rep', get: (r: any) => r.employee_name },
    { header: 'Declared cash $', field: 'declared_cash', money: true, get: (r: any) => r.declared_cash },
    { header: 'Counted $', field: 'counted_amount', money: true, get: (r: any) => r.counted_amount },
    { header: 'Variance $', field: 'variance', money: true, get: (r: any) => r.variance },
    { header: 'Status', field: 'status', get: (r: any) => r.status },
    { header: 'Comment', field: 'comment', get: (r: any) => r.comment || '' },
    { header: 'Counted by', field: 'counted_by', get: (r: any) => r.counted_by || '' },
    { header: 'Chargeback', field: 'chargeback_status', get: (r: any) => r.chargeback_status || '' },
    { header: 'Chargeback $', field: 'chargeback_amount', money: true, get: (r: any) => r.chargeback_amount },
    { header: 'DM verified', field: 'dm_verified', get: (r: any) => r.dm_verified ? 'Yes' : 'No' },
    { header: 'Envelope photo', field: 'envelope_view_url', get: (r: any) => r.envelope_view_url ? `${API_URL}${r.envelope_view_url}` : '' },
  ], [])

  const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
  const tile: React.CSSProperties = { padding: '10px 14px', borderRadius: 10, border: '1px solid var(--border)', background: 'var(--surface)', minWidth: 130 }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>✉️ Envelope Report</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Count each envelope&apos;s actual cash, comment it, mark short/over — a short envelope can be assigned to the sales rep as a chargeback.
          </p>
        </div>
        {!loading && rows.length > 0 && (
          <ReportExportBar
            title="Envelope Report"
            subtitle={`${filt.period} → ${filt.periodTo || filt.period}${status ? ` · ${status}` : ''}`}
            filename={`envelope-report_${filt.period}_${filt.periodTo || filt.period}`}
            sheets={[{ name: 'Envelopes', columns, rows }]}
          />
        )}
      </div>

      <StandardFilterBar
        value={filt} onChange={setFilt}
        periodMode="range"
        storeOptions={storeOptions} marketOptions={marketOptions} repOptions={repOptions}
        storeLabel="Stores…" marketLabel="Markets…" repLabel="Employees…"
        right={(
          <select style={sel} value={status} onChange={e => setStatus(e.target.value)}>
            <option value="">All envelopes</option>
            <option value="discrepancy">Discrepancies (short + over)</option>
            <option value="short">Short only</option>
            <option value="over">Over only</option>
            <option value="match">Match only</option>
            <option value="uncounted">Uncounted only</option>
            <option value="commented">With comments</option>
            <option value="chargeback">With chargebacks</option>
          </select>
        )}
      />

      {/* Summary tiles */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', margin: '14px 0' }}>
        <div style={tile}><div style={{ fontSize: 12, color: 'var(--text3)' }}>Envelopes</div><div style={{ fontSize: 20, fontWeight: 700 }}>{t.envelopes ?? 0}</div></div>
        <div style={tile}><div style={{ fontSize: 12, color: 'var(--text3)' }}>Counted</div><div style={{ fontSize: 20, fontWeight: 700 }}>{t.counted ?? 0}</div></div>
        <div style={tile}><div style={{ fontSize: 12, color: 'var(--text3)' }}>Short</div><div style={{ fontSize: 20, fontWeight: 700, color: '#c0392b' }}>{t.short ?? 0} · {fmt(t.short_total || 0)}</div></div>
        <div style={tile}><div style={{ fontSize: 12, color: 'var(--text3)' }}>Over</div><div style={{ fontSize: 20, fontWeight: 700, color: '#b7791f' }}>{t.over ?? 0} · {fmt(t.over_total || 0)}</div></div>
        <div style={tile}><div style={{ fontSize: 12, color: 'var(--text3)' }}>Chargebacks</div><div style={{ fontSize: 20, fontWeight: 700 }}>{t.chargebacks ?? 0} · {fmt(t.chargeback_total || 0)}</div></div>
      </div>

      {data?.market_filter_skipped && (
        <div className="card" style={{ padding: '8px 12px', marginBottom: 12, fontSize: 12, background: '#fff8e6', border: '1px solid #f3d98b' }}>
          ⚠️ Your market filter could not be applied (store roster unavailable) — showing all markets rather than silently dropping stores.
        </div>
      )}
      {err && <div className="card" style={{ padding: 12, marginBottom: 12, color: '#c0392b' }}>⚠️ {err}</div>}
      {loading && <div style={{ padding: 24, color: 'var(--text3)' }}>Loading…</div>}

      {!loading && (
        <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', textAlign: 'left' }}>
                {['Date', 'Store', 'Employee', 'Declared $', 'Photo', 'Counted $', 'Variance', 'Status', 'Comment', 'Chargeback', ''].map(h => (
                  <th key={h} style={{ padding: '8px 10px', whiteSpace: 'nowrap', fontWeight: 600, color: 'var(--text2)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r: any) => {
                const d = draftFor(r)
                const isShortDraft = d.counted !== '' && Number(d.counted) < (r.declared_cash || 0)
                return (
                  <tr key={r.closing_row_id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '6px 10px', whiteSpace: 'nowrap' }}>{r.close_date}</td>
                    <td style={{ padding: '6px 10px' }}>{r.store_address}<div style={{ fontSize: 11, color: 'var(--text3)' }}>{r.market}</div></td>
                    <td style={{ padding: '6px 10px' }}>{r.employee_name}</td>
                    <td style={{ padding: '6px 10px', textAlign: 'right' }}>{fmt(r.declared_cash)}</td>
                    <td style={{ padding: '6px 10px' }}>
                      {r.envelope_view_url
                        ? <a href={`${API_URL}${r.envelope_view_url}`} target="_blank" rel="noreferrer">📷</a>
                        : <span style={{ color: 'var(--text3)' }}>—</span>}
                    </td>
                    <td style={{ padding: '6px 10px' }}>
                      <input type="number" step="0.01" value={d.counted} placeholder="count…"
                        onChange={e => setDraft(r.closing_row_id, { counted: e.target.value, comment: d.comment, chargeback: d.chargeback })}
                        style={{ width: 90, padding: '4px 6px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }} />
                    </td>
                    <td style={{ padding: '6px 10px', textAlign: 'right', color: (r.variance ?? 0) < 0 ? '#c0392b' : undefined }}>
                      {r.variance != null ? fmt(r.variance) : '—'}
                    </td>
                    <td style={{ padding: '6px 10px', whiteSpace: 'nowrap' }}>{STATUS_BADGE[r.status] || r.status}</td>
                    <td style={{ padding: '6px 10px' }}>
                      <input value={d.comment} placeholder="comment…"
                        onChange={e => setDraft(r.closing_row_id, { counted: d.counted, comment: e.target.value, chargeback: d.chargeback })}
                        style={{ width: 150, padding: '4px 6px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }} />
                    </td>
                    <td style={{ padding: '6px 10px', whiteSpace: 'nowrap' }}>
                      <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 5 }} title="If short: assign the shortage to the sales rep as a chargeback (pending until management posts/waives it)">
                        <input type="checkbox" checked={d.chargeback}
                          disabled={!isShortDraft && !r.chargeback_id && r.status !== 'short'}
                          onChange={e => setDraft(r.closing_row_id, { counted: d.counted, comment: d.comment, chargeback: e.target.checked })} />
                        Chargeback
                      </label>
                      {r.chargeback_status && (
                        <div style={{ fontSize: 11, marginTop: 3 }}>
                          <span style={{ color: r.chargeback_status === 'posted' ? '#c0392b' : r.chargeback_status === 'waived' ? 'var(--text3)' : '#b7791f' }}>
                            {r.chargeback_status} · {fmt(r.chargeback_amount)}
                          </span>
                          {canDecide && r.chargeback_status === 'pending' && (
                            <span style={{ marginLeft: 6 }}>
                              <button className="btn btn-secondary" style={{ fontSize: 11, padding: '1px 6px' }} onClick={() => decide(r, 'posted')}>Post</button>{' '}
                              <button className="btn btn-secondary" style={{ fontSize: 11, padding: '1px 6px' }} onClick={() => decide(r, 'waived')}>Waive</button>
                            </span>
                          )}
                        </div>
                      )}
                    </td>
                    <td style={{ padding: '6px 10px' }}>
                      <button className="btn btn-primary" style={{ fontSize: 12, padding: '3px 10px' }}
                        disabled={savingId === r.closing_row_id}
                        onClick={() => saveCount(r)}>
                        {savingId === r.closing_row_id ? '…' : 'Save'}
                      </button>
                    </td>
                  </tr>
                )
              })}
              {rows.length === 0 && (
                <tr><td colSpan={11} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No envelopes for this range/filter.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
