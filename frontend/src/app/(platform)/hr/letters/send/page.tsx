'use client'
// Send a Letter — pick an employee + a template, the system pre-fills every merge field from real
// data (strike count, shortage $ from the closing recon, commission earned, etc.), you can override
// any of it before sending. RULE THREE: employee picker is pick-don't-type (First Last + email
// disambiguation via EntityPicker), never free text.
import { useEffect, useMemo, useState, useCallback } from 'react'
import { api } from '@/lib/client'
import EntityPicker from '@/components/EntityPicker'

type Template = { template_key: string; category: string; escalation_tier: number | null; label: string; active: boolean; delivery_mode: string }

const box: React.CSSProperties = { padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text1)', width: '100%' }
const label: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }
const btn: React.CSSProperties = { padding: '8px 16px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text1)', cursor: 'pointer', fontSize: 13 }
const primaryBtn: React.CSSProperties = { ...btn, background: 'var(--accent)', color: '#fff', border: 'none' }

export default function SendLetterPage() {
  const [emps, setEmps] = useState<any[]>([])
  const [templates, setTemplates] = useState<Template[]>([])
  const [categories, setCategories] = useState<Record<string, string>>({})
  const [periods, setPeriods] = useState<string[]>([])
  const [employeeId, setEmployeeId] = useState<string | null>(null)
  const [templateKey, setTemplateKey] = useState<string | null>(null)
  const [dateMode, setDateMode] = useState<'system' | 'manual'>('system')
  const [incidentDate, setIncidentDate] = useState('')
  const [period, setPeriod] = useState('')
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [merge, setMerge] = useState<Record<string, any>>({})
  const [availableFields, setAvailableFields] = useState<string[]>([])
  const [notes, setNotes] = useState<string[]>([])
  const [templateRaw, setTemplateRaw] = useState<{ subject: string; body: string; delivery_mode: string } | null>(null)
  const [loadingDefaults, setLoadingDefaults] = useState(false)
  const [sending, setSending] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => {
    api('/api/v1/storeops/employees?all_company=true').then(setEmps).catch(() => setEmps([]))
    api('/api/v1/hr/letters/templates').then(d => { setTemplates(d.templates || []); setCategories(d.categories || {}) }).catch(() => {})
    // RULE THREE (§3b): Period is pick-don't-type over the org's REAL rep_commissions periods —
    // there's nothing to communicate about a period that hasn't been calculated yet anyway.
    api('/api/v1/hr/letters/periods').then(d => setPeriods(d.periods || [])).catch(() => setPeriods([]))
  }, [])

  const empOptions = useMemo(
    () => emps.filter(e => e.employee_id && e.name && e.is_active !== false)
      .map(e => ({ id: e.employee_id, label: e.name, sublabel: e.email || undefined })),
    [emps],
  )
  const templateOptions = useMemo(
    () => templates.filter(t => t.active).map(t => ({
      id: t.template_key,
      label: `${categories[t.category] || t.category}${t.escalation_tier ? ` — tier ${t.escalation_tier}` : ''} — ${t.label}`,
      sublabel: t.delivery_mode === 'auto' ? 'auto-send' : 'needs approval',
    })),
    [templates, categories],
  )
  const selectedTemplate = templates.find(t => t.template_key === templateKey) || null
  // Union with the currently-set period so a system-derived value (which may not have a calculated
  // rep_commissions row yet) is never hidden by the picker — still pick-don't-type over real data,
  // just never silently blanks out the system default.
  const periodOptions = useMemo(() => {
    const list = new Set(periods)
    if (period) list.add(period)
    return Array.from(list).sort().reverse().map(p => ({ id: p, label: p }))
  }, [periods, period])

  const loadDefaults = useCallback(async () => {
    if (!employeeId || !templateKey) return
    setLoadingDefaults(true); setErr(''); setMsg('')
    try {
      const qs = new URLSearchParams({ employee_id: employeeId, template_key: templateKey })
      if (dateMode === 'manual' && incidentDate) qs.set('incident_date', incidentDate)
      if (period) qs.set('period', period)
      const d = await api(`/api/v1/hr/letters/merge-defaults?${qs.toString()}`)
      setMerge(d.merge || {})
      setAvailableFields(d.available_fields || [])
      setNotes(d.notes || [])
      if (dateMode === 'system' && d.derived_incident_date) setIncidentDate(d.derived_incident_date)
      if (!period && d.derived_period) setPeriod(d.derived_period)
      const tpl = templates.find(t => t.template_key === templateKey)
      const raw = { subject: (d.template?.subject) || '', body: (d.template?.body) || '', delivery_mode: tpl?.delivery_mode || 'approval' }
      setTemplateRaw(raw)
      setSubject(render(raw.subject, d.merge || {}))
      setBody(render(raw.body, d.merge || {}))
    } catch (e: any) { setErr(e?.message || 'Failed to load defaults') }
    setLoadingDefaults(false)
  }, [employeeId, templateKey, dateMode, incidentDate, period, templates])

  useEffect(() => { loadDefaults() }, [employeeId, templateKey, period])
  // Re-render preview whenever a merge field is edited by hand.
  useEffect(() => {
    if (!templateRaw) return
    setSubject(render(templateRaw.subject, merge))
    setBody(render(templateRaw.body, merge))
  }, [merge, templateRaw])

  function render(tpl: string, m: Record<string, any>) {
    return (tpl || '').replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g, (_all, k) => (m[k] ?? '') as string)
  }

  async function send(forceSend: boolean) {
    if (!employeeId || !templateKey) { setErr('Pick an employee and a template first.'); return }
    setSending(true); setErr(''); setMsg('')
    try {
      const res = await api('/api/v1/hr/letters/send', {
        method: 'POST',
        body: JSON.stringify({
          employee_id: employeeId, template_key: templateKey,
          incident_date: dateMode === 'manual' ? (incidentDate || null) : null,
          period: period || null,
          merge_overrides: merge,
          subject, body,
          force_send: forceSend,
        }),
      })
      setMsg(res.status === 'sent' ? '✅ Letter sent.' :
        res.status === 'failed' ? `⚠️ Letter recorded but sending failed: ${res.send_error || 'unknown error'}` :
        '📥 Letter queued for HR approval.')
    } catch (e: any) { setErr(e?.message || 'Send failed') }
    setSending(false)
  }

  const needsPeriod = selectedTemplate && ['kpi_miss', 'commission_statement', 'metrics_miss_2consec'].includes(selectedTemplate.category)

  return (
    <div style={{ padding: 20, maxWidth: 900 }}>
      <h2 style={{ margin: '0 0 4px' }}>📨 Send a Letter</h2>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginTop: 0 }}>
        Pick an employee and a template — every field below is filled in from real system data (you can
        edit anything before sending). The date of the incident can be system-derived or set manually.
      </p>
      {err && <div style={{ color: '#c0392b', fontSize: 13, margin: '8px 0' }}>{err}</div>}
      {msg && <div style={{ color: '#1e8e3e', fontSize: 13, margin: '8px 0' }}>{msg}</div>}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 14 }}>
        <div>
          <label style={label}>Employee</label>
          <EntityPicker options={empOptions} value={employeeId} onChange={setEmployeeId} placeholder="Search employee…" width="100%" />
        </div>
        <div>
          <label style={label}>Template</label>
          <EntityPicker options={templateOptions} value={templateKey} onChange={setTemplateKey} placeholder="Search template…" width="100%" />
        </div>
      </div>

      <div style={{ display: 'flex', gap: 16, marginBottom: 14, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div>
          <label style={label}>Incident date</label>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4 }}>
              <input type="radio" checked={dateMode === 'system'} onChange={() => setDateMode('system')} /> System-derived
            </label>
            <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4 }}>
              <input type="radio" checked={dateMode === 'manual'} onChange={() => setDateMode('manual')} /> Manual
            </label>
            <input type="date" style={{ ...box, width: 160 }} value={incidentDate} disabled={dateMode === 'system'}
              onChange={e => setIncidentDate(e.target.value)} />
          </div>
        </div>
        {needsPeriod && (
          <div>
            <label style={label}>Period</label>
            <EntityPicker options={periodOptions} value={period || null} width={160}
              placeholder="Pick a period…" onChange={v => setPeriod(v || '')} />
          </div>
        )}
        <button style={btn} disabled={!employeeId || !templateKey || loadingDefaults} onClick={loadDefaults}>
          {loadingDefaults ? 'Loading…' : '↻ Reload system defaults'}
        </button>
      </div>

      {notes.length > 0 && (
        <div style={{ background: '#fff8e1', border: '1px solid #f0d896', borderRadius: 8, padding: 10, fontSize: 12, marginBottom: 14 }}>
          {notes.map((n, i) => <div key={i}>ℹ️ {n}</div>)}
        </div>
      )}

      {availableFields.length > 0 && (
        <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, marginBottom: 14 }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>Merge fields (editable — defaults come from the system)</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            {availableFields.map(f => (
              <div key={f}>
                <label style={{ fontSize: 11, color: 'var(--text3)' }}>{f}</label>
                <input style={box} value={merge[f] ?? ''} onChange={e => setMerge({ ...merge, [f]: e.target.value })} />
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ marginBottom: 14 }}>
        <label style={label}>Subject (preview)</label>
        <input style={box} value={subject} onChange={e => setSubject(e.target.value)} />
      </div>
      <div style={{ marginBottom: 14 }}>
        <label style={label}>Body (preview)</label>
        <textarea style={{ ...box, minHeight: 220, fontFamily: 'inherit' }} value={body} onChange={e => setBody(e.target.value)} />
      </div>

      <div style={{ display: 'flex', gap: 10 }}>
        <button style={btn} disabled={sending || !employeeId || !templateKey} onClick={() => send(false)}>
          {selectedTemplate?.delivery_mode === 'auto' ? 'Send now' : '📥 Save for HR approval'}
        </button>
        <button style={primaryBtn} disabled={sending || !employeeId || !templateKey} onClick={() => send(true)}>
          {sending ? 'Sending…' : 'Send now (override approval)'}
        </button>
      </div>
    </div>
  )
}
