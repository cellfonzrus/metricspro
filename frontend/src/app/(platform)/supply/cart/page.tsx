'use client'
// Supply → Cart (index §36). The cheapest way to buy the cart INCLUDING each vendor's free-shipping
// threshold (the tenant set it on the vendor), with the "add $X more for free shipping" hints, an optional
// delivery-time limit, and stock rules (out of stock is never chosen; stock not stated is flagged).
// "Place order" turns the plan into one purchase order per vendor; the Orders page then opens each
// vendor's live window to build the vendor's cart and capture the confirmation.
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { api } from '@/lib/client'
import { panel, input, btn, btnPrimary, btnDanger, th, cell, fmtMoney, readCart, writeCart,
         AVAIL_LABEL, type CartItem } from '@/lib/supply'

export default function SupplyCartPage() {
  const router = useRouter()
  const [items, setItems] = useState<CartItem[]>([])
  const [maxDays, setMaxDays] = useState('')
  const [allowBackorder, setAllowBackorder] = useState(false)
  const [allowUnknown, setAllowUnknown] = useState(true)
  const [plan, setPlan] = useState<any>(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => { setItems(readCart()) }, [])
  function update(next: CartItem[]) { setItems(next); writeCart(next); setPlan(null) }

  const body = () => JSON.stringify({
    items: items.map(i => ({ key: i.key, label: i.label, qty: i.qty, offer_ids: i.offer_ids, basis: i.basis })),
    max_delivery_days: maxDays ? Number(maxDays) : null, allow_backorder: allowBackorder, allow_unknown: allowUnknown,
  })

  async function optimize() {
    setBusy(true); setMsg('')
    try { const r: any = await api('/api/v1/supply/cart/optimize', { method: 'POST', body: body() }); setPlan(r.plan) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }
  async function place() {
    if (!plan?.vendors?.length) return
    const summary = plan.vendors.map((v: any) => `${v.vendor_name}: ${fmtMoney(v.total)}`).join('\n')
    if (!confirm(`Create these purchase orders?\n\n${summary}\n\nNothing is sent to a vendor yet — you place each order from the Orders page.`)) return
    setBusy(true); setMsg('')
    try {
      const r: any = await api('/api/v1/supply/cart/place', { method: 'POST', body: body() })
      update([])
      router.push(`/supply/orders?cart=${encodeURIComponent(r.cart_ref)}`)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  return (
    <div style={{ padding: 20, maxWidth: 1200 }}>
      <h1 style={{ fontSize: 20, fontWeight: 700, margin: '0 0 4px' }}>Cart</h1>
      <p style={{ fontSize: 13, color: 'var(--text2)', margin: '0 0 12px' }}>
        Add items from <Link href="/supply/compare">Price compare</Link>. The plan picks the cheapest vendor for each item with
        shipping counted — sometimes one vendor for everything is cheaper because it clears the free-shipping line.
      </p>
      <section style={{ ...panel, marginBottom: 14 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr><th style={th}>Item</th><th style={th}>Quantity</th><th style={th}>Counted in</th><th style={th}></th></tr></thead>
          <tbody>
            {items.map((it, i) => (
              <tr key={it.key}>
                <td style={cell}>{it.label}</td>
                <td style={cell}><input style={{ ...input, width: 90 }} inputMode="numeric" value={it.qty}
                  onChange={e => update(items.map((x, j) => j === i ? { ...x, qty: Math.max(0, parseInt(e.target.value || '0', 10) || 0) } : x))} /></td>
                <td style={{ ...cell, fontSize: 12, color: 'var(--text2)' }}>{it.basis === 'units' ? 'units (whole packs ordered)' : "each vendor's packs"}</td>
                <td style={cell}><button style={btnDanger} onClick={() => update(items.filter((_, j) => j !== i))}>Remove</button></td>
              </tr>
            ))}
            {!items.length && <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={4}>The cart is empty.</td></tr>}
          </tbody>
        </table>
        <div style={{ display: 'flex', gap: 14, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
          <label style={{ fontSize: 13 }}>Must arrive within
            <input style={{ ...input, width: 70, marginLeft: 6 }} inputMode="numeric" placeholder="any" value={maxDays} onChange={e => { setMaxDays(e.target.value); setPlan(null) }} /> days
          </label>
          <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
            <input type="checkbox" checked={allowUnknown} onChange={e => { setAllowUnknown(e.target.checked); setPlan(null) }} /> Allow items whose stock is not stated
          </label>
          <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
            <input type="checkbox" checked={allowBackorder} onChange={e => { setAllowBackorder(e.target.checked); setPlan(null) }} /> Allow backorders
          </label>
          <span style={{ flex: 1 }} />
          <button style={btnPrimary} disabled={busy || !items.length} onClick={optimize}>Find the cheapest plan</button>
        </div>
        {msg && <div style={{ fontSize: 13, marginTop: 8 }}>{msg}</div>}
      </section>

      {plan && (
        <section style={panel}>
          <div style={{ display: 'flex', gap: 20, alignItems: 'baseline', flexWrap: 'wrap', marginBottom: 10 }}>
            <div><div style={{ fontSize: 11, color: 'var(--text2)' }}>TOTAL (items + shipping)</div><div style={{ fontSize: 22, fontWeight: 700 }}>{fmtMoney(plan.total)}</div></div>
            <div><div style={{ fontSize: 11, color: 'var(--text2)' }}>Items</div>{fmtMoney(plan.items_total)}</div>
            <div><div style={{ fontSize: 11, color: 'var(--text2)' }}>Shipping</div>{fmtMoney(plan.shipping_total)}</div>
            {plan.delivery_days?.max != null && <div><div style={{ fontSize: 11, color: 'var(--text2)' }}>Arrives in</div>{plan.delivery_days.min ?? '?'}–{plan.delivery_days.max} days</div>}
            {plan.savings_vs_single_vendor > 0 && <div style={{ color: '#16a34a' }}>Saves {fmtMoney(plan.savings_vs_single_vendor)} vs the best single vendor</div>}
            {plan.savings_vs_cheapest_each_item > 0 && <div style={{ color: '#16a34a' }}>Saves {fmtMoney(plan.savings_vs_cheapest_each_item)} vs buying each item wherever it is cheapest</div>}
          </div>
          {(plan.hints || []).map((h: string) => <div key={h} style={{ fontSize: 13, color: '#2563eb', marginBottom: 4 }}>💡 {h}</div>)}
          {(plan.flags || []).map((h: string) => <div key={h} style={{ fontSize: 13, color: '#b45309', marginBottom: 4 }}>⚠️ {h}</div>)}
          {(plan.unfillable || []).map((u: any) => <div key={u.key} style={{ fontSize: 13, color: '#dc2626', marginBottom: 4 }}>✖ {u.label}: {u.reason}</div>)}
          {(plan.vendors || []).map((v: any) => (
            <div key={v.vendor} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 10, marginTop: 10 }}>
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'baseline' }}>
                <strong>{v.vendor_name}</strong>
                <span>Items {fmtMoney(v.subtotal)}</span>
                <span>Shipping {v.shipping_known ? fmtMoney(v.shipping) : 'not estimated'}</span>
                <span style={{ fontSize: 12, color: 'var(--text2)' }}>
                  Free shipping at {v.free_shipping_threshold == null ? '—' : fmtMoney(v.free_shipping_threshold)}
                  {v.to_free_shipping != null ? ` (${fmtMoney(v.to_free_shipping)} to go)` : ''}
                </span>
                {v.delivery_days_max != null && <span style={{ fontSize: 12, color: 'var(--text2)' }}>{v.delivery_days_min ?? '?'}–{v.delivery_days_max} days</span>}
                {v.recipe && <span style={{ fontSize: 11, color: 'var(--text2)' }}>Ordering: {v.recipe.level}</span>}
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginTop: 6 }}>
                <thead><tr><th style={th}>Item</th><th style={th}>Order</th><th style={th}>Price</th><th style={th}>Line</th><th style={th}>Stock</th><th style={th}>Other vendors</th></tr></thead>
                <tbody>
                  {v.lines.map((ln: any) => (
                    <tr key={ln.key}>
                      <td style={cell}>{ln.label || ln.name}{(ln.flags || []).map((f: string) => <div key={f} style={{ fontSize: 11, color: '#b45309' }}>{f}</div>)}</td>
                      <td style={cell}>{ln.packs}{ln.pack_qty ? ` × ${ln.pack_qty}` : ''}</td>
                      <td style={cell}>{fmtMoney(ln.unit_price)}</td>
                      <td style={cell}>{fmtMoney(ln.line_total)}</td>
                      <td style={cell}>{AVAIL_LABEL[ln.availability] || ln.availability}</td>
                      <td style={{ ...cell, fontSize: 11 }}>{(ln.alternatives || []).map((a: any) => `${a.vendor_name} ${fmtMoney(a.line_total)}`).join(' · ') || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
          <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
            <button style={btnPrimary} disabled={busy || !plan.vendors?.length} onClick={place}>Place order ({plan.vendors?.length || 0} vendor{plan.vendors?.length === 1 ? '' : 's'})</button>
            <button style={btn} onClick={() => setPlan(null)}>Change the cart</button>
          </div>
          <p style={{ fontSize: 11, color: 'var(--text2)', marginTop: 6 }}>
            Plan method: {plan.method}. Placing creates one purchase order per vendor; the vendor order itself is placed from the Orders page.
          </p>
        </section>
      )}
    </div>
  )
}
