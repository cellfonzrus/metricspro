'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, getActiveOrg } from '@/lib/client'

// Integrations hub — ONE page for every connection/import surface (owner 2026-08-27: "all integrations on
// one page, clear carrier-neutral purpose, a 2-step wizard even for a 2-step job, best in class"). This is a
// NAVIGATOR + STATUS board, never a config store: each card deep-links to the page that already owns that
// config (single-source), shows a live status probe, and opens a uniform 2-step wizard. DISPLAY/config.
const orgQ = () => { const o = getActiveOrg(); return o ? `?org_id=${encodeURIComponent(o)}` : '' }

type Step = { title: string; body: string }
type Item = {
  key: string; title: string; icon?: string; purpose: string; carrier_specific?: boolean; badge?: string
  deep_link: string; status: string; steps: Step[]
}
type Cat = { category: string; blurb?: string; items: Item[] }

const STATUS: Record<string, { label: string; bg: string; fg: string }> = {
  connected:     { label: 'Connected',        bg: '#dcfce7', fg: '#166534' },
  action_needed: { label: 'Set up · paused',  bg: '#fef9c3', fg: '#854d0e' },
  not_started:   { label: 'Not set up',       bg: 'var(--surface2)', fg: 'var(--text2)' },
  unknown:       { label: 'Status unknown',   bg: 'var(--surface2)', fg: 'var(--text3)' },
  info:          { label: 'Ready',            bg: 'var(--surface2)', fg: 'var(--text2)' },
}

export default function IntegrationsPage() {
  const [data, setData] = useState<{ categories: Cat[]; summary: any } | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'todo' | 'connected'>('all')
  const [wiz, setWiz] = useState<Item | null>(null)
  const [step, setStep] = useState(0)

  const load = useCallback(() => {
    setLoading(true); setErr(null)
    api(`/api/v1/commcalc/integrations${orgQ()}`)
      .then((r: any) => setData(r)).catch(e => setErr(e?.message || String(e))).finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  const openWiz = (it: Item) => { setWiz(it); setStep(0) }
  const s = data?.summary || { total: 0, connected: 0, action_needed: 0, not_started: 0 }

  const showItem = (it: Item) =>
    filter === 'all' ? true
      : filter === 'connected' ? it.status === 'connected'
      : (it.status === 'not_started' || it.status === 'action_needed')

  const pill = (status: string) => {
    const c = STATUS[status] || STATUS.unknown
    return <span style={{ background: c.bg, color: c.fg, fontSize: 11, fontWeight: 600, padding: '2px 9px', borderRadius: 999 }}>{c.label}</span>
  }

  return (
    <div style={{ padding: '18px 22px', maxWidth: 1100 }}>
      <h1 style={{ fontSize: 20, fontWeight: 800, margin: 0 }}>Integrations</h1>
      <p style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 6, marginBottom: 12, maxWidth: 820 }}>
        Every way to get data into the platform, in one place. Each one has a plain-English purpose and a short,
        guided setup — pick a source, connect it, and it keeps your reports fed automatically.
      </p>

      {/* summary strip */}
      {data && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
          {[
            { k: 'all', label: `All ${s.total}` },
            { k: 'connected', label: `✅ Connected ${s.connected}` },
            { k: 'todo', label: `• To set up ${s.not_started + s.action_needed}` },
          ].map(c => (
            <button key={c.k} onClick={() => setFilter(c.k as any)}
              style={{ fontSize: 12, fontWeight: 600, padding: '5px 12px', borderRadius: 999, cursor: 'pointer',
                border: '1px solid var(--border)', background: filter === c.k ? 'var(--primary, #2563eb)' : 'var(--surface)',
                color: filter === c.k ? '#fff' : 'var(--text2)' }}>{c.label}</button>
          ))}
        </div>
      )}

      {err && <div style={{ background: '#fef2f2', border: '1px solid #fecaca', color: '#991b1b', borderRadius: 8, padding: '9px 12px', fontSize: 12.5, marginBottom: 10 }}>❌ {err}</div>}
      {loading && <div style={{ color: 'var(--text3)', fontSize: 13, padding: 20 }}>Loading…</div>}

      {(data?.categories || []).map(cat => {
        const items = cat.items.filter(showItem)
        if (!items.length) return null
        return (
          <div key={cat.category} style={{ marginBottom: 22 }}>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 2 }}>{cat.category}</div>
            {cat.blurb && <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>{cat.blurb}</div>}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12 }}>
              {items.map(it => (
                <div key={it.key} className="card" style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                    <span style={{ fontSize: 20, lineHeight: 1 }}>{it.icon || '🔗'}</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 700, fontSize: 13.5, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                        {it.title}
                        {it.badge && <span style={{ fontSize: 10, fontWeight: 600, padding: '1px 6px', borderRadius: 4, background: 'var(--surface2)', color: 'var(--text3)' }}>{it.badge}</span>}
                      </div>
                    </div>
                    {pill(it.status)}
                  </div>
                  <div style={{ fontSize: 12.5, color: 'var(--text2)', lineHeight: 1.4, flex: 1 }}>{it.purpose}</div>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-primary" style={{ fontSize: 12.5, padding: '5px 12px' }} onClick={() => openWiz(it)}>
                      {it.status === 'connected' ? 'Manage' : 'Set up'}
                    </button>
                    <Link href={it.deep_link} className="btn btn-secondary" style={{ fontSize: 12.5, padding: '5px 12px' }}>Open</Link>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )
      })}

      {/* 2-step wizard drawer */}
      {wiz && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', display: 'flex', justifyContent: 'flex-end', zIndex: 60 }} onClick={() => setWiz(null)}>
          <div className="card" style={{ width: 460, maxWidth: '96vw', height: '100%', borderRadius: 0, padding: 20, overflow: 'auto' }} onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
              <span style={{ fontSize: 22 }}>{wiz.icon || '🔗'}</span>
              <div style={{ fontWeight: 800, fontSize: 16 }}>{wiz.title}</div>
              <div style={{ flex: 1 }} />
              <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setWiz(null)}>Close</button>
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 6 }}>{wiz.purpose}</div>
            <div style={{ marginBottom: 14 }}>{pill(wiz.status)}</div>

            {/* step indicator */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
              {wiz.steps.map((st, i) => (
                <button key={i} onClick={() => setStep(i)}
                  style={{ flex: 1, padding: '7px 8px', borderRadius: 8, cursor: 'pointer', textAlign: 'left',
                    border: '1px solid var(--border)', background: i === step ? 'var(--surface2)' : 'var(--surface)' }}>
                  <div style={{ fontSize: 10.5, fontWeight: 700, color: i === step ? 'var(--primary, #2563eb)' : 'var(--text3)' }}>STEP {i + 1}</div>
                  <div style={{ fontSize: 12, fontWeight: 600 }}>{st.title}</div>
                </button>
              ))}
            </div>

            {/* current step */}
            <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.5, marginBottom: 16 }}>
              {wiz.steps[step]?.body}
            </div>

            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              {step === 0 ? (
                <>
                  <Link href={wiz.deep_link} className="btn btn-primary" style={{ fontSize: 13, padding: '7px 14px' }}>
                    Open {wiz.title} →
                  </Link>
                  <button className="btn btn-secondary" style={{ fontSize: 13 }} onClick={() => setStep(1)}>Next</button>
                </>
              ) : (
                <>
                  <button className="btn btn-secondary" style={{ fontSize: 13 }} onClick={() => setStep(0)}>Back</button>
                  <button className="btn btn-primary" style={{ fontSize: 13, padding: '7px 14px' }} onClick={() => { load(); }}>Recheck status</button>
                </>
              )}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 12 }}>
              Everything you enter is saved on that page — this wizard just guides you there and tracks whether it&rsquo;s connected.
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
