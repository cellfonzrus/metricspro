'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

const inp: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const PL_TYPES = ['revenue', 'cogs', 'opex', 'other']
const BS_TYPES = ['asset', 'liability', 'equity']
// common manual lines the cash-basis chart needs (the AUTO lines come from the data)
const SUGGESTED = [
  { statement: 'balance_sheet', account_type: 'asset', account_line: 'Cash / bank' },
  { statement: 'balance_sheet', account_type: 'asset', account_line: 'Fixtures / equipment' },
  { statement: 'balance_sheet', account_type: 'equity', account_line: 'Owner capital / contributions' },
  { statement: 'balance_sheet', account_type: 'equity', account_line: 'Opening retained earnings' },
  { statement: 'pl', account_type: 'opex', account_line: 'Wages / hourly payroll' },
  { statement: 'pl', account_type: 'other', account_line: 'Income taxes' },
  { statement: 'pl', account_type: 'other', account_line: 'Interest expense' },
]
type Row = { statement: string; account_type: string; account_line: string; amount: number; store_address?: string; memo?: string }

export default function JournalPage() {
  const { period } = usePeriod()
  const [rows, setRows] = useState<Row[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  function load() {
    setLoading(true)
    api(`/api/v1/account/journal/${encodeURIComponent(period)}?org_id=${ORG_ID}`).then((d: any) => {
      setRows((d.entries || []).map((e: any) => ({
        statement: e.statement, account_type: e.account_type, account_line: e.account_line,
        amount: parseFloat(e.amount) || 0, store_address: e.store_address || '', memo: e.memo || '',
      })))
    }).catch(console.error).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [period])

  const set = (i: number, patch: Partial<Row>) => setRows(r => r.map((x, j) => j === i ? { ...x, ...patch } : x))
  const addRow = (seed?: Partial<Row>) => setRows(r => [...r, { statement: 'balance_sheet', account_type: 'asset', account_line: '', amount: 0, store_address: '', memo: '', ...seed }])
  const del = (i: number) => setRows(r => r.filter((_, j) => j !== i))

  async function save() {
    setSaving(true); setMsg('')
    try {
      const r = await api(`/api/v1/account/journal/${encodeURIComponent(period)}?org_id=${ORG_ID}`, {
        method: 'PUT', body: JSON.stringify({ rows: rows.filter(x => x.account_line.trim() && x.amount) }),
      })
      setMsg(`Saved ${r.saved} entries. Re-compute statements on the dashboard to apply.`); load()
    } catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) }
    setSaving(false)
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📒 Manual Journal Entries</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>{period} · cash/bank, fixtures, owner capital, payroll, taxes — the lines the engine can't derive from the data.</p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {msg && <span style={{ fontSize: 12, color: 'var(--text2)' }}>{msg}</span>}
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : '💾 Save entries'}</button>
        </div>
      </div>

      <div className="card" style={{ padding: 12, marginBottom: 14, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>Quick add:</span>
        {SUGGESTED.map(s => (
          <button key={s.account_line} className="btn" style={{ fontSize: 12 }} onClick={() => addRow(s)}>＋ {s.account_line}</button>
        ))}
        <button className="btn" style={{ fontSize: 12 }} onClick={() => addRow()}>＋ Blank row</button>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 880 }}>
            <thead>
              <tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Statement</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Type</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Account line</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Amount</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Store (optional)</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Memo</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const types = r.statement === 'pl' ? PL_TYPES : BS_TYPES
                return (
                  <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '5px 12px' }}>
                      <select style={inp} value={r.statement} onChange={e => set(i, { statement: e.target.value, account_type: (e.target.value === 'pl' ? PL_TYPES : BS_TYPES)[0] })}>
                        <option value="pl">P&amp;L</option><option value="balance_sheet">Balance Sheet</option>
                      </select>
                    </td>
                    <td style={{ padding: '5px 12px' }}>
                      <select style={inp} value={r.account_type} onChange={e => set(i, { account_type: e.target.value })}>
                        {types.map(t => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </td>
                    <td style={{ padding: '5px 12px' }}><input style={{ ...inp, width: 200 }} value={r.account_line} placeholder="e.g. Cash / bank" onChange={e => set(i, { account_line: e.target.value })} /></td>
                    <td style={{ padding: '5px 12px' }}><input type="number" step="0.01" style={{ ...inp, width: 120, textAlign: 'right' }} value={r.amount || ''} onChange={e => set(i, { amount: parseFloat(e.target.value) || 0 })} /></td>
                    <td style={{ padding: '5px 12px' }}><input style={{ ...inp, width: 160 }} value={r.store_address} placeholder="(all stores)" onChange={e => set(i, { store_address: e.target.value })} /></td>
                    <td style={{ padding: '5px 12px' }}><input style={{ ...inp, width: 160 }} value={r.memo} onChange={e => set(i, { memo: e.target.value })} /></td>
                    <td style={{ padding: '5px 12px' }}><button className="btn" style={{ fontSize: 12 }} onClick={() => del(i)}>✕</button></td>
                  </tr>
                )
              })}
              {rows.length === 0 && <tr><td colSpan={7} style={{ padding: 30, textAlign: 'center', color: 'var(--text3)' }}>No manual entries yet. Use Quick add above.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 10 }}>
        Match an account line label to a statement line to fill it (e.g. “Cash / bank”, “Wages / hourly payroll”). Other labels appear as their own line in the chosen section. To balance the Balance Sheet, enter Cash and any Owner capital / Opening retained earnings.
      </p>
    </div>
  )
}
