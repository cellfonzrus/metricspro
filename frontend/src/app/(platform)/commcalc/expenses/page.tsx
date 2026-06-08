'use client'
import { useState, useEffect } from 'react'
import { supabase, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

const EXPENSE_CATS = [
  { name: 'Rent / Lease',          type: 'Variable' },
  { name: 'B2B Platform Fee',      type: 'Fixed' },
  { name: 'Cellsmart POS',         type: 'Fixed' },
  { name: 'Electric',              type: 'Variable' },
  { name: 'Heat / Gas',            type: 'Variable' },
  { name: 'Internet',              type: 'Fixed' },
  { name: 'Insurance',             type: 'Fixed' },
  { name: 'Advertising',           type: 'Fixed' },
  { name: 'Cleaning',              type: 'Fixed' },
  { name: 'Garbage / Waste',       type: 'Variable' },
  { name: 'Maintenance',           type: 'Fixed' },
  { name: 'ADT Security',          type: 'Fixed' },
  { name: 'Back Office Fee',       type: 'Fixed' },
  { name: 'Taxes / Accounting',    type: 'Fixed' },
  { name: 'Employee Salaries',     type: 'Fixed' },
  { name: 'Owner / Mgmt Salaries', type: 'Fixed' },
]

export default function ExpensesPage() {
  const { period } = usePeriod()
  const [stores, setStores] = useState<any[]>([])
  const [expenses, setExpenses] = useState<Record<string, Record<string, number>>>({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      supabase.from('stores').select('store_code, address, market').eq('is_active', true).order('address'),
      supabase.from('commcalc_store_expenses').select('*').eq('period', period),
    ]).then(([{ data: storeData }, { data: expData }]) => {
      setStores(storeData || [])
      const map: Record<string, Record<string, number>> = {}
      ;(expData || []).forEach((e: any) => {
        if (!map[e.store_code]) map[e.store_code] = {}
        map[e.store_code][e.expense_name] = parseFloat(e.amount) || 0
      })
      setExpenses(map)
    }).catch(console.error).finally(() => setLoading(false))
  }, [period])

  function getVal(storeCode: string, name: string): number {
    return expenses[storeCode]?.[name] || 0
  }

  function setVal(storeCode: string, name: string, val: number) {
    setExpenses(e => ({
      ...e,
      [storeCode]: { ...e[storeCode], [name]: val },
    }))
  }

  async function save() {
    setSaving(true)
    try {
      // Delete existing for period
      await supabase.from('commcalc_store_expenses').delete().eq('period', period)
      // Insert all non-zero values
      const rows: any[] = []
      stores.forEach(s => {
        EXPENSE_CATS.forEach(cat => {
          const amt = getVal(s.store_code, cat.name)
          if (amt > 0) {
            rows.push({
              org_id: ORG_ID,
              period,
              store_code: s.store_code,
              expense_name: cat.name,
              expense_type: cat.type,
              amount: amt,
            })
          }
        })
      })
      if (rows.length > 0) {
        await supabase.from('commcalc_store_expenses').insert(rows)
      }
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (e: any) { alert(e.message) }
    setSaving(false)
  }

  function storeTotal(storeCode: string): number {
    return EXPENSE_CATS.reduce((s, cat) => s + getVal(storeCode, cat.name), 0)
  }

  const grandTotal = stores.reduce((s, store) => s + storeTotal(store.store_code), 0)

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Store Expenses</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · Total: <strong>{fmt(grandTotal)}</strong>
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {saved && <span style={{ color: 'var(--green)', fontSize: 13 }}>✅ Saved</span>}
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? '...' : '💾 Save All'}
          </button>
        </div>
      </div>

      <div style={{ background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 10, padding: '10px 16px', marginBottom: 16, fontSize: 13, color: '#1d4ed8' }}>
        💡 Enter monthly amounts. Leave blank for $0. After saving, run Calculation to include expenses in Gross Profit report.
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : stores.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
          No stores found. Add stores in StoreOps first.
        </div>
      ) : (
        <div style={{ overflowX: 'auto', background: 'white', border: '1px solid var(--border)', borderRadius: 12 }}>
          <table style={{ minWidth: stores.length * 160 + 200, borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--accent)' }}>
                <th style={{ padding: '10px 16px', color: 'white', fontSize: 12, textAlign: 'left', position: 'sticky', left: 0, background: 'var(--accent)', width: 200 }}>
                  Expense
                </th>
                {stores.map(s => (
                  <th key={s.store_code} style={{ padding: '8px 10px', color: 'white', fontSize: 11, textAlign: 'right', minWidth: 140 }}>
                    <div style={{ fontWeight: 700 }}>{s.store_code}</div>
                    <div style={{ fontWeight: 400, opacity: 0.7, fontSize: 10 }}>{s.address?.substring(0, 20)}</div>
                  </th>
                ))}
                <th style={{ padding: '10px 14px', color: 'white', fontSize: 12, textAlign: 'right', width: 120 }}>Total</th>
              </tr>
            </thead>
            <tbody>
              {EXPENSE_CATS.map((cat, ci) => {
                const rowTotal = stores.reduce((s, store) => s + getVal(store.store_code, cat.name), 0)
                return (
                  <tr key={cat.name} style={{ background: ci % 2 === 1 ? '#fafbfc' : 'white' }}>
                    <td style={{ padding: '6px 16px', fontSize: 13, fontWeight: 500, position: 'sticky', left: 0, background: ci % 2 === 1 ? '#fafbfc' : 'white', borderBottom: '1px solid var(--border)' }}>
                      {cat.name}
                      <span style={{ marginLeft: 6, fontSize: 10, color: cat.type === 'Fixed' ? '#2563eb' : '#16a34a', background: cat.type === 'Fixed' ? '#dbeafe' : '#dcfce7', padding: '1px 5px', borderRadius: 999 }}>
                        {cat.type}
                      </span>
                    </td>
                    {stores.map(s => (
                      <td key={s.store_code} style={{ padding: '4px 8px', borderBottom: '1px solid var(--border)' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                          <span style={{ color: 'var(--text3)', fontSize: 13 }}>$</span>
                          <input
                            type="number"
                            min="0"
                            step="0.01"
                            value={getVal(s.store_code, cat.name) || ''}
                            placeholder="0"
                            onChange={e => setVal(s.store_code, cat.name, parseFloat(e.target.value) || 0)}
                            style={{
                              width: '100%', border: '1px solid transparent', borderRadius: 4,
                              padding: '4px 6px', fontSize: 13, textAlign: 'right', background: 'transparent',
                              outline: 'none',
                            }}
                            onFocus={e => (e.target.style.border = '1px solid var(--accent2)')}
                            onBlur={e => (e.target.style.border = '1px solid transparent')}
                          />
                        </div>
                      </td>
                    ))}
                    <td style={{ padding: '6px 14px', textAlign: 'right', fontWeight: 600, fontSize: 13, borderBottom: '1px solid var(--border)', color: rowTotal > 0 ? 'var(--text)' : 'var(--text3)' }}>
                      {rowTotal > 0 ? fmt(rowTotal) : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
            <tfoot>
              <tr style={{ background: 'var(--accent)', fontWeight: 700 }}>
                <td style={{ padding: '10px 16px', color: 'white', fontSize: 13, position: 'sticky', left: 0, background: 'var(--accent)' }}>Total</td>
                {stores.map(s => (
                  <td key={s.store_code} style={{ padding: '10px 10px', textAlign: 'right', color: 'white', fontSize: 13 }}>
                    {fmt(storeTotal(s.store_code))}
                  </td>
                ))}
                <td style={{ padding: '10px 14px', textAlign: 'right', color: 'white', fontSize: 13 }}>
                  {fmt(grandTotal)}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </div>
  )
}
