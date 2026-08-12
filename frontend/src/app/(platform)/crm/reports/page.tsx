'use client'
// CRM reports — conversion, why we lose, and who is actually working the pipeline. Full export set
// (RULE FOUR) over the currently filtered rows, standard filter bar (RULE FIVE).
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import { ExportButtons, type ExportColumn, type ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { panel, input, label, btn, th, cell, fmtMoney } from '@/lib/crm'

type TabKey = 'conversion' | 'activity' | 'source'

export default function CrmReportsPage() {
  const [tab, setTab] = useState<TabKey>('conversion')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [conv, setConv] = useState<any>(null)
  const [act, setAct] = useState<any[]>([])
  const [summary, setSummary] = useState<any>(null)
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true); setMsg('')
    const p = new URLSearchParams()
    if (start) p.set('start', start)
    if (end) p.set('end', end)
    try {
      const [c, a, s] = await Promise.all([
        api(`/api/v1/crm/reports/conversion?${p}`),
        api(`/api/v1/crm/reports/activity?${p}`),
        api(`/api/v1/crm/summary?${p}`),
      ])
      setConv(c); setAct(a?.rows || []); setSummary(s)
    } catch (e: any) { setMsg(e?.message || String(e)) }
    setLoading(false)
  }, [start, end])
  useEffect(() => { load() }, [load])

  const { columns, rows, title } = useMemo((): { columns: ExportColumn[]; rows: any[]; title: string } => {
    if (tab === 'activity') {
      return {
        title: 'CRM Rep Activity',
        rows: act,
        columns: [
          { header: 'Rep', field: 'employee_id', get: (r: any) => r.employee_id, role: 'rep' },
          { header: 'Total touches', field: 'total', get: (r: any) => r.total, type: 'number' },
          { header: 'Calls', field: 'call', get: (r: any) => r.call || 0, type: 'number' },
          { header: 'Texts', field: 'sms', get: (r: any) => r.sms || 0, type: 'number' },
          { header: 'Emails', field: 'email', get: (r: any) => r.email || 0, type: 'number' },
          { header: 'Notes', field: 'note', get: (r: any) => r.note || 0, type: 'number' },
          { header: 'Outcomes recorded', field: 'disposition', get: (r: any) => r.disposition || 0, type: 'number' },
        ],
      }
    }
    if (tab === 'source') {
      return {
        title: 'CRM Source ROI',
        rows: summary?.by_source || [],
        columns: [
          { header: 'Source', field: 'source', get: (r: any) => r.source },
          { header: 'Leads', field: 'leads', get: (r: any) => r.leads, type: 'number' },
          { header: 'Won', field: 'won', get: (r: any) => r.won, type: 'number' },
          { header: 'Conversion %', field: 'conversion', get: (r: any) => r.conversion, type: 'number' },
          { header: 'Won value', field: 'value', get: (r: any) => r.value, money: true },
        ],
      }
    }
    return {
      title: 'CRM Conversion',
      rows: conv?.by_stage || [],
      columns: [
        { header: 'Stage', field: 'stage', get: (r: any) => r.stage },
        { header: 'Leads', field: 'count', get: (r: any) => r.count, type: 'number' },
        { header: 'Value', field: 'value', get: (r: any) => r.value, money: true },
        { header: 'Probability %', field: 'probability', get: (r: any) => r.probability, type: 'number' },
      ],
    }
  }, [tab, act, conv, summary])

  const payload = useCallback((): ExportPayload => ({
    title,
    subtitle: (start || end) ? `${start || '…'} → ${end || '…'}` : 'all time',
    filename: title.toLowerCase().replace(/\s+/g, '-'),
    sheets: [{ name: 'Report', columns, rows }],
  }), [title, columns, rows, start, end])

  return (
    <div style={{ padding: 20, maxWidth: 1200 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 12 }}>📈 CRM Reports</h1>

      <div style={{ ...panel, display: 'flex', gap: 10, alignItems: 'end', flexWrap: 'wrap', marginBottom: 12 }}>
        <div><span style={label}>From</span><input type="date" value={start} onChange={e => setStart(e.target.value)} style={{ ...input, width: 150 }} /></div>
        <div><span style={label}>To</span><input type="date" value={end} onChange={e => setEnd(e.target.value)} style={{ ...input, width: 150 }} /></div>
        <button onClick={load} style={btn}>Apply</button>
        <div style={{ flex: 1 }} />
        <ExportButtons payload={payload} compact />
        <SendReportButton title={title} exportPayload={payload} compact filters={{ start, end, report: tab }} />
      </div>

      <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
        {([['conversion', 'Conversion & funnel'], ['source', 'Where leads come from'], ['activity', 'Rep activity']] as [TabKey, string][])
          .map(([k, l]) => (
            <button key={k} onClick={() => setTab(k)}
                    style={{ ...btn, background: tab === k ? '#2563eb' : 'var(--surface)',
                             borderColor: tab === k ? '#2563eb' : 'var(--border)',
                             color: tab === k ? '#fff' : 'var(--text)' }}>{l}</button>
          ))}
      </div>

      {msg && <div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626', marginBottom: 12 }}>{msg}</div>}
      {loading && <div style={{ color: 'var(--text2)' }}>Loading…</div>}

      {tab === 'conversion' && conv && (
        <div style={{ ...panel, marginBottom: 12, display: 'flex', gap: 24, flexWrap: 'wrap' }}>
          <div><div style={{ fontSize: 11, color: 'var(--text2)' }}>TOTAL</div><div style={{ fontSize: 22, fontWeight: 700 }}>{conv.totals.total}</div></div>
          <div><div style={{ fontSize: 11, color: 'var(--text2)' }}>WON</div><div style={{ fontSize: 22, fontWeight: 700, color: '#16a34a' }}>{conv.totals.won}</div></div>
          <div><div style={{ fontSize: 11, color: 'var(--text2)' }}>LOST</div><div style={{ fontSize: 22, fontWeight: 700, color: '#dc2626' }}>{conv.totals.lost}</div></div>
          <div><div style={{ fontSize: 11, color: 'var(--text2)' }}>WIN RATE</div><div style={{ fontSize: 22, fontWeight: 700 }}>{conv.totals.win_rate}%</div></div>
          <div><div style={{ fontSize: 11, color: 'var(--text2)' }}>CLOSE RATE</div><div style={{ fontSize: 22, fontWeight: 700 }}>{conv.totals.close_rate}%</div></div>
        </div>
      )}

      <div style={{ ...panel, padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr>{columns.map(c => <th key={c.header} style={th}>{c.header}</th>)}</tr></thead>
          <tbody>
            {rows.map((r: any, i: number) => (
              <tr key={i}>{columns.map(c => (
                <td key={c.header} style={cell}>{c.money ? fmtMoney(Number(c.get(r))) : String(c.get(r) ?? '—')}</td>
              ))}</tr>
            ))}
            {!loading && rows.length === 0 && (
              <tr><td colSpan={columns.length} style={{ ...cell, color: 'var(--text2)', textAlign: 'center', padding: 20 }}>Nothing in this period.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {tab === 'conversion' && conv?.lost_reasons?.length > 0 && (
        <div style={{ ...panel, marginTop: 14 }}>
          <div style={{ fontWeight: 700, marginBottom: 8 }}>Why we lose</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr><th style={th}>Reason</th><th style={th}>Count</th></tr></thead>
            <tbody>{conv.lost_reasons.map((r: any) => (
              <tr key={r.reason}><td style={cell}>{r.reason}</td><td style={cell}>{r.count}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  )
}
