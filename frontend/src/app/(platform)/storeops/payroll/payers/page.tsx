'use client'
// Who pays payroll — the configurable payer registry + the per-store default map (migration 431).
//
// RULE TWO (SAP-configurable): the parties who disburse payroll are CONFIG, never hard-coded. A tenant
// adds its own accounting address, its district managers, and any third party that hands out cash or
// writes cheques. RULE THREE: the approval screen PICKS from this list rather than typing an address.
//
// The org default is what pre-fills every employee nobody has routed — "by default the accounting
// email as defined by the user", from the owner's directive. Exactly one payer can hold it.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'

type Payer = {
  id: string; name: string; kind: 'accounting' | 'dm' | 'third_party'
  email?: string | null; phone?: string | null; dm_employee_id?: string | null
  note?: string | null; is_active?: boolean; is_default?: boolean
}

const card: React.CSSProperties = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 16 }
const btn: React.CSSProperties = { padding: '7px 13px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', fontSize: 13, cursor: 'pointer', fontWeight: 600 }
const btnP: React.CSSProperties = { ...btn, background: 'var(--accent)', color: '#fff', border: 'none' }
const inp: React.CSSProperties = { padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text1)' }

const KINDS: [Payer['kind'], string, string][] = [
  ['accounting', 'Accounting', 'Your finance inbox. The usual default — it receives the payout statement and pays.'],
  ['dm', 'District manager', 'The DM of each store on the statement. Leave the employee blank to resolve each store’s own DM at send time.'],
  ['third_party', 'Third party', 'Anyone outside the company who disburses cash or issues cheques.'],
]

export default function PayrollPayersPage() {
  const [payers, setPayers] = useState<Payer[]>([])
  const [storeMap, setStoreMap] = useState<Record<string, string>>({})
  const [stores, setStores] = useState<string[]>([])
  const [ready, setReady] = useState(true)
  const [note, setNote] = useState('')
  const [msg, setMsg] = useState(''); const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState<Partial<Payer>>({ kind: 'accounting' })

  const load = useCallback(async () => {
    setErr('')
    try {
      const d = await api('/api/v1/storeops/payroll/payers')
      setReady(d?.ready !== false); setNote(d?.note || '')
      setPayers(d?.payers || []); setStoreMap(d?.stores || {})
    } catch (e: any) { setErr(e?.message || 'Could not load') }
    try {
      const o = await apiCached('/api/v1/core/filter-options', LOOKUP)
      setStores(((o?.stores || []) as any[]).map(s => (typeof s === 'string' ? s : s.id || s.label)).filter(Boolean).sort())
    } catch { /* the store list is a convenience; the page still works without it */ }
  }, [])
  useEffect(() => { load() }, [load])

  async function save() {
    if (!draft.name?.trim()) { setErr('Give the payer a name'); return }
    setBusy(true); setErr(''); setMsg('')
    try {
      await api('/api/v1/storeops/payroll/payers', { method: 'POST', body: JSON.stringify(draft) })
      setMsg(`Added ${draft.name}.`); setDraft({ kind: 'accounting' }); await load()
    } catch (e: any) { setErr(e?.message || 'Could not save') }
    setBusy(false)
  }

  async function patch(id: string, body: any, okMsg: string) {
    setBusy(true); setErr(''); setMsg('')
    try {
      await api(`/api/v1/storeops/payroll/payers/${id}`, { method: 'PATCH', body: JSON.stringify(body) })
      setMsg(okMsg); await load()
    } catch (e: any) { setErr(e?.message || 'Could not update') }
    setBusy(false)
  }

  async function remove(p: Payer) {
    if (!window.confirm(`Remove ${p.name}? Stores routed to them fall back to the default payer.`)) return
    setBusy(true); setErr('')
    try {
      await api(`/api/v1/storeops/payroll/payers/${p.id}`, { method: 'DELETE' })
      setMsg(`Removed ${p.name}.`); await load()
    } catch (e: any) { setErr(e?.message || 'Could not remove') }
    setBusy(false)
  }

  async function routeStore(store: string, payerId: string) {
    setBusy(true); setErr(''); setMsg('')
    try {
      await api('/api/v1/storeops/payroll/store-payers', {
        method: 'PUT', body: JSON.stringify({ stores: { [store]: payerId || null } }),
      })
      setStoreMap(m => { const n = { ...m }; if (payerId) n[store] = payerId; else delete n[store]; return n })
      setMsg(`${store} routed.`)
    } catch (e: any) { setErr(e?.message || 'Could not route that store') }
    setBusy(false)
  }

  const defaultPayer = payers.find(p => p.is_default)

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 4 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Who pays payroll</h1>
        <div style={{ flex: 1 }} />
        <Link href="/storeops/payroll/approvals" style={{ ...btn, textDecoration: 'none', color: 'var(--text1)' }}>← Hours Approval</Link>
      </div>
      <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13, margin: '0 0 14px', maxWidth: '78ch' }}>
        Once HR approves a week, each of these parties gets a statement listing only the employees they
        pay. Set one as the default — it pre-fills every employee nobody has routed anywhere else.
      </p>

      {!ready && <div style={{ ...card, background: '#fff7ed', borderColor: '#fdba74', color: '#9a3412', marginBottom: 12 }}>{note}</div>}
      {err && <div style={{ ...card, background: '#fef2f2', borderColor: '#fca5a5', color: '#991b1b', marginBottom: 12, fontSize: 13 }}>{err}</div>}
      {msg && <div style={{ ...card, background: '#ecfdf5', borderColor: '#a7f3d0', color: '#065f46', marginBottom: 12, fontSize: 13 }}>{msg}</div>}
      {ready && !defaultPayer && payers.length > 0 && (
        <div style={{ ...card, background: '#fff7ed', borderColor: '#fdba74', color: '#9a3412', marginBottom: 12, fontSize: 13 }}>
          No default payer yet — employees whose store isn&apos;t routed will have nowhere to go at send time.
        </div>
      )}

      <div style={{ ...card, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10 }}>Add a payer</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <input style={{ ...inp, width: 180 }} placeholder="Name (e.g. Accounts Payable)"
            value={draft.name || ''} onChange={e => setDraft(d => ({ ...d, name: e.target.value }))} />
          <select style={{ ...inp, width: 170 }} value={draft.kind}
            onChange={e => setDraft(d => ({ ...d, kind: e.target.value as Payer['kind'] }))}>
            {KINDS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
          <input style={{ ...inp, width: 220 }} placeholder={draft.kind === 'dm' ? 'Email (optional for a DM)' : 'Email (required)'}
            value={draft.email || ''} onChange={e => setDraft(d => ({ ...d, email: e.target.value }))} />
          {draft.kind === 'dm' && (
            <input style={{ ...inp, width: 190 }} placeholder="Pin to one employee ID (optional)"
              value={draft.dm_employee_id || ''} onChange={e => setDraft(d => ({ ...d, dm_employee_id: e.target.value }))} />
          )}
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text2)' }}>
            <input type="checkbox" checked={!!draft.is_default}
              onChange={e => setDraft(d => ({ ...d, is_default: e.target.checked }))} /> default
          </label>
          <button style={{ ...btnP, opacity: busy ? 0.5 : 1 }} disabled={busy} onClick={save}>Add</button>
        </div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>
          {KINDS.find(k => k[0] === draft.kind)?.[2]}
        </div>
      </div>

      <div className="table-wrapper" style={{ marginBottom: 22 }}>
        <table>
          <thead><tr><th>Payer</th><th>Type</th><th>Reaches</th><th>Default</th><th>Active</th><th></th></tr></thead>
          <tbody>
            {payers.length === 0 && (
              <tr><td colSpan={6} style={{ color: 'var(--text3)', padding: 20, textAlign: 'center' }}>
                No payers configured yet — add your accounting inbox first.
              </td></tr>
            )}
            {payers.map(p => (
              <tr key={p.id}>
                <td style={{ fontWeight: 600 }}>{p.name}{p.note && <div style={{ fontSize: 11, color: 'var(--text3)' }}>{p.note}</div>}</td>
                <td>{KINDS.find(k => k[0] === p.kind)?.[1] || p.kind}</td>
                <td style={{ fontSize: 12.5 }}>
                  {p.kind === 'dm' && !p.dm_employee_id
                    ? <span style={{ color: 'var(--text2)' }}>each store&apos;s own DM</span>
                    : (p.email || <span style={{ color: '#b45309' }}>no email</span>)}
                </td>
                <td>
                  {p.is_default
                    ? <span className="badge badge-green">default</span>
                    : <button style={{ ...btn, padding: '3px 9px', fontSize: 12 }} disabled={busy}
                        onClick={() => patch(p.id, { is_default: true }, `${p.name} is now the default.`)}>make default</button>}
                </td>
                <td>
                  <input type="checkbox" checked={p.is_active !== false} disabled={busy}
                    onChange={e => patch(p.id, { is_active: e.target.checked }, 'Updated.')} />
                </td>
                <td><button style={{ ...btn, padding: '3px 9px', fontSize: 12, color: '#dc2626' }}
                  disabled={busy} onClick={() => remove(p)}>Remove</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 4px' }}>Default payer per store</h2>
      <p style={{ color: 'var(--text2)', fontSize: 13, margin: '0 0 10px', maxWidth: '78ch' }}>
        Every employee inherits their store&apos;s payer. Leave a store on the default unless someone else
        pays it — an individual can still be switched for a single week on the approval screen.
      </p>
      <div className="table-wrapper">
        <table>
          <thead><tr><th>Store</th><th>Paid by</th></tr></thead>
          <tbody>
            {stores.length === 0 && (
              <tr><td colSpan={2} style={{ color: 'var(--text3)', padding: 20, textAlign: 'center' }}>No stores loaded.</td></tr>
            )}
            {stores.map(s => (
              <tr key={s}>
                <td style={{ fontWeight: 600 }}>{s}</td>
                <td>
                  <select style={{ ...inp, minWidth: 220 }} value={storeMap[s] || ''} disabled={busy}
                    onChange={e => routeStore(s, e.target.value)}>
                    <option value="">{defaultPayer ? `Company default — ${defaultPayer.name}` : 'Company default — none set'}</option>
                    {payers.filter(p => p.is_active !== false).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
