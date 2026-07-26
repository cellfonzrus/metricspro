'use client'
// HR Letters — Template Library (owner directive 2026-07-26). Every category is seeded with a
// professional default (subject/body with {{merge_field}} tokens) the first time this page loads
// (backend self-heals per-org — see letters.py `_ensure_letter_templates`). Editable per-org;
// delivery_mode ('auto' | 'approval') controls whether an AUTOMATED fire (late clock-in / metrics-miss)
// sends immediately or queues for HR approval — a manual send from the Send Letter page always lets
// the sender choose "Send now" regardless of this setting.
import { useEffect, useState, useCallback } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'

type Template = {
  id: string
  template_key: string
  category: string
  escalation_tier: number | null
  label: string
  subject: string
  body: string
  delivery_mode: 'auto' | 'approval'
  active: boolean
  is_default: boolean
}

const th: React.CSSProperties = { textAlign: 'left', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }
const td: React.CSSProperties = { padding: '8px 10px', fontSize: 13, borderTop: '1px solid var(--border)', verticalAlign: 'top' }
const btn: React.CSSProperties = { padding: '6px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text1)', cursor: 'pointer', fontSize: 13 }
const primaryBtn: React.CSSProperties = { ...btn, background: 'var(--accent)', color: '#fff', border: 'none' }
const box: React.CSSProperties = { padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text1)', width: '100%' }

export default function HRLettersPage() {
  const [data, setData] = useState<{ templates: Template[]; categories: Record<string, string>; merge_fields: Record<string, string[]> } | null>(null)
  const [config, setConfig] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [editing, setEditing] = useState<Template | null>(null)
  const [draft, setDraft] = useState<{ subject: string; body: string; delivery_mode: string; active: boolean } | null>(null)
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const [t, c] = await Promise.all([
        api('/api/v1/hr/letters/templates'),
        api('/api/v1/hr/letters/config').catch(() => null),
      ])
      setData(t); setConfig(c)
    } catch (e: any) { setErr(e?.message || 'Failed to load templates') }
    setLoading(false)
  }, [])
  useEffect(() => { load() }, [load])

  function openEdit(t: Template) {
    setEditing(t)
    setDraft({ subject: t.subject, body: t.body, delivery_mode: t.delivery_mode, active: t.active })
    setMsg(''); setErr('')
  }

  async function save() {
    if (!editing || !draft) return
    setSaving(true); setErr(''); setMsg('')
    try {
      await api(`/api/v1/hr/letters/templates/${editing.template_key}`, { method: 'PUT', body: JSON.stringify(draft) })
      setMsg(`Saved "${editing.label}".`)
      setEditing(null); setDraft(null)
      await load()
    } catch (e: any) { setErr(e?.message || 'Save failed') }
    setSaving(false)
  }

  async function saveConfig() {
    if (!config) return
    setSaving(true); setErr(''); setMsg('')
    try {
      const saved = await api('/api/v1/hr/letters/config', { method: 'PUT', body: JSON.stringify(config) })
      setConfig(saved)
      setMsg('Automation settings saved.')
    } catch (e: any) { setErr(e?.message || 'Save failed') }
    setSaving(false)
  }

  const templates = data?.templates || []
  const byCategory: Record<string, Template[]> = {}
  for (const t of templates) (byCategory[t.category] ||= []).push(t)
  const categoryOrder = Object.keys(data?.categories || {})

  return (
    <div style={{ padding: 20, maxWidth: 1100 }}>
      <h2 style={{ margin: '0 0 4px' }}>✉️ HR Letters — Template Library</h2>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginTop: 0 }}>
        Customizable letter templates for every flag we track — late clock-in strikes, cash/inventory/accessory
        shortage, and KPI/commission performance. Edit the wording, subject, and whether a letter sends
        automatically or waits for HR approval. Send an actual letter to an employee from{' '}
        <Link href="/hr/letters/send" style={{ color: 'var(--accent)' }}>Send a Letter</Link>; review anything
        queued for approval on the <Link href="/hr/letters/queue" style={{ color: 'var(--accent)' }}>Approval Queue</Link>;
        see everything ever sent on the <Link href="/hr/letters/sent" style={{ color: 'var(--accent)' }}>Sent Letters</Link> log.
      </p>

      {err && <div style={{ color: '#c0392b', fontSize: 13, margin: '8px 0' }}>{err}</div>}
      {msg && <div style={{ color: '#1e8e3e', fontSize: 13, margin: '8px 0' }}>{msg}</div>}
      {loading && <div style={{ fontSize: 13, color: 'var(--text2)' }}>Loading…</div>}

      {config && (
        <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 14, margin: '14px 0', background: 'var(--surface)' }}>
          <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 13 }}>⚙️ Automation (off by default — the library is always usable manually either way)</div>
          <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', fontSize: 13 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <input type="checkbox" checked={!!config.late_clockin?.enabled}
                onChange={e => setConfig({ ...config, late_clockin: { ...config.late_clockin, enabled: e.target.checked } })} />
              Auto-check late clock-ins daily
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              Grace (min)
              <input type="number" min={0} max={60} value={config.late_clockin?.grace_minutes ?? 5} style={{ ...box, width: 70 }}
                onChange={e => setConfig({ ...config, late_clockin: { ...config.late_clockin, grace_minutes: Number(e.target.value) } })} />
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              Strike window (days)
              <input type="number" min={1} max={365} value={config.late_clockin?.strike_window_days ?? 90} style={{ ...box, width: 80 }}
                onChange={e => setConfig({ ...config, late_clockin: { ...config.late_clockin, strike_window_days: Number(e.target.value) } })} />
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <input type="checkbox" checked={!!config.metrics_miss?.enabled}
                onChange={e => setConfig({ ...config, metrics_miss: { ...config.metrics_miss, enabled: e.target.checked } })} />
              Auto-check KPI/commission miss monthly (2 consecutive months)
            </label>
            <button style={primaryBtn} disabled={saving} onClick={saveConfig}>{saving ? 'Saving…' : 'Save automation settings'}</button>
          </div>
        </div>
      )}

      {categoryOrder.map(cat => (
        <div key={cat} style={{ marginBottom: 20 }}>
          <h3 style={{ fontSize: 14, margin: '0 0 6px' }}>{data?.categories?.[cat] || cat}</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse', background: 'var(--surface)', borderRadius: 8, overflow: 'hidden' }}>
            <thead><tr>
              <th style={th}>Template</th><th style={th}>Subject</th><th style={th}>Delivery</th><th style={th}>Active</th><th style={th}></th>
            </tr></thead>
            <tbody>
              {(byCategory[cat] || []).map(t => (
                <tr key={t.template_key}>
                  <td style={td}>{t.label}{t.escalation_tier ? ` (tier ${t.escalation_tier})` : ''}</td>
                  <td style={td}>{t.subject}</td>
                  <td style={td}>{t.delivery_mode === 'auto' ? '🟢 Auto-send' : '🟡 Needs approval'}</td>
                  <td style={td}>{t.active ? '✅' : '⛔ inactive'}</td>
                  <td style={td}><button style={btn} onClick={() => openEdit(t)}>Edit</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}

      {editing && draft && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 3000 }}
          onMouseDown={e => { if (e.target === e.currentTarget) { setEditing(null); setDraft(null) } }}>
          <div style={{ background: 'var(--surface)', borderRadius: 12, padding: 20, width: 640, maxHeight: '85vh', overflowY: 'auto' }}>
            <h3 style={{ marginTop: 0 }}>Edit — {editing.label}</h3>
            <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 10 }}>
              Available merge fields: {(data?.merge_fields?.[editing.category] || []).map(f => `{{${f}}}`).join(', ')}
            </div>
            <label style={{ fontSize: 12, fontWeight: 600 }}>Subject</label>
            <input style={{ ...box, marginBottom: 10 }} value={draft.subject} onChange={e => setDraft({ ...draft, subject: e.target.value })} />
            <label style={{ fontSize: 12, fontWeight: 600 }}>Body</label>
            <textarea style={{ ...box, marginBottom: 10, minHeight: 220, fontFamily: 'inherit' }}
              value={draft.body} onChange={e => setDraft({ ...draft, body: e.target.value })} />
            <div style={{ display: 'flex', gap: 16, marginBottom: 14, fontSize: 13 }}>
              <label>Delivery mode{' '}
                <select style={{ ...box, width: 'auto', display: 'inline-block' }} value={draft.delivery_mode}
                  onChange={e => setDraft({ ...draft, delivery_mode: e.target.value })}>
                  <option value="approval">Needs HR approval</option>
                  <option value="auto">Send automatically</option>
                </select>
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <input type="checkbox" checked={draft.active} onChange={e => setDraft({ ...draft, active: e.target.checked })} />
                Active
              </label>
            </div>
            {(editing.escalation_tier === 3 || editing.escalation_tier === 5) && draft.delivery_mode === 'auto' && (
              <div style={{ color: '#b8860b', fontSize: 12, marginBottom: 10 }}>
                ⚠️ This is a tier-{editing.escalation_tier} disciplinary letter (suspension/termination language).
                The safe default is "Needs HR approval" — only switch to auto-send if you're sure.
              </div>
            )}
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button style={btn} onClick={() => { setEditing(null); setDraft(null) }}>Cancel</button>
              <button style={primaryBtn} disabled={saving} onClick={save}>{saving ? 'Saving…' : 'Save'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
