'use client'
// The Kanban board. Drag a card to the next stage; a stage that demands an outcome refuses the drop
// and asks for one instead of silently accepting it — that refusal is what keeps "why did we lose
// this?" answerable.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import {
  panel, input, label, btn, btnPrimary, fmtMoney, fmtPhone, relTime,
  PRIORITY_COLOR, type Lead, type Stage, type Disposition,
} from '@/lib/crm'

export default function PipelineBoardPage() {
  const [rows, setRows] = useState<Lead[]>([])
  const [stages, setStages] = useState<Stage[]>([])
  const [dispositions, setDispositions] = useState<Disposition[]>([])
  const [reasons, setReasons] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [dragId, setDragId] = useState('')
  const [over, setOver] = useState('')
  const [store, setStore] = useState('')
  const [mine, setMine] = useState(false)
  // A drop that needs an outcome parks here until the rep supplies one.
  const [pending, setPending] = useState<{ lead: Lead; stage: Stage } | null>(null)
  const [dispId, setDispId] = useState('')
  const [reasonId, setReasonId] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setMsg('')
    try {
      const p = new URLSearchParams({ status: 'open', limit: '1000' })
      if (store) p.set('store_code', store)
      if (mine) p.set('mine', 'true')
      const r = await api(`/api/v1/crm/leads?${p}`)
      setRows(r.rows || []); setStages(r.stages || [])
    } catch (e: any) { setMsg(e?.message || String(e)) }
    setLoading(false)
  }, [store, mine])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    Promise.all([api('/api/v1/crm/lists/dispositions'), api('/api/v1/crm/lists/reason-codes')])
      .then(([d, r]) => { setDispositions(d || []); setReasons(r || []) })
      .catch(() => { /* config unreachable — a drop onto a closing stage will 400 with the reason */ })
  }, [])

  async function move(lead: Lead, stage: Stage, disposition_id?: string, reason_code_id?: string) {
    setMsg('')
    // Optimistic: the card jumps immediately, and a refusal snaps it back on reload.
    setRows(p => p.map(l => (l.id === lead.id ? { ...l, stage_id: stage.id, stage_name: stage.name } : l)))
    try {
      await api(`/api/v1/crm/leads/${lead.id}/stage`, {
        method: 'POST',
        body: JSON.stringify({ stage_id: stage.id, disposition_id, reason_code_id }),
      })
      setPending(null); setDispId(''); setReasonId('')
      load()
    } catch (e: any) {
      setMsg(e?.message || String(e))
      load()
    }
  }

  function onDrop(stage: Stage) {
    setOver('')
    const lead = rows.find(l => l.id === dragId)
    setDragId('')
    if (!lead || lead.stage_id === stage.id) return
    if (stage.requires_disposition || stage.is_won || stage.is_lost) {
      setPending({ lead, stage })
      return
    }
    move(lead, stage)
  }

  const chosen = dispositions.find(d => d.id === dispId)
  const ordered = [...stages].sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0))

  return (
    <div style={{ padding: 20 }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🗂️ Pipeline Board</h1>
        <div style={{ flex: 1 }} />
        <input value={store} onChange={e => setStore(e.target.value)} placeholder="Store" style={{ ...input, width: 130 }} />
        <label style={{ fontSize: 13, display: 'flex', gap: 5, alignItems: 'center' }}>
          <input type="checkbox" checked={mine} onChange={e => setMine(e.target.checked)} /> Mine only
        </label>
        <Link href="/crm/leads/new" style={{ ...btnPrimary, textDecoration: 'none' }}>➕ Log a lead</Link>
      </div>

      {msg && <div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626', marginBottom: 12 }}>{msg}</div>}
      {loading && <div style={{ color: 'var(--text2)' }}>Loading…</div>}

      {pending && (
        <div style={{ ...panel, borderColor: '#2563eb', marginBottom: 12 }}>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>
            Moving “{pending.lead.display_name}” to {pending.stage.name} — what happened?
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'end' }}>
            <div style={{ flex: '1 1 220px' }}>
              <span style={label}>Outcome *</span>
              <select value={dispId} onChange={e => { setDispId(e.target.value); setReasonId('') }} style={input}>
                <option value="">Pick…</option>
                {dispositions.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            </div>
            {chosen?.requires_reason && (
              <div style={{ flex: '1 1 200px' }}>
                <span style={label}>Reason *</span>
                <select value={reasonId} onChange={e => setReasonId(e.target.value)} style={input}>
                  <option value="">Pick…</option>
                  {reasons.filter((r: any) => !r.disposition_id || r.disposition_id === dispId)
                    .map((r: any) => <option key={r.id} value={r.id}>{r.name}</option>)}
                </select>
              </div>
            )}
            <button style={btnPrimary} disabled={!dispId || (!!chosen?.requires_reason && !reasonId)}
                    onClick={() => move(pending.lead, pending.stage, dispId, reasonId || undefined)}>
              Move it
            </button>
            <button style={btn} onClick={() => { setPending(null); setDispId(''); setReasonId(''); load() }}>Cancel</button>
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 12, overflowX: 'auto', alignItems: 'flex-start', paddingBottom: 12 }}>
        {ordered.map(stage => {
          const cards = rows.filter(l => l.stage_id === stage.id)
          const value = cards.reduce((s, l) => s + Number(l.value_estimate || 0), 0)
          return (
            <div key={stage.id}
                 onDragOver={e => { e.preventDefault(); setOver(stage.id) }}
                 onDragLeave={() => setOver(o => (o === stage.id ? '' : o))}
                 onDrop={() => onDrop(stage)}
                 style={{
                   ...panel, minWidth: 260, maxWidth: 300, flex: '0 0 auto',
                   background: over === stage.id ? 'var(--surface)' : 'var(--surface2)',
                   borderColor: over === stage.id ? '#2563eb' : 'var(--border)',
                   maxHeight: '75vh', overflowY: 'auto',
                 }}>
              <div style={{ fontWeight: 700, fontSize: 14 }}>
                {stage.is_won ? '🏆 ' : stage.is_lost ? '❌ ' : ''}{stage.name}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text2)', marginBottom: 10 }}>
                {cards.length} · {fmtMoney(value)} · {stage.probability}%
                {stage.sla_hours ? ` · SLA ${stage.sla_hours}h` : ''}
              </div>
              {cards.map(l => (
                <div key={l.id} draggable onDragStart={() => setDragId(l.id)} onDragEnd={() => setDragId('')}
                     style={{
                       border: '1px solid var(--border)', borderLeft: `3px solid ${PRIORITY_COLOR[l.priority]}`,
                       borderRadius: 6, padding: 8, marginBottom: 8, background: 'var(--surface)',
                       cursor: 'grab', opacity: dragId === l.id ? 0.5 : 1,
                     }}>
                  <Link href={`/crm/leads/${l.id}`} style={{ fontWeight: 600, fontSize: 13 }}>{l.display_name}</Link>
                  <div style={{ fontSize: 11, color: 'var(--text2)' }}>{fmtPhone(l.phone)}</div>
                  <div style={{ fontSize: 11, color: 'var(--text2)' }}>
                    {l.interest_name || '—'} · {fmtMoney(l.value_estimate)}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 3 }}>
                    {l.agency_name ? `🤝 ${l.agency_name}` : (l.owner_employee_id || '⚠️ unassigned')}
                    {' · '}{relTime(l.last_activity_at || l.created_at)}
                  </div>
                </div>
              ))}
              {cards.length === 0 && <div style={{ fontSize: 12, color: 'var(--text2)' }}>Empty — drop a lead here.</div>}
            </div>
          )
        })}
        {!loading && ordered.length === 0 && (
          <div style={panel}>No stages configured. <Link href="/crm/settings">Set up the pipeline →</Link></div>
        )}
      </div>
    </div>
  )
}
