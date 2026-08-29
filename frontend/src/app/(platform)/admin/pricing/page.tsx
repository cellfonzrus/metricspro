'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, fmt } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

// Pricing & Free Trial (mig 908) — SUPER-ADMIN only. This is where the price the public sees is
// SET. The marketing site (/welcome) renders whatever is published here and carries no price of its
// own, so a change on this page IS the change on the website.
//
// Two safety rules the UI enforces visibly, because a mistake here is public:
//   · A package is invisible until `is_public` is ticked. The seeded packages ship unpublished with
//     price 0, so nothing goes live before the operator types a real number.
//   · Changing the trial length applies to companies signing up FROM NOW ON. It never moves the end
//     date of a trial already running — those move only via Extend below.

type Pkg = {
  key: string; name: string; tagline: string | null; price: number; cycle: string; currency: string
  unit_label: string | null; price_note: string | null; features: string[] | null; cta_label: string | null
  is_featured: boolean; is_public: boolean; sort_order: number; notes: string | null
}
type Settings = {
  trial_enabled: boolean; trial_days: number; currency: string; show_pricing: boolean
  pricing_headline: string | null; pricing_subhead: string | null; trial_note: string | null
}
type Trial = { status: string; days_left: number | null; ends_at: string | null; expired: boolean } | null
type TenantTrial = { org_id: string; name: string; is_active: boolean; created_at: string; package_key: string | null; trial: Trial }

const CYCLES = ['monthly', 'annual']
const BLANK: Pkg = {
  key: '', name: '', tagline: '', price: 0, cycle: 'monthly', currency: 'USD', unit_label: '',
  price_note: '', features: [], cta_label: '', is_featured: false, is_public: false, sort_order: 0, notes: '',
}
const statusLabel: Record<string, string> = {
  trialing: 'On trial', active: 'Subscribed', trial_expired: 'Trial ended', cancelled: 'Cancelled',
}
const statusColor: Record<string, string> = {
  trialing: '#2563eb', active: '#16a34a', trial_expired: '#b45309', cancelled: '#64748b',
}

function msg(e: unknown, fallback: string) {
  return (e instanceof Error && e.message) ? e.message : fallback
}

export default function PricingAdmin() {
  const { user, loading } = useAuth()
  const isSuper = !!user?.super_admin
  const [ready, setReady] = useState(true)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [packages, setPackages] = useState<Pkg[]>([])
  const [tenants, setTenants] = useState<TenantTrial[]>([])
  const [draft, setDraft] = useState<Pkg | null>(null)   // package being edited (null = modal closed)
  const [isNew, setIsNew] = useState(false)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api('/api/v1/billing/pricing')
      .then(d => { setSettings(d.settings); setPackages(d.packages || []); setReady(d.ready !== false) })
      .catch(e => setErr(msg(e, 'Failed to load pricing')))
    api('/api/v1/billing/trials')
      .then(d => setTenants(d.tenants || []))
      .catch(() => {})
  }, [])
  useEffect(() => { if (isSuper) load() }, [isSuper, load])

  async function saveSettings(patch: Partial<Settings>) {
    setBusy(true); setErr(''); setNote('')
    try {
      const d = await api('/api/v1/billing/pricing/settings', { method: 'POST', body: JSON.stringify(patch) })
      setSettings(d.settings); setNote('Saved.')
    } catch (e) { setErr(msg(e, 'Save failed')) } finally { setBusy(false) }
  }

  async function savePackage() {
    if (!draft) return
    setBusy(true); setErr(''); setNote('')
    try {
      await api('/api/v1/billing/pricing/packages', {
        method: 'POST',
        body: JSON.stringify({ ...draft, features: (draft.features || []).join('\n') }),
      })
      setDraft(null); load(); setNote('Package saved.')
    } catch (e) { setErr(msg(e, 'Save failed')) } finally { setBusy(false) }
  }

  async function togglePublish(p: Pkg) {
    const going = !p.is_public
    if (going && !confirm(`Publish “${p.name}” to the public pricing page at ${p.price ? fmt(p.price) : '$0'}?`)) return
    setBusy(true); setErr('')
    try {
      await api('/api/v1/billing/pricing/packages', {
        method: 'POST',
        body: JSON.stringify({ ...p, is_public: going, features: (p.features || []).join('\n') }),
      })
      load()
    } catch (e) { setErr(msg(e, 'Save failed')) } finally { setBusy(false) }
  }

  async function removePackage(key: string) {
    if (!confirm(`Delete the “${key}” package? This removes it from the price list entirely.`)) return
    setBusy(true); setErr('')
    try { await api(`/api/v1/billing/pricing/packages/${encodeURIComponent(key)}`, { method: 'DELETE' }); load() }
    catch (e) { setErr(msg(e, 'Delete failed')) } finally { setBusy(false) }
  }

  async function tenantPlan(org_id: string, body: Record<string, unknown>) {
    setBusy(true); setErr('')
    try { await api('/api/v1/billing/pricing/tenant-plan', { method: 'POST', body: JSON.stringify({ org_id, ...body }) }); load() }
    catch (e) { setErr(msg(e, 'Update failed')) } finally { setBusy(false) }
  }

  if (loading) return <div style={{ padding: 24, color: 'var(--text3)' }}>Loading…</div>
  if (!isSuper) return (
    <div style={{ padding: 24, maxWidth: 720 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700 }}>🏷️ Pricing &amp; Free Trial</h1>
      <div className="card" style={{ marginTop: 12 }}>
        This page sets the prices shown on the public website and the length of the free trial, so it is
        limited to platform super-admins. Your own company&apos;s plan lives under <b>Billing</b>.
      </div>
    </div>
  )

  const inp: React.CSSProperties = { padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 14, width: '100%' }
  const lbl: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }
  const published = packages.filter(p => p.is_public)
  const trialing = tenants.filter(t => t.trial?.status === 'trialing')
  const lapsed = tenants.filter(t => t.trial?.status === 'trial_expired')

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px' }}>🏷️ Pricing &amp; Free Trial</h1>
      <p className="pg-note" style={{ color: 'var(--text3)', fontSize: 13, marginTop: 0 }}>
        The prices published here are what the public website shows — the site has no price of its own.
        Nothing is visible to the public until you tick <b>Published</b>.
      </p>

      {!ready && <div className="card" style={{ borderColor: '#f59e0b', color: '#b45309', padding: 12, marginBottom: 12 }}>
        ⚠️ Migration <code>908_pricing_and_trial.sql</code> hasn&apos;t been applied yet — prices and the trial length
        can&apos;t be saved. Run it in the Supabase SQL editor.</div>}
      {err && <div className="card" style={{ borderColor: '#c0392b', color: '#c0392b', padding: 12, marginBottom: 12 }}>{err}</div>}
      {note && <div className="card" style={{ borderColor: '#16a34a', color: '#16a34a', padding: 12, marginBottom: 12 }}>{note}</div>}

      {/* ── Free trial ───────────────────────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 4px' }}>Free trial</h2>
        <p style={{ fontSize: 12.5, color: 'var(--text3)', margin: '0 0 14px' }}>
          How long a company that signs up gets for free. Changing this applies to <b>new signups only</b> —
          a trial already running keeps its end date unless you extend it below.
        </p>
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, fontWeight: 600 }}>
            <input type="checkbox" checked={!!settings?.trial_enabled} disabled={busy}
              onChange={e => saveSettings({ trial_enabled: e.target.checked })} />
            Offer a free trial
          </label>
          <div style={{ width: 150 }}>
            <label style={lbl} htmlFor="trial-days">Trial length (days)</label>
            <input id="trial-days" style={inp} type="number" min={0} max={365}
              value={settings?.trial_days ?? 30} disabled={busy || !settings?.trial_enabled}
              onChange={e => setSettings(s => s ? { ...s, trial_days: Number(e.target.value) } : s)}
              onBlur={e => saveSettings({ trial_days: Number(e.target.value) })} />
          </div>
          <div style={{ flex: 1, minWidth: 220 }}>
            <label style={lbl} htmlFor="trial-note">Small print on the site</label>
            <input id="trial-note" style={inp} placeholder="No card required." value={settings?.trial_note ?? ''}
              disabled={busy}
              onChange={e => setSettings(s => s ? { ...s, trial_note: e.target.value } : s)}
              onBlur={e => saveSettings({ trial_note: e.target.value })} />
          </div>
        </div>
      </div>

      {/* ── Public pricing section copy ──────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 4px' }}>What the website says</h2>
        <p style={{ fontSize: 12.5, color: 'var(--text3)', margin: '0 0 14px' }}>
          The heading above the price cards. Turning the section off hides pricing from the public page
          entirely — the trial call-to-action stays.
        </p>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
          <input type="checkbox" checked={!!settings?.show_pricing} disabled={busy}
            onChange={e => saveSettings({ show_pricing: e.target.checked })} />
          Show pricing on the public site
        </label>
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <label style={lbl} htmlFor="p-headline">Headline</label>
            <input id="p-headline" style={inp} value={settings?.pricing_headline ?? ''} disabled={busy}
              onChange={e => setSettings(s => s ? { ...s, pricing_headline: e.target.value } : s)}
              onBlur={e => saveSettings({ pricing_headline: e.target.value })} />
          </div>
          <div>
            <label style={lbl} htmlFor="p-subhead">Sub-heading</label>
            <input id="p-subhead" style={inp} value={settings?.pricing_subhead ?? ''} disabled={busy}
              onChange={e => setSettings(s => s ? { ...s, pricing_subhead: e.target.value } : s)}
              onBlur={e => saveSettings({ pricing_subhead: e.target.value })} />
          </div>
        </div>
      </div>

      {/* ── Packages ─────────────────────────────────────────────────────────────── */}
      <div className="card" style={{ padding: 0, overflow: 'hidden', marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', padding: '14px 16px', borderBottom: '1px solid var(--border)' }}>
          <div>
            <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Packages</h2>
            <div style={{ fontSize: 12.5, color: 'var(--text3)' }}>
              {published.length} of {packages.length} published to the public page
            </div>
          </div>
          <button className="btn btn-primary" style={{ marginLeft: 'auto' }} disabled={busy}
            onClick={() => { setDraft({ ...BLANK }); setIsNew(true) }}>+ New package</button>
        </div>
        {packages.length === 0
          ? <div style={{ padding: 20, color: 'var(--text3)' }}>No packages yet — add one to publish a price.</div>
          : packages.map(p => (
            <div key={p.key} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px',
              borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
              <span style={{ flex: 1.2, minWidth: 150, fontWeight: 600 }}>
                {p.name}
                {p.is_featured && <span className="badge badge-blue" style={{ marginLeft: 6 }}>Featured</span>}
                <div style={{ fontSize: 12, color: 'var(--text3)', fontWeight: 400 }}>{p.tagline || p.key}</div>
              </span>
              <span style={{ flex: 1, minWidth: 130, fontSize: 13.5 }}>
                {p.price ? <b>{fmt(p.price)}</b> : <span style={{ color: '#b45309' }}>no price set</span>}
                <span style={{ color: 'var(--text3)' }}> / {p.cycle}</span>
                {p.unit_label && <div style={{ fontSize: 12, color: 'var(--text3)' }}>{p.unit_label}</div>}
              </span>
              <span style={{ width: 110 }}>
                <span className={p.is_public ? 'badge badge-green' : 'badge badge-slate'}>
                  {p.is_public ? 'Published' : 'Draft'}
                </span>
              </span>
              <span style={{ display: 'flex', gap: 8 }}>
                <button className="btn btn-secondary" disabled={busy}
                  onClick={() => { setDraft({ ...p, features: p.features || [] }); setIsNew(false) }}>Edit</button>
                <button className="btn btn-secondary" disabled={busy} onClick={() => togglePublish(p)}>
                  {p.is_public ? 'Unpublish' : 'Publish'}
                </button>
                <button className="btn btn-secondary" style={{ color: '#c0392b' }} disabled={busy}
                  onClick={() => removePackage(p.key)}>Delete</button>
              </span>
            </div>
          ))}
      </div>

      {/* ── Trials in flight ─────────────────────────────────────────────────────── */}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)' }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Companies on trial</h2>
          <div style={{ fontSize: 12.5, color: 'var(--text3)' }}>
            {trialing.length} trialing · {lapsed.length} lapsed. A lapsed trial is <b>not</b> locked out —
            switch the company off under Companies (Tenants) if that is what you want.
          </div>
        </div>
        {tenants.length === 0
          ? <div style={{ padding: 20, color: 'var(--text3)' }}>No companies yet.</div>
          : tenants.map(t => {
            const st = t.trial?.status || 'active'
            return (
              <div key={t.org_id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '11px 16px',
                borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
                <span style={{ flex: 1.3, minWidth: 150, fontWeight: 600 }}>
                  {t.name}{!t.is_active && <span style={{ color: '#b45309', fontSize: 12 }}> · disabled</span>}
                </span>
                <span style={{ flex: 1, minWidth: 130, fontSize: 13 }}>
                  <span style={{ color: statusColor[st] || 'var(--text2)', fontWeight: 600 }}>
                    {statusLabel[st] || st}
                  </span>
                  {t.trial?.days_left != null && st === 'trialing' &&
                    <span style={{ color: 'var(--text3)' }}> · {t.trial.days_left} day{t.trial.days_left === 1 ? '' : 's'} left</span>}
                </span>
                <span style={{ display: 'flex', gap: 8 }}>
                  <button className="btn btn-secondary" disabled={busy}
                    onClick={() => tenantPlan(t.org_id, { extend_days: 14 })}>+14 days</button>
                  {st !== 'active' && <button className="btn btn-secondary" disabled={busy}
                    onClick={() => tenantPlan(t.org_id, { plan_status: 'active' })}>Mark subscribed</button>}
                </span>
              </div>
            )
          })}
      </div>

      {/* ── Package editor ───────────────────────────────────────────────────────── */}
      {draft && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)', display: 'flex',
          alignItems: 'flex-start', justifyContent: 'center', padding: 24, overflowY: 'auto', zIndex: 50 }}
          onClick={() => setDraft(null)}>
          <div className="card" style={{ maxWidth: 620, width: '100%', marginTop: 24 }} onClick={e => e.stopPropagation()}>
            <h2 style={{ fontSize: 17, fontWeight: 700, margin: '0 0 14px' }}>
              {isNew ? 'New package' : `Edit ${draft.name || draft.key}`}
            </h2>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div>
                <label style={lbl} htmlFor="pk-key">Key (slug)</label>
                <input id="pk-key" style={inp} value={draft.key} disabled={!isNew}
                  placeholder="growth" onChange={e => setDraft({ ...draft, key: e.target.value })} />
              </div>
              <div>
                <label style={lbl} htmlFor="pk-name">Name</label>
                <input id="pk-name" style={inp} value={draft.name}
                  placeholder="Growth" onChange={e => setDraft({ ...draft, name: e.target.value })} />
              </div>
              <div style={{ gridColumn: '1 / -1' }}>
                <label style={lbl} htmlFor="pk-tagline">Tagline</label>
                <input id="pk-tagline" style={inp} value={draft.tagline ?? ''}
                  onChange={e => setDraft({ ...draft, tagline: e.target.value })} />
              </div>
              <div>
                <label style={lbl} htmlFor="pk-price">Price</label>
                <input id="pk-price" style={inp} type="number" min={0} step="0.01" value={draft.price}
                  onChange={e => setDraft({ ...draft, price: Number(e.target.value) })} />
              </div>
              <div>
                <label style={lbl} htmlFor="pk-cycle">Cycle</label>
                <select id="pk-cycle" style={inp} value={draft.cycle}
                  onChange={e => setDraft({ ...draft, cycle: e.target.value })}>
                  {CYCLES.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
              <div>
                <label style={lbl} htmlFor="pk-unit">What the price buys</label>
                <input id="pk-unit" style={inp} placeholder="per store / month" value={draft.unit_label ?? ''}
                  onChange={e => setDraft({ ...draft, unit_label: e.target.value })} />
              </div>
              <div>
                <label style={lbl} htmlFor="pk-note">Price note</label>
                <input id="pk-note" style={inp} placeholder="billed annually" value={draft.price_note ?? ''}
                  onChange={e => setDraft({ ...draft, price_note: e.target.value })} />
              </div>
              <div style={{ gridColumn: '1 / -1' }}>
                <label style={lbl} htmlFor="pk-features">What&apos;s included (one per line)</label>
                <textarea id="pk-features" style={{ ...inp, minHeight: 96, fontFamily: 'inherit' }}
                  value={(draft.features || []).join('\n')}
                  onChange={e => setDraft({ ...draft, features: e.target.value.split('\n') })} />
              </div>
              <div>
                <label style={lbl} htmlFor="pk-cta">Button text</label>
                <input id="pk-cta" style={inp} placeholder="Start free trial" value={draft.cta_label ?? ''}
                  onChange={e => setDraft({ ...draft, cta_label: e.target.value })} />
              </div>
              <div>
                <label style={lbl} htmlFor="pk-sort">Sort order</label>
                <input id="pk-sort" style={inp} type="number" value={draft.sort_order}
                  onChange={e => setDraft({ ...draft, sort_order: Number(e.target.value) })} />
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14 }}>
                <input type="checkbox" checked={draft.is_featured}
                  onChange={e => setDraft({ ...draft, is_featured: e.target.checked })} />
                Highlight this card
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, fontWeight: 600 }}>
                <input type="checkbox" checked={draft.is_public}
                  onChange={e => setDraft({ ...draft, is_public: e.target.checked })} />
                Published (public)
              </label>
              <div style={{ gridColumn: '1 / -1' }}>
                <label style={lbl} htmlFor="pk-notes">Internal notes (never shown publicly)</label>
                <input id="pk-notes" style={inp} value={draft.notes ?? ''}
                  onChange={e => setDraft({ ...draft, notes: e.target.value })} />
              </div>
            </div>
            {draft.is_public && !draft.price &&
              <div style={{ marginTop: 12, fontSize: 13, color: '#b45309' }}>
                ⚠️ This is published with no price — the site will show it as &ldquo;Talk to us&rdquo;.
              </div>}
            <div style={{ display: 'flex', gap: 8, marginTop: 18 }}>
              <button className="btn btn-primary" disabled={busy} onClick={savePackage}>Save</button>
              <button className="btn btn-secondary" disabled={busy} onClick={() => setDraft(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
