'use client'
// GOOGLE STORE-RATING SURFACES for the commission module — DISPLAY-ONLY analytics.
//
// Owner directive 2026-08-06 (Google Reviews Phase 1.5): "surface an employee's Google store rating(s)
// wherever the employee appears". This file is mod-commission's self-contained consumer of the two
// manager-gated endpoints mod-people exposes:
//
//   (a) GET /api/v1/storeops/google-reviews/employee/{employee_id}?org_id=…
//         → { employee_id, employee_name, stores:[{store_code,address,market,rating,review_count,
//             target,status,reviews:[…],action_plan,fetched_at}], note }
//   (b) GET /api/v1/storeops/google-reviews/employee-summary?org_id=…&employee_ids=<csv>
//         → { summaries: { "<employee_id>": [{store_code,rating,review_count,target,status}] } }
//
// THREE HARD RULES THIS FILE OBEYS:
//  1. NON-MONEY. Nothing here reads, writes or influences a payout, rate, tier or plan rule. It renders
//     a rating next to a person; that is all.
//  2. INVISIBLE UNTIL THE PEOPLE PACKAGE MERGES. Those endpoints may not exist yet in production, so
//     every call is wrapped: a throw / 404 / 403 / empty body caches an EMPTY result and renders
//     NOTHING — no error text, no console output, no empty box, no layout gap. A commission page must
//     look byte-identical to today when Google Reviews is off, unconfigured, or not yet deployed.
//  3. NEVER N CALLS. A table of 80 reps issues ONE batched summary request (chunked only if the id list
//     would build an absurd URL), not one per row. Results are cached per org for the browser session.
//
// It deliberately does NOT import components/GoogleReviewsCard.tsx (mod-people's self-view card) — that
// file belongs to another agent and is an employee-self surface with action-plan WRITE affordances. The
// chips/panel below are read-only manager views over the same data.
import { useCallback, useEffect, useMemo, useReducer, useState } from 'react'
import { api, getActiveOrg } from '@/lib/client'

// ── shapes (structural — mirrors mod-people's contract, tolerant of missing fields) ────────────────
export type GRSummary = {
  store_code?: string
  rating?: number | null
  review_count?: number | null
  target?: number | null
  status?: 'above' | 'below' | 'unknown' | string
}
export type GRReview = {
  author_name?: string; rating?: number | null; review_text?: string
  relative_time?: string; possible_mention?: boolean
}
export type GRStore = GRSummary & {
  address?: string; market?: string; reviews?: GRReview[]
  action_plan?: { status?: string; due_date?: string; plan_text?: string; dm_comments?: string } | null
  fetched_at?: string
}

type Person = { employee_id: string; id?: string | null; name: string; aliases?: string[] }
type Index = { byKey: Map<string, Person | null> }   // null ⇒ AMBIGUOUS key, never guess a person

const orgOf = () => getActiveOrg() || ''
const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }
const orgQ = () => { const o = getActiveOrg(); return o ? `?org_id=${encodeURIComponent(o)}` : '' }

/** Order-insensitive name key: "ALI, MOHAMMAD KHALID" and "Mohammad Khalid Ali" collapse to the same
 *  string, because the sales files, the StoreOps roster and the commission snapshot each spell a rep
 *  differently. Punctuation and double spaces are dropped. */
export function repNameKey(s?: string | null): string {
  return String(s || '')
    .toLowerCase()
    .replace(/[^a-z0-9\s]+/g, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .sort()
    .join(' ')
}

function buildIndex(people: Person[]): Index {
  const byKey = new Map<string, Person | null>()
  for (const p of people || []) {
    if (!p?.employee_id) continue
    const keys = new Set<string>()
    for (const a of [p.name, ...(p.aliases || [])]) {
      const k = repNameKey(a)
      if (k) keys.add(k)
    }
    for (const k of keys) {
      const cur = byKey.get(k)
      if (cur === undefined) byKey.set(k, p)
      else if (cur && cur.employee_id !== p.employee_id) byKey.set(k, null)   // two people, same spelling → refuse
    }
  }
  return { byKey }
}

// ── caches (module scope, per browser session) ────────────────────────────────────────────────────
let _indexCache: { org: string; p: Promise<Index> } | null = null
const _summaries = new Map<string, GRSummary[]>()   // `${org}|${employee_id}` → list (empty = asked, nothing there)
const _details = new Map<string, GRStore[]>()       // `${org}|${employee_id}` → per-store detail
const _notes = new Map<string, string>()
// ONE-SHOT id-flavour negotiation. StoreOps stores "who" two ways — the BUSINESS employees.employee_id
// and the NUMERIC employees.id (see storeops.router._emp_id_variants). We send business ids, which is
// what every existing google-reviews endpoint keys on; if a whole batch comes back with nothing AND the
// roster carries distinct numeric ids, we retry ONCE with those. Cheap insurance against an id-shape
// mismatch that would otherwise silently render nothing.
let _flavor: 'business' | 'numeric' = 'business'
let _numericTried = false

const CHUNK = 60   // ~60 uuids ≈ 2.2 KB of query string — comfortably inside any proxy's request-line cap

function loadIndex(): Promise<Index> {
  const org = orgOf()
  if (_indexCache && _indexCache.org === org) return _indexCache.p
  const p = api(`/api/v1/commcalc/rep-employee-map${orgQ()}`)
    .then((r: any) => buildIndex(r?.people || []))
    .catch(() => buildIndex([]))
  _indexCache = { org, p }
  return p
}

async function pull(slice: Person[], flavor: 'business' | 'numeric', org: string): Promise<number> {
  const ids = slice.map(p => (flavor === 'numeric' ? String(p.id || '') : p.employee_id))
  let data: any = null
  try {
    data = await api(`/api/v1/storeops/google-reviews/employee-summary`
      + `?employee_ids=${encodeURIComponent(ids.join(','))}${orgParam()}`)
  } catch { data = null }                            // not deployed / not permitted / offline → silent
  const m = (data && data.summaries) || {}
  let hits = 0
  slice.forEach((p, i) => {
    const raw = m[ids[i]]
    const list: GRSummary[] = Array.isArray(raw) ? raw : []
    if (list.length) hits++
    const k = `${org}|${p.employee_id}`
    if (list.length || !_summaries.has(k)) _summaries.set(k, list)
  })
  return hits
}

async function fetchSummaries(people: Person[]): Promise<boolean> {
  const org = orgOf()
  const seen = new Set<string>()
  const missing = people.filter(p => {
    if (!p?.employee_id || seen.has(p.employee_id)) return false
    seen.add(p.employee_id)
    return !_summaries.has(`${org}|${p.employee_id}`)
  })
  if (!missing.length) return false
  let hits = 0
  for (let i = 0; i < missing.length; i += CHUNK) hits += await pull(missing.slice(i, i + CHUNK), _flavor, org)
  if (hits === 0 && !_numericTried && _flavor === 'business') {
    _numericTried = true
    const withNumeric = missing.filter(p => p.id && p.id !== p.employee_id)
    if (withNumeric.length) {
      let n = 0
      for (let i = 0; i < withNumeric.length; i += CHUNK) n += await pull(withNumeric.slice(i, i + CHUNK), 'numeric', org)
      if (n > 0) _flavor = 'numeric'
    }
  }
  return true
}

/** Batch-load the Google rating summary for every rep NAME currently on screen. ONE request (chunked
 *  only for very large rosters), cached per org. Returns empty lists — never throws, never logs. */
export function useGoogleRatings(names: (string | null | undefined)[]) {
  const key = useMemo(
    () => Array.from(new Set((names || []).map(repNameKey).filter(Boolean))).sort().join('|'),
    [names],
  )
  const [idx, setIdx] = useState<Index | null>(null)
  const [tick, bump] = useReducer((x: number) => x + 1, 0)

  useEffect(() => {
    let alive = true
    loadIndex().then(i => { if (alive) setIdx(i) }).catch(() => { /* silent by design */ })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    if (!idx || !key) return
    let alive = true
    const people: Person[] = []
    for (const k of key.split('|')) {
      const p = idx.byKey.get(k)
      if (p) people.push(p)
    }
    if (!people.length) return
    fetchSummaries(people).then(changed => { if (alive && changed) bump() }).catch(() => { /* silent */ })
    return () => { alive = false }
  }, [idx, key])

  const ratingsFor = useCallback((name?: string | null): GRSummary[] => {
    const p = idx?.byKey.get(repNameKey(name))
    if (!p) return []
    return _summaries.get(`${orgOf()}|${p.employee_id}`) || []
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idx, tick])

  const employeeIdFor = useCallback((name?: string | null): string => {
    const p = idx?.byKey.get(repNameKey(name))
    return p ? p.employee_id : ''
  }, [idx])

  const hasAny = useMemo(() => {
    if (!idx) return false
    for (const k of key.split('|')) {
      const p = k && idx.byKey.get(k)
      if (p && (_summaries.get(`${orgOf()}|${p.employee_id}`) || []).length) return true
    }
    return false
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idx, key, tick])

  return { ratingsFor, employeeIdFor, hasAny }
}

// ── presentation ──────────────────────────────────────────────────────────────────────────────────
const statusColor = (s?: string) =>
  s === 'above' ? 'var(--green)' : s === 'below' ? 'var(--amber)' : 'var(--text3)'
const statusBg = (s?: string) =>
  s === 'above' ? 'rgba(22,163,74,0.10)' : s === 'below' ? 'rgba(217,119,6,0.12)' : 'var(--surface2)'
const one = (n: any) => (n == null || n === '' || isNaN(Number(n)) ? '—' : Number(n).toFixed(1))

/** "S123 ★4.6/4.7" chips, one per store the employee works at. Renders NOTHING when there is no data —
 *  no placeholder, no dash, no reserved space. */
export function GoogleRatingChips({ list, compact }: { list?: GRSummary[]; compact?: boolean }) {
  const rows = (list || []).filter(Boolean)
  if (!rows.length) return null
  return (
    <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap', verticalAlign: 'middle' }}>
      {rows.map((s, i) => (
        <span key={`${s.store_code || i}`}
          title={`${s.store_code || 'store'} — Google rating ${one(s.rating)} vs target ${one(s.target)}`
            + `${s.review_count != null ? ` · ${s.review_count} reviews` : ''}`
            + `${s.status === 'below' ? ' · BELOW target' : s.status === 'above' ? ' · at/above target' : ' · not rated yet'}`}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 3,
            padding: compact ? '1px 5px' : '2px 7px', borderRadius: 11,
            fontSize: compact ? 10.5 : 11.5, fontWeight: 600, whiteSpace: 'nowrap',
            color: statusColor(s.status), background: statusBg(s.status),
            border: `1px solid ${s.status === 'unknown' || !s.status ? 'var(--border)' : statusColor(s.status)}`,
          }}>
          {s.store_code || '—'} ★{one(s.rating)}/{one(s.target)}
        </span>
      ))}
    </span>
  )
}

/** The same thing as ONE export/CSV cell — RULE FOUR: what you see is what exports. */
export function ratingsText(list?: GRSummary[]): string {
  return (list || [])
    .map(s => `${s.store_code || '?'} ${one(s.rating)}/${one(s.target)}${s.status === 'below' ? ' (below)' : ''}`)
    .join(' · ')
}

/** Full per-store detail for ONE employee — endpoint (a). Self-resolving (takes the rep's display name),
 *  self-fetching, and renders nothing at all if the rep can't be resolved, the endpoint isn't there, or
 *  the caller isn't a manager. Recent reviews sit behind a toggle, collapsed by default. */
export function GoogleRatingDetail({ repName, title }: { repName?: string | null; title?: string }) {
  const [stores, setStores] = useState<GRStore[] | null>(null)
  const [note, setNote] = useState('')
  const [open, setOpen] = useState<Record<string, boolean>>({})

  useEffect(() => {
    let alive = true
    setStores(null); setNote('')
    const nm = repName || ''
    if (!nm) return
    loadIndex().then(async (idx) => {
      const p = idx.byKey.get(repNameKey(nm))
      if (!p || !alive) return
      const org = orgOf()
      const ck = `${org}|${p.employee_id}`
      if (_details.has(ck)) { if (alive) { setStores(_details.get(ck) || []); setNote(_notes.get(ck) || '') } ; return }
      const id = _flavor === 'numeric' && p.id ? p.id : p.employee_id
      let d: any = null
      try { d = await api(`/api/v1/storeops/google-reviews/employee/${encodeURIComponent(id)}${orgQ()}`) } catch { d = null }
      const list: GRStore[] = Array.isArray(d?.stores) ? d.stores : []
      _details.set(ck, list); _notes.set(ck, String(d?.note || ''))
      if (alive) { setStores(list); setNote(String(d?.note || '')) }
    }).catch(() => { /* silent by design */ })
    return () => { alive = false }
  }, [repName])

  if (!stores || !stores.length) return null

  return (
    <div className="card" style={{ padding: 0, marginBottom: 16 }}>
      <div style={{ padding: '9px 12px', background: 'var(--surface2)', fontWeight: 700, fontSize: 13.5 }}>
        ⭐ {title || 'Google store ratings'}
        <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}> · display only — does not affect pay</span>
      </div>
      <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
        {stores.map((s, i) => {
          const k = s.store_code || String(i)
          const reviews = s.reviews || []
          return (
            <div key={k} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 700, fontSize: 13 }}>{s.store_code || '—'}</span>
                {s.address && <span style={{ color: 'var(--text3)', fontSize: 12 }}>{s.address}</span>}
                {s.market && <span style={{ color: 'var(--text3)', fontSize: 12 }}>· {s.market}</span>}
                <span style={{ flex: 1 }} />
                <GoogleRatingChips list={[s]} />
                {s.review_count != null && <span style={{ fontSize: 11.5, color: 'var(--text3)' }}>{s.review_count} reviews</span>}
              </div>
              {s.action_plan?.status && (
                <div style={{ marginTop: 6, fontSize: 12, color: 'var(--text2)' }}>
                  📝 Action plan: <b>{s.action_plan.status.replace(/_/g, ' ')}</b>
                  {s.action_plan.due_date ? ` · due ${s.action_plan.due_date}` : ''}
                </div>
              )}
              {reviews.length > 0 && (
                <div style={{ marginTop: 6 }}>
                  <button className="btn btn-secondary" style={{ fontSize: 11.5, padding: '2px 8px' }}
                    onClick={() => setOpen(o => ({ ...o, [k]: !o[k] }))}>
                    {open[k] ? 'Hide' : 'Show'} recent reviews ({reviews.length})
                  </button>
                  {open[k] && (
                    <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 8 }}>
                      {reviews.map((r, j) => (
                        <div key={j} style={{ fontSize: 12.5, borderLeft: '3px solid var(--border)', paddingLeft: 8 }}>
                          <div style={{ color: 'var(--text2)' }}>
                            <b>{r.author_name || 'Google user'}</b> · ★{one(r.rating)}
                            {r.relative_time ? ` · ${r.relative_time}` : ''}
                            {r.possible_mention && <span style={{ marginLeft: 6, color: 'var(--accent)', fontSize: 11 }}>possible mention of this employee</span>}
                          </div>
                          {r.review_text && <div style={{ color: 'var(--text2)', marginTop: 2 }}>{r.review_text}</div>}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
        {note && <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>{note}</div>}
      </div>
    </div>
  )
}
