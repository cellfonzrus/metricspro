'use client'
// Insurance & Leases — policies, documents, AI reading, expiry notices (migs 964-967).
//
// OWNER DIRECTIVE 2026-09-05: "there should be a link to upload the insurance policy and assign that
// policy to multiple stores as one insurance policy can cover multiple stores, the uploaded policy
// should then be interpreted by the system using ai and the fields filled ... Please to upload the
// certificate of insurance of respective stores ... B[o]th of these will have a multiple contact
// information to be send a notification when a coi is expir[ing] or th[e] lease is getting over at
// least 60 days in advance or as per lease requirement."
//
// A POLICY lives here because it is not a per-store thing — one contract, one premium, one coverage
// period, many stores. Each store's own CERTIFICATE stays on Store Setup's lease panel, which is
// where the rest of that store's paperwork already is.
//
// SERVER-GATED whole (store_lease.can_see_lease, fail-closed 403 below market manager unless
// granted store_lease_docs). This page renders the gate's answer; hiding fields client-side is never
// the protection.
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { sel } from '../lib'
import ExtractionReview from './ExtractionReview'

const lbl: React.CSSProperties = { fontSize: 11, fontWeight: 600, color: 'var(--text3)', textTransform: 'uppercase' }
const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 10, padding: 14, background: 'var(--surface)' }
const group: React.CSSProperties = { display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }

function Field({ label, width = 160, children }: { label: string; width?: number; children: React.ReactNode }) {
  return <div style={{ display: 'flex', flexDirection: 'column', gap: 3, width }}><span style={lbl}>{label}</span>{children}</div>
}

function daysBadge(days: number, inWindow: boolean) {
  const bg = days < 0 ? '#fee2e2' : inWindow ? '#fef3c7' : 'var(--surface2)'
  const fg = days < 0 ? '#991b1b' : inWindow ? '#92400e' : 'var(--text3)'
  const txt = days < 0 ? `expired ${-days}d ago` : `${days}d`
  return <span style={{ fontSize: 11, fontWeight: 700, background: bg, color: fg, padding: '1px 6px', borderRadius: 4 }}>{txt}</span>
}

export default function InsurancePoliciesPage() {
  const [loading, setLoading] = useState(true)
  const [denied, setDenied] = useState(false)
  const [msg, setMsg] = useState('')
  const [policies, setPolicies] = useState<any[]>([])
  const [types, setTypes] = useState<any[]>([])
  const [floors, setFloors] = useState<any>({ lease: 60, insurance: 60 })
  const [stores, setStores] = useState<any[]>([])
  const [expiry, setExpiry] = useState<any>({ upcoming: [], due_now: [] })
  const [open, setOpen] = useState<Record<string, string>>({})   // policyId -> open tab
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [extraction, setExtraction] = useState<Record<string, any>>({})
  const [contactDraft, setContactDraft] = useState<Record<string, any[]>>({})
  const [notices, setNotices] = useState<any>(null)

  async function load() {
    setLoading(true); setMsg('')
    try {
      const [p, s, e] = await Promise.all([
        api('/api/v1/storeops/insurance-policies'),
        api('/api/v1/storeops/stores?include_inactive=true').catch(() => []),
        api('/api/v1/storeops/doc-expiry').catch(() => ({ upcoming: [], due_now: [] })),
      ])
      setPolicies(p?.policies || [])
      setTypes(p?.coverage_types || [])
      setFloors(p?.notice_days_default || { lease: 60, insurance: 60 })
      setStores(s || [])
      setExpiry(e || { upcoming: [], due_now: [] })
      setContactDraft(Object.fromEntries((p?.policies || []).map((x: any) => [x.id, (x.contacts || []).map((c: any) => ({ ...c }))])))
      setDenied(false)
    } catch (err: any) {
      if (err?.status === 403) setDenied(true)
      else setMsg('Load failed: ' + (err?.message || err))
    }
    setLoading(false)
  }
  useEffect(() => { load() }, [])

  const flag = (k: string, v: boolean) => setBusy(b => ({ ...b, [k]: v }))

  async function addPolicy() {
    flag('new', true); setMsg('')
    try {
      await api('/api/v1/storeops/insurance-policies', { method: 'POST', body: JSON.stringify({ policy_number: '', insurer: '' }) })
      await load()
    } catch (err: any) { setMsg('Could not create the policy: ' + (err?.message || err)) }
    flag('new', false)
  }

  async function savePolicy(p: any) {
    flag(p.id, true); setMsg('')
    try {
      await api(`/api/v1/storeops/insurance-policies?policy_id=${encodeURIComponent(p.id)}`, {
        method: 'PUT',
        body: JSON.stringify({
          policy_number: p.policy_number, insurer: p.insurer, coverage_type: p.coverage_type || null,
          coverage_start: p.coverage_start || null, coverage_end: p.coverage_end || null,
          premium: p.premium, premium_frequency: p.premium_frequency, premium_due: p.premium_due || null,
          inclusions_summary: p.inclusions_summary, notice_days: p.notice_days, notes: p.notes,
          is_active: p.is_active !== false,
        }),
      })
      setMsg('✓ saved'); await load()
    } catch (err: any) { setMsg('Save failed: ' + (err?.message || err)) }
    flag(p.id, false)
  }

  async function removePolicy(p: any) {
    if (!confirm(`Delete policy ${p.policy_number || p.insurer || ''}? Its uploaded documents are kept.`)) return
    flag(p.id, true)
    try { await api(`/api/v1/storeops/insurance-policies?policy_id=${encodeURIComponent(p.id)}`, { method: 'DELETE' }); await load() }
    catch (err: any) { setMsg('Delete failed: ' + (err?.message || err)) }
    flag(p.id, false)
  }

  async function toggleStore(p: any, code: string) {
    const cur: string[] = p.store_codes || []
    const next = cur.includes(code) ? cur.filter(c => c !== code) : [...cur, code]
    setPolicies(ps => ps.map(x => x.id === p.id ? { ...x, store_codes: next } : x))
    try {
      await api(`/api/v1/storeops/insurance-policies/stores?policy_id=${encodeURIComponent(p.id)}`, {
        method: 'PUT', body: JSON.stringify({ store_codes: next }),
      })
    } catch (err: any) { setMsg('Assignment failed: ' + (err?.message || err)); await load() }
  }

  async function uploadPolicyDoc(p: any, file: File) {
    flag(`up-${p.id}`, true); setMsg('')
    try {
      const data = await new Promise<string>((resolve, reject) => {
        const rd = new FileReader()
        rd.onload = () => resolve(String(rd.result || ''))
        rd.onerror = () => reject(new Error('could not read the file'))
        rd.readAsDataURL(file)
      })
      const r = await api('/api/v1/storeops/insurance-policies/doc', {
        method: 'POST', body: JSON.stringify({ policy_id: p.id, file_name: file.name, data }),
      })
      setMsg('✓ policy uploaded')
      await load()
      if (r?.document?.id) await readDoc(p.id, r.document.id)
    } catch (err: any) { setMsg('Upload failed: ' + (err?.message || err)) }
    flag(`up-${p.id}`, false)
  }

  async function readDoc(policyId: string, documentId: string) {
    flag(`ai-${policyId}`, true); setMsg('')
    try {
      const r = await api('/api/v1/storeops/document-extract', { method: 'POST', body: JSON.stringify({ document_id: documentId }) })
      setExtraction(e => ({ ...e, [policyId]: r?.extraction }))
      setOpen(o => ({ ...o, [policyId]: 'ai' }))
    } catch (err: any) { setMsg('Could not read the document: ' + (err?.message || err)) }
    flag(`ai-${policyId}`, false)
  }

  async function download(docId: string) {
    try {
      const r = await api(`/api/v1/storeops/store-lease/doc-url?doc_id=${encodeURIComponent(docId)}`)
      if (r?.url) window.open(r.url, '_blank', 'noopener')
    } catch (err: any) { setMsg('Download failed: ' + (err?.message || err)) }
  }

  async function saveContacts(p: any) {
    flag(`c-${p.id}`, true)
    try {
      await api('/api/v1/storeops/document-contacts', {
        method: 'PUT',
        body: JSON.stringify({ subject_kind: 'insurance_policy', subject_ref: p.id, contacts: contactDraft[p.id] || [] }),
      })
      setMsg('✓ contacts saved'); await load()
    } catch (err: any) { setMsg('Could not save contacts: ' + (err?.message || err)) }
    flag(`c-${p.id}`, false)
  }

  async function saveFloors() {
    flag('cfg', true)
    try {
      await api('/api/v1/storeops/store-lease/tenant-defaults', {
        method: 'PUT',
        body: JSON.stringify({ doc_expiry_notice_days: { lease: Number(floors.lease) || 60, insurance: Number(floors.insurance) || 60 } }),
      })
      setMsg('✓ notice settings saved')
    } catch (err: any) { setMsg('Could not save: ' + (err?.message || err)) }
    flag('cfg', false)
  }

  async function runNotices(send: boolean) {
    flag('notices', true); setNotices(null)
    try {
      const r = await api(`/api/v1/storeops/doc-expiry/run-now?send=${send ? 'true' : 'false'}`, { method: 'POST' })
      setNotices(r)
      if (send) { setMsg('✓ notices sent'); await load() }
    } catch (err: any) { setMsg('Could not run: ' + (err?.message || err)) }
    flag('notices', false)
  }

  if (loading) return <div style={{ padding: 24, color: 'var(--text3)' }}>Loading insurance &amp; leases…</div>
  if (denied) return (
    <div style={{ padding: 24, color: 'var(--text3)' }}>
      Insurance policies, leases and their documents are restricted to management roles.
    </div>
  )

  const planned = (notices?.results || []).flatMap((r: any) => r.planned || [])

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 20, fontWeight: 800, margin: 0 }}>🛡️ Insurance &amp; Leases</h1>
        <span style={{ fontSize: 13, color: 'var(--text3)' }}>
          One policy can cover many stores. Each store&apos;s own certificate lives on{' '}
          <Link href="/storeops/setup/stores" style={{ textDecoration: 'underline' }}>Store Setup</Link>.
        </span>
        {msg && <span style={{ fontSize: 12, color: msg.startsWith('✓') ? '#166534' : '#b91c1c' }}>{msg}</span>}
      </div>

      {/* Expiring — the whole point of the notice contacts */}
      <div style={card}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
          <span style={{ fontSize: 14, fontWeight: 700 }}>⏳ Expiring</span>
          <Field label="Lease notice (days)" width={130}>
            <input style={sel} type="number" min={1} value={floors.lease ?? 60}
              onChange={e => setFloors((f: any) => ({ ...f, lease: e.target.value }))} />
          </Field>
          <Field label="Insurance notice (days)" width={150}>
            <input style={sel} type="number" min={1} value={floors.insurance ?? 60}
              onChange={e => setFloors((f: any) => ({ ...f, insurance: e.target.value }))} />
          </Field>
          <button className="btn" disabled={!!busy.cfg} onClick={saveFloors} style={{ marginBottom: 1 }}>
            {busy.cfg ? '⏳' : '💾 Save notice settings'}
          </button>
          <button className="btn" disabled={!!busy.notices} onClick={() => runNotices(false)} style={{ marginBottom: 1 }}>
            {busy.notices ? '⏳' : '👁️ Preview notices'}
          </button>
          <button className="btn" disabled={!!busy.notices} onClick={() => runNotices(true)} style={{ marginBottom: 1 }}>
            📧 Send due notices now
          </button>
        </div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>
          Notices go out at the LONGER of a document&apos;s own notice requirement and the company
          minimum above — a lease demanding 180 days beats a 60-day minimum, never the other way
          round. Reminders repeat at 30, 14, 7 and 1 day out, and once on expiry. They run
          automatically every day; the buttons above are for checking or forcing a send.
        </div>
        {(expiry.upcoming || []).length === 0
          ? <div style={{ fontSize: 13, color: 'var(--text3)' }}>Nothing with an expiry date on record yet.</div>
          : (
            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
              <thead><tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 11 }}>
                <th style={{ padding: '3px 6px' }}>DOCUMENT</th><th>EXPIRES</th><th>IN</th>
                <th>NOTICE WINDOW</th><th>CONTACTS</th>
              </tr></thead>
              <tbody>
                {(expiry.upcoming || []).slice(0, 40).map((u: any, i: number) => (
                  <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '4px 6px' }}>{u.label}</td>
                    <td>{u.expires_on}</td>
                    <td>{daysBadge(u.days_out, u.in_window)}</td>
                    <td style={{ color: 'var(--text3)' }}>{u.notice_days}d</td>
                    <td style={{ color: u.contacts ? 'var(--text2)' : '#b91c1c' }}>
                      {u.contacts ? `${u.contacts} to notify` : 'nobody to notify'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        {notices && (
          <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text2)' }}>
            {planned.length === 0
              ? 'Nothing is due to be notified today.'
              : <>
                <b>{notices.dry_run ? 'Would send' : 'Sent'} {planned.length} notice(s):</b>
                {planned.slice(0, 20).map((p: any, i: number) => (
                  <div key={i}>· {p.subject} → {(p.to || []).join(', ')}</div>
                ))}
              </>}
          </div>
        )}
      </div>

      {/* Policies */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <span style={{ fontSize: 14, fontWeight: 700 }}>Policies ({policies.length})</span>
        <button className="btn btn-primary" disabled={!!busy.new} onClick={addPolicy}>
          {busy.new ? '⏳' : '➕ Add a policy'}
        </button>
      </div>

      {policies.length === 0 && (
        <div style={{ ...card, color: 'var(--text3)', fontSize: 13 }}>
          No policies yet. Add one, upload the policy document, and the reader will fill in the
          coverage period, type, premium, policy number and a summary of what is included — for you
          to check and keep.
        </div>
      )}

      {policies.map(p => {
        const tab = open[p.id] || ''
        const docs: any[] = p.documents || []
        return (
          <div key={p.id} style={card}>
            <div style={group}>
              <Field label="Policy number"><input style={sel} value={p.policy_number || ''}
                onChange={e => setPolicies(ps => ps.map(x => x.id === p.id ? { ...x, policy_number: e.target.value } : x))} /></Field>
              <Field label="Insurance company" width={190}><input style={sel} value={p.insurer || ''}
                onChange={e => setPolicies(ps => ps.map(x => x.id === p.id ? { ...x, insurer: e.target.value } : x))} /></Field>
              <Field label="Coverage type" width={200}>
                <select style={sel} value={p.coverage_type || ''}
                  onChange={e => setPolicies(ps => ps.map(x => x.id === p.id ? { ...x, coverage_type: e.target.value } : x))}>
                  <option value="">—</option>
                  {types.map((t: any) => <option key={t.key} value={t.key}>{t.label}</option>)}
                </select>
              </Field>
              <Field label="Coverage from" width={140}><input style={sel} type="date" value={p.coverage_start || ''}
                onChange={e => setPolicies(ps => ps.map(x => x.id === p.id ? { ...x, coverage_start: e.target.value } : x))} /></Field>
              <Field label="Coverage to" width={140}><input style={sel} type="date" value={p.coverage_end || ''}
                onChange={e => setPolicies(ps => ps.map(x => x.id === p.id ? { ...x, coverage_end: e.target.value } : x))} /></Field>
              <Field label="Premium ($)" width={120}><input style={sel} type="number" value={p.premium ?? ''}
                onChange={e => setPolicies(ps => ps.map(x => x.id === p.id ? { ...x, premium: e.target.value } : x))} /></Field>
              <Field label="Premium due" width={140}><input style={sel} type="date" value={p.premium_due || ''}
                onChange={e => setPolicies(ps => ps.map(x => x.id === p.id ? { ...x, premium_due: e.target.value } : x))} /></Field>
              <Field label="Notice required (days)" width={150}>
                <input style={sel} type="number" placeholder={String(floors.insurance ?? 60)} value={p.notice_days ?? ''}
                  title="This policy's own advance-notice requirement. Blank uses the company minimum; the longer of the two always wins."
                  onChange={e => setPolicies(ps => ps.map(x => x.id === p.id ? { ...x, notice_days: e.target.value } : x))} />
              </Field>
            </div>

            <div style={{ ...group, marginTop: 10 }}>
              <Field label="Summary of inclusions" width={520}>
                <textarea style={{ ...sel, height: 54, resize: 'vertical' }} value={p.inclusions_summary || ''}
                  onChange={e => setPolicies(ps => ps.map(x => x.id === p.id ? { ...x, inclusions_summary: e.target.value } : x))} />
              </Field>
              <Field label="Notes" width={260}><input style={sel} value={p.notes || ''}
                onChange={e => setPolicies(ps => ps.map(x => x.id === p.id ? { ...x, notes: e.target.value } : x))} /></Field>
            </div>

            {Array.isArray(p.extra_items) && p.extra_items.length > 0 && (
              <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text2)' }}>
                <span style={lbl}>Extra items</span>
                {p.extra_items.map((x: any, i: number) => <div key={i}>· <b>{x.label}</b>: {x.value}</div>)}
              </div>
            )}

            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
              <button className="btn btn-primary" disabled={!!busy[p.id]} onClick={() => savePolicy(p)}>
                {busy[p.id] ? '⏳ Saving…' : '💾 Save policy'}
              </button>
              <label className="btn" style={{ cursor: busy[`up-${p.id}`] ? 'default' : 'pointer', margin: 0 }}>
                {busy[`up-${p.id}`] ? '⏳ Uploading…' : '⬆️ Upload the policy document'}
                <input type="file" accept=".pdf,.png,.jpg,.jpeg,.webp" style={{ display: 'none' }} disabled={!!busy[`up-${p.id}`]}
                  onChange={e => { const f = e.target.files?.[0]; if (f) uploadPolicyDoc(p, f); e.currentTarget.value = '' }} />
              </label>
              <button className="btn" onClick={() => setOpen(o => ({ ...o, [p.id]: tab === 'stores' ? '' : 'stores' }))}>
                🏬 Stores covered ({(p.store_codes || []).length})
              </button>
              <button className="btn" onClick={() => setOpen(o => ({ ...o, [p.id]: tab === 'contacts' ? '' : 'contacts' }))}>
                📇 Notify ({(p.contacts || []).length})
              </button>
              {docs.length > 0 && (
                <button className="btn" disabled={!!busy[`ai-${p.id}`]} onClick={() => readDoc(p.id, docs[0].id)}>
                  {busy[`ai-${p.id}`] ? '⏳ Reading…' : '✨ Read the policy with AI'}
                </button>
              )}
              <button className="btn" onClick={() => removePolicy(p)} style={{ marginLeft: 'auto', color: '#b91c1c' }}>🗑️</button>
            </div>

            {docs.length > 0 && (
              <div style={{ marginTop: 8 }}>
                {docs.map((d: any, i: number) => (
                  <div key={d.id} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, padding: '2px 0' }}>
                    <button className="btn" style={{ fontSize: 12, padding: '2px 8px' }} onClick={() => download(d.id)}>⬇️</button>
                    <span style={{ fontWeight: i === 0 ? 700 : 400 }}>{d.file_name || 'document'}{i === 0 ? ' (current)' : ''}</span>
                    <span style={{ color: 'var(--text3)' }}>{String(d.uploaded_at || '').slice(0, 10)}</span>
                    {i > 0 && <button className="btn" style={{ fontSize: 11, padding: '1px 6px' }} onClick={() => readDoc(p.id, d.id)}>✨ read this version</button>}
                  </div>
                ))}
              </div>
            )}

            {tab === 'stores' && (
              <div style={{ marginTop: 10, padding: 10, background: 'var(--surface2)', borderRadius: 8 }}>
                <div style={{ ...lbl, marginBottom: 6 }}>Stores this one policy covers</div>
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                  {stores.map((s: any) => (
                    <label key={s.id || s.store_code} style={{ display: 'flex', gap: 5, alignItems: 'center', fontSize: 13 }}>
                      <input type="checkbox" checked={(p.store_codes || []).includes(s.store_code)}
                        onChange={() => toggleStore(p, s.store_code)} />
                      {s.store_code}{s.is_active === false ? ' (inactive)' : ''}
                    </label>
                  ))}
                </div>
              </div>
            )}

            {tab === 'contacts' && (
              <div style={{ marginTop: 10, padding: 10, background: 'var(--surface2)', borderRadius: 8 }}>
                <div style={{ ...lbl, marginBottom: 6 }}>Who to notify before this policy expires</div>
                {(contactDraft[p.id] || []).map((c: any, i: number) => (
                  <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 5, flexWrap: 'wrap' }}>
                    <input style={{ ...sel, width: 150 }} placeholder="Name" value={c.name || ''}
                      onChange={e => setContactDraft(d => ({ ...d, [p.id]: d[p.id].map((x, j) => j === i ? { ...x, name: e.target.value } : x) }))} />
                    <input style={{ ...sel, width: 210 }} placeholder="Email" value={c.email || ''}
                      onChange={e => setContactDraft(d => ({ ...d, [p.id]: d[p.id].map((x, j) => j === i ? { ...x, email: e.target.value } : x) }))} />
                    <input style={{ ...sel, width: 130 }} placeholder="Phone" value={c.phone || ''}
                      onChange={e => setContactDraft(d => ({ ...d, [p.id]: d[p.id].map((x, j) => j === i ? { ...x, phone: e.target.value } : x) }))} />
                    <input style={{ ...sel, width: 130 }} placeholder="Role" value={c.role || ''}
                      onChange={e => setContactDraft(d => ({ ...d, [p.id]: d[p.id].map((x, j) => j === i ? { ...x, role: e.target.value } : x) }))} />
                    <input style={{ ...sel, width: 110 }} type="number" placeholder="Days ahead" title="Optional: this person's own lead time. It can only make the notice EARLIER, never later than the company minimum."
                      value={c.notice_days ?? ''}
                      onChange={e => setContactDraft(d => ({ ...d, [p.id]: d[p.id].map((x, j) => j === i ? { ...x, notice_days: e.target.value } : x) }))} />
                    <button className="btn" style={{ fontSize: 12, padding: '3px 8px' }}
                      onClick={() => setContactDraft(d => ({ ...d, [p.id]: d[p.id].filter((_, j) => j !== i) }))}>✕</button>
                  </div>
                ))}
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="btn" style={{ fontSize: 12, padding: '3px 8px' }}
                    onClick={() => setContactDraft(d => ({ ...d, [p.id]: [...(d[p.id] || []), { name: '', email: '', notify_expiry: true }] }))}>➕ Add contact</button>
                  <button className="btn btn-primary" style={{ fontSize: 12, padding: '3px 8px' }}
                    disabled={!!busy[`c-${p.id}`]} onClick={() => saveContacts(p)}>💾 Save contacts</button>
                </div>
              </div>
            )}

            {tab === 'ai' && extraction[p.id] && (
              <div style={{ marginTop: 10 }}>
                <ExtractionReview extraction={extraction[p.id]} onAccepted={load} />
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
