'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'

// Configurable Cash Deposit Reconciliation categories + adjustment types (mig 509, OWNER DIRECTIVE
// 2026-08-05). "Bill Payment Cash Deposit"/"Store Cash Deposit" are lazy-seeded on first load — mirrors
// /closing/expense-categories exactly. `basis` drives WHICH already-computed cash figure a category
// reconciles against (never re-derived): bill_payment_cash | store_cash | total_cash | manual (a
// tenant-added bucket with no auto-computed expected figure yet). Categories are never hard-deleted —
// an already-posted bank_deposit row references one by id; deactivate instead.
const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 13 }
const BASIS_OPTS: [string, string][] = [
  ['bill_payment_cash', 'Bill-payment cash (declared bill-pay on-cash)'],
  ['store_cash', 'Store cash (total cash minus bill-payment cash)'],
  ['total_cash', 'Total cash (the whole envelope, no split)'],
  ['manual', 'Manual / no formula yet (deposits still tracked; expected stays $0)'],
]

type Cat = { id?: string; name: string; basis: string; is_preset?: boolean; is_active?: boolean; sort_order?: number }
type AdjType = { id?: string; name: string; is_active?: boolean; sort_order?: number }

export default function DepositCategoriesPage() {
  const [cats, setCats] = useState<Cat[]>([])
  const [types, setTypes] = useState<AdjType[]>([])
  const [msg, setMsg] = useState('')
  const [msg2, setMsg2] = useState('')
  const [busy, setBusy] = useState(false)

  const loadCats = useCallback(() => {
    api('/api/v1/closing/deposit-categories').then((d: any) => setCats(d?.categories || []))
      .catch((e: any) => setMsg('❌ ' + (e?.message || e)))
  }, [])
  const loadTypes = useCallback(() => {
    api('/api/v1/closing/deposit-adjustment-types').then((d: any) => setTypes(d?.types || []))
      .catch((e: any) => setMsg2('❌ ' + (e?.message || e)))
  }, [])
  useEffect(() => { loadCats(); loadTypes() }, [loadCats, loadTypes])

  const setCat = (i: number, patch: Partial<Cat>) => setCats(cs => cs.map((c, j) => j === i ? { ...c, ...patch } : c))
  const addCat = () => setCats(cs => [...cs, { name: '', basis: 'manual', is_active: true }])
  const setType = (i: number, patch: Partial<AdjType>) => setTypes(ts => ts.map((t, j) => j === i ? { ...t, ...patch } : t))
  const addType = () => setTypes(ts => [...ts, { name: '', is_active: true }])

  async function saveCats() {
    const cleaned = cats.map((c, i) => ({ ...c, sort_order: i })).filter(c => c.name.trim())
    setBusy(true)
    try {
      const r: any = await api('/api/v1/closing/deposit-categories', { method: 'PUT', body: JSON.stringify({ categories: cleaned }) })
      setMsg(`✅ Saved ${r?.saved ?? cleaned.length} categor${(r?.saved ?? cleaned.length) === 1 ? 'y' : 'ies'}.`); loadCats()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }
  async function saveTypes() {
    const cleaned = types.map((t, i) => ({ ...t, sort_order: i })).filter(t => t.name.trim())
    setBusy(true)
    try {
      const r: any = await api('/api/v1/closing/deposit-adjustment-types', { method: 'PUT', body: JSON.stringify({ types: cleaned }) })
      setMsg2(`✅ Saved ${r?.saved ?? cleaned.length} type${(r?.saved ?? cleaned.length) === 1 ? '' : 's'}.`); loadTypes()
    } catch (e: any) { setMsg2('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 6 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🗂️ Cash Deposit Categories</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 780 }}>
            Categories split a bank deposit into buckets that reconcile against the matching cash bucket
            (bill-payment cash collected reconciles against <b>Bill Payment Cash Deposit</b>; the remaining
            store cash against <b>Store Cash Deposit</b>). Add more as needed. Deactivate a category instead
            of deleting it — deposits already posted against it stay readable. Default include/exclude
            adjustment toggles live on the <Link href="/closing/cash-config" style={{ color: 'var(--accent)' }}>Cash Config</Link> page;
            run the report itself on <Link href="/closing/deposit-recon" style={{ color: 'var(--accent)' }}>Cash Deposit Recon</Link>.
          </p>
        </div>
        <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
      </div>

      <div style={{ position: 'sticky', top: 0, zIndex: 5, background: 'var(--bg)', padding: '10px 0', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', borderBottom: '1px solid var(--border)' }}>
        <button className="btn btn-primary" disabled={busy} style={{ fontSize: 13 }} onClick={saveCats}>💾 Save categories</button>
        {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
      </div>

      <div className="card table-wrapper" style={{ marginTop: 16, padding: 0 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Name', 'Reconciles against', 'Active', ''].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {cats.map((c, i) => (
              <tr key={c.id || i}>
                <td style={cell}>
                  <input style={{ ...sel, width: '100%' }} value={c.name} placeholder="e.g. Register 2 Cash Deposit"
                    onChange={e => setCat(i, { name: e.target.value })} />
                  {c.is_preset && <div style={{ fontSize: 10, color: 'var(--text3)' }}>preset</div>}
                </td>
                <td style={cell}>
                  <select style={sel} value={c.basis} onChange={e => setCat(i, { basis: e.target.value })}>
                    {BASIS_OPTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </td>
                <td style={cell}>
                  <input type="checkbox" checked={c.is_active !== false} onChange={e => setCat(i, { is_active: e.target.checked })} />
                </td>
                <td style={cell}>{!c.is_active && <span style={{ fontSize: 11, color: 'var(--text3)' }}>hidden from new deposits</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button className="btn btn-secondary" style={{ fontSize: 13, marginTop: 8 }} onClick={addCat}>＋ Add category</button>

      <h2 style={{ fontSize: 16, fontWeight: 700, marginTop: 28 }}>🏷️ Other adjustment types</h2>
      <p style={{ color: 'var(--text2)', fontSize: 13, margin: '4px 0 12px', maxWidth: 780 }}>
        The tenant-configured &quot;any other adjustment item&quot; bucket the report can optionally subtract
        from expected deposit (cash expenses and bill-payment cash are already covered automatically — this
        is only for anything else, e.g. a safe change fund or a bank fee). No presets — add your own.
      </p>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 10 }}>
        <button className="btn btn-primary" disabled={busy} style={{ fontSize: 13 }} onClick={saveTypes}>💾 Save adjustment types</button>
        {msg2 && <span style={{ fontSize: 13 }}>{msg2}</span>}
      </div>
      <div className="card table-wrapper" style={{ padding: 0 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Name', 'Active'].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {types.map((t, i) => (
              <tr key={t.id || i}>
                <td style={cell}>
                  <input style={{ ...sel, width: '100%' }} value={t.name} placeholder="e.g. Safe Change Fund"
                    onChange={e => setType(i, { name: e.target.value })} />
                </td>
                <td style={cell}>
                  <input type="checkbox" checked={t.is_active !== false} onChange={e => setType(i, { is_active: e.target.checked })} />
                </td>
              </tr>
            ))}
            {types.length === 0 && <tr><td colSpan={2} style={{ ...cell, color: 'var(--text3)' }}>No adjustment types yet.</td></tr>}
          </tbody>
        </table>
      </div>
      <button className="btn btn-secondary" style={{ fontSize: 13, marginTop: 8 }} onClick={addType}>＋ Add adjustment type</button>
    </div>
  )
}
