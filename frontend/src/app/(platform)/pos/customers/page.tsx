'use client'
// POS module — Phase 1: Customers / Account Manager (ported from the standalone pos-system app;
// data access rewired from direct Supabase to the FastAPI /pos router).
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

interface Customer {
  id: string
  cust_number: number
  account_type: string
  company_name: string | null
  first_name: string | null
  last_name: string | null
  middle_initial: string | null
  dob: string | null
  driver_license_state: string | null
  primary_account_no: string | null
  password: string | null
  email: string | null
  phone_primary: string | null
  phone_secondary: string | null
  address_1: string | null
  address_2: string | null
  city: string | null
  state: string | null
  zip: string | null
  referral_source: string | null
  credit_limit: number | null
  accept_checks: boolean
  is_active: boolean
  created_at: string
}

interface CustomerNote {
  id: string
  note: string
  severity: string
  created_at: string
  employee_id: string | null
}

const SEVERITY_COLORS: Record<string, string> = { normal: '#6b7280', important: '#f39c12', urgent: '#e74c3c' }

function SeverityChip({ severity }: { severity: string }) {
  const color = SEVERITY_COLORS[severity] || SEVERITY_COLORS.normal
  return (
    <span style={{ display: 'inline-block', border: `1px solid ${color}`, color, borderRadius: 4, padding: '0 6px', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.5px', marginRight: 6 }}>
      {severity}
    </span>
  )
}

const US_STATES = ['AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY']
const REFERRAL_SOURCES = ['None','Walk In Customer','Amazon','Another Customer','Internet','Newspaper','Radio','TV']

const emptyForm = {
  account_type: 'Personal',
  company_name: '',
  first_name: '',
  last_name: '',
  middle_initial: '',
  dob: '',
  driver_license_state: '',
  primary_account_no: '',
  password: '',
  email: '',
  phone_primary: '',
  phone_secondary: '',
  address_1: '',
  address_2: '',
  city: '',
  state: 'NY',
  zip: '',
  referral_source: 'None',
  credit_limit: 100000,
  accept_checks: true,
  is_active: true,
}

const input: React.CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', width: '100%', outline: 'none' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }
const cell: React.CSSProperties = { padding: '7px 12px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
const panel: React.CSSProperties = { background: 'var(--surface2)', borderRadius: 8, padding: 14, border: '1px solid var(--border)' }

export default function PosCustomersPage() {
  const { permissions } = useAuth()
  const [customers, setCustomers] = useState<Customer[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [activeOnly, setActiveOnly] = useState(true)
  const [selected, setSelected] = useState<Customer | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [formData, setFormData] = useState({ ...emptyForm })
  const [formTab, setFormTab] = useState('General')
  const [detailTab, setDetailTab] = useState('Activations History')
  const [saving, setSaving] = useState(false)
  const [editMode, setEditMode] = useState(false)
  const [noteText, setNoteText] = useState('')
  const [noteSeverity, setNoteSeverity] = useState('normal')
  const [showNotes, setShowNotes] = useState(false)
  const [notes, setNotes] = useState<CustomerNote[]>([])
  const [notesError, setNotesError] = useState('')
  const [savingNote, setSavingNote] = useState(false)
  const [listError, setListError] = useState('')
  // PII (SSN / driver license) lives outside the customers table — dedicated endpoints only
  const [piiForm, setPiiForm] = useState({ ssn: '', driver_license_num: '' })
  const [piiDirty, setPiiDirty] = useState({ ssn: false, dl: false })
  const [piiLast4, setPiiLast4] = useState<{ ssn_last4: string | null, dl_last4: string | null } | null>(null)
  const [revealedPii, setRevealedPii] = useState<{ ssn: string | null, driver_license_num: string | null } | null>(null)
  const [piiError, setPiiError] = useState('')
  const [revealing, setRevealing] = useState(false)

  // Mirrors the backend gate (_require_pos_perm): explicit pos_view_pii grant, or org-wide scope
  // (which is also the default when scope is unset). The server re-checks — this only hides the button.
  const p = permissions as Record<string, unknown>
  const canViewPii = p?.pos_view_pii === true || String(p?.scope ?? 'all') === 'all'

  async function loadCustomers(q = { search, activeOnly }) {
    setLoading(true)
    setListError('')
    try {
      const params = new URLSearchParams()
      if (q.search.trim()) params.set('search', q.search.trim())
      params.set('active_only', String(q.activeOnly))
      const r = await api(`/api/v1/pos/customers?${params}`)
      setCustomers(r.customers || [])
    } catch (err: any) {
      setListError(`Failed to load customers: ${err?.message || err}`)
      setCustomers([])
    }
    setLoading(false)
  }

  useEffect(() => { loadCustomers() }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  async function selectCustomer(c: Customer) {
    setSelected(c)
    setDetailTab('Activations History')
    setRevealedPii(null)
    setPiiError('')
    setPiiLast4(null)
    await loadNotes(c.id)
    try {
      const pd = await api(`/api/v1/pos/customers/${c.id}/pii-last4`)
      setPiiLast4(pd || null)
    } catch { setPiiLast4(null) }
  }

  async function loadNotes(customerId: string) {
    setNotesError('')
    try {
      const r = await api(`/api/v1/pos/customers/${customerId}/notes`)
      setNotes(r.notes || [])
    } catch (err: any) {
      setNotesError(`Failed to load notes: ${err?.message || err}`)
      setNotes([])
    }
  }

  async function revealPii() {
    if (!selected) return
    setRevealing(true)
    setPiiError('')
    try {
      const data = await api(`/api/v1/pos/customers/${selected.id}/pii`)
      setRevealedPii({ ssn: data?.ssn ?? null, driver_license_num: data?.driver_license_num ?? null })
    } catch (err: any) {
      const msg = String(err?.message || err)
      setPiiError(/403|permission|denied|not.?authorized|not allow/i.test(msg) ? 'Not authorized to view full PII' : 'Failed to load PII')
    }
    setRevealing(false)
  }

  async function saveCustomer() {
    setSaving(true)
    try {
      // dob: '' is fine — the backend coerces empty string to null
      const payload = { ...formData }
      let customerId: string
      if (editMode && selected) {
        const r = await api(`/api/v1/pos/customers/${selected.id}`, { method: 'PATCH', body: JSON.stringify(payload) })
        customerId = selected.id
        setSelected(r?.customer || { ...selected, ...payload, dob: formData.dob || null })
      } else {
        const r = await api('/api/v1/pos/customers', { method: 'POST', body: JSON.stringify(payload) })
        customerId = r?.customer?.id
        if (!customerId) throw new Error('Create succeeded but no customer id was returned')
      }

      // Only touch stored PII if the user actually typed in the SSN / DL inputs.
      if (piiDirty.ssn || piiDirty.dl) {
        const values = {
          ssn: piiForm.ssn.trim() || null,
          dl: piiForm.driver_license_num.trim() || null,
        }
        let okToSet = true
        if (editMode) {
          // The PII endpoint sets both fields at once; an untouched field with stored data
          // must be backfilled with its current value or it would be cleared.
          const needsBackfill = (!piiDirty.ssn && !!piiLast4?.ssn_last4) || (!piiDirty.dl && !!piiLast4?.dl_last4)
          if (needsBackfill) {
            try {
              const cur = await api(`/api/v1/pos/customers/${customerId}/pii`)
              if (!piiDirty.ssn) values.ssn = cur?.ssn ?? null
              if (!piiDirty.dl) values.dl = cur?.driver_license_num ?? null
            } catch {
              okToSet = false
              alert('SSN and Driver License are stored together. To change one without clearing the other, please enter both values. Your other changes were saved.')
            }
          }
        }
        if (okToSet) {
          await api(`/api/v1/pos/customers/${customerId}/pii`, {
            method: 'POST',
            body: JSON.stringify({ ssn: values.ssn, driver_license: values.dl }),
          })
        }
      }

      setShowForm(false)
      setEditMode(false)
      loadCustomers()
      if (editMode && selected) {
        setRevealedPii(null)
        try {
          const pd = await api(`/api/v1/pos/customers/${selected.id}/pii-last4`)
          setPiiLast4(pd || null)
        } catch { setPiiLast4(null) }
      }
    } catch (e) {
      console.error(e)
      const msg = e && typeof e === 'object' && 'message' in e ? (e as { message: string }).message : 'Unknown error'
      alert(`Failed to save customer: ${msg}`)
    }
    setSaving(false)
  }

  async function saveNote() {
    if (!selected || !noteText.trim() || savingNote) return
    setSavingNote(true)
    try {
      // The author is stamped server-side from the login
      await api(`/api/v1/pos/customers/${selected.id}/notes`, {
        method: 'POST',
        body: JSON.stringify({ note: noteText.trim(), severity: noteSeverity }),
      })
      setNoteText('')
      setNoteSeverity('normal')
      await loadNotes(selected.id)
    } catch (err: any) {
      alert(`Failed to save note: ${err?.message || err}`)
    } finally {
      setSavingNote(false)
    }
  }

  function openNew() {
    setFormData({ ...emptyForm })
    setPiiForm({ ssn: '', driver_license_num: '' })
    setPiiDirty({ ssn: false, dl: false })
    setFormTab('General')
    setEditMode(false)
    setShowForm(true)
  }

  function openEdit() {
    if (!selected) return
    setFormData({
      account_type: selected.account_type || 'Personal',
      company_name: selected.company_name || '',
      first_name: selected.first_name || '',
      last_name: selected.last_name || '',
      middle_initial: selected.middle_initial || '',
      dob: selected.dob || '',
      driver_license_state: selected.driver_license_state || '',
      primary_account_no: selected.primary_account_no || '',
      password: selected.password || '',
      email: selected.email || '',
      phone_primary: selected.phone_primary || '',
      phone_secondary: selected.phone_secondary || '',
      address_1: selected.address_1 || '',
      address_2: selected.address_2 || '',
      city: selected.city || '',
      state: selected.state || 'NY',
      zip: selected.zip || '',
      referral_source: selected.referral_source || 'None',
      credit_limit: selected.credit_limit || 100000,
      accept_checks: selected.accept_checks,
      is_active: selected.is_active,
    })
    // Never prefill real PII — inputs stay blank with masked last-4 placeholders
    setPiiForm({ ssn: '', driver_license_num: '' })
    setPiiDirty({ ssn: false, dl: false })
    setFormTab('General')
    setEditMode(true)
    setShowForm(true)
  }

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>👤 Account Manager</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Records: {customers.length}{selected ? ` · selected: ${selected.first_name || ''} ${selected.last_name || ''} (#${selected.cust_number})` : ''}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <button className="btn btn-primary" onClick={openNew}>+ New Customer</button>
          {selected && <button className="btn btn-secondary" onClick={openEdit}>View/Edit</button>}
          {selected && <button className="btn btn-secondary" onClick={() => setShowNotes(true)}>Cust. Care Notes</button>}
        </div>
      </div>

      {/* Search bar */}
      <div style={{ ...panel, marginBottom: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && loadCustomers()}
          placeholder="Search name, company, phone, email, account no…"
          style={{ ...input, flex: 1, minWidth: 220 }}
        />
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer', whiteSpace: 'nowrap' }}>
          <input type="checkbox" checked={activeOnly} onChange={e => setActiveOnly(e.target.checked)} />
          Active only
        </label>
        <button className="btn btn-primary" onClick={() => loadCustomers()}>Search</button>
        <button className="btn btn-secondary" onClick={() => { setSearch(''); loadCustomers({ search: '', activeOnly }) }}>Clear</button>
      </div>

      {listError && (
        <div style={{ ...panel, marginBottom: 14, fontSize: 12, color: '#dc2626' }}>{listError}</div>
      )}

      {/* Accounts table */}
      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div className="table-wrapper" style={{ overflowX: 'auto', marginBottom: 14 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1000, fontSize: 13 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Cust #','Account Type','Company Name','Last Name','MI','First Name','Primary Account No','DOB','DL State','Phone','Email'].map(h =>
                <th key={h} style={{ textAlign: 'left', padding: 8, fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {customers.map(c => (
                <tr key={c.id} onClick={() => selectCustomer(c)}
                  style={{ cursor: 'pointer', background: selected?.id === c.id ? 'var(--surface2)' : 'transparent', opacity: c.is_active ? 1 : 0.55 }}>
                  <td style={{ ...cell, fontWeight: 600 }}>{c.cust_number}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{c.account_type}</td>
                  <td style={cell}>{c.company_name || ''}</td>
                  <td style={{ ...cell, fontWeight: 500 }}>{c.last_name || ''}</td>
                  <td style={cell}>{c.middle_initial || ''}</td>
                  <td style={cell}>{c.first_name || ''}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{c.primary_account_no || ''}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{c.dob ? new Date(c.dob).toLocaleDateString() : ''}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{c.driver_license_state || ''}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{c.phone_primary || ''}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{c.email || ''}</td>
                </tr>
              ))}
              {customers.length === 0 && (
                <tr><td colSpan={11} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>No records found</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* PII panel — masked identity info for the selected customer */}
      {selected && (
        <div style={{ ...panel, marginBottom: 14, display: 'flex', alignItems: 'center', gap: 20, fontSize: 12, flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 700 }}>Identity (PII)</span>
          <span style={{ color: 'var(--text2)' }}>SSN: <strong style={{ color: 'var(--text)', fontWeight: 600 }}>{revealedPii ? (revealedPii.ssn || '—') : piiLast4?.ssn_last4 ? `•••-••-${piiLast4.ssn_last4}` : '—'}</strong></span>
          <span style={{ color: 'var(--text2)' }}>Driver License: <strong style={{ color: 'var(--text)', fontWeight: 600 }}>{revealedPii ? (revealedPii.driver_license_num || '—') : piiLast4?.dl_last4 ? `•••• ${piiLast4.dl_last4}` : '—'}</strong>{selected.driver_license_state ? ` (${selected.driver_license_state})` : ''}</span>
          {revealedPii ? (
            <button className="btn btn-secondary" style={{ fontSize: 11, padding: '3px 12px' }} onClick={() => setRevealedPii(null)}>Hide</button>
          ) : canViewPii ? (
            // Hidden entirely without pos_view_pii — the endpoint would deny anyway
            <button className="btn btn-primary" style={{ fontSize: 11, padding: '3px 12px', cursor: revealing ? 'wait' : 'pointer', opacity: revealing ? 0.7 : 1 }} disabled={revealing} onClick={revealPii}>{revealing ? 'Revealing…' : 'Reveal'}</button>
          ) : null}
          {piiError && <span style={{ color: '#dc2626' }}>{piiError}</span>}
        </div>
      )}

      {/* Bottom tabs — Activations / Notes / Sales / Documents */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
        <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', background: 'var(--surface2)', flexWrap: 'wrap' }}>
          {['Activations History','Notes','Sales History','Scanned Documents'].map(tab => (
            <button key={tab} onClick={() => setDetailTab(tab)}
              style={{ padding: '10px 18px', fontSize: 12, fontWeight: detailTab === tab ? 700 : 400, color: detailTab === tab ? 'var(--text)' : 'var(--text2)', background: detailTab === tab ? 'var(--surface)' : 'transparent', border: 'none', borderBottom: detailTab === tab ? '2px solid var(--accent, #3498db)' : '2px solid transparent', cursor: 'pointer' }}>
              {tab === 'Notes' && selected ? `Notes (${notes.length})` : tab}
            </button>
          ))}
        </div>

        {detailTab === 'Activations History' && (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>
            {selected ? 'Activations are not available yet in this module (coming in a later phase)' : 'Select a customer to view activations'}
          </div>
        )}

        {detailTab === 'Notes' && (
          <div style={{ padding: 16 }}>
            {!selected ? (
              <div style={{ padding: 20, textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>Select a customer to view notes</div>
            ) : (
              <>
                {notesError && <div style={{ color: '#dc2626', fontSize: 12, marginBottom: 10 }}>{notesError}</div>}
                <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
                  <textarea value={noteText} onChange={e => setNoteText(e.target.value)} placeholder="Type a note about this customer..." style={{ ...input, resize: 'none', height: 60, flex: 1 }} />
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignSelf: 'flex-end' }}>
                    <select value={noteSeverity} onChange={e => setNoteSeverity(e.target.value)} style={{ ...input, width: 'auto', padding: '6px 8px', fontSize: 12 }}>
                      <option value="normal">Normal</option>
                      <option value="important">Important</option>
                      <option value="urgent">Urgent</option>
                    </select>
                    <button className="btn btn-primary" disabled={savingNote || !noteText.trim()} onClick={saveNote}>{savingNote ? 'Saving…' : 'Add Note'}</button>
                  </div>
                </div>
                {notes.length === 0 ? (
                  <div style={{ color: 'var(--text3)', fontSize: 13, textAlign: 'center', padding: 14 }}>No notes yet for this customer</div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {notes.map(n => (
                      <div key={n.id} style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px' }}>
                        <div style={{ fontSize: 12, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{n.note}</div>
                        <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>
                          <SeverityChip severity={n.severity || 'normal'} />
                          {new Date(n.created_at).toLocaleString()}
                          {n.employee_id ? ` — ${n.employee_id}` : ''}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {detailTab === 'Sales History' && (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>
            {selected ? `Sales history for ${selected.first_name || ''} ${selected.last_name || ''}` : 'Select a customer to view sales history'}
          </div>
        )}

        {detailTab === 'Scanned Documents' && (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>
            {selected ? 'No scanned documents found' : 'Select a customer to view documents'}
          </div>
        )}
      </div>

      {/* Customer form modal */}
      {showForm && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 700, maxHeight: '90vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>

            {/* Modal header */}
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <b style={{ fontSize: 14 }}>{editMode ? 'Edit Customer' : 'New Customer'}</b>
              <button onClick={() => setShowForm(false)} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
            </div>

            {/* Tabs */}
            <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
              {['General','Marketing','Settings'].map(tab => (
                <button key={tab} onClick={() => setFormTab(tab)}
                  style={{ padding: '10px 20px', fontSize: 12, fontWeight: formTab === tab ? 700 : 400, color: formTab === tab ? 'var(--text)' : 'var(--text2)', background: formTab === tab ? 'var(--surface)' : 'transparent', border: 'none', borderBottom: formTab === tab ? '2px solid var(--accent, #3498db)' : '2px solid transparent', cursor: 'pointer' }}>
                  {tab}
                </button>
              ))}
            </div>

            {/* Form body */}
            <div style={{ padding: 20, overflowY: 'auto', flex: 1 }}>

              {formTab === 'General' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                  {/* Account type */}
                  <div style={panel}>
                    <label style={{ ...label, marginBottom: 8 }}>Select Account Type</label>
                    <div style={{ display: 'flex', gap: 20 }}>
                      {['Personal','Business'].map(t => (
                        <label key={t} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer', color: formData.account_type === t ? 'var(--text)' : 'var(--text2)' }}>
                          <input type="radio" checked={formData.account_type === t} onChange={() => setFormData(f => ({ ...f, account_type: t }))} />
                          {t} Account
                        </label>
                      ))}
                    </div>
                  </div>

                  {formData.account_type === 'Business' && (
                    <div>
                      <label style={label}>Company Name</label>
                      <input value={formData.company_name} onChange={e => setFormData(f => ({ ...f, company_name: e.target.value }))} style={input} />
                    </div>
                  )}

                  {/* Name row */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 60px 1fr', gap: 10 }}>
                    <div><label style={label}>First Name</label><input value={formData.first_name} onChange={e => setFormData(f => ({ ...f, first_name: e.target.value }))} style={input} /></div>
                    <div><label style={label}>M.I.</label><input value={formData.middle_initial} maxLength={1} onChange={e => setFormData(f => ({ ...f, middle_initial: e.target.value }))} style={input} /></div>
                    <div><label style={label}>Last Name</label><input value={formData.last_name} onChange={e => setFormData(f => ({ ...f, last_name: e.target.value }))} style={input} /></div>
                  </div>

                  {/* SSN / DOB */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                    <div><label style={label}>SSN</label><input value={piiForm.ssn} placeholder={editMode && piiLast4?.ssn_last4 ? `•••-••-${piiLast4.ssn_last4}` : '___-__-____'} onChange={e => { const v = e.target.value; setPiiForm(f => ({ ...f, ssn: v })); setPiiDirty(d => ({ ...d, ssn: true })) }} style={input} /></div>
                    <div><label style={label}>Date of Birth</label><input type="date" value={formData.dob} onChange={e => setFormData(f => ({ ...f, dob: e.target.value }))} style={input} /></div>
                  </div>

                  {/* Account / Password */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                    <div><label style={label}>Primary Account No.</label><input value={formData.primary_account_no} onChange={e => setFormData(f => ({ ...f, primary_account_no: e.target.value }))} style={input} /></div>
                    <div><label style={label}>Account Password</label><input value={formData.password} onChange={e => setFormData(f => ({ ...f, password: e.target.value }))} style={input} /></div>
                  </div>

                  {/* Driver License */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 120px 1fr', gap: 10 }}>
                    <div><label style={label}>Driver License No.</label><input value={piiForm.driver_license_num} placeholder={editMode && piiLast4?.dl_last4 ? `•••• ${piiLast4.dl_last4}` : ''} onChange={e => { const v = e.target.value; setPiiForm(f => ({ ...f, driver_license_num: v })); setPiiDirty(d => ({ ...d, dl: true })) }} style={input} /></div>
                    <div><label style={label}>DL State</label>
                      <select value={formData.driver_license_state} onChange={e => setFormData(f => ({ ...f, driver_license_state: e.target.value }))} style={input}>
                        <option value="">--</option>
                        {US_STATES.map(s => <option key={s}>{s}</option>)}
                      </select>
                    </div>
                    <div><label style={label}>Referral Source</label>
                      <select value={formData.referral_source} onChange={e => setFormData(f => ({ ...f, referral_source: e.target.value }))} style={input}>
                        {REFERRAL_SOURCES.map(s => <option key={s}>{s}</option>)}
                      </select>
                    </div>
                  </div>

                  {/* Address */}
                  <div><label style={label}>Address</label><input value={formData.address_1} onChange={e => setFormData(f => ({ ...f, address_1: e.target.value }))} style={input} /></div>
                  <div><label style={label}>Address 2</label><input value={formData.address_2} onChange={e => setFormData(f => ({ ...f, address_2: e.target.value }))} style={input} /></div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 100px', gap: 10 }}>
                    <div><label style={label}>City</label><input value={formData.city} onChange={e => setFormData(f => ({ ...f, city: e.target.value }))} style={input} /></div>
                    <div><label style={label}>State</label>
                      <select value={formData.state} onChange={e => setFormData(f => ({ ...f, state: e.target.value }))} style={input}>
                        {US_STATES.map(s => <option key={s}>{s}</option>)}
                      </select>
                    </div>
                    <div><label style={label}>Zip</label><input value={formData.zip} onChange={e => setFormData(f => ({ ...f, zip: e.target.value }))} style={input} /></div>
                  </div>

                  {/* Phones */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                    <div><label style={label}>Phone</label><input value={formData.phone_primary} placeholder="(___) ___-____" onChange={e => setFormData(f => ({ ...f, phone_primary: e.target.value }))} style={input} /></div>
                    <div><label style={label}>Mobile Phone (Current)</label><input value={formData.phone_secondary} placeholder="(___) ___-____" onChange={e => setFormData(f => ({ ...f, phone_secondary: e.target.value }))} style={input} /></div>
                  </div>

                  <div><label style={label}>E-mail</label><input type="email" value={formData.email} onChange={e => setFormData(f => ({ ...f, email: e.target.value }))} style={input} /></div>
                </div>
              )}

              {formTab === 'Marketing' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                  <div style={panel}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 10 }}>Marketing Preferences</div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                      {['Do not Call','Do not Direct Mail','Do not Email','Do not SMS'].map(pref => (
                        <label key={pref} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
                          <input type="checkbox" />
                          {pref}
                        </label>
                      ))}
                    </div>
                  </div>
                  <div style={panel}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 10 }}>Wireless Service Profile</div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                      <div><label style={label}>Current Carrier</label>
                        <select style={input}>
                          {['','Verizon','AT&T','T-Mobile','Sprint','Boost','Cricket','MetroPCS','Other'].map(c => <option key={c}>{c}</option>)}
                        </select>
                      </div>
                      <div><label style={label}>Contract Expiration Date</label><input type="date" style={input} /></div>
                    </div>
                  </div>
                </div>
              )}

              {formTab === 'Settings' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                  <div>
                    <label style={label}>Accept Checks</label>
                    <select value={formData.accept_checks ? 'Accept Checks' : 'No Checks'} onChange={e => setFormData(f => ({ ...f, accept_checks: e.target.value === 'Accept Checks' }))} style={{ ...input, width: 200 }}>
                      <option>Accept Checks</option>
                      <option>No Checks</option>
                    </select>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                    <div><label style={label}>Credit Limit</label><input type="number" value={formData.credit_limit} onChange={e => setFormData(f => ({ ...f, credit_limit: Number(e.target.value) }))} style={input} /></div>
                  </div>
                  <div>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
                      <input type="checkbox" checked={formData.is_active} onChange={e => setFormData(f => ({ ...f, is_active: e.target.checked }))} />
                      Active Customer
                    </label>
                  </div>
                </div>
              )}
            </div>

            {/* Footer buttons */}
            <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn btn-secondary" onClick={() => setShowForm(false)}>Cancel</button>
              <button className="btn btn-primary" disabled={saving} onClick={saveCustomer}>
                {saving ? 'Saving…' : editMode ? 'Save' : 'Save & Close'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Care notes modal */}
      {showNotes && selected && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 210, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 500, maxHeight: '80vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <b style={{ fontSize: 14 }}>Customer Care Notes — {selected.first_name} {selected.last_name}</b>
              <button onClick={() => setShowNotes(false)} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
            </div>
            <div style={{ padding: 16, flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 10 }}>
              {notesError && <div style={{ color: '#dc2626', fontSize: 12 }}>{notesError}</div>}
              {notes.length === 0 && <div style={{ color: 'var(--text3)', fontSize: 13, textAlign: 'center', padding: 20 }}>No notes yet</div>}
              {notes.map(n => (
                <div key={n.id} style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px' }}>
                  <div style={{ fontSize: 12, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{n.note}</div>
                  <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>
                    <SeverityChip severity={n.severity || 'normal'} />
                    {new Date(n.created_at).toLocaleString()}
                    {n.employee_id ? ` — ${n.employee_id}` : ''}
                  </div>
                </div>
              ))}
            </div>
            <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8 }}>
              <textarea value={noteText} onChange={e => setNoteText(e.target.value)} placeholder="Type a note..." style={{ ...input, resize: 'none', height: 60, flex: 1 }} />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignSelf: 'flex-end' }}>
                <select value={noteSeverity} onChange={e => setNoteSeverity(e.target.value)} style={{ ...input, width: 'auto', padding: '6px 8px', fontSize: 12 }}>
                  <option value="normal">Normal</option>
                  <option value="important">Important</option>
                  <option value="urgent">Urgent</option>
                </select>
                <button className="btn btn-primary" disabled={savingNote || !noteText.trim()} onClick={saveNote}>{savingNote ? 'Saving…' : 'Add Note'}</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
