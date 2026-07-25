'use client'
import { useEffect, useState, useCallback } from 'react'
import { api } from '@/lib/client'

// Catalog Categories — the user-editable category layer on top of the uploaded product catalog
// (migs 230/231). The loaded catalog file's Category is the DEFAULT; here a user recategorizes individual
// products (per-tenant OVERRIDES) WITHOUT losing the file. Categories chosen from the existing set
// (pick-don't-type, RULE THREE) with an explicit "create new". Products whose (override-or-file) category
// is in the tenant's accessory set classify as accessory sales (see Sales Report → Classification settings
// → "Use the product catalog to classify accessories"). Gated on the Classification permission (read-open).

const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

// ③ (Gate-1 follow-up 2026-07-25) — DE-DUPE case-variant category options.
// Effective/override categories are stored LOWERCASED while the catalog file's Category keeps its own
// casing, so 'Accessories' (file) and 'accessories' (override) used to render as two separate options in
// the same <select> — they look like different categories but classify identically. One option per
// case-folded category; the display spelling prefers `prefer` (the row's FILE spelling — the one the
// tenant recognizes), then any mixed-case spelling over an all-lowercase one.
function dedupeCats(values: (string | null | undefined)[], prefer?: string): string[] {
  const out = new Map<string, string>()
  const preferred = (prefer || '').trim()
  for (const raw of values) {
    const v = (raw || '').trim()
    if (!v) continue
    const k = v.toLowerCase()
    const cur = out.get(k)
    if (cur === undefined) { out.set(k, v); continue }
    if (preferred && v === preferred) out.set(k, v)
    else if (cur === cur.toLowerCase() && v !== v.toLowerCase() && cur !== preferred) out.set(k, v)
  }
  return Array.from(out.values()).sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()))
}

type Row = {
  product_id: number | null; product_desc: string; sku: string; upc: string; department: string
  file_category: string; effective_category: string; overridden: boolean; is_accessory: boolean
  cost: number | null; retail_price: number | null
}

export default function CatalogCategoriesPage() {
  const [rows, setRows] = useState<Row[]>([])
  const [cats, setCats] = useState<string[]>([])
  const [accCats, setAccCats] = useState<string[]>([])
  const [enabled, setEnabled] = useState(false)
  const [canEdit, setCanEdit] = useState(true)
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [hint, setHint] = useState('')
  // filters (RULE FIVE — the ones that apply to a catalog: category + free-text + only-overridden)
  const [fCat, setFCat] = useState('')
  const [search, setSearch] = useState('')        // what the user is typing
  const [searchQ, setSearchQ] = useState('')      // ③ debounced value — the only one that triggers a fetch
  const [onlyOv, setOnlyOv] = useState(false)
  // per-row "create new category" text
  const [newCat, setNewCat] = useState<Record<string, string>>({})

  // ③ (Gate-1 follow-up 2026-07-25) — DEBOUNCE the search box. `search` was a direct dependency of the
  // loader, so every keystroke fired a full /commcalc/catalog?limit=1000 request (a multi-thousand-row
  // catalog scan, server-side filtered). 350ms of quiet before we query.
  useEffect(() => {
    const t = setTimeout(() => setSearchQ(search.trim()), 350)
    return () => clearTimeout(t)
  }, [search])

  const load = useCallback(async () => {
    setLoading(true); setMsg('')
    try {
      const qs = new URLSearchParams()
      if (fCat) qs.set('category', fCat)
      if (searchQ) qs.set('search', searchQ)
      if (onlyOv) qs.set('only_overridden', 'true')
      qs.set('limit', '1000')
      const d = await api(`/api/v1/commcalc/catalog?${qs.toString()}`)
      if (d?.ok === false) { setHint(d?.hint || 'Catalog unavailable.'); setRows([]) }
      else {
        setRows(d?.rows || []); setCats(dedupeCats(d?.categories || []))
        setAccCats(d?.accessory_categories || []); setEnabled(!!d?.catalog_classify_enabled)
        setCanEdit(d?.can_edit !== false); setHint('')
      }
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setLoading(false)
  }, [fCat, searchQ, onlyOv])
  useEffect(() => { load() }, [load])

  // rowKey — UPC preferred, else SKU, else product_id, else product_desc (matches the override precedence)
  function rowKey(r: Row): { match_type: string; match_value: string } {
    if (r.upc) return { match_type: 'upc', match_value: r.upc }
    if (r.sku) return { match_type: 'sku', match_value: r.sku }
    if (r.product_id != null) return { match_type: 'product_id', match_value: String(r.product_id) }
    return { match_type: 'product_desc', match_value: r.product_desc }
  }

  async function setCategory(r: Row, category: string) {
    const k = rowKey(r)
    setMsg('Saving…')
    try {
      await api('/api/v1/commcalc/catalog/override', { method: 'PUT', body: JSON.stringify({ ...k, category }) })
      setMsg('✅ Saved.'); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  const idKey = (r: Row) => `${r.upc || ''}|${r.sku || ''}|${r.product_id ?? ''}|${r.product_desc}`
  const accSet = new Set(accCats.map(c => c.toLowerCase()))

  return (
    <div style={{ padding: 24, maxWidth: 1200, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>🗂️ Catalog Categories</h1>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>
            The uploaded catalog is the default; recategorize items here without re-uploading. Products in an accessory category feed accessory sales &amp; commission.
          </div>
        </div>
        <a href="/commcalc/upload" style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent)' }}>Upload / re-upload catalog →</a>
      </div>

      {!enabled && (
        <div style={{ background: '#fffbeb', border: '1px solid #fde047', borderRadius: 8, padding: '8px 12px', fontSize: 12, color: '#92400e', margin: '12px 0' }}>
          Catalog-driven accessory classification is <b>OFF</b>. Turn it on in <a href="/commcalc/sales-report" style={{ color: 'var(--accent)' }}>Sales Report → ⚙️ Classification settings</a> for these categories to affect accessory sales/pay. You can still recategorize here.
        </div>
      )}
      {!canEdit && (
        <div style={{ background: '#fef9c3', border: '1px solid #fde047', borderRadius: 8, padding: '8px 12px', fontSize: 12, color: '#92400e', margin: '12px 0' }}>
          🔒 Read-only — editing requires the <b>Classification settings</b> permission.
        </div>
      )}
      {hint && <div style={{ background: '#fee2e2', border: '1px solid #fecaca', borderRadius: 8, padding: '8px 12px', fontSize: 12, color: '#991b1b', margin: '12px 0' }}>{hint}</div>}

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', margin: '14px 0' }}>
        <input style={{ ...sel, width: 240 }} placeholder="Search product / SKU / UPC / department…" value={search} onChange={e => setSearch(e.target.value)} />
        <select style={sel} value={fCat} onChange={e => setFCat(e.target.value)}>
          <option value="">All categories</option>
          {cats.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13 }}>
          <input type="checkbox" checked={onlyOv} onChange={e => setOnlyOv(e.target.checked)} /> Only recategorized
        </label>
        <span style={{ fontSize: 12, color: msg.startsWith('❌') ? '#dc2626' : 'var(--text3)' }}>{msg || (search.trim() !== searchQ ? 'typing…' : '')}</span>
      </div>

      {loading ? <div style={{ padding: 30, textAlign: 'center', color: 'var(--text3)' }}>Loading…</div> : (
        <div className="table-wrapper">
          <table style={{ borderCollapse: 'collapse', width: '100%' }}>
            <thead>
              <tr style={{ fontSize: 11, color: 'var(--text3)', textAlign: 'left' }}>
                <th style={{ padding: '5px 8px' }}>Product</th>
                <th style={{ padding: '5px 8px' }}>SKU / UPC</th>
                <th style={{ padding: '5px 8px' }}>Department</th>
                <th style={{ padding: '5px 8px' }}>File category</th>
                <th style={{ padding: '5px 8px' }}>Effective category</th>
                <th style={{ padding: '5px 8px', textAlign: 'right' }}>Cost</th>
                <th style={{ padding: '5px 8px' }}>Accessory?</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td colSpan={7} style={{ padding: 20, textAlign: 'center', color: 'var(--text3)' }}>No catalog rows{searchQ || fCat || onlyOv ? ' match the filter' : ' — upload a product catalog first'}.</td></tr>
              ) : rows.map(r => {
                const k = idKey(r)
                // ③ case-insensitive de-dupe: `cats` + the row's own file/effective category used to be
                // Set-de-duped by EXACT string, so a row whose file category is 'Accessories' and whose
                // (lowercased) effective category is 'accessories' offered both.
                const opts = dedupeCats([...cats, r.file_category, r.effective_category], r.file_category)
                const curOpt = opts.find(c => c.toLowerCase() === (r.effective_category || '').trim().toLowerCase()) || ''
                return (
                  <tr key={k} style={{ fontSize: 12, borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '5px 8px', maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.product_desc}>{r.product_desc || '—'}</td>
                    <td style={{ padding: '5px 8px', color: 'var(--text3)' }}>{r.sku || r.upc || '—'}</td>
                    <td style={{ padding: '5px 8px' }}>{r.department || '—'}</td>
                    <td style={{ padding: '5px 8px', color: 'var(--text3)' }}>{r.file_category || '—'}</td>
                    <td style={{ padding: '5px 8px' }}>
                      <select style={{ ...sel, minWidth: 150 }} disabled={!canEdit}
                        value={curOpt}
                        onChange={e => { if (e.target.value === '__new__') return; setCategory(r, e.target.value) }}>
                        {opts.map(c => <option key={c} value={c}>{c}{c.toLowerCase() === (r.file_category || '').trim().toLowerCase() ? ' (file)' : ''}</option>)}
                        <option value="__new__">➕ Create new…</option>
                      </select>
                      {r.overridden && <span style={{ marginLeft: 6, fontSize: 10, color: '#2563eb', fontWeight: 700 }} title={`file: ${r.file_category || '—'}`}>overridden</span>}
                      <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
                        <input style={{ ...sel, fontSize: 11, padding: '2px 6px', width: 120 }} placeholder="new category…" disabled={!canEdit}
                          value={newCat[k] || ''} onChange={e => setNewCat(s => ({ ...s, [k]: e.target.value }))}
                          onKeyDown={e => { if (e.key === 'Enter' && (newCat[k] || '').trim()) setCategory(r, (newCat[k] || '').trim()) }} />
                        {r.overridden && <button className="btn btn-secondary" style={{ fontSize: 10, padding: '1px 6px' }} disabled={!canEdit} onClick={() => setCategory(r, '')}>reset</button>}
                      </div>
                    </td>
                    <td style={{ padding: '5px 8px', textAlign: 'right' }}>{r.cost != null ? `$${Number(r.cost).toFixed(2)}` : '—'}</td>
                    <td style={{ padding: '5px 8px' }}>{accSet.has((r.effective_category || '').toLowerCase()) ? <span style={{ color: 'var(--green)', fontWeight: 700 }}>✓ accessory</span> : <span style={{ color: 'var(--text3)' }}>—</span>}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
