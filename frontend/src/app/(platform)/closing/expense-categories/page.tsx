'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'

// Configurable Daily-Closing expense categories (mig 506, EEP): the 5 presets (Salary/Commission
// payroll+commission-kind; Petty/Office/Supplies expense-kind) are lazy-seeded on first load —
// mirrors /closing/count-config / /closing/tender-config. `kind` drives behaviour everywhere else in
// the package: payroll/commission require picking an employee and record a cash ADVANCE (never P&L);
// expense is a plain P&L expense that rolls up to Store Expenses once approved. Categories are never
// hard-deleted (an already-posted commcalc.closing_expense row references one by id) — deactivate
// instead.
const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 13 }
const KINDS = [
  ['expense', 'Expense (rolls up to P&L when approved)'],
  ['payroll', 'Payroll (cash advance — never P&L)'],
  ['commission', 'Commission (cash advance — never P&L)'],
]

type Cat = { id?: string; name: string; kind: string; is_preset?: boolean; is_active?: boolean; sort_order?: number }

export default function ExpenseCategoriesPage() {
  const [cats, setCats] = useState<Cat[]>([])
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api('/api/v1/closing/expense-categories').then((d: any) => setCats(d?.categories || []))
      .catch((e: any) => setMsg('❌ ' + (e?.message || e)))
  }, [])
  useEffect(() => { load() }, [load])

  const setCat = (i: number, patch: Partial<Cat>) => setCats(cs => cs.map((c, j) => j === i ? { ...c, ...patch } : c))
  const addCat = () => setCats(cs => [...cs, { name: '', kind: 'expense', is_active: true }])

  async function saveAll() {
    const cleaned = cats.map((c, i) => ({ ...c, sort_order: i })).filter(c => c.name.trim())
    setBusy(true)
    try {
      const r: any = await api('/api/v1/closing/expense-categories', { method: 'PUT', body: JSON.stringify({ categories: cleaned }) })
      setMsg(`✅ Saved ${r?.saved ?? cleaned.length} categor${(r?.saved ?? cleaned.length) === 1 ? 'y' : 'ies'}.`); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 6 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🗂️ Expense Categories</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
            Categories for expenses entered from the Daily Closing form. <b>Payroll</b>/<b>Commission</b>
            categories require picking an employee and record a cash advance from the envelope — they
            never post to the P&amp;L. <b>Expense</b> categories are plain store expenses that roll up to
            the P&amp;L Store Expenses report once a manager approves the line. Deactivate a category
            instead of deleting it — expenses already posted against it stay readable.
          </p>
        </div>
        <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
      </div>

      <div style={{ position: 'sticky', top: 0, zIndex: 5, background: 'var(--bg)', padding: '10px 0', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', borderBottom: '1px solid var(--border)' }}>
        <button className="btn btn-primary" disabled={busy} style={{ fontSize: 13 }} onClick={saveAll}>💾 Save categories</button>
        {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
      </div>

      <div className="card table-wrapper" style={{ marginTop: 16, padding: 0 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Name', 'Kind', 'Active', ''].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {cats.map((c, i) => (
              <tr key={c.id || i}>
                <td style={cell}>
                  <input style={{ ...sel, width: '100%' }} value={c.name} placeholder="e.g. Fuel Reimbursement"
                    onChange={e => setCat(i, { name: e.target.value })} />
                  {c.is_preset && <div style={{ fontSize: 10, color: 'var(--text3)' }}>preset</div>}
                </td>
                <td style={cell}>
                  <select style={sel} value={c.kind} onChange={e => setCat(i, { kind: e.target.value })}>
                    {KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </td>
                <td style={cell}>
                  <input type="checkbox" checked={c.is_active !== false} onChange={e => setCat(i, { is_active: e.target.checked })} />
                </td>
                <td style={cell}>{!c.is_active && <span style={{ fontSize: 11, color: 'var(--text3)' }}>hidden from new submissions</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button className="btn btn-secondary" style={{ fontSize: 13, marginTop: 8 }} onClick={addCat}>＋ Add category</button>
    </div>
  )
}
