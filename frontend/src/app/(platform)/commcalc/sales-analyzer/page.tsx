'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload } from '@/lib/export'

export default function SalesAnalyzerPage() {
  const { period } = usePeriod()
  const [windowDays, setWindowDays] = useState(90)
  const [repFilter, setRepFilter] = useState('')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [selRep, setSelRep] = useState('')
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
    (!selRep || c.rep_login === selRep))
  const t = data?.totals || {}

  function buildPayload(): ExportPayload {
    return {
      title: `Sales Analyzer — 3-Month Retention`, subtitle: `${period} · cohort ${data?.cohort_month || ''} · churned within ${data?.window_days || windowDays} days`,
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
          { header: 'Charged MRC', get: (r: any) => r.charged_mrc, money: true },
          { header: 'Sold for', get: (r: any) => r.sold_for, money: true },
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
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📉 Sales Analyzer — 3-Month Retention</h1>
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
          {data?.reps && <ExportButtons payload={buildPayload} />}
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
            <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>
              Churned transactions{selRep ? ` — ${reps.find((r: any) => r.rep_login === selRep)?.rep || selRep}` : ''} ({churned.length})
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 920 }}>
              <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Rep</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Phone</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Device / plan</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Charged (MRC)</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Sold for</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Store</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Activated</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Churned</th>
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
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right' }}>{c.charged_mrc ? fmt(c.charged_mrc) : '—'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right' }}>{c.sold_for ? fmt(c.sold_for) : '—'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}>{c.store || '—'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}>{c.activation_date || '—'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, color: '#b91c1c' }}>{c.churn_date || '—'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right' }}>{c.days_active}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}>{c.reason}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'center' }}>
                      {flagged[`${c.phone_number || ''}|${c.activation_date || ''}`]
                        ? <span style={{ color: '#16794a', fontSize: 11 }}>✓ flagged</span>
                        : <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => flagChurn(c)} title="Send to the Chargebacks & Fraud bucket for this rep">🔻 Charge</button>}
                    </td>
                  </tr>
                ))}
                {churned.length === 0 && <tr><td colSpan={11} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No early churn{selRep ? ' for this rep' : ''}.</td></tr>}
              </tbody>
            </table>
          </div>
          {data?.note && <p style={{ fontSize: 12, color: 'var(--text3)' }}>{data.note}</p>}
        </div>
      )}
    </div>
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
