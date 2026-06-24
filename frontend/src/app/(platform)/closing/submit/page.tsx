'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, fmt, localToday } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

// Rep-facing in-app closing form — replicates the Google "Envelopes Data" sheet, one row per
// rep per day. Posts to /closing/row (source='manual'); coexists with the sheet upload.
const inp: React.CSSProperties = { padding: '8px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 14, background: 'var(--surface)', width: '100%' }
const cell: React.CSSProperties = { padding: '6px 9px', borderBottom: '1px solid var(--border)', fontSize: 13 }

type State = {
  close_date: string; sfid: string; store_name: string; store_code: string; employee_name: string
  store_cash: string; store_cc: string; epay_cash: string; epay_cc: string; acc_sale: string; other_account: string
  upgrade_count: string; new_line_count: string; postpaid_count: string; envelope_picture: string; remarks: string
}

const blank = (): State => ({
  close_date: localToday(), sfid: '', store_name: '', store_code: '', employee_name: '',
  store_cash: '', store_cc: '', epay_cash: '', epay_cc: '', acc_sale: '', other_account: '',
  upgrade_count: '', new_line_count: '', postpaid_count: '', envelope_picture: '', remarks: '',
})

export default function SubmitClosingPage() {
  const { user } = useAuth()
  const [f, setF] = useState<State>(blank())
  const [stores, setStores] = useState<any[]>([])
  const [recent, setRecent] = useState<any[]>([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const set = (patch: Partial<State>) => setF(p => ({ ...p, ...patch }))

  useEffect(() => { api('/api/v1/closing/stores').then(s => setStores(s || [])).catch(() => {}) }, [])
  useEffect(() => { if (user?.full_name && !f.employee_name) set({ employee_name: user.full_name }) }, [user]) // eslint-disable-line

  const loadRecent = useCallback(() => {
    if (!f.close_date) return
    api(`/api/v1/closing/days?date=${f.close_date}`).then(r => setRecent(r || [])).catch(() => {})
  }, [f.close_date])
  useEffect(() => { loadRecent() }, [loadRecent])

  function pickStore(idx: string) {
    const s = stores[Number(idx)]
    if (!s) { set({ sfid: '', store_code: '', store_name: '' }); return }
    set({ sfid: s.sfid, store_code: s.store_code, store_name: s.store_address || s.store_code })
  }

  async function submit() {
    if (!f.close_date) { setMsg('❌ Pick a date.'); return }
    if (!f.sfid && !f.store_code) { setMsg('❌ Pick your store.'); return }
    if (!f.employee_name.trim()) { setMsg('❌ Enter your name.'); return }
    setBusy(true); setMsg('')
    try {
      await api('/api/v1/closing/row', { method: 'POST', body: JSON.stringify({
        close_date: f.close_date, sfid: f.sfid, store_code: f.store_code, store_name: f.store_name,
        employee_name: f.employee_name.trim(),
        store_cash: f.store_cash, store_cc: f.store_cc, epay_cash: f.epay_cash, epay_cc: f.epay_cc,
        acc_sale: f.acc_sale, other_account: f.other_account,
        upgrade_count: f.upgrade_count, new_line_count: f.new_line_count, postpaid_count: f.postpaid_count,
        envelope_picture: f.envelope_picture, remarks: f.remarks,
      }) })
      setMsg('✅ Closing submitted. You can enter another below.')
      // keep date + store + name; clear the money/counts for the next entry
      setF(p => ({ ...p, store_cash: '', store_cc: '', epay_cash: '', epay_cc: '', acc_sale: '', other_account: '',
        upgrade_count: '', new_line_count: '', postpaid_count: '', envelope_picture: '', remarks: '' }))
      loadRecent()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy(false) }
  }

  const storeIdx = stores.findIndex(s => (f.sfid && s.sfid === f.sfid) || (!f.sfid && f.store_code && s.store_code === f.store_code))

  return (
    <div style={{ maxWidth: 760 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>➕ Submit Daily Closing</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>One entry per rep per day — same fields as the closing sheet.</p>
        </div>
        <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
      </div>

      <div className="card" style={{ padding: 18 }}>
        {/* Who / where / when */}
        <Row>
          <Field label="Date"><input type="date" style={inp} value={f.close_date} onChange={e => set({ close_date: e.target.value })} /></Field>
          <Field label="Store">
            <select style={inp} value={storeIdx >= 0 ? String(storeIdx) : ''} onChange={e => pickStore(e.target.value)}>
              <option value="">Select store…</option>
              {stores.map((s, i) => <option key={i} value={i}>{s.store_address || s.store_code}{s.market ? ` · ${s.market}` : ''}</option>)}
            </select>
          </Field>
          <Field label="Employee"><input style={inp} value={f.employee_name} onChange={e => set({ employee_name: e.target.value })} placeholder="Your name" /></Field>
        </Row>

        {/* Money */}
        <SectionLabel>Money collected</SectionLabel>
        <Row>
          <Field label="Store Cash $"><input style={inp} inputMode="decimal" value={f.store_cash} onChange={e => set({ store_cash: e.target.value })} placeholder="0.00" /></Field>
          <Field label="Store CC $"><input style={inp} inputMode="decimal" value={f.store_cc} onChange={e => set({ store_cc: e.target.value })} placeholder="0.00" /></Field>
          <Field label="ePay Cash $"><input style={inp} inputMode="decimal" value={f.epay_cash} onChange={e => set({ epay_cash: e.target.value })} placeholder="0.00" /></Field>
        </Row>
        <Row>
          <Field label="ePay CC $"><input style={inp} inputMode="decimal" value={f.epay_cc} onChange={e => set({ epay_cc: e.target.value })} placeholder="0.00" /></Field>
          <Field label="Accessory Sale $"><input style={inp} inputMode="decimal" value={f.acc_sale} onChange={e => set({ acc_sale: e.target.value })} placeholder="0.00" /></Field>
          <Field label="Zelle / CashApp / Other $"><input style={inp} inputMode="decimal" value={f.other_account} onChange={e => set({ other_account: e.target.value })} placeholder="0.00" /></Field>
        </Row>

        {/* Counts */}
        <SectionLabel>Transaction counts</SectionLabel>
        <Row>
          <Field label="Upgrades #"><input style={inp} inputMode="numeric" value={f.upgrade_count} onChange={e => set({ upgrade_count: e.target.value })} placeholder="0" /></Field>
          <Field label="New Lines #"><input style={inp} inputMode="numeric" value={f.new_line_count} onChange={e => set({ new_line_count: e.target.value })} placeholder="0" /></Field>
          <Field label="Postpaid #"><input style={inp} inputMode="numeric" value={f.postpaid_count} onChange={e => set({ postpaid_count: e.target.value })} placeholder="0" /></Field>
        </Row>

        {/* Envelope + remarks */}
        <SectionLabel>Envelope & remarks</SectionLabel>
        <Row>
          <Field label="Envelope photo link" wide><input style={inp} value={f.envelope_picture} onChange={e => set({ envelope_picture: e.target.value })} placeholder="Paste a Drive/photo link" /></Field>
        </Row>
        <Row>
          <Field label="Remarks" wide><input style={inp} value={f.remarks} onChange={e => set({ remarks: e.target.value })} placeholder="Optional note" /></Field>
        </Row>

        <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 16 }}>
          <button className="btn btn-primary" style={{ fontSize: 14 }} disabled={busy} onClick={submit}>{busy ? '⏳ Submitting…' : '✅ Submit closing'}</button>
          {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
        </div>
      </div>

      {/* Today's submissions */}
      <div style={{ marginTop: 20 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text2)', marginBottom: 8 }}>Submissions for {f.close_date} ({recent.length})</div>
        {recent.length === 0 ? (
          <div className="card" style={{ padding: 20, textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>No entries yet for this date.</div>
        ) : (
          <div className="card table-wrapper" style={{ padding: 0 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {['Employee', 'Store', 'Cash', 'Credit', 'Acc', 'Other', 'Upg', 'New', 'Post', 'Src'].map(h =>
                  <th key={h} style={{ textAlign: 'left', padding: '6px 9px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {recent.map((r: any) => (
                  <tr key={r.id}>
                    <td style={cell}>{r.employee_name || '—'}</td>
                    <td style={cell}>{r.store_address || r.store_name || r.store_code || '—'}</td>
                    <td style={cell}>{fmt((r.store_cash || 0) + (r.epay_cash || 0))}</td>
                    <td style={cell}>{fmt((r.store_cc || 0) + (r.epay_cc || 0))}</td>
                    <td style={cell}>{fmt(r.acc_sale)}</td>
                    <td style={cell}>{fmt(r.other_account)}</td>
                    <td style={cell}>{r.upgrade_count}</td>
                    <td style={cell}>{r.new_line_count}</td>
                    <td style={cell}>{r.postpaid_count}</td>
                    <td style={cell}><span style={{ fontSize: 11, color: 'var(--text3)' }}>{r.source === 'manual' ? 'form' : 'sheet'}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

const Row = ({ children }: { children: React.ReactNode }) => (
  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>{children}</div>
)
const Field = ({ label, children, wide }: { label: string; children: React.ReactNode; wide?: boolean }) => (
  <label style={{ flex: wide ? '1 1 100%' : '1 1 200px', minWidth: 160 }}>
    <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>{label}</div>
    {children}
  </label>
)
const SectionLabel = ({ children }: { children: React.ReactNode }) => (
  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', margin: '14px 0 8px', borderTop: '1px solid var(--border)', paddingTop: 12 }}>{children}</div>
)
