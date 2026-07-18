'use client'
import { useEffect, useRef, useState } from 'react'
import { api, fmt } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import { type EntityOption } from '@/lib/entity-picker-core'

// Total Processor (VidaPay / Total Access) commission report — the MA Commission Details + MA Daily
// Tx roll-up (mig 083). Sign-flipped: positive = money the dealer RECEIVES. Org-scoped: shows the
// data uploaded into THIS tenant (Data Imports → MA cards, or the mailbox rules). Read-only.

const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const tile: React.CSSProperties = { flex: 1, minWidth: 150, border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px' }
const th: React.CSSProperties = { textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }
const td: React.CSSProperties = { padding: '5px 8px', fontSize: 13 }

function currentPeriod() {
  const d = new Date()
  return d.toLocaleString('en-US', { month: 'long', year: 'numeric' })
}

export default function MaCommissionPage() {
  const [period, setPeriod] = useState(currentPeriod())
  const [d, setD] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  // RULE FIVE (§3d): store(s)/rep(s) multi. Server-side narrowing (stores/reps params) keeps the tiles +
  // tables + export all correct under a filter (WYSIWYG §3c). No `market` — account-keyed processor data.
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())

  async function load(p = period, f: StandardFilterValue = filt) {
    setBusy(true); setMsg('')
    try {
      const qs = new URLSearchParams({ period: p.trim() })
      if (f.stores.length) qs.set('stores', f.stores.join(','))
      if (f.reps.length) qs.set('reps', f.reps.join(','))
      setD(await api(`/api/v1/commcalc/ma-commission/summary?${qs.toString()}`))
    }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }
  useEffect(() => { load() }, [])  // eslint-disable-line react-hooks/exhaustive-deps
  // Re-fetch (server re-aggregates) when the store/rep filter changes — skip the initial mount.
  const didMount = useRef(false)
  useEffect(() => {
    if (!didMount.current) { didMount.current = true; return }
    load(period, filt)   // eslint-disable-line react-hooks/exhaustive-deps
  }, [filt])   // eslint-disable-line react-hooks/exhaustive-deps
  // Stable pick-don't-type options from the backend (computed pre-filter, so the list never collapses).
  const storeOpts: EntityOption[] = (d?.store_options || []).map((o: any) => ({ id: o.id, label: o.label }))
  const repOpts: EntityOption[] = (d?.rep_options || []).map((r: string) => ({ id: r, label: r }))
  // Airtime (raw_ma_daily_tx) has NO rep column, so the server narrows commission by rep but CANNOT narrow
  // airtime — under a rep filter it stays all-reps. To keep a rep-filtered view from mixing rep commission
  // with all-reps airtime, when a rep filter is active we (a) suppress the airtime tile with a caveat,
  // (b) blank the airtime column + drop it from Total payable (= rep commission only), and (c) hide
  // airtime-only stores the selected rep never touched — identically on screen and in the export.
  const repFilterActive = filt.reps.length > 0
  const byStoreRows: any[] = (d?.by_store || []).filter((s: any) => !repFilterActive || (s.activations || 0) > 0 || (s.payable || 0) !== 0)

  // RULE FOUR export: tiles summary + the by-store / by-rep / spiff-by-month report tables.
  const storeCols: ExportColumn[] = [
    { header: 'Store / account', field: 'store', role: 'store', get: (r: any) => r.name || r.account_id },
    { header: 'Account', field: 'account_id', get: (r: any) => r.account_id },
    { header: 'Activations', get: (r: any) => r.activations },
    { header: 'Rebates', money: true, get: (r: any) => r.rebates },
    { header: 'Spiffs', money: true, get: (r: any) => r.spiffs },
    // Under a rep filter airtime is not rep-attributable → zeroed with a header caveat, and excluded from Total.
    { header: repFilterActive ? 'Airtime margin (all reps — excluded under rep filter)' : 'Airtime margin', money: true, get: (r: any) => repFilterActive ? 0 : (r.airtime_margin || 0) },
    { header: repFilterActive ? 'Total payable (rep commission only)' : 'Total payable', money: true, get: (r: any) => repFilterActive ? (r.payable || 0) : ((r.payable || 0) + (r.airtime_margin || 0)) },
  ]
  const repCols: ExportColumn[] = [
    { header: 'Rep (processor login)', field: 'rep', role: 'rep', get: (r: any) => r.rep },
    { header: 'Activations', get: (r: any) => r.activations },
    { header: 'Rebates', money: true, get: (r: any) => r.rebates },
    { header: 'Spiffs', money: true, get: (r: any) => r.spiffs },
    { header: 'Avg MRC', money: true, get: (r: any) => r.avg_mrc ?? 0 },
    { header: 'Payable', money: true, get: (r: any) => r.payable },
  ]
  const exportSheets = d?.ready ? [
    { name: 'Summary', columns: [{ header: 'Metric', get: (r: any) => r.k }, { header: 'Value', get: (r: any) => r.v }] as ExportColumn[], rows: [
      { k: 'Total payable', v: fmt(d.total_payable) }, { k: 'Activations', v: d.activations?.total },
      { k: 'Rebates', v: fmt(d.components?.rebates) }, { k: 'Spiffs (M1–M6)', v: fmt(d.components?.spiffs_total) },
      { k: 'Airtime margin', v: repFilterActive ? 'all reps — not narrowed by rep filter' : fmt(d.airtime?.margin) },
    ] },
    { name: 'By store', columns: storeCols, rows: byStoreRows },
    { name: 'By rep', columns: repCols, rows: d.by_rep || [] },
    { name: 'Spiff by month', columns: [{ header: 'Month', get: (r: any) => r.m }, { header: 'Amount', money: true, get: (r: any) => r.v }] as ExportColumn[],
      rows: Object.entries(d.spiff_by_month || {}).map(([m, v]) => ({ m: m.toUpperCase(), v })) },
  ] : []

  return (
    <div style={{ maxWidth: 1020 }}>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📡 Total Processor Commissions</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          What the payment processor (VidaPay / Total Access) owes you — activations with rebates &amp; month 1–6
          spiffs from <b>MA Commission Details</b>, plus airtime margin from <b>MA Daily Tx</b>. Positive = money you
          receive. Upload the reports on <a href="/commcalc/upload" style={{ color: 'var(--accent,#2563eb)' }}>Data Imports</a> (no
          period needed) or auto-import them with a mailbox rule.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <input style={{ ...sel, width: 160 }} placeholder="e.g. June 2026 (blank = all)" value={period} onChange={e => setPeriod(e.target.value)} />
        <button className="btn btn-primary" disabled={busy} onClick={() => load()}>{busy ? '…' : 'Load'}</button>
        {d?.date_range && <span style={{ fontSize: 12, color: 'var(--text3)' }}>data {d.date_range[0]} → {d.date_range[1]} · {d.rows} activation rows</span>}
        <div style={{ flex: 1 }} />
        {d?.ready && !d.note && <ReportExportBar title={`Total Processor Commissions ${period}`} filename={`total_processor_${period.replace(/\s+/g, '_')}`} sheets={exportSheets} />}
      </div>
      {/* RULE FIVE standard bar — store(s)/rep(s) multi (period = the input above; no market dimension). */}
      {(storeOpts.length > 0 || repOpts.length > 0 || filt.stores.length > 0 || filt.reps.length > 0) && (
        <StandardFilterBar value={filt} onChange={setFilt} show={{ period: false, markets: false }}
          storeOptions={storeOpts} repOptions={repOpts} storeLabel="Accounts…" repLabel="Reps…" />
      )}
      {msg && <div className="card" style={{ padding: 12, marginBottom: 12, fontSize: 13 }}>{msg}</div>}
      {d && d.ready === false && <div className="card" style={{ padding: 14, marginBottom: 14, background: '#fffbeb', border: '1px solid #fde68a', fontSize: 13 }}>⚠️ {d.note}</div>}
      {d?.ready && d.note && <div className="card" style={{ padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--text2)' }}>{d.note}</div>}

      {d?.ready && !d.note && <>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
          <div style={tile}><div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 700, textTransform: 'uppercase' }}>Total payable</div>
            <div style={{ fontSize: 22, fontWeight: 800, color: '#059669' }}>{fmt(d.total_payable)}</div></div>
          <div style={tile}><div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 700, textTransform: 'uppercase' }}>Activations</div>
            <div style={{ fontSize: 22, fontWeight: 800 }}>{d.activations.total}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>{d.activations.new} new · {d.activations.add} add · {d.activations.byop} BYOP</div></div>
          <div style={tile}><div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 700, textTransform: 'uppercase' }}>Rebates</div>
            <div style={{ fontSize: 22, fontWeight: 800 }}>{fmt(d.components.rebates)}</div></div>
          <div style={tile}><div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 700, textTransform: 'uppercase' }}>Spiffs (M1–M6)</div>
            <div style={{ fontSize: 22, fontWeight: 800 }}>{fmt(d.components.spiffs_total)}</div></div>
          <div style={tile}><div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 700, textTransform: 'uppercase' }}>Airtime margin</div>
            {repFilterActive ? (<>
              <div style={{ fontSize: 22, fontWeight: 800, color: 'var(--text3)' }}>—</div>
              <div style={{ fontSize: 12, color: '#b45309' }}>airtime = all reps — not narrowed by rep. Clear the rep filter to see it.</div>
            </>) : (<>
              <div style={{ fontSize: 22, fontWeight: 800 }}>{fmt(d.airtime.margin)}</div>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>{d.airtime.orders} top-ups · {fmt(d.airtime.retail)} retail</div>
            </>)}</div>
        </div>

        <div className="card" style={{ padding: 14, marginBottom: 14 }}>
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>Residual spiffs by month-in-life</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {Object.entries(d.spiff_by_month || {}).map(([m, v]: any) => (
              <div key={m} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '8px 14px', textAlign: 'center' }}>
                <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 700 }}>{m.toUpperCase()}</div>
                <div style={{ fontSize: 15, fontWeight: 700 }}>{fmt(v)}</div>
              </div>
            ))}
            <div style={{ alignSelf: 'center', fontSize: 12, color: 'var(--text3)', maxWidth: 320 }}>
              The processor pre-computes each activation&apos;s month 1–6 residual spiffs — compare against the Payout Schedules expectation.
            </div>
          </div>
        </div>

        <div className="card" style={{ padding: 0, marginBottom: 14 }}>
          <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>By store ({byStoreRows.length})</div>
          {repFilterActive && (
            <div style={{ padding: '8px 14px', fontSize: 12, color: '#92400e', background: '#fffbeb', borderBottom: '1px solid #fde68a' }}>
              Rep filter active — <b>Total payable</b> is rep commission only. Airtime margin has no rep dimension (all reps), so it is excluded here; stores the selected rep never touched are hidden.
            </div>
          )}
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>{['Store / account', 'Activations', 'Rebates', 'Spiffs', repFilterActive ? 'Airtime (n/a — all reps)' : 'Airtime margin', repFilterActive ? 'Total payable (rep only)' : 'Total payable'].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
            <tbody>
              {byStoreRows.map((s: any) => (
                <tr key={s.account_id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ ...td, fontWeight: 600 }}>{s.name || s.account_id}{s.name && <span style={{ fontSize: 11, color: 'var(--text3)' }}> · {s.account_id}</span>}</td>
                  <td style={td}>{s.activations}</td>
                  <td style={td}>{fmt(s.rebates)}</td>
                  <td style={td}>{fmt(s.spiffs)}</td>
                  <td style={{ ...td, color: repFilterActive ? 'var(--text3)' : undefined }}>{repFilterActive ? '—' : fmt(s.airtime_margin)}</td>
                  <td style={{ ...td, fontWeight: 700 }}>{repFilterActive ? fmt(s.payable) : fmt(s.payable + s.airtime_margin)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card" style={{ padding: 0, marginBottom: 14 }}>
          <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>By rep ({d.by_rep.length})</div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>{['Rep (processor login)', 'Activations', 'Rebates', 'Spiffs', 'Avg MRC', 'Payable'].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
            <tbody>
              {d.by_rep.map((r: any) => (
                <tr key={r.rep} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ ...td, fontWeight: 600 }}>{r.rep}</td>
                  <td style={td}>{r.activations}</td>
                  <td style={td}>{fmt(r.rebates)}</td>
                  <td style={td}>{fmt(r.spiffs)}</td>
                  <td style={td}>{r.avg_mrc != null ? fmt(r.avg_mrc) : '—'}</td>
                  <td style={{ ...td, fontWeight: 700 }}>{fmt(r.payable)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {d.by_platform.length > 1 && (
          <div className="card" style={{ padding: 14 }}>
            <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>By platform</div>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              {d.by_platform.map((p: any) => (
                <div key={p.platform} style={{ fontSize: 13 }}><b>{p.platform}</b>: {p.activations} activations · {fmt(p.payable)}</div>
              ))}
            </div>
          </div>
        )}
      </>}
    </div>
  )
}
