'use client'
// New Referral — capture the REFERRING party + the (optional) commission terms, then hand them the QR.
// The referred customer's name/phone/products are captured LATER at the store when the QR is scanned
// (the public /r/[token] page), so all this form needs up front is a way to reach the referrer.
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { api } from '@/lib/client'
import { panel, input, label, btn, btnPrimary, fmtPhone, PRODUCTS, type ReferralConfig } from '@/lib/referral'

export default function NewReferralPage() {
  const router = useRouter()
  const [cfg, setCfg] = useState<ReferralConfig | null>(null)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [f, setF] = useState({
    referrer_name: '', referrer_phone: '', referrer_email: '',
    commission_amount: '', payout_date: '', notes: '',
  })
  const [products, setProducts] = useState<string[]>([])

  useEffect(() => { (async () => { try { setCfg(await api('/api/v1/referral/config')) } catch { /* defaults still work */ } })() }, [])

  const set = (k: string, v: any) => setF(p => ({ ...p, [k]: v }))
  const toggle = (p: string) => setProducts(prev => prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p])

  async function save() {
    setSaving(true); setMsg('')
    try {
      const r = await api('/api/v1/referral/referrals', {
        method: 'POST',
        body: JSON.stringify({
          ...f,
          commission_amount: f.commission_amount === '' ? null : Number(f.commission_amount),
          payout_date: f.payout_date || null,
          products,
        }),
      })
      router.push(`/referral/list/${r.referral.id}`)
    } catch (e: any) { setMsg(e?.message || String(e)); setSaving(false) }
  }

  return (
    <div style={{ padding: 20, maxWidth: 720 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>➕ New Referral</h1>
      <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 14 }}>
        Enter the person doing the referring — we generate a QR they show to their friend. The friend's
        details are captured at the counter when the QR is scanned.
      </div>

      {cfg && !cfg.qr_signing_configured && (
        <div style={{ ...panel, borderColor: '#f39c12', marginBottom: 14, fontSize: 13 }}>
          ⚠️ QR signing is not configured on this server yet — the referral saves, but no scannable QR can
          be minted until a download secret is set. Contact your administrator.
        </div>
      )}

      <div style={{ ...panel, display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 12 }}>
        <div style={{ gridColumn: '1 / -1' }}>
          <span style={label}>Referrer phone *</span>
          <input value={f.referrer_phone} onChange={e => set('referrer_phone', e.target.value)}
                 placeholder="(516) 555-0134" inputMode="tel" autoFocus style={{ ...input, fontSize: 16 }} />
          {f.referrer_phone && <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 3 }}>{fmtPhone(f.referrer_phone)}</div>}
        </div>
        <div><span style={label}>Referrer name</span><input value={f.referrer_name} onChange={e => set('referrer_name', e.target.value)} style={input} /></div>
        <div><span style={label}>Referrer email</span><input value={f.referrer_email} onChange={e => set('referrer_email', e.target.value)} type="email" style={input} /></div>

        <div><span style={label}>Commission amount ($)</span>
          <input value={f.commission_amount} onChange={e => set('commission_amount', e.target.value)} inputMode="decimal"
                 placeholder={cfg ? String(cfg.default_commission_amount) : 'default'} style={input} />
          <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 3 }}>Blank = tenant default. Paid only after activation + approval.</div>
        </div>
        <div><span style={label}>Payout date</span>
          <input type="date" value={f.payout_date} onChange={e => set('payout_date', e.target.value)} style={input} />
          <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 3 }}>Blank = approval date + {cfg?.default_payout_offset_days ?? 30} days.</div>
        </div>

        <div style={{ gridColumn: '1 / -1' }}>
          <span style={label}>Product interest (optional — the customer confirms at redemption)</span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {PRODUCTS.map(p => (
              <button key={p} type="button" onClick={() => toggle(p)}
                      style={{ ...btn, borderRadius: 999, ...(products.includes(p) ? { background: '#2563eb', color: '#fff', borderColor: '#2563eb' } : {}) }}>
                {products.includes(p) ? '✓ ' : ''}{p}
              </button>
            ))}
          </div>
        </div>

        <div style={{ gridColumn: '1 / -1' }}>
          <span style={label}>Notes</span>
          <textarea value={f.notes} onChange={e => set('notes', e.target.value)} rows={2} style={{ ...input, resize: 'vertical' }} />
        </div>
      </div>

      {msg && <div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626', marginTop: 12 }}>{msg}</div>}

      <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
        <button onClick={save} disabled={saving || (!f.referrer_phone.trim() && !f.referrer_email.trim())} style={btnPrimary}>
          {saving ? 'Saving…' : 'Create referral'}
        </button>
        <Link href="/referral/list" style={{ ...btn, textDecoration: 'none' }}>Cancel</Link>
      </div>
    </div>
  )
}
