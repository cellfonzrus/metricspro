'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'

// GP Category Map — assign each POS department to a Gross-Profit category so GP/P&L compute for ANY POS
// taxonomy (not just Boost's). Defaults (when unmapped): device = Android/IPHONE/TABLET-XP (counted at
// sale price), accessory = Ondigo, blank department = plan, everything else = other. Backed by
// commcalc.gp_category_map (migration 069). Overrides only — leaving a department on its default is fine.
// Changing a mapping affects the GP report on its next load (recompute not required; GP reads live).

const CAT_DESC: Record<string, string> = {
  device: 'Phone / device sales — counted at sale price (ext_price)',
  accessory: 'Accessories — counted at gross profit',
  plan: 'Plan / activation GP — counted at gross profit',
  other: 'Everything else — counted at gross profit',
  exclude: 'Dropped from the GP report entirely',
}
const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

export default function GpCategoryMapPage() {
  const [cats, setCats] = useState<string[]>(['device', 'accessory', 'plan', 'other', 'exclude'])
  const [depts, setDepts] = useState<{ department: string; count: number; category: string; mapped: boolean }[]>([])
  const [ready, setReady] = useState(true)
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(true)

  async function load() {
    setLoading(true)
    try {
      const [cfg, dd] = await Promise.all([api('/api/v1/commcalc/gp-category-map'), api('/api/v1/commcalc/gp-departments')])
      setCats(cfg?.categories || cats)
      setReady(cfg?.ready !== false)
      setDepts(dd?.departments || [])
    } catch (e: any) { setMsg(e?.message || 'Load failed') }
    setLoading(false)
  }
  useEffect(() => { load() }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  async function setCat(department: string, category: string) {
    // empty category string = revert to built-in default (DELETE on the backend)
    setDepts(p => p.map(d => d.department === department ? { ...d, category: category || d.category, mapped: !!category } : d))
    try {
      await api('/api/v1/commcalc/gp-category-map', { method: 'POST', body: JSON.stringify({ department, category }) })
      setMsg(category ? `"${department || '(blank)'}" → ${category}` : `"${department || '(blank)'}" reverted to default`)
      load()  // re-pull so the computed default shows after a revert
    } catch (e: any) {
      setMsg(e?.message || 'Save failed — is migration 069_gp_category_map.sql applied?')
    }
    setTimeout(() => setMsg(''), 3500)
  }

  return (
    <div style={{ padding: 24, maxWidth: 880 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>💰 GP Category Map</h1>
      <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 6 }}>
        Map each POS department to a Gross-Profit category so GP & P&L compute for your store taxonomy.
        Leave a department on its <b>default</b> to keep the built-in behavior.
      </p>
      {!ready && (
        <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>
          Run migration <code>069_gp_category_map.sql</code> to save overrides. Until then the report uses the built-in defaults.
        </div>
      )}
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>{msg}</div>}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
        {cats.map(c => (
          <span key={c} title={CAT_DESC[c] || ''} style={{ fontSize: 11, padding: '3px 8px', borderRadius: 12, background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text2)' }}>
            <b>{c}</b>{CAT_DESC[c] ? ` · ${CAT_DESC[c]}` : ''}
          </span>
        ))}
      </div>

      {loading ? <div style={{ color: 'var(--text3)' }}>Loading departments…</div> : depts.length === 0 ? (
        <div style={{ color: 'var(--text3)', fontSize: 13 }}>No departments found in raw_sales for this org yet — upload sales first.</div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 11, textTransform: 'uppercase' }}>
              <th style={{ padding: '6px 8px' }}>POS Department</th>
              <th style={{ padding: '6px 8px' }}>Lines</th>
              <th style={{ padding: '6px 8px' }}>GP Category</th>
              <th style={{ padding: '6px 8px' }}></th>
            </tr>
          </thead>
          <tbody>
            {depts.map(d => (
              <tr key={d.department || '(blank)'} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '7px 8px', fontWeight: 600 }}>{d.department || <span style={{ color: 'var(--text3)' }}>(blank department)</span>}</td>
                <td style={{ padding: '7px 8px', color: 'var(--text2)' }}>{d.count.toLocaleString()}</td>
                <td style={{ padding: '7px 8px' }}>
                  <select style={sel} value={d.category} onChange={e => setCat(d.department, e.target.value)}>
                    {cats.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </td>
                <td style={{ padding: '7px 8px' }}>
                  {d.mapped
                    ? <button onClick={() => setCat(d.department, '')} style={{ ...sel, cursor: 'pointer', fontSize: 11 }}>↺ default</button>
                    : <span style={{ color: 'var(--text3)', fontSize: 11 }}>default</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
