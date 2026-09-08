'use client'
import { Fragment, useEffect, useState } from 'react'
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
type CatRow = { value: string; label?: string; rolls_up_to?: string; source?: string }
type ItemRow = {
  item_key: string; product_desc: string; sku: string; lines: number; returns: number
  gp: number; ext_price: number; category: string; bucket: string
  source: 'item' | 'department' | 'default'; mapped: boolean
}

const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

export default function GpCategoryMapPage() {
  const [cats, setCats] = useState<string[]>(['device', 'accessory', 'plan', 'other', 'exclude'])
  const [catRows, setCatRows] = useState<CatRow[]>([])
  const [buckets, setBuckets] = useState<string[]>(['device', 'accessory', 'plan', 'other', 'exclude'])
  const [depts, setDepts] = useState<{ department: string; count: number; category: string; mapped: boolean }[]>([])
  const [ready, setReady] = useState(true)
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(true)
  // Drill-down: department label -> its items. A department the tenant cannot split by its LABEL
  // (the blank one, above all) is split by product instead.
  const [open, setOpen] = useState<string | null>(null)
  const [items, setItems] = useState<Record<string, ItemRow[]>>({})
  const [itemsBusy, setItemsBusy] = useState(false)
  const [adding, setAdding] = useState(false)
  const [newCat, setNewCat] = useState({ label: '', rolls_up_to: 'other' })

  async function load() {
    setLoading(true)
    try {
      const [cfg, dd, ic] = await Promise.all([
        api('/api/v1/commcalc/gp-category-map'),
        api('/api/v1/commcalc/gp-departments'),
        api('/api/v1/commcalc/item-categories').catch(() => null),
      ])
      setReady(cfg?.ready !== false)
      setDepts(dd?.departments || [])
      // The per-org registry is the source of truth for what a category IS; the built-in list is
      // only the fallback for a tenant whose registry has not been seeded yet.
      const rows: CatRow[] = ic?.gp || []
      setCatRows(rows)
      setCats(rows.length ? rows.map((r: CatRow) => r.value) : (cfg?.categories || cats))
      if (ic?.gp_buckets?.length) setBuckets(ic.gp_buckets)
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

  async function toggle(dept: string) {
    const key = dept
    if (open === key) { setOpen(null); return }
    setOpen(key)
    if (items[key]) return           // already pulled — don't refetch on every expand
    setItemsBusy(true)
    try {
      const r = await api('/api/v1/commcalc/gp-department-items?department=' + encodeURIComponent(dept))
      setItems(p => ({ ...p, [key]: r?.items || [] }))
    } catch (e: any) { setMsg(e?.message || 'Could not load items') }
    setItemsBusy(false)
  }

  async function setItemCat(dept: string, it: ItemRow, category: string) {
    setItems(p => ({
      ...p,
      [dept]: (p[dept] || []).map(x => x.item_key === it.item_key
        ? { ...x, category: category || x.category, mapped: !!category, source: category ? 'item' : 'default' }
        : x),
    }))
    try {
      await api('/api/v1/commcalc/gp-item-category', {
        method: 'POST', body: JSON.stringify({ item_key: it.item_key, category }),
      })
      setMsg(category
        ? `"${it.product_desc || it.item_key}" → ${category}`
        : `"${it.product_desc || it.item_key}" reverted to the department rule`)
      const r = await api('/api/v1/commcalc/gp-department-items?department=' + encodeURIComponent(dept))
      setItems(p => ({ ...p, [dept]: r?.items || [] }))
    } catch (e: any) {
      setMsg(e?.message || 'Save failed — is migration 992_gp_item_category.sql applied?')
    }
    setTimeout(() => setMsg(''), 3500)
  }

  async function addCategory() {
    const label = newCat.label.trim()
    if (!label) return
    const value = label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
    if (!value) { setMsg('Give the category a name with at least one letter or digit.'); return }
    try {
      await api('/api/v1/commcalc/item-categories', {
        method: 'PUT',
        body: JSON.stringify({ dimension: 'gp', value, label, rolls_up_to: newCat.rolls_up_to }),
      })
      setMsg(`Added "${label}" — its lines count in ${newCat.rolls_up_to}.`)
      setNewCat({ label: '', rolls_up_to: 'other' })
      setAdding(false)
      setItems({})            // categories changed: item rows must be re-read, not patched
      load()
    } catch (e: any) { setMsg(e?.message || 'Could not add the category') }
    setTimeout(() => setMsg(''), 4000)
  }

  const catLabel = (v: string) => catRows.find(c => c.value === v)?.label || v
  const catBucket = (v: string) => catRows.find(c => c.value === v)?.rolls_up_to || v

  return (
    <div style={{ padding: 24, maxWidth: 980 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>💰 GP Category Map</h1>
      <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 6 }}>
        Map each POS department to a Gross-Profit category so GP &amp; P&amp;L compute for your store taxonomy.
        Leave a department on its <b>default</b> to keep the built-in behavior. <b>Click a department</b>
        to categorise its products one by one — a blank or mixed department carries products that mean
        different things, and an item&rsquo;s own category always beats the department rule.
        Leave a department on its <b>default</b> to keep the built-in behavior.
      </p>
      {!ready && (
        <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>
          Run migration <code>069_gp_category_map.sql</code> to save overrides. Until then the report uses the built-in defaults.
        </div>
      )}
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>{msg}</div>}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 10, alignItems: 'center' }}>
        {cats.map(c => {
          const b = catBucket(c)
          const custom = !CAT_DESC[c]
          return (
            <span key={c} title={CAT_DESC[c] || `Counts in the ${b} bucket`}
              style={{ fontSize: 11, padding: '3px 8px', borderRadius: 12, background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text2)' }}>
              <b>{catLabel(c)}</b>{CAT_DESC[c] ? ` · ${CAT_DESC[c]}` : ` · counts in ${b}`}
              {custom && <span style={{ marginLeft: 5, fontSize: 10, opacity: 0.7 }}>added</span>}
            </span>
          )
        })}
        <button onClick={() => setAdding(a => !a)} style={{ ...sel, cursor: 'pointer', fontSize: 11, fontWeight: 600 }}>
          {adding ? '× Cancel' : '+ Add category'}
        </button>
      </div>

      {adding && (
        <div className="card" style={{ padding: 12, marginBottom: 14, display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>
            <div style={{ marginBottom: 4 }}>Category name</div>
            <input className="input" style={{ fontSize: 13 }} placeholder="e.g. Protection"
              value={newCat.label} onChange={e => setNewCat(c => ({ ...c, label: e.target.value }))} />
          </label>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>
            <div style={{ marginBottom: 4 }}>Its money counts in</div>
            <select style={sel} value={newCat.rolls_up_to} onChange={e => setNewCat(c => ({ ...c, rolls_up_to: e.target.value }))}>
              {buckets.map(b => <option key={b} value={b}>{b}</option>)}
            </select>
          </label>
          <button onClick={addCategory} className="btn btn-primary" style={{ fontSize: 13 }}>Add</button>
          {/* The GP report has exactly these buckets. A category that pointed at none of them would
              have its lines counted into nothing and they would leave the report silently — so the
              bucket is asked for here rather than defaulted behind the reader's back. */}
          <div style={{ fontSize: 11.5, color: 'var(--text3)', flexBasis: '100%', lineHeight: 1.5 }}>
            A new category is a label you can assign to items. It must say which bucket its money
            counts in, so nothing you categorise ever drops out of the GP report.
          </div>
        </div>
      )}

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
            {depts.map(d => {
              const key = d.department
              const rows = items[key] || []
              const isOpen = open === key
              const assigned = rows.filter(r => r.mapped).length
              return (
                <Fragment key={key || '(blank)'}>
                  <tr style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '7px 8px', fontWeight: 600 }}>
                      <button onClick={() => toggle(key)} title="Show the products in this department"
                        style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 11, marginRight: 6, padding: 0, color: 'var(--text2)' }}>
                        {isOpen ? '▾' : '▸'}
                      </button>
                      {d.department || <span style={{ color: 'var(--text3)' }}>(blank department)</span>}
                    </td>
                    <td style={{ padding: '7px 8px', color: 'var(--text2)' }}>{d.count.toLocaleString()}</td>
                    <td style={{ padding: '7px 8px' }}>
                      <select style={sel} value={d.category} onChange={e => setCat(d.department, e.target.value)}>
                        {cats.map(c => <option key={c} value={c}>{catLabel(c)}</option>)}
                      </select>
                    </td>
                    <td style={{ padding: '7px 8px' }}>
                      {d.mapped
                        ? <button onClick={() => setCat(d.department, '')} style={{ ...sel, cursor: 'pointer', fontSize: 11 }}>↺ default</button>
                        : <span style={{ color: 'var(--text3)', fontSize: 11 }}>default</span>}
                    </td>
                  </tr>
                  {isOpen && (
                    <tr>
                      <td colSpan={4} style={{ padding: '4px 8px 14px 26px', background: 'var(--surface2)' }}>
                        {itemsBusy && !rows.length ? (
                          <div style={{ color: 'var(--text3)', fontSize: 12, padding: '8px 0' }}>Loading products…</div>
                        ) : !rows.length ? (
                          <div style={{ color: 'var(--text3)', fontSize: 12, padding: '8px 0' }}>No lines found for this department.</div>
                        ) : (
                          <>
                            <div style={{ fontSize: 11.5, color: 'var(--text2)', margin: '6px 0 8px' }}>
                              <b>{rows.length}</b> product{rows.length === 1 ? '' : 's'} · {assigned} assigned individually.
                              An item's own category always wins over the department rule above.
                            </div>
                            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                              <thead>
                                <tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 10.5, textTransform: 'uppercase' }}>
                                  <th style={{ padding: '4px 6px' }}>Product</th>
                                  <th style={{ padding: '4px 6px', textAlign: 'right' }}>Lines</th>
                                  <th style={{ padding: '4px 6px', textAlign: 'right' }}>GP</th>
                                  <th style={{ padding: '4px 6px' }}>Category</th>
                                  <th style={{ padding: '4px 6px' }}>Source</th>
                                </tr>
                              </thead>
                              <tbody>
                                {rows.map(it => (
                                  <tr key={it.item_key} style={{ borderTop: '1px solid var(--border)' }}>
                                    <td style={{ padding: '5px 6px' }}>
                                      {it.product_desc || <span style={{ color: 'var(--text3)' }}>{it.item_key}</span>}
                                      {it.sku && <span style={{ marginLeft: 6, fontSize: 10, color: 'var(--text3)' }}>{it.sku}</span>}
                                      {it.returns > 0 && <span style={{ marginLeft: 6, fontSize: 10, color: '#92400e', background: '#fef3c7', padding: '1px 5px', borderRadius: 999 }}>{it.returns} return{it.returns === 1 ? '' : 's'}</span>}
                                    </td>
                                    <td style={{ padding: '5px 6px', textAlign: 'right', color: 'var(--text2)' }}>{it.lines.toLocaleString()}</td>
                                    <td style={{ padding: '5px 6px', textAlign: 'right', color: 'var(--text2)' }}>
                                      {it.gp.toLocaleString(undefined, { style: 'currency', currency: 'USD' })}
                                    </td>
                                    <td style={{ padding: '5px 6px' }}>
                                      <select style={{ ...sel, fontSize: 12 }} value={it.category}
                                        onChange={e => setItemCat(key, it, e.target.value)}>
                                        {cats.map(c => <option key={c} value={c}>{catLabel(c)}</option>)}
                                      </select>
                                    </td>
                                    <td style={{ padding: '5px 6px', fontSize: 11, color: 'var(--text3)' }}>
                                      {it.source === 'item'
                                        ? <button onClick={() => setItemCat(key, it, '')} title="Clear this item's category and follow the department rule again"
                                            style={{ ...sel, cursor: 'pointer', fontSize: 10.5, padding: '2px 6px' }}>↺ this item</button>
                                        : it.source === 'department' ? 'department rule' : 'built-in default'}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}
