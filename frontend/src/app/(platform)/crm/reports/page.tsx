'use client'
// CRM reports — conversion, why we lose, and who is actually working the pipeline. Full export set
// (RULE FOUR) over the currently filtered rows, standard filter bar (RULE FIVE).
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import { ExportButtons, type ExportColumn, type ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { panel, input, label, btn, th, cell, fmtMoney } from '@/lib/crm'
import DrillDownPanel, { DrillTileLinks, useDrillTile, type DrillPanel, type DrillRow } from '@/components/DrillDownPanel'

// WHAT IDENTIFIES ONE LEAD IN A DRILL-DOWN — the client side of `crm/reports_drilldown._lead_fields`.
// `Stage` is the one that earns its comment: a lead whose stage is not in the stage vocabulary is
// DROPPED by `pipeline_core.funnel` while the TOTAL tile still counts it, so the panel says which
// rows those are rather than showing them as a stage of their own.
const LEAD_COLUMNS = [
  { header: 'State', get: (r: DrillRow) => String(r.fields?.state || '—'),
    sortValue: (r: DrillRow) => String(r.fields?.state || '') },
  { header: 'Stage', get: (r: DrillRow) => (r.reason === 'stage_unknown'
      ? 'not in the stage list' : String(r.fields?.stage || '—')),
    sortValue: (r: DrillRow) => String(r.fields?.stage || '') },
  { header: 'Source', get: (r: DrillRow) => String(r.fields?.source || '—'),
    sortValue: (r: DrillRow) => String(r.fields?.source || '') },
  { header: 'Lost reason', get: (r: DrillRow) => String(r.fields?.lost_reason || '—'),
    sortValue: (r: DrillRow) => String(r.fields?.lost_reason || '') },
  { header: 'Value', get: (r: DrillRow) => (r.fields?.value == null ? '—' : fmtMoney(Number(r.fields.value))),
    sortValue: (r: DrillRow) => Number(r.fields?.value || 0) },
  { header: 'Owner', get: (r: DrillRow) => String(r.fields?.owner || '—'),
    sortValue: (r: DrillRow) => String(r.fields?.owner || '') },
]

// And one logged touch — the client side of `_activity_fields`.
const ACTIVITY_COLUMNS = [
  { header: 'Kind', get: (r: DrillRow) => String(r.fields?.kind || '—'),
    sortValue: (r: DrillRow) => String(r.fields?.kind || '') },
  { header: 'Rep', get: (r: DrillRow) => String(r.fields?.rep || '—'),
    sortValue: (r: DrillRow) => String(r.fields?.rep || '') },
  { header: 'Lead', get: (r: DrillRow) => String(r.fields?.lead || '—'),
    sortValue: (r: DrillRow) => String(r.fields?.lead || '') },
  { header: 'Logged', get: (r: DrillRow) => String(r.fields?.logged_at || '—'),
    sortValue: (r: DrillRow) => String(r.fields?.logged_at || '') },
]

type TabKey = 'conversion' | 'activity' | 'source'

/** A table LINE on any of the three tabs, in the only respect this screen reads one: the key its own
 *  endpoint bucketed its panels on, and (on the funnel) the count the stages add up to. */
type ReportLine = { employee_id?: string; source?: string; stage?: string; count?: number }

export default function CrmReportsPage() {
  const [tab, setTab] = useState<TabKey>('conversion')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [conv, setConv] = useState<any>(null)
  const [act, setAct] = useState<any[]>([])
  // The activity response WHOLE, not just its rows: the per-rep panels ride on it (§CORE-17).
  const [actMeta, setActMeta] = useState<{ drilldown_by_rep?: Record<string, DrillPanel> } | null>(null)
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
      setConv(c); setAct(a?.rows || []); setActMeta(a); setSummary(s)
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

  // EVERY FIGURE OPENS TO ITS RECORDS (§CORE-17). No narrowing is needed or applied: the only
  // filters on this screen are the two dates and they are query parameters, so the server folded
  // exactly the leads shown.
  //
  // A RATE GETS NO PANEL. `win_rate` and `close_rate` are quotients — there is no set of leads that
  // adds up to 34% — so they offer the COUNTS they divide instead, and the server serves no panel
  // under those keys at all (crm/reports_drilldown.lead_catalogue).
  const tilePanels: Record<string, DrillPanel> = conv?.drilldown || {}
  const tileDrill = useDrillTile(tilePanels)
  const dl = tileDrill.dl

  // ONE PANEL PER TABLE ROW, from whichever grouping the open tab is showing. Each map is keyed by
  // the SAME key its own endpoint bucketed on — stage name, source name, `actor_employee_id` — so a
  // row's panel holds exactly the records that row counted, not a regrouping of them.
  const { rowPanels, rowKeyOfLine, rowColumns } = useMemo(() => {
    if (tab === 'activity') {
      return { rowPanels: (actMeta?.drilldown_by_rep || {}) as Record<string, DrillPanel>,
               rowKeyOfLine: (r: ReportLine) => String(r?.employee_id ?? ''), rowColumns: ACTIVITY_COLUMNS }
    }
    if (tab === 'source') {
      return { rowPanels: (summary?.drilldown_by_source || {}) as Record<string, DrillPanel>,
               rowKeyOfLine: (r: ReportLine) => String(r?.source ?? ''), rowColumns: LEAD_COLUMNS }
    }
    return { rowPanels: (conv?.drilldown_by_stage || {}) as Record<string, DrillPanel>,
             rowKeyOfLine: (r: ReportLine) => String(r?.stage ?? ''), rowColumns: LEAD_COLUMNS }
  }, [tab, actMeta, summary, conv])
  const rowDrill = useDrillTile(rowPanels)
  const hasRowPanels = Object.keys(rowPanels).length > 0

  // REPORTED, NOT PAPERED OVER: `pipeline_core.funnel` drops a lead whose stage is not in the stage
  // vocabulary, while the TOTAL tile counts every lead. So the funnel can add up to less than the
  // total on the same screen. The figures are left alone and the gap is stated.
  const funnelSum = (conv?.by_stage || []).reduce((n: number, r: ReportLine) => n + Number(r?.count || 0), 0)
  const funnelShort = tab === 'conversion' && conv ? Number(conv.totals?.total || 0) - funnelSum : 0

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

      {msg && <div style={{ ...panel, borderColor: 'var(--c-bg-dc2626)', color: 'var(--c-fg-dc2626)', marginBottom: 12 }}>{msg}</div>}
      {loading && <div style={{ color: 'var(--text2)' }}>Loading…</div>}

      {tab === 'conversion' && conv && (
        <>
        <div style={{ ...panel, marginBottom: 12, display: 'flex', gap: 24, flexWrap: 'wrap' }}>
          <div><div style={{ fontSize: 11, color: 'var(--text2)' }}>TOTAL</div><div style={{ fontSize: 22, fontWeight: 700 }}>{conv.totals.total}</div>
            <DrillTileLinks drills={dl('total', 'the leads')} open={tileDrill.open} onToggle={tileDrill.toggle} /></div>
          <div><div style={{ fontSize: 11, color: 'var(--text2)' }}>WON</div><div style={{ fontSize: 22, fontWeight: 700, color: 'var(--c-fg-16a34a)' }}>{conv.totals.won}</div>
            <DrillTileLinks drills={dl('won', 'the leads')} open={tileDrill.open} onToggle={tileDrill.toggle} /></div>
          <div><div style={{ fontSize: 11, color: 'var(--text2)' }}>LOST</div><div style={{ fontSize: 22, fontWeight: 700, color: 'var(--c-fg-dc2626)' }}>{conv.totals.lost}</div>
            <DrillTileLinks drills={dl('lost', 'the leads')} open={tileDrill.open} onToggle={tileDrill.toggle} /></div>
          {/* A PERCENTAGE HAS NO LEADS THAT ADD UP TO IT, so these two offer the counts they divide
              rather than a panel that could only pretend to tie out. Win rate is won over the closed
              leads; close rate is the closed leads over the total. */}
          <div><div style={{ fontSize: 11, color: 'var(--text2)' }}>WIN RATE</div><div style={{ fontSize: 22, fontWeight: 700 }}>{conv.totals.win_rate}%</div>
            <DrillTileLinks drills={dl('won', 'won', 'lost', 'lost')} open={tileDrill.open} onToggle={tileDrill.toggle} /></div>
          <div><div style={{ fontSize: 11, color: 'var(--text2)' }}>CLOSE RATE</div><div style={{ fontSize: 22, fontWeight: 700 }}>{conv.totals.close_rate}%</div>
            <DrillTileLinks drills={dl('won', 'won', 'lost', 'lost', 'total', 'all leads')} open={tileDrill.open} onToggle={tileDrill.toggle} /></div>
        </div>
        <DrillDownPanel panel={tileDrill.panel} extraColumns={LEAD_COLUMNS} />
        {funnelShort > 0 && (
          <div style={{ ...panel, marginBottom: 12, fontSize: 12, color: 'var(--text2)' }}>
            The funnel below adds up to {funnelSum} of the {conv.totals.total} leads in this period.
            The {funnelShort} missing {funnelShort === 1 ? 'lead is' : 'leads are'} in a stage that is
            not in your stage list, so the funnel drops {funnelShort === 1 ? 'it' : 'them'} while the
            total still counts {funnelShort === 1 ? 'it' : 'them'}. Open TOTAL above to see which.
          </div>
        )}
        </>
      )}

      <div style={{ ...panel, padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr>{columns.map(c => <th key={c.header} style={th}>{c.header}</th>)}
            {/* A row whose grouping the server served no panel for gets no link, rather than one
                that opens nothing (§CORE-17.3c). */}
            {hasRowPanels && <th style={th}>Evidence</th>}</tr></thead>
          <tbody>
            {rows.map((r: any, i: number) => (
              <tr key={i}>{columns.map(c => (
                // A column that draws its own cell draws it here too. This table is the CRM's own
                // rather than the shared report table, so it would otherwise be a second answer to "what does a
                // body cell show" — the exact split that left three takeover columns blank until
                // 2026-10-15. `harness_column_render_lock.py` holds every such table to this.
                <td key={c.header} style={cell}>{c.render ? c.render(r)
                  : (c.money ? fmtMoney(Number(c.get(r))) : String(c.get(r) ?? '—'))}</td>
              ))}
                {hasRowPanels && (
                  <td style={cell}>
                    <DrillTileLinks drills={rowDrill.dl(rowKeyOfLine(r), 'open')}
                                    open={rowDrill.open} onToggle={rowDrill.toggle} />
                  </td>
                )}</tr>
            ))}
            {!loading && rows.length === 0 && (
              <tr><td colSpan={columns.length + (hasRowPanels ? 1 : 0)} style={{ ...cell, color: 'var(--text2)', textAlign: 'center', padding: 20 }}>Nothing in this period.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <DrillDownPanel panel={rowDrill.panel} extraColumns={rowColumns} />

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
