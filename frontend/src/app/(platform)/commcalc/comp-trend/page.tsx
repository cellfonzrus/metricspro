'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

export default function CompTrendPage() {
  // 'rep' = the commission WE pay each rep (rep_commissions.total_payout) — what "commission being
  // paid to the sales rep" means. 'account' = the legacy account-level carrier-comp trend.
  const [view, setView] = useState<'rep' | 'account'>('rep')
  const [months, setMonths] = useState(6)
  const [storeFilter, setStoreFilter] = useState('')
  const [monthFilter, setMonthFilter] = useState('')
  const [data, setData] = useState<any>(null)
  const [repData, setRepData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  function load() {
    setLoading(true)
    const url = view === 'rep'
      ? `/api/v1/commcalc/comp/rep-pay-trend?months=${months}&org_id=${ORG_ID}`
      : `/api/v1/commcalc/comp/residual-trend?months=${months}&org_id=${ORG_ID}`
    api(url)
      .then((d) => view === 'rep' ? setRepData(d) : setData(d))
      .catch((e) => view === 'rep' ? setRepData({ error: e?.message || String(e) }) : setData({ error: e?.message || String(e) }))
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [months, view])

  const totals = data?.totals_by_month || []
  const allDips = data?.dips || []
  const dips = allDips.filter((d: any) =>
    (!monthFilter || d.period === monthFilter) &&
    (!storeFilter || (d.store || '').toLowerCase().includes(storeFilter.toLowerCase()) ||
                     (d.business_name || '').toLowerCase().includes(storeFilter.toLowerCase())))

  const latest = totals[totals.length - 1] || {}
  const lostThisMonth = dips.filter((d: any) => !monthFilter || d.period === monthFilter)
    .reduce((s: number, d: any) => s + Math.abs(d.delta || 0), 0)
  const vanished = dips.filter((d: any) => d.vanished).length

  // ── per-rep view (the commission WE pay each rep) ──
  const repMonths: string[] = repData?.months || []
  const repsAll: any[] = repData?.reps || []
  const reps = repsAll.filter((r: any) => !storeFilter ||
    (r.store || '').toLowerCase().includes(storeFilter.toLowerCase()) ||
    (r.rep || '').toLowerCase().includes(storeFilter.toLowerCase()))
  const latestRepMonth = repMonths[repMonths.length - 1] || ''
  const repPerMonth = (m: string) => reps.reduce((s: number, r: any) => s + (r.by_period?.[m] || 0), 0)
  const repGrandTotal = reps.reduce((s: number, r: any) => s + (r.total || 0), 0)
  const latestRepTotal = repPerMonth(latestRepMonth)

  function buildRepPayload(): ExportPayload {
    return {
      title: 'Commission Paid per Rep', subtitle: `total_payout · last ${months} months${storeFilter ? ` · ${storeFilter}` : ''}`,
      filename: `commission-per-rep`,
      sheets: [{ name: 'By rep', rows: reps, columns: [
        { header: 'Rep', get: (r: any) => r.rep },
        { header: 'Store', get: (r: any) => r.store || '' },
        ...repMonths.map((m) => ({ header: m, get: (r: any) => r.by_period?.[m] || 0, money: true })),
        { header: 'Total', get: (r: any) => r.total, money: true },
      ] }],
    }
  }

  function buildPayload(): ExportPayload {
    return {
      title: 'Total Compensation Trend', subtitle: `last ${months} months${monthFilter ? ` · ${monthFilter}` : ''}${storeFilter ? ` · ${storeFilter}` : ''}`,
      filename: `total-compensation-trend`,
      sheets: [
        { name: 'By month', rows: totals, columns: [
          { header: 'Month', get: (r: any) => r.period },
          { header: 'Total Comp', get: (r: any) => r.total_comp ?? r.residual, money: true },
          { header: 'Residual (MI+ATU)', get: (r: any) => r.residual_mi_atu, money: true },
          { header: 'Accounts', get: (r: any) => r.accounts },
          { header: 'Δ vs prev', get: (r: any) => r.delta_vs_prev, money: true },
          { header: '% vs prev', get: (r: any) => r.pct_vs_prev },
        ] },
        { name: 'Dips', rows: dips, columns: [
          { header: 'Dipped in', get: (r: any) => r.period },
          { header: 'From month', get: (r: any) => r.prev_period },
          { header: 'Account', get: (r: any) => r.account_id },
          { header: 'Business', get: (r: any) => r.business_name },
          { header: 'Store', get: (r: any) => r.store },
          { header: 'Was', get: (r: any) => r.prev_residual, money: true },
          { header: 'Now', get: (r: any) => r.residual, money: true },
          { header: 'Drop', get: (r: any) => r.delta, money: true },
          { header: '%', get: (r: any) => r.pct },
          { header: 'Reason', get: (r: any) => r.reason },
        ] },
      ],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📊 Total Compensation</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {view === 'rep'
              ? <>The <strong>commission we actually pay each rep</strong> (the spiff stack × KPI tier = rep payout), month over month. Switch to <em>By account</em> for the carrier-compensation trend.</>
              : <>Month-over-month <strong>total carrier compensation</strong> per account from the Comprehensive Comp report (~95% promos + bounties = Commission + SPIFF, <em>not</em> residual). True <strong>Residual = MI + ATU</strong> shown alongside. A <strong>dip</strong> = comp that fell or an account that vanished.</>}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
            <button className="btn" style={{ borderRadius: 0, border: 'none', background: view === 'rep' ? 'var(--accent)' : 'transparent', color: view === 'rep' ? 'white' : 'var(--text2)' }} onClick={() => setView('rep')}>By rep</button>
            <button className="btn" style={{ borderRadius: 0, border: 'none', background: view === 'account' ? 'var(--accent)' : 'transparent', color: view === 'account' ? 'white' : 'var(--text2)' }} onClick={() => setView('account')}>By account</button>
          </div>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Window
            <select className="select" value={months} onChange={e => setMonths(parseInt(e.target.value))} style={{ marginLeft: 6 }}>
              <option value={3}>3 months</option><option value={6}>6 months</option><option value={12}>12 months</option>
            </select>
          </label>
          <input className="select" placeholder={view === 'rep' ? 'filter rep / store…' : 'filter store / business…'} value={storeFilter} onChange={e => setStoreFilter(e.target.value)} style={{ width: 180 }} />
          {view === 'rep'
            ? (repData?.reps?.length ? <><ExportButtons payload={buildRepPayload} /><SendReportButton exportPayload={buildRepPayload} compact /></> : null)
            : (data?.totals_by_month ? <><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></> : null)}
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : view === 'rep' ? (
        repData?.error ? (
          <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {repData.error}</div>
        ) : repData?.note ? (
          <div className="card" style={{ padding: 16, color: 'var(--text2)' }}>{repData.note}</div>
        ) : (
          <div style={{ display: 'grid', gap: 16 }}>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              <Tile label={`Paid to reps — ${latestRepMonth || 'latest'}`} value={fmt(latestRepTotal)} accent="#15803d" />
              <Tile label={`Total — last ${months} mo`} value={fmt(repGrandTotal)} />
              <Tile label="Reps paid" value={reps.length} />
              <Tile label="Top rep" value={reps[0]?.rep || '—'} />
            </div>
            <div className="card" style={{ padding: 0, overflow: 'auto' }}>
              <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>
                Commission paid per rep (total payout) — newest month last; highest total first
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 640 }}>
                <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                  <th style={{ textAlign: 'left', padding: '8px 12px' }}>Rep</th>
                  <th style={{ textAlign: 'left', padding: '8px 12px' }}>Store</th>
                  {repMonths.map((m) => <th key={m} style={{ textAlign: 'right', padding: '8px 12px' }}>{m}</th>)}
                  <th style={{ textAlign: 'right', padding: '8px 12px' }}>Total</th>
                </tr></thead>
                <tbody>
                  {reps.map((r: any) => (
                    <tr key={r.rep} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '7px 12px', fontSize: 13, fontWeight: 600 }}>{r.rep}</td>
                      <td style={{ padding: '7px 12px', fontSize: 12, color: 'var(--text3)' }}>{r.store || '—'}</td>
                      {repMonths.map((m) => <td key={m} style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{r.by_period?.[m] ? fmt(r.by_period[m]) : '—'}</td>)}
                      <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, fontWeight: 700 }}>{fmt(r.total)}</td>
                    </tr>
                  ))}
                  {reps.length === 0 && <tr><td colSpan={repMonths.length + 3} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No commissions{storeFilter ? ' match the filter' : ' calculated yet'}.</td></tr>}
                </tbody>
                {reps.length > 0 && (
                  <tfoot><tr style={{ borderTop: '2px solid var(--border)', background: 'var(--surface2)' }}>
                    <td style={{ padding: '8px 12px', fontSize: 12, fontWeight: 700 }}>All reps</td>
                    <td />
                    {repMonths.map((m) => <td key={m} style={{ padding: '8px 12px', textAlign: 'right', fontSize: 13, fontWeight: 700 }}>{fmt(repPerMonth(m))}</td>)}
                    <td style={{ padding: '8px 12px', textAlign: 'right', fontSize: 13, fontWeight: 700 }}>{fmt(repGrandTotal)}</td>
                  </tr></tfoot>
                )}
              </table>
            </div>
          </div>
        )
      ) : data?.error ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {data.error}</div>
      ) : data?.note ? (
        <div className="card" style={{ padding: 16, color: 'var(--text2)' }}>{data.note}</div>
      ) : (
        <div style={{ display: 'grid', gap: 16 }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <Tile label={`Total comp — ${latest.period || 'latest'}`} value={fmt(latest.total_comp ?? latest.residual ?? 0)} />
            <Tile label={`Residual (MI+ATU) — ${latest.period || 'latest'}`} value={fmt(latest.residual_mi_atu || 0)} accent="#15803d" />
            <Tile label="Δ vs prior month" value={fmt(latest.delta_vs_prev || 0)} accent={(latest.delta_vs_prev || 0) < 0 ? '#b91c1c' : '#15803d'} />
            <Tile label="Active accounts" value={latest.accounts ?? 0} />
            <Tile label={`Comp lost to dips${monthFilter ? '' : ' (all shown)'}`} value={fmt(lostThisMonth)} accent="#b91c1c" />
            <Tile label="Accounts vanished" value={vanished} accent="#b45309" />
          </div>

          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>By month — total compensation vs. true residual (MI+ATU). Click a month to filter the dips below</div>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 640 }}>
              <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Month</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Total Comp</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }} title="From carrier_category_map (migration 038)">Commission</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>SPIFF</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Reimb</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Residual (MI+ATU)</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Accounts</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Δ vs prev</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>% vs prev</th>
              </tr></thead>
              <tbody>
                {totals.map((r: any) => (
                  <tr key={r.period} onClick={() => setMonthFilter(monthFilter === r.period ? '' : r.period)}
                      style={{ borderTop: '1px solid var(--border)', cursor: 'pointer', background: monthFilter === r.period ? 'var(--surface2,#eef2ff)' : undefined }}>
                    <td style={{ padding: '7px 12px', fontSize: 13, fontWeight: 600 }}>{r.period}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(r.total_comp ?? r.residual)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, color: '#16794a' }}>{fmt(r.components?.COMMISSION || 0)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, color: '#b45309' }}>{fmt(r.components?.SPIFF || 0)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, color: '#7c3aed' }}>{fmt(r.components?.REIMBURSEMENT || 0)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, color: '#15803d' }}>{fmt(r.residual_mi_atu || 0)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{r.accounts}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, color: (r.delta_vs_prev ?? 0) < 0 ? '#b91c1c' : ((r.delta_vs_prev ?? 0) > 0 ? '#15803d' : 'var(--text3)') }}>{r.delta_vs_prev == null ? '—' : fmt(r.delta_vs_prev)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, color: (r.pct_vs_prev ?? 0) < 0 ? '#b91c1c' : 'var(--text3)' }}>{r.pct_vs_prev == null ? '—' : `${r.pct_vs_prev}%`}</td>
                  </tr>
                ))}
                {totals.length === 0 && <tr><td colSpan={9} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No comp data.</td></tr>}
              </tbody>
            </table>
          </div>

          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>
              Residual dips{monthFilter ? ` — ${monthFilter}` : ''} ({dips.length}) — biggest drop first
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 980 }}>
              <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Dipped in</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Account</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Business / store</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Was</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Now</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Drop</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>%</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Why</th>
              </tr></thead>
              <tbody>
                {dips.map((d: any, i: number) => (
                  <tr key={i} style={{ borderTop: '1px solid var(--border)', background: d.vanished ? '#fff7ed' : '#fffafa' }}>
                    <td style={{ padding: '6px 12px', fontSize: 12, fontWeight: 600 }}>{d.period}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}>{d.account_id || '—'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}>{d.business_name || d.store || '—'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(d.prev_residual)}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right', color: d.residual === 0 ? '#b91c1c' : undefined }}>{fmt(d.residual)}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right', color: '#b91c1c', fontWeight: 600 }}>{fmt(d.delta)}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right' }}>{d.pct}%</td>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}>{d.reason}</td>
                  </tr>
                ))}
                {dips.length === 0 && <tr><td colSpan={8} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No residual dips{monthFilter ? ` in ${monthFilter}` : ''}.</td></tr>}
              </tbody>
            </table>
          </div>
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
