'use client'
// AI extraction review (migs 964-967, owner directive 2026-09-05) — the screen where a HUMAN turns
// a machine reading of a lease / policy / COI into saved data.
//
// WHY THIS SCREEN EXISTS AT ALL: account/liabilities_due.py books rent and insurance premiums from
// the lease record, so an extracted premium or rent must never land there on its own. The backend
// enforces that (doc_intel.apply_plan refuses money-guarded fields without an explicit money
// confirmation, and ACH columns always); this panel is the human half of the same rule — nothing is
// pre-ticked, every value shows the VERBATIM sentence it came from and the page it is on, and the
// money fields are visually separated behind their own confirmation.
//
// Shared by the Insurance & Leases page (policies) and the Store Setup lease panel (leases/COIs).
import { useState } from 'react'
import { api } from '@/lib/client'

const lbl: React.CSSProperties = { fontSize: 11, fontWeight: 600, color: 'var(--text3)', textTransform: 'uppercase' }
const snip: React.CSSProperties = {
  fontSize: 11, color: 'var(--text3)', fontStyle: 'italic', marginTop: 2,
  borderLeft: '2px solid var(--border)', paddingLeft: 6,
}

function fmt(v: any) {
  if (v == null) return ''
  if (Array.isArray(v)) {
    return v.map((e: any) => (e && typeof e === 'object' && 'monthly_rent' in e)
      ? `${e.effective_from}: $${Number(e.monthly_rent).toLocaleString()}` : JSON.stringify(e)).join(' · ')
  }
  if (typeof v === 'object') {
    if (v.kind) return v.kind === 'week' ? `week ${v.value} of the month` : `day ${v.value} of the month`
    return JSON.stringify(v)
  }
  return String(v)
}

function Confidence({ c }: { c: number | null }) {
  if (c == null) return <span style={{ fontSize: 11, color: 'var(--text3)' }}>—</span>
  const pct = Math.round(c * 100)
  const color = c >= 0.85 ? '#166534' : c >= 0.6 ? '#a16207' : '#b91c1c'
  return <span style={{ fontSize: 11, fontWeight: 700, color }}>{pct}%</span>
}

export default function ExtractionReview({ extraction, onAccepted, reviewedBy }: {
  extraction: any
  onAccepted?: () => void
  reviewedBy?: string
}) {
  const [accept, setAccept] = useState<Record<string, boolean>>({})
  const [confirmMoney, setConfirmMoney] = useState(false)
  const [acceptClauses, setAcceptClauses] = useState(false)
  const [acceptContacts, setAcceptContacts] = useState<Record<number, boolean>>({})
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [refused, setRefused] = useState<any[]>([])

  if (!extraction) return null
  const fields: any[] = extraction.fields || []
  const clauses: any[] = extraction.clauses || []
  const extras: any[] = extraction.extra_items || []
  const contacts: any[] = extraction.contacts || []
  const money = fields.filter(f => f.money_guarded)
  const plain = fields.filter(f => !f.money_guarded)
  const status = String(extraction.status || '')

  if (status === 'not_extracted' || status === 'failed') {
    return (
      <div style={{ padding: 12, background: 'var(--surface2)', borderRadius: 8, fontSize: 13, color: 'var(--text2)' }}>
        {extraction.error || 'This document was not read automatically. Fill the fields in by hand — '
          + 'the document itself is saved and downloadable.'}
      </div>
    )
  }

  const chosenMoney = money.filter(f => accept[f.key]).length

  async function save() {
    setBusy(true); setMsg(''); setRefused([])
    try {
      const r = await api('/api/v1/storeops/document-extraction/accept', {
        method: 'POST',
        body: JSON.stringify({
          extraction_id: extraction.id,
          accept: Object.keys(accept).filter(k => accept[k]),
          confirm_money: confirmMoney,
          accept_clauses: acceptClauses,
          accept_contacts: Object.keys(acceptContacts).filter(i => acceptContacts[Number(i)]).map(Number),
          reviewed_by: reviewedBy || '',
        }),
      })
      setRefused(r?.refused || [])
      const n = (r?.applied || []).length
      setMsg(n ? `✓ saved ${n} field${n === 1 ? '' : 's'}${r?.contacts_saved ? `, ${r.contacts_saved} contact(s)` : ''}`
        : 'Nothing was saved — tick the values you want to keep.')
      if (n && onAccepted) onAccepted()
    } catch (err: any) { setMsg('Save failed: ' + (err?.message || err)) }
    setBusy(false)
  }

  const Row = ({ f }: { f: any }) => (
    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
      <input type="checkbox" checked={!!accept[f.key]} style={{ marginTop: 3 }}
        onChange={e => setAccept(a => ({ ...a, [f.key]: e.target.checked }))} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, fontWeight: 700 }}>{f.label}</span>
          <span style={{ fontSize: 13 }}>{fmt(f.value)}</span>
          <Confidence c={f.confidence} />
          {f.money_guarded && <span style={{ fontSize: 10, fontWeight: 700, color: '#b45309', background: '#fef3c7', padding: '1px 5px', borderRadius: 4 }}>MONEY</span>}
        </div>
        {f.source_text && <div style={snip}>&ldquo;{f.source_text}&rdquo;{f.source_page ? ` — page ${f.source_page}` : ''}</div>}
      </div>
    </div>
  )

  return (
    <div style={{ padding: 12, background: 'var(--surface2)', borderRadius: 8, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ fontSize: 12, color: 'var(--text2)' }}>
        Read by {extraction.model || 'the document reader'} · nothing below is saved until you tick it.
        Each value shows the sentence it came from, so you can check it without opening the file.
      </div>

      {plain.length > 0 && (
        <div>
          <div style={{ ...lbl, marginBottom: 4 }}>Fields found</div>
          {plain.map(f => <Row key={f.key} f={f} />)}
        </div>
      )}

      {money.length > 0 && (
        <div style={{ border: '1px solid #fcd34d', background: '#fffbeb', borderRadius: 8, padding: 10 }}>
          <div style={{ ...lbl, color: '#92400e', marginBottom: 4 }}>Amounts — these feed the books</div>
          <div style={{ fontSize: 12, color: '#92400e', marginBottom: 6 }}>
            Rent and premiums entered here are what the finance reports bill and book. Check each one
            against the quoted text before you keep it.
          </div>
          {money.map(f => <Row key={f.key} f={f} />)}
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8, fontSize: 12, fontWeight: 700, color: '#92400e' }}>
            <input type="checkbox" checked={confirmMoney} onChange={e => setConfirmMoney(e.target.checked)} />
            I checked these amounts against the document and want them saved to the books.
          </label>
          {chosenMoney > 0 && !confirmMoney &&
            <div style={{ fontSize: 12, color: '#b91c1c', marginTop: 4 }}>
              {chosenMoney} amount{chosenMoney === 1 ? '' : 's'} ticked — tick the confirmation above or they will not be saved.
            </div>}
        </div>
      )}

      {clauses.length > 0 && (
        <div>
          <div style={{ ...lbl, marginBottom: 4 }}>Critical clauses — in plain English</div>
          {clauses.map((c, i) => (
            <div key={i} style={{ padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
              <div style={{ fontSize: 12, fontWeight: 700 }}>
                {c.clause_number ? `${c.clause_number} · ` : ''}{c.title || (c.category || '').replace(/_/g, ' ')}
                {c.source_page ? <span style={{ fontWeight: 400, color: 'var(--text3)' }}> — page {c.source_page}</span> : null}
              </div>
              <div style={{ fontSize: 13 }}>{c.plain_english}</div>
              {c.source_text && <div style={snip}>&ldquo;{c.source_text}&rdquo;</div>}
            </div>
          ))}
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 6, fontSize: 12 }}>
            <input type="checkbox" checked={acceptClauses} onChange={e => setAcceptClauses(e.target.checked)} />
            Keep this clause summary on the lease record
          </label>
        </div>
      )}

      {extras.length > 0 && (
        <div>
          <div style={{ ...lbl, marginBottom: 4 }}>Also worth knowing</div>
          {extras.map((x, i) => (
            <div key={i} style={{ fontSize: 12, padding: '3px 0' }}>
              <b>{x.label}:</b> {x.value}
              {x.note ? <span style={{ color: 'var(--text3)' }}> — {x.note}</span> : null}
              {x.source_page ? <span style={{ color: 'var(--text3)' }}> (page {x.source_page})</span> : null}
            </div>
          ))}
        </div>
      )}

      {contacts.length > 0 && (
        <div>
          <div style={{ ...lbl, marginBottom: 4 }}>Contacts found — add as expiry notification contacts</div>
          {contacts.map((c, i) => (
            <label key={i} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, padding: '2px 0' }}>
              <input type="checkbox" checked={!!acceptContacts[i]}
                onChange={e => setAcceptContacts(a => ({ ...a, [i]: e.target.checked }))} />
              <span>{[c.name, c.role, c.email, c.phone].filter(Boolean).join(' · ')}</span>
            </label>
          ))}
        </div>
      )}

      {refused.length > 0 && (
        <div style={{ fontSize: 12, color: '#b91c1c' }}>
          Not saved: {refused.map((r: any) => `${r.key} (${String(r.reason).replace(/_/g, ' ')})`).join(', ')}
        </div>
      )}

      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <button className="btn btn-primary" disabled={busy} onClick={save}>
          {busy ? '⏳ Saving…' : '✓ Save the ticked values'}
        </button>
        {msg && <span style={{ fontSize: 12, color: msg.startsWith('✓') ? '#166534' : '#b91c1c' }}>{msg}</span>}
      </div>
    </div>
  )
}
