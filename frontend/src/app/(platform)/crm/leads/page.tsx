'use client'
// The lead list. Universal filter bar (RULE FIVE), search, multi-select bulk actions, and exports
// through ReportShell (RULE FOUR — what you see is what exports).
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { ExportButtons, type ExportColumn, type ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import {
  panel, input, label, btn, btnPrimary, th, cell, fmtMoney, fmtPhone, fmtDate, relTime,
  STATUS_COLOR, PRIORITY_COLOR, type Lead, type Stage, type RefRow, type Disposition,
} from '@/lib/crm'

export default function LeadsPage() {
  const [rows, setRows] = useState<Lead[]>([])
  const [stages, setStages] = useState<Stage[]>([])
  const [sources, setSources] = useState<RefRow[]>([])
  const [agencies, setAgencies] = useState<RefRow[]>([])
  const [dispositions, setDispositions] = useState<Disposition[]>([])
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [sel, setSel] = useState<Set<string>>(new Set())

  const [q, setQ] = useState('')
  const [status, setStatus] = useState('open')
  const [stageId, setStageId] = useState('')
  const [sourceId, setSourceId] = useState('')
  const [agencyId, setAgencyId] = useState('')
  const [owner, setOwner] = useState('')
  const [storeCode, setStoreCode] = useState('')
  const [market, setMarket] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [mine, setMine] = useState(false)
  const [overdueOnly, setOverdueOnly] = useState(false)

  const [bulkTarget, setBulkTarget] = useState('')
  const [bulkKind, setBulkKind] = useState<'employee' | 'agency'>('employee')

  const load = useCallback(async () => {
    setLoading(true); setMsg('')
    try {
      const p = new URLSearchParams()
      if (q) p.set('q', q)
      if (status) p.set('status', status)
      if (stageId) p.set('stage_id', stageId)
      if (sourceId) p.set('source_id', sourceId)
      if (agencyId) p.set('agency_id', agencyId)
      if (owner) p.set('owner', owner)
      if (storeCode) p.set('store_code', storeCode)
      if (market) p.set('market', market)
      if (start) p.set('start', start)
      if (end) p.set('end', end)
      if (mine) p.set('mine', 'true')
      if (overdueOnly) p.set('overdue_only', 'true')
      const r = await api(`/api/v1/crm/leads?${p}`)
      setRows(r.rows || []); setStages(r.stages || []); setSel(new Set())
      if (r.note) setMsg(r.note)
    } catch (e: any) { setMsg(e?.message || String(e)) }
    setLoading(false)
  }, [q, status, stageId, sourceId, agencyId, owner, storeCode, market, start, end, mine, overdueOnly])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    (async () => {
      try {
        const [s, a, d] = await Promise.all([
          api('/api/v1/crm/lists/sources'), api('/api/v1/crm/lists/agencies'),
          api('/api/v1/crm/lists/dispositions'),
        ])
        setSources(s || []); setAgencies(a || []); setDispositions(d || [])
      } catch { /* config unreachable — filters degrade to free text, the list still works */ }
    })()
  }, [])

  const toggle = (id: string) => setSel(p => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n })
  const allSelected = rows.length > 0 && sel.size === rows.length

  async function bulkAssign() {
    if (!sel.size || !bulkTarget) return
    setMsg('')
    try {
      const body: any = { lead_ids: [...sel], reason: 'bulk assignment' }
      body[bulkKind === 'agency' ? 'agency_id' : 'employee_id'] = bulkTarget
      const r = await api('/api/v1/crm/leads/bulk-assign', { method: 'POST', body: JSON.stringify(body) })
      setMsg(`Assigned ${r.assigned}. ${r.failed?.length ? `${r.failed.length} failed.` : ''}`)
      load()
    } catch (e: any) { setMsg(e?.message || String(e)) }
  }

  async function bulkDispose(dispositionId: string) {
    if (!sel.size || !dispositionId) return
    const d = dispositions.find(x => x.id === dispositionId)
    if (d?.requires_reason) { setMsg(`"${d.name}" needs a reason — dispose these one at a time.`); return }
    setMsg('')
    try {
      const r = await api('/api/v1/crm/leads/bulk-dispose', {
        method: 'POST', body: JSON.stringify({ lead_ids: [...sel], disposition_id: dispositionId }),
      })
      setMsg(`Recorded on ${r.disposed}. ${r.failed?.length ? `${r.failed.length} failed.` : ''}`)
      load()
    } catch (e: any) { setMsg(e?.message || String(e)) }
  }

  // RULE FOUR — the full export set (Excel / PDF / Print / email / WhatsApp) over the CURRENTLY
  // FILTERED rows: what you see is what exports.
  const exportColumns: ExportColumn[] = useMemo(() => [
    { header: 'Lead #', field: 'lead_no', get: (r: Lead) => r.lead_no, type: 'number' },
    { header: 'Name', field: 'name', get: (r: Lead) => r.display_name || '' },
    { header: 'Phone', field: 'phone', get: (r: Lead) => fmtPhone(r.phone) },
    { header: 'Email', field: 'email', get: (r: Lead) => r.email || '' },
    { header: 'Stage', field: 'stage', get: (r: Lead) => r.stage_name || '' },
    { header: 'Status', field: 'status', get: (r: Lead) => r.status },
    { header: 'Source', field: 'source', get: (r: Lead) => r.source_name || '' },
    { header: 'Wants', field: 'interest', get: (r: Lead) => r.interest_name || '' },
    { header: 'Rep', field: 'owner', get: (r: Lead) => r.owner_employee_id || '', role: 'rep' },
    { header: 'Agency', field: 'agency', get: (r: Lead) => r.agency_name || '' },
    { header: 'Store', field: 'store', get: (r: Lead) => r.store_code || '', role: 'store' },
    { header: 'Value', field: 'value', get: (r: Lead) => r.value_estimate, money: true },
    { header: 'Score', field: 'score', get: (r: Lead) => r.score, type: 'number' },
    { header: 'Created', field: 'created', get: (r: Lead) => fmtDate(r.created_at), type: 'date' },
    { header: 'Last activity', field: 'last_activity', get: (r: Lead) => fmtDate(r.last_activity_at), type: 'date' },
    { header: 'Next action', field: 'next_action', get: (r: Lead) => fmtDate(r.next_action_at), type: 'date' },
  ], [])
  const payload = useCallback((): ExportPayload => ({
    title: 'CRM Leads',
    subtitle: [status && `status: ${status}`, q && `search: ${q}`, storeCode && `store: ${storeCode}`,
               market && `market: ${market}`, (start || end) && `${start || '…'} → ${end || '…'}`]
      .filter(Boolean).join(' · '),
    filename: 'crm-leads',
    sheets: [{ name: 'Leads', columns: exportColumns, rows }],
  }), [exportColumns, rows, status, q, storeCode, market, start, end])

  return (
    <div style={{ padding: 20, maxWidth: 1600 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📇 Leads</h1>
        <div style={{ flex: 1 }} />
        <Link href="/crm/leads/new" style={{ ...btnPrimary, textDecoration: 'none' }}>➕ Log a lead</Link>
      </div>

      <div style={{ ...panel, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'end', marginBottom: 12 }}>
        <div style={{ flex: '1 1 220px' }}>
          <span style={label}>Search</span>
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Name, phone, email or lead #" style={input} />
        </div>
        <div><span style={label}>Status</span>
          <select value={status} onChange={e => setStatus(e.target.value)} style={{ ...input, width: 130 }}>
            <option value="">All</option><option value="open">Open</option>
            <option value="won">Won</option><option value="lost">Lost</option>
            <option value="disqualified">Disqualified</option>
          </select>
        </div>
        <div><span style={label}>Stage</span>
          <select value={stageId} onChange={e => setStageId(e.target.value)} style={{ ...input, width: 150 }}>
            <option value="">All stages</option>
            {stages.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
        <div><span style={label}>Source</span>
          <select value={sourceId} onChange={e => setSourceId(e.target.value)} style={{ ...input, width: 150 }}>
            <option value="">All sources</option>
            {sources.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
        <div><span style={label}>Agency</span>
          <select value={agencyId} onChange={e => setAgencyId(e.target.value)} style={{ ...input, width: 150 }}>
            <option value="">Any</option>
            {agencies.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </div>
        <div><span style={label}>Rep</span><input value={owner} onChange={e => setOwner(e.target.value)} placeholder="Employee id" style={{ ...input, width: 120 }} /></div>
        <div><span style={label}>Store</span><input value={storeCode} onChange={e => setStoreCode(e.target.value)} placeholder="All" style={{ ...input, width: 110 }} /></div>
        <div><span style={label}>Market</span><input value={market} onChange={e => setMarket(e.target.value)} placeholder="All" style={{ ...input, width: 110 }} /></div>
        <div><span style={label}>From</span><input type="date" value={start} onChange={e => setStart(e.target.value)} style={{ ...input, width: 140 }} /></div>
        <div><span style={label}>To</span><input type="date" value={end} onChange={e => setEnd(e.target.value)} style={{ ...input, width: 140 }} /></div>
        <label style={{ fontSize: 13, display: 'flex', gap: 5, alignItems: 'center' }}>
          <input type="checkbox" checked={mine} onChange={e => setMine(e.target.checked)} /> Mine only
        </label>
        <label style={{ fontSize: 13, display: 'flex', gap: 5, alignItems: 'center' }}>
          <input type="checkbox" checked={overdueOnly} onChange={e => setOverdueOnly(e.target.checked)} /> Overdue
        </label>
      </div>

      {sel.size > 0 && (
        <div style={{ ...panel, display: 'flex', gap: 10, alignItems: 'end', flexWrap: 'wrap', marginBottom: 12, borderColor: '#2563eb' }}>
          <div style={{ fontWeight: 600, fontSize: 13, alignSelf: 'center' }}>{sel.size} selected</div>
          <div><span style={label}>Assign to</span>
            <select value={bulkKind} onChange={e => { setBulkKind(e.target.value as any); setBulkTarget('') }} style={{ ...input, width: 120 }}>
              <option value="employee">Teammate</option><option value="agency">Agency</option>
            </select>
          </div>
          {bulkKind === 'agency' ? (
            <select value={bulkTarget} onChange={e => setBulkTarget(e.target.value)} style={{ ...input, width: 180 }}>
              <option value="">Pick an agency…</option>
              {agencies.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          ) : (
            <input value={bulkTarget} onChange={e => setBulkTarget(e.target.value)} placeholder="Employee id" style={{ ...input, width: 180 }} />
          )}
          <button onClick={bulkAssign} disabled={!bulkTarget} style={btn}>Assign</button>
          <div style={{ width: 1, alignSelf: 'stretch', background: 'var(--border)' }} />
          <div><span style={label}>Record outcome</span>
            <select onChange={e => { bulkDispose(e.target.value); e.currentTarget.selectedIndex = 0 }} style={{ ...input, width: 200 }}>
              <option value="">Pick an outcome…</option>
              {dispositions.filter(d => !d.requires_reason).map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </div>
          <button onClick={() => setSel(new Set())} style={btn}>Clear selection</button>
        </div>
      )}

      {msg && <div style={{ ...panel, borderColor: '#f39c12', marginBottom: 12 }}>{msg}</div>}

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
        <div style={{ fontSize: 13, color: 'var(--text2)' }}>{rows.length} lead(s)</div>
        <div style={{ flex: 1 }} />
        <ExportButtons payload={payload} compact />
        <SendReportButton title="CRM Leads" exportPayload={payload} compact
                          filters={{ status, stage_id: stageId, q, start, end, store_code: storeCode, market }} />
      </div>

      <div style={{ ...panel, padding: 0 }}>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr>
                <th style={{ ...th, width: 30 }}>
                  <input type="checkbox" checked={allSelected}
                         onChange={e => setSel(e.target.checked ? new Set(rows.map(r => r.id)) : new Set())} />
                </th>
                <th style={th}>#</th><th style={th}>Name</th><th style={th}>Phone</th>
                <th style={th}>Stage</th><th style={th}>Status</th><th style={th}>Source</th>
                <th style={th}>Wants</th><th style={th}>Owner</th><th style={th}>Value</th>
                <th style={th}>Score</th><th style={th}>Next action</th><th style={th}>Last activity</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(l => (
                <tr key={l.id} style={{ background: sel.has(l.id) ? 'var(--surface2)' : undefined }}>
                  <td style={cell}><input type="checkbox" checked={sel.has(l.id)} onChange={() => toggle(l.id)} /></td>
                  <td style={cell}>{l.lead_no}</td>
                  <td style={cell}><Link href={`/crm/leads/${l.id}`} style={{ fontWeight: 600 }}>{l.display_name}</Link></td>
                  <td style={cell}>{fmtPhone(l.phone)}</td>
                  <td style={cell}>{l.stage_name || '—'}</td>
                  <td style={{ ...cell, color: STATUS_COLOR[l.status], fontWeight: 600 }}>{l.status}</td>
                  <td style={cell}>{l.source_name || '—'}</td>
                  <td style={cell}>{l.interest_name || '—'}</td>
                  <td style={cell}>{l.agency_name ? `🤝 ${l.agency_name}` : (l.owner_employee_id || <span style={{ color: '#f39c12' }}>unassigned</span>)}</td>
                  <td style={cell}>{fmtMoney(l.value_estimate)}</td>
                  <td style={{ ...cell, color: PRIORITY_COLOR[l.priority], fontWeight: 600 }}>{l.score}</td>
                  <td style={cell}>{l.next_action_at ? relTime(l.next_action_at) : '—'}</td>
                  <td style={cell}>{relTime(l.last_activity_at || l.created_at)}</td>
                </tr>
              ))}
              {!loading && rows.length === 0 && (
                <tr><td colSpan={13} style={{ ...cell, color: 'var(--text2)', textAlign: 'center', padding: 24 }}>
                  No leads match these filters.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      {loading && <div style={{ color: 'var(--text2)', marginTop: 10 }}>Loading…</div>}
    </div>
  )
}
