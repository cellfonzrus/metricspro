'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

export default function SalesAnalyzerPage() {
  const { period } = usePeriod()
  const [windowDays, setWindowDays] = useState(90)
  const [repFilter, setRepFilter] = useState('')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [selRep, setSelRep] = useState('')
  const [lossFilter, setLossFilter] = useState('')   // '' | employee | customer | mixed
  const [flagged, setFlagged] = useState<Record<string, boolean>>({})

  async function flagChurn(c: any) {
    const k = `${c.phone_number || ''}|${c.activation_date || ''}`
    try {
      await api('/api/v1/commcalc/chargeback-review', { method: 'POST', body: JSON.stringify({
        source: 'analyzer_churn', severity: 'warning', needs_review: true,
        store: c.store, period, occurred_date: c.churn_date || c.activation_date,
        phone_number: c.phone_number, esn: c.device_serial, imei: c.device_serial,
        amount: 0, suggested_rep: c.rep,
        detail: `Early churn (${c.days_active ?? '?'}d): ${c.device_model || ''}${c.sold_for ? ` · sold ${c.sold_for}` : ''}`,
        dedupe_key: `churn:${k}`,
      }) })
      setFlagged(f => ({ ...f, [k]: true }))
    } catch (e: any) { alert('Flag failed: ' + (e?.message || e)) }
  }

  function load() {
    setLoading(true)
    api(`/api/v1/commcalc/sales-analyzer/${encodeURIComponent(period)}?window_days=${windowDays}&org_id=${ORG_ID}`)
      .then(setData).catch((e) => setData({ error: e?.message || String(e) })).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [period, windowDays])

  const reps = (data?.reps || []).filter((r: any) => !repFilter || (r.rep || '').toLowerCase().includes(repFilter.toLowerCase()))
  const churned = (data?.churned || []).filter((c: any) =>
    (!repFilter || (c.rep || '').toLowerCase().includes(repFilter.toLowerCase())) &&
    (!selRep || c.rep_login === selRep) &&
    (!lossFilter || c.loss_type === lossFilter))
  const t = data?.totals || {}

  function buildPayload(): ExportPayload {
    return {
      title: `Retention Analysis — 3-Month`, subtitle: `${period} · cohort ${data?.cohort_month || ''} · churned within ${data?.window_days || windowDays} days`,
      filename: `sales-analyzer-3mr-${period.replace(/\s+/g, '-')}`,
      sheets: [
        { name: 'By Rep', rows: reps, columns: [
          { header: 'Rep', get: (r: any) => r.rep },
          { header: 'Cohort (acts 3mo ago)', get: (r: any) => r.cohort },
          { header: 'Retained', get: (r: any) => r.retained },
          { header: 'Churned', get: (r: any) => r.churned },
          { header: '3MR %', get: (r: any) => r.retention_pct },
        ] },
        { name: 'Churned line items', rows: churned, columns: [
          { header: 'Rep', get: (r: any) => r.rep },
          { header: 'Phone', get: (r: any) => r.phone_number },
          { header: 'Device / plan', get: (r: any) => r.device_model },
          { header: 'Sold for', get: (r: any) => r.sold_for, money: true },
          { header: 'Cost', get: (r: any) => r.device_cost, money: true },
          { header: 'Margin', get: (r: any) => r.margin, money: true },
          { header: 'Accessory', get: (r: any) => r.accessory_sale, money: true },
          { header: 'Charged MRC', get: (r: any) => r.charged_mrc, money: true },
          { header: 'Loss type', get: (r: any) => r.loss_type },
          { header: 'Why', get: (r: any) => (r.loss_reasons || []).join('; ') },
          { header: 'Store', get: (r: any) => r.store },
          { header: 'Activated', get: (r: any) => r.activation_date },
          { header: 'Churned', get: (r: any) => r.churn_date },
          { header: 'Days active', get: (r: any) => r.days_active },
          { header: 'Reason', get: (r: any) => r.reason },
        ] },
      ],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📉 Retention Analysis — 3-Month</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · cohort = activations in <strong>{data?.cohort_month || '…'}</strong> · churned before the 3rd bill (≤ {windowDays} days)
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Churn window
            <select className="select" value={windowDays} onChange={e => setWindowDays(parseInt(e.target.value))} style={{ marginLeft: 6 }}>
              <option value={60}>60 days</option><option value={90}>90 days (3rd bill)</option><option value={120}>120 days</option>
            </select>
          </label>
          <input className="select" placeholder="filter rep…" value={repFilter} onChange={e => setRepFilter(e.target.value)} style={{ width: 140 }} />
          {data?.reps && <><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></>}
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : data?.error ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {data.error}</div>
      ) : (
        <div style={{ display: 'grid', gap: 16 }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <Tile label="Cohort (acts 3mo ago)" value={t.cohort ?? 0} />
            <Tile label="Retained to 3rd bill" value={t.retained ?? 0} />
            <Tile label="Churned early" value={t.churned ?? 0} accent="#b91c1c" />
            <Tile label="3MR retention" value={`${t.retention_pct ?? 0}%`} accent={(t.retention_pct ?? 0) >= 70 ? '#15803d' : '#b45309'} />
            <Tile label="Lost — sold value" value={fmt(t.lost_value_sold || 0)} />
            <Tile label="Lost — monthly MRC" value={fmt(t.lost_mrc || 0)} />
            <Tile label="Lost — accessory attach" value={fmt(t.lost_accessory || 0)} />
            <Tile label="Employee-driven loss" value={t.employee_driven ?? 0} accent="#b45309" />
            <Tile label="Customer-driven loss" value={t.customer_driven ?? 0} accent="#15803d" />
          </div>

          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>By rep — click a row to see their churned transactions</div>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 640 }}>
              <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Rep</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Cohort</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Retained</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Churned</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>3MR %</th>
              </tr></thead>
              <tbody>
                {reps.map((r: any) => (
                  <tr key={r.rep_login || r.rep} onClick={() => setSelRep(selRep === r.rep_login ? '' : r.rep_login)}
                      style={{ borderTop: '1px solid var(--border)', cursor: 'pointer', background: selRep === r.rep_login ? 'var(--surface2,#eef2ff)' : undefined }}>
                    <td style={{ padding: '7px 12px', fontSize: 13 }}>{r.rep}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{r.cohort}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{r.retained}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, color: r.churned ? '#b91c1c' : 'var(--text3)' }}>{r.churned}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, fontWeight: 600, color: r.retention_pct >= 70 ? '#15803d' : '#b45309' }}>{r.retention_pct}%</td>
                  </tr>
                ))}
                {reps.length === 0 && <tr><td colSpan={5} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No cohort activations found for {data?.cohort_month}.</td></tr>}
              </tbody>
            </table>
          </div>

          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 700, fontSize: 13 }}>Churned transactions{selRep ? ` — ${reps.find((r: any) => r.rep_login === selRep)?.rep || selRep}` : ''} ({churned.length})</span>
              <span style={{ flex: 1 }} />
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>Loss:</span>
              {[['', 'All'], ['employee', '👤 Employee'], ['customer', '🙂 Customer'], ['mixed', '◐ Mixed']].map(([v, l]) => (
                <button key={v} onClick={() => setLossFilter(v)} style={{ padding: '4px 10px', borderRadius: 14, border: '1px solid var(--border)',
                  cursor: 'pointer', fontSize: 12, fontWeight: 600, background: lossFilter === v ? '#1E3A5F' : 'var(--surface)', color: lossFilter === v ? '#fff' : 'var(--text2)' }}>{l}</button>
              ))}
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1120 }}>
              <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Rep</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Phone</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Device / plan</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Sold for</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Cost</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Margin</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Accessory</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Charged (MRC)</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Loss type</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Store</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Days</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Reason</th>
                <th style={{ textAlign: 'center', padding: '8px 12px' }}>→ CB</th>
              </tr></thead>
              <tbody>
                {churned.map((c: any, i: number) => (
                  <tr key={i} style={{ borderTop: '1px solid var(--border)', background: '#fffafa' }}>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}>{c.rep}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}>{c.phone_number}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}>{c.device_model || '—'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right' }}>{c.sold_for ? fmt(c.sold_for) : '—'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right', color: 'var(--text3)' }}>{c.device_cost ? fmt(c.device_cost) : '—'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right', color: (c.margin ?? 0) <= 0 ? '#b91c1c' : '#15803d', fontWeight: 600 }}>{c.sold_for ? fmt(c.margin) : '—'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right', color: (c.accessory_sale ?? 0) <= 0 ? '#b45309' : 'var(--text)' }}>{(c.accessory_sale ?? 0) > 0 ? fmt(c.accessory_sale) : '$0'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right' }}>{c.charged_mrc ? fmt(c.charged_mrc) : '—'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}><LossPill type={c.loss_type} reasons={c.loss_reasons} /></td>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}>{c.store || '—'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right', color: c.fast_churn ? '#b91c1c' : undefined }}>{c.days_active}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}>{c.reason}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'center' }}>
                      {flagged[`${c.phone_number || ''}|${c.activation_date || ''}`]
                        ? <span style={{ color: '#16794a', fontSize: 11 }}>✓ flagged</span>
                        : <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => flagChurn(c)} title="Send to the Chargebacks & Fraud bucket for this rep">🔻 Charge</button>}
                    </td>
                  </tr>
                ))}
                {churned.length === 0 && <tr><td colSpan={13} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No early churn{selRep ? ' for this rep' : ''}{lossFilter ? ` (${lossFilter}-driven)` : ''}.</td></tr>}
              </tbody>
            </table>
          </div>
          {data?.note && <p style={{ fontSize: 12, color: 'var(--text3)' }}>{data.note}</p>}
        </div>
      )}
    </div>
  )
}

function LossPill({ type, reasons }: { type?: string; reasons?: string[] }) {
  const cfg: Record<string, { bg: string; fg: string; label: string }> = {
    employee: { bg: '#fef3c7', fg: '#92400e', label: '👤 Employee' },
    customer: { bg: '#dcfce7', fg: '#15803d', label: '🙂 Customer' },
    mixed: { bg: '#e5e7eb', fg: '#374151', label: '◐ Mixed' },
  }
  const c = cfg[type || 'mixed'] || cfg.mixed
  return (
    <span title={(reasons || []).join(' · ')} style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px',
      borderRadius: 12, background: c.bg, color: c.fg, whiteSpace: 'nowrap', cursor: reasons?.length ? 'help' : 'default' }}>
      {c.label}
    </span>
  )
}

function Tile({ label, value, accent }: { label: string; value: any; accent?: string }) {
  return (
    <div className="card" style={{ padding: '12px 16px', minWidth: 150 }}>
      <div style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, color: accent || 'var(--text)' }}>{value}</div>
    </div>
  )
}
