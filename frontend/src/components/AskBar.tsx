'use client'
// AskBar — a natural-language query / command bar (owner 2026-08-29 modernization track). Type a plain
// question ("net income last month", "diversey sales", "kpi") and it does two DETERMINISTIC things:
//   1. Quick answer — recognises a metric intent + a period and fetches the number from the SAME endpoints
//      the reports use (no LLM, no API key, so it can't hallucinate a figure), with a link to the full
//      report.
//   2. Search the platform — ranks everything the viewer may already open (pages, settings, reports)
//      PLUS the stores and people they may already see, by what the words MEAN, and lets you open any
//      destination (Enter opens the top hit).
// Opened with ⌘/ (Ctrl-/) or the nav button; Esc closes. Everything here is display/navigation only.
//
// WHAT CHANGED 2026-10-05, and why (owner: *"i asked who is working in 509 nostrand and it took me to
// Carrier Earned vs Employee Paid"* → *"need this to be a true search and ai console built for the
// platform"*). The search half had two faults and both are fixed in shared, proved modules rather than
// tuned here: it scored one point per typed word found anywhere in a REPORT's text, so "who", "is" and
// "in" carried the whole ranking (now `@/lib/search-rank`), and the only catalogue it searched was the
// 58 curated report entries, so a store address could never match (now `@/lib/search-catalog`, which
// folds the registries that already exist). The owner's division of labour: *"search module will only
// search what modules are build but ask assistant will act as a ai"* — so a question nothing in the
// catalogue explains is handed to the assistant instead of guessed at.
import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { api, fmt, getActiveOrg } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { useAuth } from '@/lib/auth-context'
import { REPORT_CATEGORIES } from '@/lib/reports'
import { canSeeItem, TENANT_NAV, type Permissions, type Scope } from '@/lib/rbac'
import { SCREENS } from '@/components/ScreenLink'
import { buildCatalog, entityOnly, type StoreSource, type PersonSource } from '@/lib/search-catalog'
import { rank, intent as searchIntent, type Hit, type SearchKind } from '@/lib/search-rank'
// The SAME panel the helpdesk page mounts — one Ask AI in the product, mounted in the one
// overlay that is already available everywhere (⌘/), so the assistant needed no new widget
// and no change to the platform layout.
import AiAssistant from '@/components/AiAssistant'

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

// What the search half can find. Assembled by `@/lib/search-catalog` from the registries that
// ALREADY exist — there is no catalogue declared here, which is the point: a second list of "which
// pages exist" would drift from the nav the moment either changed.
//
// PERMISSIONS ARE APPLIED HERE, BEFORE RANKING, by each source's own existing gate: `canSeeItem` for
// the nav and the report catalogue (so search can never surface a page the viewer could not already
// click), and the server's own scope gate for the stores and people, which arrive already narrowed.
function useSearchCatalog(permissions: Permissions, stores: StoreSource, people: PersonSource) {
  return useMemo(() => {
    // One shaped NavItem per candidate, so the nav and the report catalogue are gated by the SAME
    // predicate (`canSeeItem`) rather than two readings of it. A ReportDef carries no icon.
    const seeable = (href: string, label: string, module: string, scopes?: Scope[]) =>
      canSeeItem(permissions, { href, label, icon: '', module, scopes })
    const nav = TENANT_NAV
      .map(g => ({ group: g.group, items: g.items.filter(it => seeable(it.href, it.label, it.module, it.scopes)) }))
      .filter(g => g.items.length > 0)
    const reports = REPORT_CATEGORIES
      .map(c => ({ category: c.category, reports: c.reports.filter(r => seeable(r.href, r.label, r.module, r.scopes)) }))
      .filter(c => c.reports.length > 0)
    // SCREENS is the spellings registry (`ScreenLink`), and every one of its hrefs IS a nav href —
    // which is what makes this safe: an alias only ever lands on a destination the fold already
    // holds, or on nothing. A screen whose page the viewer may not open is dropped with it.
    const openable = new Set(nav.flatMap(g => g.items.map(it => it.href.split('#')[0].split('?')[0])))
    const screens = Object.values(SCREENS)
      .filter(sc => openable.has(sc.href.split('#')[0].split('?')[0]))
      .map(sc => ({ href: sc.href, label: sc.label, blurb: sc.blurb, aliases: sc.aliases }))
    return buildCatalog({ nav, reports, screens, stores, people })
  }, [permissions, stores, people])
}

// The stores and the people, from the endpoints that ALREADY serve them — no new data path, which is
// what the duplicate-check gate asks for. `/core/filter-options` is the one home that folds the two
// raw store vocabularies to one option per physical store (§13e: 58 raw spellings for 31 real
// stores), and it is the same list this viewer's own filter bars already offer them.
// `/storeops/employees/visible` is the roster narrowed by the server's `visible_people_keyset`
// (§14w), so a rep searching finds themselves and a manager finds their team.
//
// Fetched ONCE, the first time the overlay is opened — not per keystroke. Either failing leaves the
// search working over pages and reports alone, because a search box that errors is worse than one
// that finds less.
function useEntities(open: boolean) {
  const [stores, setStores] = useState<StoreSource>([])
  const [people, setPeople] = useState<PersonSource>([])
  const asked = useRef(false)
  useEffect(() => {
    if (!open || asked.current) return
    asked.current = true
    let live = true
    api(join('/api/v1/core/filter-options', orgParam()))
      .then((d: { stores?: StoreSource }) => { if (live && Array.isArray(d?.stores)) setStores(d.stores) })
      .catch(() => {})
    api(join('/api/v1/storeops/employees/visible', orgParam()))
      .then((d: { employees?: PersonSource }) => { if (live && Array.isArray(d?.employees)) setPeople(d.employees) })
      .catch(() => {})
    return () => { live = false }
  }, [open])
  return { stores, people }
}

// How each kind of hit reads in the list. A destination says where it lives; an entity says what it
// is, because there is no page to open for it (see `entityOnly` in search-catalog.ts).
const KIND_LABEL: Record<SearchKind, string> = {
  page: 'Page', report: 'Report', setting: 'Setting',
  store: 'Store', person: 'Person', customer: 'Customer',
}

export default function AskBar({ collapsed }: { collapsed?: boolean }) {
  const router = useRouter()
  const { period } = usePeriod()
  const { permissions } = useAuth()
  const [open, setOpen] = useState(false)
  const { stores, people } = useEntities(open)
  const catalog = useSearchCatalog(permissions || {}, stores, people)
  const [q, setQ] = useState('')
  const [ans, setAns] = useState<{ intent: Intent; period: string; label: string; value?: string; href?: string; loading: boolean } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  // The assistant hand-off (index §52) — see the block below `onSubmit`.
  const [askAI, setAskAI] = useState('')
  const [canAskAI, setCanAskAI] = useState(false)

  // ⌘/ (Ctrl-/) opens — ⌘K is already taken by the nav menu-filter, so the ask bar uses a distinct key.
  // Esc is handled on the input.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === '/') { e.preventDefault(); setOpen(o => !o) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  // Any prominent trigger elsewhere (e.g. the top-header ask bar) opens THIS one overlay by dispatching
  // `askbar:open` — so there is a single overlay + single set of handlers, no duplicate instances.
  useEffect(() => {
    const onOpen = () => setOpen(true)
    window.addEventListener('askbar:open', onOpen)
    return () => window.removeEventListener('askbar:open', onOpen)
  }, [])
  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 30) }, [open])

  // ONE ranking decision, taken in the shared proved module. `hits` is empty for an empty query:
  // the suggestion list below is a separate, deliberate thing rather than a rank of nothing.
  const hits: Hit[] = useMemo(() => rank(catalog, q, 8), [q, catalog])
  // And ONE decision about what to DO with it, so the mouse, Enter and the panel cannot disagree.
  const mode = useMemo(() => searchIntent(q, hits), [q, hits])
  // The first thing the viewer can actually OPEN — Enter follows this, never an entity.
  const firstDest = useMemo(() => hits.find(h => !entityOnly(h.item)) || null, [hits])
  // Suggestions for an untouched box: the reports, as before.
  const suggestions = useMemo(
    () => catalog.filter(i => i.kind === 'report').slice(0, 8), [catalog])

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

  const go = useCallback((href: string) => { setOpen(false); setAskAI(''); setQ(''); router.push(href) }, [router])
  // Enter: the deterministic figure first, then the first thing that can be OPENED. When the console
  // judged the query a question nothing explains, Enter asks the assistant rather than navigating —
  // which is the owner's reported defect stated as a keystroke.
  const onSubmit = () => {
    if (ans?.href) { go(ans.href); return }
    if (mode === 'ask' && canAskAI && q.trim().length > 3) { setAskAI(q.trim()); return }
    if (firstDest) go(firstDest.item.href)
  }

  // ── THE ASSISTANT HAND-OFF (index §52) ───────────────────────────────────────────────────────
  // This bar is DETERMINISTIC and stays the first answer: it recognises a metric intent and reads the
  // figure from the report's own endpoint, with no model involved, so it cannot invent a number. What
  // it cannot do is answer a COMPOSITIONAL question — "which store was best", "who is pulling me
  // down", "what do I need to pull sales up" — because those need a report grouped, ranked and
  // compared rather than a single metric looked up.
  //
  // So the assistant is offered HERE rather than as a fourth place to ask a question (the
  // duplicate-check gate: this bar and the Ask-AI panel already existed). The typed question is
  // handed to the SAME <AiAssistant> panel the helpdesk page mounts, inside this one overlay, and the
  // deterministic answer above it is left on screen — the user sees both, and can tell which is which.
  // `askAI` is set only by a click, so no question reaches a model (or spends a token) unasked.
  useEffect(() => {
    let live = true
    api(`/api/v1/core/data-qa/status${orgParam() ? `?${orgParam()}` : ''}`)
      .then((d: { allowed?: boolean; configured?: boolean }) => {
        if (live) setCanAskAI(!!d?.allowed && !!d?.configured)
      })
      .catch(() => { if (live) setCanAskAI(false) })
    return () => { live = false }
  }, [])
  // Closing the overlay forgets the hand-off, so reopening never re-asks a question (or re-spends a
  // token). Done in the close PATH rather than in an effect on `open` — an effect that calls setState
  // in its body cascades a render, which this repo's lint rules reject.
  const closeOverlay = useCallback(() => { setOpen(false); setAskAI('') }, [])

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
        <div onClick={closeOverlay}
          style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)', zIndex: 1000, display: 'flex',
            alignItems: 'flex-start', justifyContent: 'center', padding: '12vh 16px 16px' }}>
          <div onClick={e => e.stopPropagation()} className="card"
            style={{ width: 'min(620px, 96vw)', padding: 0, overflow: 'hidden', boxShadow: 'var(--shadow-lg)' }}>
            <input ref={inputRef} value={q} onChange={e => setQ(e.target.value)}
              onKeyDown={e => { if (e.key === 'Escape') closeOverlay(); if (e.key === 'Enter') onSubmit() }}
              placeholder="Ask a question, or search pages, reports, stores and people"
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
              {/* Search results. A destination opens; an entity (a store, a person) has no page to
                  open, so selecting it asks the assistant ABOUT it — see `entityOnly`. */}
              {!q && suggestions.length > 0 && (
                <div style={{ padding: 6 }}>
                  <div style={{ fontSize: 11, color: 'var(--text3)', padding: '4px 10px' }}>Jump to a report</div>
                  {suggestions.map(it => (
                    <button key={it.key} onClick={() => go(it.href)}
                      style={{ width: '100%', textAlign: 'left', display: 'flex', alignItems: 'baseline', gap: 10, padding: '8px 10px',
                        borderRadius: 7, border: 'none', background: 'transparent', cursor: 'pointer' }}>
                      <span style={{ fontSize: 13.5, fontWeight: 550, color: 'var(--text)' }}>{it.label}</span>
                      <span style={{ fontSize: 11, color: 'var(--text3)' }}>{it.category}</span>
                    </button>
                  ))}
                </div>
              )}
              {q && hits.length > 0 && (
                <div style={{ padding: 6 }}>
                  {hits.map((h, i) => {
                    const it = h.item
                    const isEntity = entityOnly(it)
                    const lead = mode === 'navigate' && h === firstDest
                    return (
                      <button key={it.key}
                        onClick={() => (isEntity ? setAskAI(`${q.trim()}`) : go(it.href))}
                        disabled={isEntity && !canAskAI}
                        title={isEntity ? `${it.label} — ask the assistant about it` : it.desc || it.label}
                        style={{ width: '100%', textAlign: 'left', display: 'flex', alignItems: 'baseline', gap: 10, padding: '8px 10px',
                          borderRadius: 7, border: 'none', background: lead ? 'var(--surface2)' : 'transparent',
                          cursor: isEntity && !canAskAI ? 'default' : 'pointer' }}>
                        <span style={{ fontSize: 13.5, fontWeight: 550, color: 'var(--text)' }}>{it.label}</span>
                        {it.context && <span style={{ fontSize: 11, color: 'var(--text3)' }}>{it.context}</span>}
                        <span style={{ flex: 1 }} />
                        <span style={{ fontSize: 10.5, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                          {KIND_LABEL[it.kind]}
                        </span>
                        {!isEntity && it.category && <span style={{ fontSize: 11, color: 'var(--text3)' }}>{it.category}</span>}
                        {i === 0 && isEntity && <span style={{ fontSize: 11, color: 'var(--text3)' }}>↓ ask about it</span>}
                      </button>
                    )
                  })}
                </div>
              )}
              {q && hits.length === 0 && mode !== 'ask' && (
                <div style={{ padding: '18px 16px', fontSize: 13, color: 'var(--text3)' }}>Nothing on the platform matches that. Try a metric (“net income”, “activations”), a report name, a store or a person.</div>
              )}
              {/* Ask the assistant — offered for any typed question, and the one place in the app
                  where a model is asked about the numbers. Nothing is sent until this is clicked. */}
              {canAskAI && q.trim().length > 3 && !askAI && (
                <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border)' }}>
                  <button onClick={() => setAskAI(q.trim())}
                    style={{ width: '100%', textAlign: 'left', display: 'flex', alignItems: 'center', gap: 8,
                      padding: '8px 10px', borderRadius: 7,
                      border: mode === 'ask' ? '1px solid var(--accent2)' : '1px dashed var(--border)',
                      background: 'transparent', cursor: 'pointer', fontSize: 13 }}>
                    <span>🤖</span>
                    <span>Ask the assistant: <b>“{q.trim()}”</b></span>
                    {mode === 'ask' && <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text3)' }}>Enter ↵</span>}
                  </button>
                  <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 5 }}>
                    {mode === 'ask'
                      ? 'That reads as a question, and nothing on the platform is named that — so it goes to the assistant rather than to the closest-looking page.'
                      : 'It reads your own reports and can group, rank, pivot and chart them.'}
                  </div>
                </div>
              )}
              {askAI && (
                <div style={{ borderTop: '1px solid var(--border)', background: 'var(--surface2)' }}>
                  <AiAssistant initialOpen compact initialQuestion={askAI} />
                </div>
              )}
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
