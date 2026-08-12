'use client'
// CRM setup — the tenant's own pipeline, stages, sources, outcomes, follow-up cadences, routing and
// scoring. RULE TWO: none of this is hard-coded, so a tenant that sells differently configures it
// rather than asking for a code change.
import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { panel, input, label, btn, btnPrimary, th, cell, type CrmConfig } from '@/lib/crm'

type ListName = 'pipelines' | 'stages' | 'sources' | 'interests' | 'dispositions' | 'reason-codes'
  | 'cadences' | 'cadence-steps' | 'assignment-rules' | 'score-rules' | 'queues'

const TABS: { key: ListName; label: string; cols: string[]; hint: string }[] = [
  { key: 'pipelines', label: 'Pipelines', cols: ['key', 'name', 'description', 'is_default', 'sort_order'],
    hint: 'A sales process. Most tenants need one; add another when B2B or FWA is genuinely worked differently.' },
  { key: 'stages', label: 'Stages', cols: ['pipeline_id', 'key', 'name', 'sort_order', 'probability', 'is_won', 'is_lost', 'sla_hours', 'requires_disposition'],
    hint: 'The columns on the board. `probability` drives the weighted forecast; `sla_hours` is how long a lead may sit here before it escalates.' },
  { key: 'sources', label: 'Lead sources', cols: ['key', 'name', 'category', 'sort_order'],
    hint: 'Where leads come from. This is what the source-ROI report groups by.' },
  { key: 'interests', label: 'What they want', cols: ['key', 'name', 'category', 'sort_order'],
    hint: 'New line, upgrade, FWA, accessories, business…' },
  { key: 'dispositions', label: 'Outcomes', cols: ['key', 'name', 'outcome', 'requires_followup', 'default_followup_hours', 'requires_reason', 'closes_lead', 'sort_order'],
    hint: 'What a rep picks after every touch. `requires_followup` is what books the next step automatically; `requires_reason` is what makes "why did we lose it?" answerable.' },
  { key: 'reason-codes', label: 'Reasons', cols: ['key', 'name', 'disposition_id', 'sort_order'],
    hint: 'Pick-don\'t-type reasons for a lost or disqualified lead.' },
  { key: 'cadences', label: 'Follow-up cadences', cols: ['name', 'trigger', 'pipeline_id', 'stage_id', 'idle_hours'],
    hint: 'A sequence of reminders. `on_create` chases a new lead; `no_activity` re-engages one that went quiet.' },
  { key: 'cadence-steps', label: 'Cadence steps', cols: ['cadence_id', 'step_no', 'offset_hours', 'task_type', 'title', 'body', 'assign_to'],
    hint: 'Each step becomes a follow-up task at `offset_hours` after the trigger, shifted into business hours.' },
  { key: 'assignment-rules', label: 'Lead routing', cols: ['name', 'priority', 'strategy', 'target_employee_id', 'target_queue_id', 'target_agency_id'],
    hint: 'First matching rule wins, lowest `priority` first. Keep a catch-all at the bottom or leads land unassigned.' },
  { key: 'score-rules', label: 'Lead scoring', cols: ['name', 'field', 'op', 'value', 'points'],
    hint: 'Points added when a lead matches. 60+ = hot, 25+ = warm.' },
  { key: 'queues', label: 'Queues', cols: ['key', 'name'],
    hint: 'Shared pools for round-robin routing.' },
]

const BOOL_COLS = new Set(['is_default', 'is_won', 'is_lost', 'requires_followup', 'requires_reason', 'closes_lead', 'is_active', 'portal_enabled'])
const NUM_COLS = new Set(['sort_order', 'probability', 'sla_hours', 'default_followup_hours', 'step_no', 'offset_hours', 'priority', 'points', 'idle_hours'])

export default function CrmSettingsPage() {
  const [tab, setTab] = useState<ListName>('stages')
  const [rows, setRows] = useState<any[]>([])
  const [cfg, setCfg] = useState<CrmConfig | null>(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState<Record<string, any>>({})
  const [editRow, setEditRow] = useState<Record<string, any> | null>(null)

  const active = TABS.find(t => t.key === tab)!

  const load = useCallback(async () => {
    setMsg('')
    try {
      const [r, c] = await Promise.all([
        api(`/api/v1/crm/lists/${tab}?include_inactive=true`),
        api('/api/v1/crm/config'),
      ])
      setRows(r || []); setCfg(c)
    } catch (e: any) { setMsg(e?.message || String(e)) }
  }, [tab])
  useEffect(() => { load() }, [load])

  function coerce(col: string, v: any) {
    if (BOOL_COLS.has(col)) return !!v
    if (NUM_COLS.has(col)) return v === '' || v === null || v === undefined ? null : Number(v)
    return v === '' ? null : v
  }

  async function save(row: Record<string, any> | null) {
    setBusy(true); setMsg('')
    const src = row || draft
    const body: Record<string, any> = {}
    for (const c of active.cols) if (c in src) body[c] = coerce(c, src[c])
    try {
      if (row?.id) await api(`/api/v1/crm/lists/${tab}/${row.id}`, { method: 'PUT', body: JSON.stringify(body) })
      else await api(`/api/v1/crm/lists/${tab}`, { method: 'POST', body: JSON.stringify(body) })
      setDraft({}); setEditRow(null)
      await load()
    } catch (e: any) { setMsg(e?.message || String(e)) }
    setBusy(false)
  }

  async function remove(id: string) {
    setBusy(true); setMsg('')
    try { await api(`/api/v1/crm/lists/${tab}/${id}`, { method: 'DELETE' }); await load() }
    catch (e: any) { setMsg(e?.message || String(e)) }
    setBusy(false)
  }

  async function saveConfig(patch: Record<string, any>) {
    setBusy(true); setMsg('')
    try {
      const c = await api('/api/v1/crm/config', { method: 'PUT', body: JSON.stringify(patch) })
      setCfg(c); setMsg('Saved.')
    } catch (e: any) { setMsg(e?.message || String(e)) }
    setBusy(false)
  }

  const field = (col: string, value: any, onChange: (v: any) => void) => {
    if (BOOL_COLS.has(col)) return <input type="checkbox" checked={!!value} onChange={e => onChange(e.target.checked)} />
    return <input value={value ?? ''} onChange={e => onChange(e.target.value)}
                  inputMode={NUM_COLS.has(col) ? 'numeric' : undefined}
                  style={{ ...input, minWidth: 80, padding: '4px 6px', fontSize: 12 }} />
  }

  return (
    <div style={{ padding: 20, maxWidth: 1500 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>⚙️ CRM Settings</h1>
      <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 12 }}>
        How your team sells: the stages, the outcomes, how fast a follow-up is chased and who a lead goes to.
      </div>
      {cfg && !cfg.can_edit && (
        <div style={{ ...panel, borderColor: '#f39c12', marginBottom: 12 }}>
          You can view this setup but not change it — the “CRM” settings permission is needed.
        </div>
      )}
      {msg && <div style={{ ...panel, borderColor: msg === 'Saved.' ? '#16a34a' : '#dc2626',
                            color: msg === 'Saved.' ? '#16a34a' : '#dc2626', marginBottom: 12 }}>{msg}</div>}

      {cfg && (
        <div style={{ ...panel, marginBottom: 14 }}>
          <div style={{ fontWeight: 700, marginBottom: 10 }}>How the follow-up engine behaves</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 12 }}>
            <div><span style={label}>A lead is “quiet” after (hours)</span>
              <input defaultValue={cfg.stale_lead_hours} style={input} disabled={!cfg.can_edit}
                     onBlur={e => saveConfig({ stale_lead_hours: Number(e.target.value) })} /></div>
            <div><span style={label}>Escalate to the manager after a further (hours)</span>
              <input defaultValue={cfg.escalate_after_hours} style={input} disabled={!cfg.can_edit}
                     onBlur={e => saveConfig({ escalate_after_hours: Number(e.target.value) })} /></div>
            <div><span style={label}>Grace before a follow-up counts as missed (hours)</span>
              <input defaultValue={cfg.miss_grace_hours} style={input} disabled={!cfg.can_edit}
                     onBlur={e => saveConfig({ miss_grace_hours: Number(e.target.value) })} /></div>
            <div><span style={label}>Duplicate check</span>
              <select defaultValue={cfg.duplicate_match} style={input} disabled={!cfg.can_edit}
                      onChange={e => saveConfig({ duplicate_match: e.target.value })}>
                <option value="phone">Phone</option><option value="email">Email</option>
                <option value="both">Phone or email</option><option value="none">Off</option>
              </select></div>
            <div><span style={label}>Time zone</span>
              <input defaultValue={cfg.timezone} style={input} disabled={!cfg.can_edit}
                     onBlur={e => saveConfig({ timezone: e.target.value })} /></div>
            <div><span style={label}>Nudge reps who logged nothing, at hour</span>
              <input defaultValue={cfg.daily_logging_reminder_hour} style={input} disabled={!cfg.can_edit}
                     onBlur={e => saveConfig({ daily_logging_reminder_hour: Number(e.target.value) })} /></div>
            <div><span style={label}>Customer lookup needs a permission</span>
              <select defaultValue={String(cfg.lookup_requires_grant)} style={input} disabled={!cfg.can_edit}
                      onChange={e => saveConfig({ lookup_requires_grant: e.target.value === 'true' })}>
                <option value="true">Yes — only granted roles</option>
                <option value="false">No — anyone in the CRM</option>
              </select>
              <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 3 }}>
                Money columns stay permission-only either way.
              </div></div>
            <div><span style={label}>Web-to-Lead key (blank = off)</span>
              <input defaultValue={cfg.intake_key || ''} style={input} disabled={!cfg.can_edit}
                     onBlur={e => saveConfig({ intake_key: e.target.value || null })} /></div>
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
        {TABS.map(t => (
          <button key={t.key} onClick={() => { setTab(t.key); setDraft({}); setEditRow(null) }}
                  style={{ ...btn, background: tab === t.key ? '#2563eb' : 'var(--surface)',
                           borderColor: tab === t.key ? '#2563eb' : 'var(--border)',
                           color: tab === t.key ? '#fff' : 'var(--text)' }}>
            {t.label}
          </button>
        ))}
      </div>

      <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>{active.hint}</div>

      <div style={{ ...panel, padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead><tr>{active.cols.map(c => <th key={c} style={th}>{c.replace(/_/g, ' ')}</th>)}<th style={th}>active</th><th style={th} /></tr></thead>
          <tbody>
            {rows.map(r => {
              const editing = editRow?.id === r.id
              return (
                <tr key={r.id} style={{ opacity: r.is_active === false ? 0.5 : 1 }}>
                  {active.cols.map(c => (
                    <td key={c} style={cell}>
                      {editing && cfg?.can_edit
                        ? field(c, editRow?.[c], v => setEditRow(p => ({ ...p, [c]: v })))
                        : BOOL_COLS.has(c) ? (r[c] ? '✓' : '') : String(r[c] ?? '—')}
                    </td>
                  ))}
                  <td style={cell}>{r.is_active === false ? 'no' : 'yes'}</td>
                  <td style={cell}>
                    {cfg?.can_edit && (editing ? (
                      <>
                        <button style={{ ...btnPrimary, padding: '3px 8px', fontSize: 11 }} disabled={busy}
                                onClick={() => save(editRow)}>Save</button>{' '}
                        <button style={{ ...btn, padding: '3px 8px', fontSize: 11 }} onClick={() => setEditRow(null)}>Cancel</button>
                      </>
                    ) : (
                      <>
                        <button style={{ ...btn, padding: '3px 8px', fontSize: 11 }} onClick={() => setEditRow({ ...r })}>Edit</button>{' '}
                        <button style={{ ...btn, padding: '3px 8px', fontSize: 11 }} disabled={busy}
                                onClick={() => remove(r.id)}>Remove</button>
                      </>
                    ))}
                  </td>
                </tr>
              )
            })}
            {cfg?.can_edit && (
              <tr style={{ background: 'var(--surface)' }}>
                {active.cols.map(c => (
                  <td key={c} style={cell}>{field(c, draft[c], v => setDraft(p => ({ ...p, [c]: v })))}</td>
                ))}
                <td style={cell} />
                <td style={cell}>
                  <button style={{ ...btnPrimary, padding: '3px 8px', fontSize: 11 }} disabled={busy}
                          onClick={() => save(null)}>Add</button>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 8 }}>
        Removing a stage, outcome or source deactivates it — historical leads keep pointing at a readable value.
        Rules, cadence steps and queue members are removed outright.
      </div>
    </div>
  )
}
