'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { ExportButtons, ExportPayload, ExportColumn } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { MultiSelect } from '@/lib/multiselect'

// On-Inventory 3-Way Rebate Recon (OWNER DIRECTIVE 2026-07-28): the on-inventory report cross-checked
// against (2) the IMEI rebate data already on each ledger row (asset_ledger.reimbursement/date — the
// same field GET /asset/aging-rebate calls "rebate received") and (3) the ePay commission-side payment
// history for that IMEI (raw_payment_detail). Read-only — never edits asset_ledger/flags/investigation.
//
// Duplicated from the in-flight agent/asset/market-filter-dropdown package (see backend
// oninv_recon.py docstring): SAME sentinel value, flagged for merge-time dedupe.
const NO_MARKET_VALUE = '__no_market__'

type Leg2 = { paid: boolean; amount: number | null; date: string | null }
type Leg3 = { status: 'paid' | 'not_paid' | 'na' | null; amount: number | null; last_date: string | null; payment_count: number; payment_types: string | null }
type Classification = 'missing_phone_candidate' | 'non_activated' | 'conflict' | 'unmatchable'
type Row = {
  store: string | null; market: string | null; esn_imei: string | null; device_model: string | null
  acquired_date: string | null; aging_days: number | null; device_value: number
  classification: Classification; leg2: Leg2; leg3: Leg3
}
type ClassCounts = { count: number; exposure: number }
type StoreSummary = {
  store: string; market: string | null; device_count: number; total_exposure: number
  classes: Record<Classification, ClassCounts>
}
type Data = {
  migrated: boolean; message?: string; as_of: string
  rows: Row[]; stores: StoreSummary[]
  totals: Record<Classification, ClassCounts>
  grand_total_devices?: number; grand_total_exposure?: number
  device_value_column?: string
}

const CLASS_META: Record<Classification, { label: string; color: string; help: string }> = {
  missing_phone_candidate: {
    label: 'Missing-Phone Candidate', color: '#dc2626',
    help: 'On-Inventory, but a rebate/commission was paid on this IMEI — the device is demonstrably activated or left the store. The inventory record is likely wrong, or the phone walked.',
  },
  conflict: {
    label: 'Conflict', color: '#d97706',
    help: 'The Distributor ledger and the commission (ePay) side DISAGREE on whether a rebate was paid for this IMEI — needs a human look, not silently resolved either way.',
  },
  non_activated: {
    label: 'Non-Activated (true stock)', color: '#059669',
    help: 'On-Inventory with no rebate evidence anywhere checked — genuinely unsold stock.',
  },
  unmatchable: {
    label: 'Unmatchable', color: '#6b7280',
    help: 'The on-inventory row has no usable IMEI/ESN — can’t be checked against either leg. Never silently dropped.',
  },
}
const CLASS_ORDER: Classification[] = ['missing_phone_candidate', 'conflict', 'non_activated', 'unmatchable']

function fmtDate(s: string | null) { return s ? String(s).slice(0, 10) : '—' }

function Leg2Cell({ l }: { l: Leg2 }) {
  if (!l.paid) return <span style={{ color: 'var(--text3)' }}>No rebate on ledger</span>
  return <span>{fmt(l.amount || 0)} <span style={{ color: 'var(--text3)' }}>on {fmtDate(l.date)}</span></span>
}
function Leg3Cell({ l }: { l: Leg3 }) {
  if (l.status === 'na') return <span style={{ color: 'var(--text3)', fontStyle: 'italic' }}>ePay not loaded</span>
  if (l.status !== 'paid') return <span style={{ color: 'var(--text3)' }}>No ePay payment found</span>
  return (
    <span>
      {fmt(l.amount || 0)} <span style={{ color: 'var(--text3)' }}>
        · {l.payment_count} pmt{l.payment_count === 1 ? '' : 's'} · last {fmtDate(l.last_date)}
      </span>
      {l.payment_types && <div style={{ fontSize: 11, color: 'var(--text3)' }}>{l.payment_types}</div>}
    </span>
  )
}

export default function OninvThreeWayReconPage() {
  const [market, setMarket] = useState('')
  const [selStores, setSelStores] = useState<string[]>([])
  const [markets, setMarkets] = useState<string[]>([])
  const [stores, setStores] = useState<{ store: string; market: string }[]>([])
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [data, setData] = useState<Data | null>(null)
  const [loading, setLoading] = useState(true)
  const [classFilter, setClassFilter] = useState<Classification | ''>('')
  const [storeFilter, setStoreFilter] = useState('')

  useEffect(() => {
    apiCached(`/api/v1/asset/filter-options?org_id=${ORG_ID}`, LOOKUP)
      .then((d: any) => { setMarkets(d.markets || []); setStores(d.stores || []) })
      .catch(console.error)
  }, [])
  useEffect(() => { load() }, [market, selStores, dateFrom, dateTo])

  async function load() {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ org_id: ORG_ID })
      if (market) qs.set('market', market)
      if (selStores.length) qs.set('store', selStores.join(','))
      if (dateFrom) qs.set('date_from', dateFrom)
      if (dateTo) qs.set('date_to', dateTo)
      setData(await api(`/api/v1/asset/oninv-3way-recon?${qs.toString()}`))
    } catch (e) { console.error(e) }
    setLoading(false)
  }

  function onMarketChange(v: string) {
    setMarket(v)
    const allowed = new Set(
      (v === NO_MARKET_VALUE ? stores.filter(s => !s.market) : v ? stores.filter(s => s.market === v) : stores)
        .map(s => s.store)
    )
    setSelStores(prev => prev.filter(s => allowed.has(s)))
  }
  const visibleStores = market === NO_MARKET_VALUE ? stores.filter(s => !s.market)
    : market ? stores.filter(s => s.market === market) : stores

  const filteredRows = useMemo(() => {
    const rows = data?.rows || []
    return rows.filter(r =>
      (!classFilter || r.classification === classFilter) &&
      (!storeFilter || r.store === storeFilter)
    )
  }, [data, classFilter, storeFilter])

  function buildPayload(): ExportPayload {
    const cols: ExportColumn[] = [
      { header: 'Store', get: r => r.store },
      { header: 'Market', get: r => r.market },
      { header: 'IMEI/ESN', get: r => r.esn_imei },
      { header: 'Device', get: r => r.device_model },
      { header: 'Acquired', get: r => fmtDate(r.acquired_date) },
      { header: 'Aging Days', get: r => r.aging_days, align: 'right' },
      { header: 'Device Value', get: r => r.device_value, money: true },
      { header: 'Classification', get: r => CLASS_META[r.classification as Classification]?.label || r.classification },
      { header: 'Leg 2 (Distributor rebate)', get: r => r.leg2.paid ? `${r.leg2.amount} on ${fmtDate(r.leg2.date)}` : 'No rebate on ledger' },
      { header: 'Leg 3 (ePay commission)', get: r => r.leg3.status === 'na' ? 'ePay not loaded' : r.leg3.status === 'paid' ? `${r.leg3.amount} (${r.leg3.payment_count} pmts, last ${fmtDate(r.leg3.last_date)}) — ${r.leg3.payment_types || ''}` : 'No ePay payment found' },
    ]
    const summaryRows = (data?.stores || []).map(s => ({
      store: s.store, market: s.market, device_count: s.device_count, total_exposure: s.total_exposure,
      missing: s.classes.missing_phone_candidate.count, missing_exp: s.classes.missing_phone_candidate.exposure,
      conflict: s.classes.conflict.count, conflict_exp: s.classes.conflict.exposure,
      non_act: s.classes.non_activated.count, non_act_exp: s.classes.non_activated.exposure,
      unmatch: s.classes.unmatchable.count, unmatch_exp: s.classes.unmatchable.exposure,
    }))
    const summaryCols: ExportColumn[] = [
      { header: 'Store', get: r => r.store }, { header: 'Market', get: r => r.market },
      { header: 'Devices', get: r => r.device_count, align: 'right' },
      { header: 'Total Exposure', get: r => r.total_exposure, money: true },
      { header: 'Missing-Phone #', get: r => r.missing, align: 'right' },
      { header: 'Missing-Phone $', get: r => r.missing_exp, money: true },
      { header: 'Conflict #', get: r => r.conflict, align: 'right' },
      { header: 'Conflict $', get: r => r.conflict_exp, money: true },
      { header: 'Non-Activated #', get: r => r.non_act, align: 'right' },
      { header: 'Non-Activated $', get: r => r.non_act_exp, money: true },
      { header: 'Unmatchable #', get: r => r.unmatch, align: 'right' },
      { header: 'Unmatchable $', get: r => r.unmatch_exp, money: true },
    ]
    const filterParts = [
      market === NO_MARKET_VALUE ? '(no market)' : market || null,
      selStores.length ? selStores.join(', ') : null,
      (dateFrom || dateTo) ? `Acquired ${dateFrom || '…'} to ${dateTo || '…'}` : null,
      classFilter ? `Classification: ${CLASS_META[classFilter].label}` : null,
      storeFilter ? `Store: ${storeFilter}` : null,
    ].filter(Boolean)
    return {
      title: 'On-Inventory 3-Way Rebate Recon',
      subtitle: `${filterParts.join(' · ') || 'All markets'}${data?.as_of ? ` · as of ${data.as_of}` : ''}`,
      filename: 'oninv-3way-recon',
      sheets: [
        { name: 'Per-Store Summary', rows: summaryRows, columns: summaryCols },
        { name: 'Devices', rows: filteredRows, columns: cols },
      ],
    }
  }

  const selStyle = { padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

  return (
    <div>
      <div style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <a href="/commcalc/asset" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Asset Ledger</a>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>🔍 On-Inventory 3-Way Rebate Recon</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 780 }}>
            Every On-Inventory IMEI cross-checked against (2) the Distributor rebate already on its ledger row and
            (3) the ePay commission history for that IMEI — to find phones that were actually activated/left
            (missing-phone candidates) vs. genuinely unsold stock (non-activated).
          </p>
        </div>
        {data?.migrated && <><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></>}
      </div>

      {/* Filters */}
      <div className="card" style={{ padding: 14, marginBottom: 20, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text2)' }}>Filters:</span>
        <select style={selStyle} value={market} onChange={e => onMarketChange(e.target.value)}>
          <option value="">All markets</option>
          <option value={NO_MARKET_VALUE}>(no market)</option>
          {markets.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <MultiSelect allLabel="All stores" width={190} searchable
          options={visibleStores.map(s => ({ value: s.store }))}
          value={selStores} onChange={setSelStores} />
        <label style={{ fontSize: 12, color: 'var(--text3)' }}>Acquired</label>
        <input type="date" style={selStyle} value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>to</span>
        <input type="date" style={selStyle} value={dateTo} onChange={e => setDateTo(e.target.value)} />
        {(market || selStores.length || dateFrom || dateTo) && (
          <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 10px' }}
            onClick={() => { setMarket(''); setSelStores([]); setDateFrom(''); setDateTo('') }}>✕ Clear</button>
        )}
        {data?.as_of && <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text3)' }}>As of {data.as_of} · $ column: {data.device_value_column || 'owed_to_vip'}</span>}
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>Loading…</div>
      ) : !data?.migrated ? (
        <div className="card" style={{ padding: 24, textAlign: 'center', color: '#b91c1c' }}>
          {data?.message || 'This report is not available yet — ask the operator to run migration 310.'}
        </div>
      ) : !data.rows.length ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>No on-inventory devices for this filter.</div>
      ) : (
        <>
          {/* Grand total classification tiles (click to filter the device table) */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 16, marginBottom: 24 }}>
            {CLASS_ORDER.map(c => {
              const t = data.totals[c] || { count: 0, exposure: 0 }
              const meta = CLASS_META[c]
              const active = classFilter === c
              return (
                <div key={c} className="card" title={meta.help}
                  onClick={() => { setClassFilter(active ? '' : c); setStoreFilter('') }}
                  style={{ padding: '18px 22px', borderTop: `3px solid ${meta.color}`, cursor: 'pointer',
                           outline: active ? `2px solid ${meta.color}` : 'none' }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{meta.label}</div>
                  <div style={{ fontSize: 24, fontWeight: 700, color: meta.color, marginTop: 6 }}>{t.count.toLocaleString()}</div>
                  <div style={{ fontSize: 13, color: 'var(--text3)', marginTop: 2 }}>{fmt(t.exposure)} exposure</div>
                </div>
              )
            })}
          </div>

          {/* Per-store summary */}
          <div className="card" style={{ padding: 0, marginBottom: 24 }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14 }}>
              🏪 Per-Store Summary <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>— click a store to filter devices below</span>
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 980 }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)' }}>
                    {['Store', 'Market', 'Devices', 'Total $', 'Missing-Phone', 'Conflict', 'Non-Activated', 'Unmatchable'].map((h, i) => (
                      <th key={h} style={{ textAlign: i < 2 ? 'left' : 'right', padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.stores.map((s, i) => {
                    const active = storeFilter === s.store
                    return (
                      <tr key={s.store} onClick={() => { setStoreFilter(active ? '' : s.store); }}
                        style={{ borderTop: '1px solid var(--border)', cursor: 'pointer',
                                 background: active ? 'var(--accent)12' : (i % 2 === 0 ? 'transparent' : 'var(--surface2)') }}>
                        <td style={{ padding: '8px 12px', fontSize: 12, fontWeight: 600 }}>{s.store}</td>
                        <td style={{ padding: '8px 12px', fontSize: 12, color: 'var(--text2)' }}>{s.market || '—'}</td>
                        <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{s.device_count}</td>
                        <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right', fontWeight: 700 }}>{fmt(s.total_exposure)}</td>
                        {CLASS_ORDER.slice(0, 3).map(c => (
                          <td key={c} style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right', color: s.classes[c].count ? CLASS_META[c].color : 'var(--text3)', fontWeight: s.classes[c].count ? 600 : 400 }}>
                            {s.classes[c].count} <span style={{ color: 'var(--text3)', fontWeight: 400 }}>· {fmt(s.classes[c].exposure)}</span>
                          </td>
                        ))}
                        <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right', color: 'var(--text3)' }}>{s.classes.unmatchable.count}</td>
                      </tr>
                    )
                  })}
                </tbody>
                <tfoot>
                  <tr style={{ borderTop: '2px solid var(--border)', fontWeight: 700, background: 'var(--surface2)' }}>
                    <td style={{ padding: '10px 12px', fontSize: 12 }}>Total ({data.stores.length} stores)</td>
                    <td />
                    <td style={{ padding: '10px 12px', fontSize: 12, textAlign: 'right' }}>{(data.grand_total_devices ?? 0).toLocaleString()}</td>
                    <td style={{ padding: '10px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(data.grand_total_exposure ?? 0)}</td>
                    {CLASS_ORDER.slice(0, 3).map(c => (
                      <td key={c} style={{ padding: '10px 12px', fontSize: 12, textAlign: 'right', color: CLASS_META[c].color }}>
                        {data.totals[c].count} · {fmt(data.totals[c].exposure)}
                      </td>
                    ))}
                    <td style={{ padding: '10px 12px', fontSize: 12, textAlign: 'right', color: 'var(--text3)' }}>{data.totals.unmatchable.count}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </div>

          {/* Device detail */}
          <div className="card" style={{ padding: 0 }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
              <span>📱 Devices <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>· {filteredRows.length.toLocaleString()} of {data.rows.length.toLocaleString()}</span></span>
              {(classFilter || storeFilter) && (
                <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 10px' }}
                  onClick={() => { setClassFilter(''); setStoreFilter('') }}>✕ Clear device filter</button>
              )}
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1100 }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)' }}>
                    {['Store', 'Model', 'IMEI/ESN', 'Acquired', 'Aging', 'Value', 'Classification', 'Leg 2 — Distributor Rebate', 'Leg 3 — ePay Commission'].map(h => (
                      <th key={h} style={{ textAlign: 'left', padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filteredRows.slice(0, 1000).map((r, i) => (
                    <tr key={(r.esn_imei || '') + i} style={{ borderTop: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)' }}>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.store || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.device_model || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 11, fontFamily: 'monospace' }}>{r.esn_imei || <span style={{ color: '#dc2626' }}>(blank)</span>}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, whiteSpace: 'nowrap' }}>{fmtDate(r.acquired_date)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.aging_days ?? '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, fontWeight: 600 }}>{fmt(r.device_value)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>
                        <span style={{ color: CLASS_META[r.classification]?.color, fontWeight: 600 }}>{CLASS_META[r.classification]?.label || r.classification}</span>
                      </td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}><Leg2Cell l={r.leg2} /></td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}><Leg3Cell l={r.leg3} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {filteredRows.length > 1000 && (
                <div style={{ padding: '10px 12px', fontSize: 12, color: 'var(--text3)' }}>
                  Showing first 1,000 of {filteredRows.length.toLocaleString()} — narrow with the filters above or export for the full list.
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
