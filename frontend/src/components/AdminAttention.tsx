'use client'
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { useAuth } from '@/lib/auth-context'
import { api, getActiveOrg } from '@/lib/client'
import { canSeeAttention } from '@/lib/rbac'

// ── ADMIN ATTENTION (owner directive 2026-07-25, mig 717) ────────────────────────────────────────────
// "if any imports are not scheduled as defined in the entire system it should come up as a pop for every
// admin as soon as they log in on the main page and take them to the upload menu to manually upload the
// data or fix the import channel … also the admin should be notified for pending mappings or duplicate data"
//
// Behaviour:
//   • Renders NOTHING for a non-admin (gate mirrors the backend `can_view_attention`, via rbac.canSeeAttention).
//   • Fetches GET /api/v1/core/attention ONCE per mount (cheap providers only — a login never pays for a
//     40k-row scan; the heavy scans are behind the "Run full check" button, deep=1).
//   • Pops the modal ONCE PER LOGIN SESSION per acting tenant (sessionStorage), so it does not nag on every
//     navigation; a persistent header pill remains and reopens it on demand.
//   • Every item carries the deep link that FIXES it (import → the channel page or the upload menu).
//   • Multi-tenant: api() scopes the call to the ACTING org, so a super-admin acting as a tenant sees that
//     tenant's items only. Switching tenants changes the dismissal key, so the new tenant's popup shows.
//   • Fail-silent: any error (mig 717 un-run, 403, offline) leaves the whole component invisible. It can
//     never block or break a page.

type Item = {
  group: string; key: string; severity: 'error' | 'warning' | 'info'
  label: string; detail: string; count: number
  deep_link: string | null; deep_link_label: string | null; provider?: string
}
type Payload = {
  items: Item[]
  deferred: { key: string; label: string; group: string }[]
  counts: { total: number; error: number; warning: number; import: number; mapping: number; duplicate: number }
  deep: boolean; ready?: boolean; hint?: string | null
}

const SEV: Record<string, { color: string; bg: string; icon: string }> = {
  error: { color: '#b91c1c', bg: '#fef2f2', icon: '⛔' },
  warning: { color: '#b45309', bg: '#fffbeb', icon: '⚠️' },
  info: { color: '#1d4ed8', bg: '#eff6ff', icon: 'ℹ️' },
}
const GROUP_LABEL: Record<string, string> = {
  import: 'Imports overdue or never run',
  mapping: 'Pending mappings',
  duplicate: 'Possible duplicate data',
  other: 'Other',
}
const GROUP_ORDER = ['import', 'mapping', 'duplicate', 'other']

export default function AdminAttention() {
  const { permissions, loading, session } = useAuth()
  const [data, setData] = useState<Payload | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const allowed = canSeeAttention(permissions)

  const load = useCallback(async (deep: boolean) => {
    setBusy(true)
    try {
      const d: Payload = await api(`/api/v1/core/attention${deep ? '?deep=1' : ''}`)
      setData(d)
      return d
    } catch {
      setData(null)   // fail-silent: 403 / un-run migration / offline → the component stays invisible
      return null
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => {
    if (loading || !allowed || !session) return
    let alive = true
    load(false).then(d => {
      if (!alive || !d || !(d.items || []).length) return
      // Once per login SESSION per acting tenant — sessionStorage clears when the tab/session ends, so the
      // next real login pops again while ordinary navigation inside the session does not.
      try {
        const k = `mp_attention_seen_${getActiveOrg() || 'default'}`
        if (window.sessionStorage.getItem(k) === '1') return
        window.sessionStorage.setItem(k, '1')
      } catch { /* private mode → just show it */ }
      setOpen(true)
    })
    return () => { alive = false }
  }, [loading, allowed, session, load])

  if (!allowed || !data || !(data.items || []).length) return null
  const c = data.counts || { total: 0, error: 0, warning: 0, import: 0, mapping: 0, duplicate: 0 }
  const groups = GROUP_ORDER
    .map(g => ({ g, items: (data.items || []).filter(i => (i.group || 'other') === g) }))
    .filter(x => x.items.length > 0)

  return (
    <>
      {/* persistent indicator — stays after the popup is dismissed */}
      <button onClick={() => setOpen(true)} title="Items needing an administrator's attention"
        style={{
          fontSize: 13, fontWeight: 700, cursor: 'pointer',
          color: c.error ? '#b91c1c' : '#b45309', background: c.error ? '#fef2f2' : '#fffbeb',
          border: `1px solid ${c.error ? '#fecaca' : '#fde68a'}`, borderRadius: 8, padding: '5px 10px',
        }}>
        {c.error ? '⛔' : '⚠️'} {c.total} needs attention
      </button>

      {open && (
        <div role="dialog" aria-modal="true" onClick={() => setOpen(false)}
          style={{
            position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)', zIndex: 1000,
            display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '6vh 16px',
          }}>
          <div onClick={e => e.stopPropagation()} className="card"
            style={{ background: 'white', maxWidth: 760, width: '100%', maxHeight: '84vh', overflowY: 'auto',
              borderRadius: 14, boxShadow: '0 20px 60px rgba(0,0,0,0.25)', padding: 0 }}>
            <div style={{ padding: '18px 22px 12px', borderBottom: '1px solid var(--border)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <div style={{ fontSize: 17, fontWeight: 800 }}>Needs your attention</div>
                <button onClick={() => setOpen(false)} aria-label="Close"
                  style={{ marginLeft: 'auto', background: 'none', border: 'none', fontSize: 20,
                    cursor: 'pointer', color: 'var(--text3)', lineHeight: 1 }}>×</button>
              </div>
              <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 4 }}>
                {c.import > 0 && <>{c.import} import{c.import === 1 ? '' : 's'} overdue or never run. </>}
                {c.mapping > 0 && <>{c.mapping} pending mapping issue{c.mapping === 1 ? '' : 's'}. </>}
                {c.duplicate > 0 && <>{c.duplicate} possible duplicate-data signal{c.duplicate === 1 ? '' : 's'}. </>}
              </div>
            </div>

            <div style={{ padding: '8px 22px 4px' }}>
              {groups.map(({ g, items }) => (
                <div key={g} style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.06em',
                    textTransform: 'uppercase', color: 'var(--text3)', marginBottom: 6 }}>
                    {GROUP_LABEL[g] || g}
                  </div>
                  {items.map(it => {
                    const s = SEV[it.severity] || SEV.info
                    return (
                      <div key={it.key} style={{ display: 'flex', gap: 10, alignItems: 'flex-start',
                        border: '1px solid var(--border)', borderLeft: `4px solid ${s.color}`,
                        background: s.bg, borderRadius: 10, padding: '10px 12px', marginBottom: 8 }}>
                        <span style={{ fontSize: 15, lineHeight: '20px' }}>{s.icon}</span>
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <div style={{ fontSize: 13.5, fontWeight: 700, color: s.color }}>{it.label}</div>
                          <div style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 2 }}>{it.detail}</div>
                        </div>
                        {it.deep_link && (
                          <Link href={it.deep_link} onClick={() => setOpen(false)}
                            style={{ alignSelf: 'center', whiteSpace: 'nowrap', fontSize: 12.5, fontWeight: 700,
                              textDecoration: 'none', color: 'white', background: s.color,
                              borderRadius: 8, padding: '6px 11px' }}>
                            {it.deep_link_label || 'Fix'} →
                          </Link>
                        )}
                      </div>
                    )
                  })}
                </div>
              ))}
            </div>

            <div style={{ padding: '10px 22px 18px', borderTop: '1px solid var(--border)', marginTop: 10,
              display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              {!data.deep && (data.deferred || []).length > 0 && (
                <button className="btn" disabled={busy} onClick={() => load(true)}
                  title={`Also runs the slower checks: ${(data.deferred || []).map(d => d.label).join(', ')}`}
                  style={{ fontSize: 12.5 }}>
                  {busy ? 'Checking…' : 'Run full check'}
                </button>
              )}
              <Link href="/admin/import-health" onClick={() => setOpen(false)}
                style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--accent)', textDecoration: 'none' }}>
                Manage import schedules →
              </Link>
              <button onClick={() => setOpen(false)}
                style={{ marginLeft: 'auto', fontSize: 12.5, background: 'none', border: '1px solid var(--border)',
                  borderRadius: 8, padding: '6px 12px', cursor: 'pointer', color: 'var(--text2)' }}>
                Dismiss for now
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
