'use client'
// Supply → Vendors: set up each supply vendor (index §36, mig 1021).
//
// The owner's rule: "when setting up the vendor module the tenant needs to define [the free-shipping
// threshold] and the approx delivery time for the vendor" — a portal vendor cannot be saved without both
// (the backend refuses it too). The vendor IS the Purchase Orders vendor roster row (commcalc.po_vendor);
// its portal login is saved through the platform's one login store (the password is write-only and never
// comes back). Portal behaviour (catalog pages, selectors, the optional ordering recipe) is the vendor's
// portal config — a vendors.json block from the price-compare kit — never code.
import { useCallback, useEffect, useState } from 'react'
import { api, apiUpload } from '@/lib/client'
import LiveVendorWindow from '@/components/supply/LiveVendorWindow'
import { panel, input, label, btn, btnPrimary, th, cell, fmtMoney, fmtDate, type Vendor } from '@/lib/supply'

const BLANK = {
  id: '', name: '', contact_name: '', email: '', phone: '', terms: '', notes: '', is_price_source: true,
  portal_url: '', catalog_urls: '', free_shipping_threshold: '', shipping_fee_below_threshold: '',
  delivery_days_min: '', delivery_days_max: '', portal_config: '{}',
}

export default function SupplyVendorsPage() {
  const [rows, setRows] = useState<Vendor[]>([])
  const [note, setNote] = useState('')
  const [form, setForm] = useState<any>(BLANK)
  const [login, setLogin] = useState<any>({ username: '', password: '', enabled: false, frequency: 'daily', hour: 6 })
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [live, setLive] = useState<{ sid: string; title: string } | null>(null)
  const [uploadVendor, setUploadVendor] = useState('')
  const [uploadMsg, setUploadMsg] = useState('')

  const load = useCallback(async () => {
    try {
      const r: any = await api('/api/v1/supply/vendors')
      setRows(r.rows || []); setNote(r.migrated === false ? r.note : '')
    } catch (e: any) { setNote(e?.message || String(e)) }
  }, [])
  useEffect(() => { load() }, [load])

  function edit(v: Vendor) {
    setMsg('')
    setForm({
      id: v.id, name: v.name || '', contact_name: v.contact_name || '', email: v.email || '', phone: v.phone || '',
      terms: v.terms || '', notes: v.notes || '', is_price_source: !!v.is_price_source, portal_url: v.portal_url || '',
      catalog_urls: (v.catalog_urls || []).join('\n'),
      free_shipping_threshold: v.free_shipping_threshold ?? '', shipping_fee_below_threshold: v.shipping_fee_below_threshold ?? '',
      delivery_days_min: v.delivery_days_min ?? '', delivery_days_max: v.delivery_days_max ?? '',
      portal_config: JSON.stringify(v.portal_config || {}, null, 2),
    })
    setLogin({ username: v.login?.username || '', password: '', enabled: !!v.login?.enabled,
               frequency: v.login?.frequency || 'daily', hour: v.login?.hour ?? 6 })
  }

  async function saveVendor() {
    setBusy(true); setMsg('')
    let cfg: any = {}
    try { cfg = form.portal_config.trim() ? JSON.parse(form.portal_config) : {} }
    catch { setMsg('❌ The portal config is not valid JSON.'); setBusy(false); return }
    const body: any = {
      name: form.name, contact_name: form.contact_name, email: form.email, phone: form.phone, terms: form.terms,
      notes: form.notes, is_price_source: form.is_price_source, portal_url: form.portal_url,
      catalog_urls: form.catalog_urls.split(/\s+/).filter(Boolean), portal_config: cfg,
      free_shipping_threshold: form.free_shipping_threshold, shipping_fee_below_threshold: form.shipping_fee_below_threshold,
      delivery_days_min: form.delivery_days_min, delivery_days_max: form.delivery_days_max,
    }
    try {
      if (form.id) await api(`/api/v1/supply/vendors/${form.id}`, { method: 'PATCH', body: JSON.stringify(body) })
      else {
        const r: any = await api('/api/v1/supply/vendors', { method: 'POST', body: JSON.stringify(body) })
        setForm((f: any) => ({ ...f, id: r.vendor?.id || '' }))
      }
      setMsg('✅ Vendor saved.'); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  async function saveLogin() {
    if (!form.id) { setMsg('Save the vendor first.'); return }
    setBusy(true); setMsg('')
    const body: any = { username: login.username, enabled: login.enabled, frequency: login.frequency, hour: Number(login.hour) }
    if (login.password) body.password = login.password
    try {
      await api(`/api/v1/supply/vendors/${form.id}/login`, { method: 'PUT', body: JSON.stringify(body) })
      setLogin((l: any) => ({ ...l, password: '' })); setMsg('✅ Login saved (the password is stored with the other portal logins and never shown again).'); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  async function readCatalog(v: Vendor) {
    setMsg('')
    try {
      const r: any = await api(`/api/v1/supply/vendors/${v.id}/catalog/read`, { method: 'POST', body: '{}' })
      if (r.blocked || r.requires_confirm || r.phase === 'route_disabled') { setMsg('⚠️ ' + (r.message || r.block_reason || 'The portal login is paused.')); return }
      setLive({ sid: r.sid, title: `Reading ${v.name}'s catalog` })
    } catch (e: any) {
      setMsg('❌ ' + (e?.status === 503
        ? 'This server cannot open a browser (the vendor portal read runs on the browser worker, which is not configured here). Upload the price-compare kit\'s products file instead.'
        : (e?.message || e)))
    }
  }

  async function upload(ev: React.ChangeEvent<HTMLInputElement>) {
    const f = ev.target.files?.[0]
    if (!f) return
    setUploadMsg('Uploading…')
    const fd = new FormData()
    fd.append('file', f)
    if (uploadVendor) fd.append('vendor_id', uploadVendor)
    try {
      const r: any = await apiUpload('/api/v1/supply/catalog/upload', fd)
      const names = Object.fromEntries(rows.map(v => [v.id, v.name]))
      const parts = (r.landed || []).map((l: any) => `${names[l.vendor_id] || l.vendor_id}: ${l.rows_ingested} priced${l.rejected ? `, ${l.rejected} skipped` : ''}`)
      const un = (r.unmapped || []).map((u: any) => `${u.vendor_key} (${u.rows} rows)`)
      setUploadMsg((parts.length ? '✅ ' + parts.join(' · ') : '⚠️ Nothing landed.') + (un.length ? ` — not matched to a vendor: ${un.join(', ')}. ${r.note || ''}` : ''))
      load()
    } catch (e: any) { setUploadMsg('❌ ' + (e?.message || e)) }
    ev.target.value = ''
  }

  const f = (k: string) => (e: any) => setForm((p: any) => ({ ...p, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }))
  const current = rows.find(r => r.id === form.id)

  return (
    <div style={{ padding: 20, maxWidth: 1200 }}>
      <h1 style={{ fontSize: 20, fontWeight: 700, margin: '0 0 4px' }}>Supply vendors</h1>
      <p style={{ fontSize: 13, color: 'var(--text2)', margin: '0 0 14px' }}>
        Each vendor you order supplies from. For a vendor with an online portal, set its free-shipping threshold and
        approximate delivery time — the cart uses them to pick the cheapest way to order.
      </p>
      {note && <div style={{ ...panel, borderColor: '#f39c12', marginBottom: 12, fontSize: 13 }}>{note}</div>}

      <section style={{ ...panel, marginBottom: 14, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr>
            <th style={th}>Vendor</th><th style={th}>Free shipping at</th><th style={th}>Fee below</th><th style={th}>Delivery</th>
            <th style={th}>Login</th><th style={th}>Prices read</th><th style={th}>Ordering</th><th style={th}>Needs attention</th><th style={th}></th>
          </tr></thead>
          <tbody>
            {rows.map(v => (
              <tr key={v.id} style={{ opacity: v.is_active ? 1 : 0.5 }}>
                <td style={cell}><button style={{ ...btn, border: 'none', padding: 0, background: 'none', color: '#2563eb' }} onClick={() => edit(v)}>{v.name}</button>
                  {!v.is_price_source && <div style={{ fontSize: 11, color: 'var(--text2)' }}>roster only</div>}</td>
                <td style={cell}>{v.free_shipping_threshold == null ? '—' : fmtMoney(v.free_shipping_threshold)}</td>
                <td style={cell}>{v.shipping_fee_below_threshold == null ? '—' : fmtMoney(v.shipping_fee_below_threshold)}</td>
                <td style={cell}>{v.delivery_days_max == null ? '—' : `${v.delivery_days_min ?? v.delivery_days_max}–${v.delivery_days_max} days`}</td>
                <td style={cell}>{v.login ? (v.login.has_password ? (v.login.auth_status || 'saved') : 'no password') : '—'}</td>
                <td style={cell}>{fmtDate(v.catalog_seen_at)}</td>
                <td style={{ ...cell, fontSize: 11 }}>{v.recipe?.level || '—'}</td>
                <td style={{ ...cell, fontSize: 11 }}>
                  {(v.attention || []).filter(a => a.severity === 'warn').map(a => <div key={a.code} style={{ color: '#b45309' }}>• {a.text}</div>)}
                </td>
                <td style={cell}>{v.is_price_source && v.data_source_id && <button style={btn} onClick={() => readCatalog(v)}>Read prices now</button>}</td>
              </tr>
            ))}
            {!rows.length && <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={9}>No vendors yet — add the first one below.</td></tr>}
          </tbody>
        </table>
      </section>

      {live && <LiveVendorWindow sid={live.sid} title={live.title} onClose={() => { setLive(null); load() }} />}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: 14, marginTop: 14 }}>
        <section style={panel}>
          <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 10px' }}>{form.id ? `Edit ${form.name}` : 'Add a vendor'}</h2>
          <label style={label}>Name</label><input style={input} value={form.name} onChange={f('name')} />
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <div style={{ flex: 1 }}><label style={label}>Contact</label><input style={input} value={form.contact_name} onChange={f('contact_name')} /></div>
            <div style={{ flex: 1 }}><label style={label}>Phone</label><input style={input} value={form.phone} onChange={f('phone')} /></div>
          </div>
          <label style={{ ...label, marginTop: 8 }}>Email</label><input style={input} value={form.email} onChange={f('email')} />
          <label style={{ ...label, marginTop: 10, display: 'flex', gap: 6, alignItems: 'center' }}>
            <input type="checkbox" checked={form.is_price_source} onChange={f('is_price_source')} /> This vendor has an online portal (read prices / order through it)
          </label>
          {form.is_price_source && <>
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <div style={{ flex: 1 }}><label style={label}>Free shipping when the order is at least ($) *</label>
                <input style={input} inputMode="decimal" value={form.free_shipping_threshold} onChange={f('free_shipping_threshold')} placeholder="0 = always free" /></div>
              <div style={{ flex: 1 }}><label style={label}>Shipping fee below that ($, estimate)</label>
                <input style={input} inputMode="decimal" value={form.shipping_fee_below_threshold} onChange={f('shipping_fee_below_threshold')} /></div>
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <div style={{ flex: 1 }}><label style={label}>Delivery — fastest (days)</label>
                <input style={input} inputMode="numeric" value={form.delivery_days_min} onChange={f('delivery_days_min')} /></div>
              <div style={{ flex: 1 }}><label style={label}>Delivery — slowest (days) *</label>
                <input style={input} inputMode="numeric" value={form.delivery_days_max} onChange={f('delivery_days_max')} /></div>
            </div>
            <label style={{ ...label, marginTop: 8 }}>Portal login page URL *</label>
            <input style={input} value={form.portal_url} onChange={f('portal_url')} placeholder="https://…" />
            <label style={{ ...label, marginTop: 8 }}>Catalog links (one per line — where the price read starts)</label>
            <textarea style={{ ...input, minHeight: 60, fontFamily: 'monospace', fontSize: 12 }} value={form.catalog_urls} onChange={f('catalog_urls')} />
            <label style={{ ...label, marginTop: 8 }}>Portal config (JSON — a vendors.json block from the price-compare kit, plus an optional &quot;ordering&quot; recipe)</label>
            <textarea style={{ ...input, minHeight: 120, fontFamily: 'monospace', fontSize: 12 }} value={form.portal_config} onChange={f('portal_config')} />
            {current?.recipe && <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 4 }}>Ordering: {current.recipe.text}</div>}
            {(current?.recipe_errors || []).map(e => <div key={e} style={{ fontSize: 12, color: '#dc2626' }}>{e}</div>)}
          </>}
          <label style={{ ...label, marginTop: 8 }}>Terms / notes</label>
          <input style={input} value={form.terms} onChange={f('terms')} placeholder="Net 30…" />
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <button style={btnPrimary} disabled={busy || !form.name.trim()} onClick={saveVendor}>Save vendor</button>
            {form.id && <button style={btn} onClick={() => { setForm(BLANK); setMsg('') }}>New vendor</button>}
          </div>
          {msg && <div style={{ fontSize: 13, marginTop: 8 }}>{msg}</div>}
        </section>

        <div>
          {form.id && form.is_price_source && (
            <section style={{ ...panel, marginBottom: 14 }}>
              <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 10px' }}>Portal login — {form.name}</h2>
              <label style={label}>User id / email</label>
              <input style={input} value={login.username} onChange={e => setLogin((l: any) => ({ ...l, username: e.target.value }))} autoComplete="off" />
              <label style={{ ...label, marginTop: 8 }}>Password</label>
              <input style={input} type="password" value={login.password} autoComplete="new-password"
                     placeholder={current?.login?.has_password ? 'saved — leave blank to keep it' : ''}
                     onChange={e => setLogin((l: any) => ({ ...l, password: e.target.value }))} />
              <label style={{ ...label, marginTop: 10, display: 'flex', gap: 6, alignItems: 'center' }}>
                <input type="checkbox" checked={login.enabled} onChange={e => setLogin((l: any) => ({ ...l, enabled: e.target.checked }))} />
                Read prices automatically
              </label>
              {login.enabled && <div style={{ display: 'flex', gap: 8 }}>
                <select style={input} value={login.frequency} onChange={e => setLogin((l: any) => ({ ...l, frequency: e.target.value }))}>
                  <option value="daily">Daily</option><option value="weekly">Weekly</option>
                </select>
                <input style={input} type="number" min={0} max={23} value={login.hour} onChange={e => setLogin((l: any) => ({ ...l, hour: e.target.value }))} />
              </div>}
              <button style={{ ...btnPrimary, marginTop: 10 }} disabled={busy || !login.username} onClick={saveLogin}>Save login</button>
              {current?.login?.auth_message && <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 6 }}>Last sign-in: {current.login.auth_message}</div>}
            </section>
          )}
          <section style={panel}>
            <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 6px' }}>Upload prices from the price-compare kit</h2>
            <p style={{ fontSize: 12, color: 'var(--text2)', margin: '0 0 8px' }}>
              The kit writes <code>products.json</code> and <code>products.csv</code>. Each row&apos;s vendor key is matched to the vendor
              whose portal config has that <code>&quot;key&quot;</code> (or the same name) — or pick one vendor for the whole file.
            </p>
            <select style={{ ...input, marginBottom: 8 }} value={uploadVendor} onChange={e => setUploadVendor(e.target.value)}>
              <option value="">Match each row by its vendor key</option>
              {rows.filter(v => v.is_active).map(v => <option key={v.id} value={v.id}>All rows → {v.name}</option>)}
            </select>
            <input type="file" accept=".json,.csv" onChange={upload} />
            {uploadMsg && <div style={{ fontSize: 13, marginTop: 8 }}>{uploadMsg}</div>}
          </section>
        </div>
      </div>
    </div>
  )
}
