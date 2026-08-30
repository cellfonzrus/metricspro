'use client'
// AskBar — a natural-language query / command bar (owner 2026-08-29 modernization track). Type a plain
// question ("net income last month", "diversey sales", "kpi") and it does two DETERMINISTIC things:
//   1. Quick answer — recognises a metric intent + a period and fetches the number from the SAME endpoints
//      the reports use (no LLM, no API key, so it can't hallucinate a figure), with a link to the full
//      report.
//   2. Jump to a report — ranks the permission-filtered report catalogue by the words you typed and lets
//      you open any of them (Enter opens the top hit).
// Opened with ⌘/ (Ctrl-/) or the nav button; Esc closes. Everything here is display/navigation only.
import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { api, fmt, getActiveOrg } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { useAuth } from '@/lib/auth-context'
import { REPORT_CATEGORIES } from '@/lib/reports'
import { canSeeItem, type Permissions } from '@/lib/rbac'

const MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december']
const enc = encodeURIComponent
const orgParam = () => { const o = getActiveOrg(); return o ? `org_id=${enc(o)}` : '' }
const join = (base: string, q: string) => (q ? `${base}${base.includes('?') ? '&' : '?'}${q}` : base)

// Parse a period out of the query; fall back to the app's current period. Handles "last month",
// "this month", a bare or full month name, and the YYYY-MM form.
function resolvePeriod(q: string, current: string): { period: string; label: string } {
  const ql = q.toLowerCase()
  const parseCur = () => {
    const parts = current.trim().split(/\s+/)
    const mi = MONTHS.indexOf((parts[0] || '').toLowerCase())
    const yr = Number(parts[1]) || new Date().getFullYear()
    return mi >= 0 ? { m: mi, y: yr } : { m: new Date().getMonth(), y: new Date().getFullYear() }
  }
  const fmtP = (m: number, y: number) => `${MONTHS[m][0].toUpperCase()}${MONTHS[m].slice(1)} ${y}`
  if (/\blast month\b|\bprevious month\b|\bprior month\b/.test(ql)) {
    const { m, y } = parseCur(); const pm = m === 0 ? 11 : m - 1; const py = m === 0 ? y - 1 : y
    return { period: fmtP(pm, py), label: 'last month' }
  }
  if (/\bthis month\b|\bcurrent month\b/.test(ql)) return { period: current, label: 'this month' }
  const ym = ql.match(/(20\d\d)-(0[1-9]|1[0-2])/)
  if (ym) { const m = Number(ym[2]) - 1; return { period: fmtP(m, Number(ym[1])), label: fmtP(m, Number(ym[1])) } }
  const mi = MONTHS.findIndex(mo => new RegExp(`\\b${mo}\\b`).test(ql))
  if (mi >= 0) { const y = Number((ql.match(/20\d\d/) || [])[0]) || parseCur().y; return { period: fmtP(mi, y), label: fmtP(mi, y) } }
  return { period: current, label: '' }
}

// Metric intents → a resolver that reads the SAME endpoint the report uses and returns a formatted value.
type Intent = { id: string; kw: string[]; label: string; resolve: (period: string, orgQ: string) => Promise<{ value: string; href: string } | null> }
const INTENTS: Intent[] = [
  { id: 'net_income', kw: ['net income', 'net profit', 'bottom line', 'p&l', 'pnl', 'p and l', 'profit'], label: 'Net income',
    resolve: async (p, o) => { const d = await api(join(`/api/v1/account/overview/${enc(p)}`, o)); const c = (d?.scopes || []).find((s: any) => s.scope_key === 'consolidated'); return c ? { value: fmt(c.net_income || 0), href: '/accounts' } : null } },
  { id: 'gross_profit', kw: ['gross profit', 'gp', 'margin'], label: 'Gross profit',
    resolve: async (p, o) => { const d = await api(join(`/api/v1/account/overview/${enc(p)}`, o)); const c = (d?.scopes || []).find((s: any) => s.scope_key === 'consolidated'); return c ? { value: fmt(c.gross_profit || 0), href: '/accounts' } : null } },
  { id: 'revenue', kw: ['revenue', 'sales', 'turnover', 'top line'], label: 'Revenue (MTD)',
    resolve: async (p, o) => { const d = await api(join(`/api/v1/commcalc/sales-report/narrative?period=${enc(p)}`, o)); return d?.facts?.revenue != null ? { value: fmt(d.facts.revenue), href: '/commcalc/sales-report' } : null } },
  { id: 'activations', kw: ['activation', 'activations', 'acts', 'boxes', 'ta'], label: 'Activations (MTD)',
    resolve: async (p, o) => { const d = await api(join(`/api/v1/commcalc/exec-mtd/${enc(p)}/narrative`, o)); return d?.facts?.total_activation != null ? { value: Number(d.facts.total_activation).toLocaleString(), href: '/commcalc/exec/mtd' } : null } },
  { id: 'payout', kw: ['payout', 'commission', 'incentive', 'commissions'], label: 'Incentive payout',
    resolve: async (p, o) => { const rows = await api(join(`/api/v1/commcalc/commissions/${enc(p)}`, o)); const t = (rows || []).reduce((s: number, r: any) => s + (r.total_payout || 0), 0); return { value: fmt(t), href: '/commcalc' } } },
]

// The permission-filtered, flattened report catalogue for the jump list.
function useReportIndex(permissions: Permissions) {
  return useMemo(() => {
    const out: { href: string; label: string; category: string; desc?: string; hay: string }[] = []
    for (const cat of REPORT_CATEGORIES) {
      for (const r of cat.reports) {
        if (!canSeeItem(permissions, { href: r.href, label: r.label, icon: '', module: r.module, scopes: r.scopes } as any)) continue
        out.push({ href: r.href, label: r.label, category: cat.category, desc: r.desc, hay: `${r.label} ${cat.category} ${r.desc || ''}`.toLowerCase() })
      }
    }
    return out
  }, [permissions])
}

export default function AskBar({ collapsed }: { collapsed?: boolean }) {
  const router = useRouter()
  const { period } = usePeriod()
  const { permissions } = useAuth()
  const reports = useReportIndex(permissions || {})
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [ans, setAns] = useState<{ intent: Intent; period: string; label: string; value?: string; href?: string; loading: boolean } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // ⌘/ (Ctrl-/) opens — ⌘K is already taken by the nav menu-filter, so the ask bar uses a distinct key.
  // Esc is handled on the input.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === '/') { e.preventDefault(); setOpen(o => !o) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 30) }, [open])

  // Rank reports by how many typed words they match (label/category/desc), then alphabetically.
  const hits = useMemo(() => {
    const terms = q.toLowerCase().split(/\s+/).filter(t => t.length > 1)
    if (!terms.length) return reports.slice(0, 8)
    return reports
      .map(r => ({ r, score: terms.reduce((s, t) => s + (r.hay.includes(t) ? 1 : 0), 0) + (r.label.toLowerCase().startsWith(terms[0]) ? 0.5 : 0) }))
      .filter(x => x.score > 0)
      .sort((a, b) => b.score - a.score || a.r.label.localeCompare(b.r.label))
      .slice(0, 8)
      .map(x => x.r)
  }, [q, reports])

  // Detect the best metric intent and fetch its value for the resolved period (debounced, cancellable).
  useEffect(() => {
    const ql = q.toLowerCase().trim()
    if (!ql) { setAns(null); return }
    const intent = INTENTS.find(i => i.kw.some(k => ql.includes(k)))
    if (!intent) { setAns(null); return }
    const { period: p, label } = resolvePeriod(ql, period)
    let live = true
    setAns({ intent, period: p, label, loading: true })
    const t = setTimeout(async () => {
      try { const r = await intent.resolve(p, orgParam()); if (live) setAns(a => a ? { ...a, loading: false, value: r?.value, href: r?.href } : a) }
      catch { if (live) setAns(a => a ? { ...a, loading: false } : a) }
    }, 250)
    return () => { live = false; clearTimeout(t) }
  }, [q, period])

  const go = useCallback((href: string) => { setOpen(false); setQ(''); router.push(href) }, [router])
  const onSubmit = () => { if (ans?.href) go(ans.href); else if (hits[0]) go(hits[0].href) }

  return (
    <>
      {/* Nav trigger — mirrors the HelpToggle placement. Collapsed rail shows the icon only. */}
      {collapsed ? (
        <button className="mp-icon-btn" onClick={() => setOpen(true)} title="Ask (⌘/)" aria-label="Ask"
          style={{ margin: '0 auto 8px' }}>🔎</button>
      ) : (
        <div style={{ padding: '0 12px 10px' }}>
          <button onClick={() => setOpen(true)} title="Ask a question or jump to a report (⌘/)"
            style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '6px 9px', borderRadius: 7,
              cursor: 'pointer', fontSize: 12, color: 'rgba(255,255,255,0.72)', background: 'rgba(255,255,255,0.07)',
              border: '1px solid rgba(255,255,255,0.14)' }}>
            <span aria-hidden>🔎</span><span style={{ flex: 1, textAlign: 'left' }}>Ask…</span>
            <kbd style={{ fontSize: 10, opacity: 0.6, border: '1px solid rgba(255,255,255,0.2)', borderRadius: 4, padding: '0 4px' }}>⌘/</kbd>
          </button>
        </div>
      )}

      {open && (
        <div onClick={() => setOpen(false)}
          style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)', zIndex: 1000, display: 'flex',
            alignItems: 'flex-start', justifyContent: 'center', padding: '12vh 16px 16px' }}>
          <div onClick={e => e.stopPropagation()} className="card"
            style={{ width: 'min(620px, 96vw)', padding: 0, overflow: 'hidden', boxShadow: 'var(--shadow-lg)' }}>
            <input ref={inputRef} value={q} onChange={e => setQ(e.target.value)}
              onKeyDown={e => { if (e.key === 'Escape') setOpen(false); if (e.key === 'Enter') onSubmit() }}
              placeholder="Ask a question or search reports —  e.g. “net income last month”, “sales august”, “kpi”"
              style={{ width: '100%', border: 'none', borderBottom: '1px solid var(--border)', padding: '15px 18px',
                fontSize: 15, outline: 'none', background: 'var(--surface)', color: 'var(--text)' }} />
            <div style={{ maxHeight: '52vh', overflow: 'auto' }}>
              {/* Quick answer */}
              {ans && (
                <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
                  <div style={{ fontSize: 11, fontWeight: 650, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text3)', marginBottom: 4 }}>
                    {ans.intent.label}{ans.label ? ` · ${ans.label}` : ` · ${ans.period}`}
                  </div>
                  {ans.loading ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text3)', fontSize: 13 }}><span className="spinner" style={{ width: 14, height: 14 }} /> Reading the numbers…</div>
                  ) : ans.value != null ? (
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
                      <span style={{ fontSize: 26, fontWeight: 700, letterSpacing: '-0.02em', fontVariantNumeric: 'tabular-nums' }}>{ans.value}</span>
                      {ans.href && <button onClick={() => go(ans.href!)} style={{ fontSize: 12.5, color: 'var(--accent2)', background: 'none', border: 'none', cursor: 'pointer' }}>View report →</button>}
                    </div>
                  ) : (
                    <div style={{ fontSize: 13, color: 'var(--text3)' }}>No computed figure for {ans.period} yet. <button onClick={() => ans.href && go(ans.href)} style={{ color: 'var(--accent2)', background: 'none', border: 'none', cursor: 'pointer' }}>Open the report →</button></div>
                  )}
                </div>
              )}
              {/* Report jump list */}
              {hits.length > 0 ? (
                <div style={{ padding: 6 }}>
                  {!q && <div style={{ fontSize: 11, color: 'var(--text3)', padding: '4px 10px' }}>Jump to a report</div>}
                  {hits.map((r, i) => (
                    <button key={r.href} onClick={() => go(r.href)}
                      style={{ width: '100%', textAlign: 'left', display: 'flex', alignItems: 'baseline', gap: 10, padding: '8px 10px',
                        borderRadius: 7, border: 'none', background: i === 0 && q ? 'var(--surface2)' : 'transparent', cursor: 'pointer' }}>
                      <span style={{ fontSize: 13.5, fontWeight: 550, color: 'var(--text)' }}>{r.label}</span>
                      <span style={{ fontSize: 11, color: 'var(--text3)' }}>{r.category}</span>
                    </button>
                  ))}
                </div>
              ) : q ? (
                <div style={{ padding: '18px 16px', fontSize: 13, color: 'var(--text3)' }}>No matching report. Try a metric (“net income”, “activations”) or a report name.</div>
              ) : null}
            </div>
            <div style={{ padding: '8px 16px', borderTop: '1px solid var(--border)', fontSize: 11, color: 'var(--text3)', display: 'flex', gap: 14 }}>
              <span><b>Enter</b> open</span><span><b>Esc</b> close</span><span>Answers are computed from live report data.</span>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
