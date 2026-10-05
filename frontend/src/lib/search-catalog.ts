// THE ONE HOME for "what can the platform search find" (owner directive 2026-10-05).
//
// WHY THIS FILE EXISTS. The ⌘/ bar searched ONE registry — the 58 curated report entries in
// `lib/reports.ts`, of which 27 carry a description. Asked *"who is working in 509 nostrand"* it
// could not possibly win: no report is called 509 Nostrand, so the only words left to rank on were
// "who", "is" and "in". Widening the RANKING (`lib/search-rank.ts`) fixes how a result is chosen;
// this file fixes WHAT there is to choose from.
//
// THE RULE IT ENFORCES, and the reason it is a file rather than a hook: the searchable catalogue is
// ASSEMBLED from the registries that already exist, never re-declared. The platform already holds
// five of them —
//
//   • `route-index.ts`       — every page that EXISTS, derived from a walk of `src/app` (§54.7). This
//                              source is what makes the catalogue complete rather than curated: 34 of
//                              319 static pages were in no registry at all, including the
//                              `/account/password` somebody actually searched for.
//   • `rbac.ts` NAV          — every destination, already RBAC/capability/layout-filtered upstream
//   • `lib/reports.ts`       — the curated report catalogue, which is where the descriptions live
//   • `ScreenLink.ts` SCREENS— the spellings prose uses for a screen ("cash setup", "closing gate")
//   • the store option list  — folded to one option per physical store by `core.scope`
//   • the visible roster     — the people this login may see
//
// — and a sixth copy of "which pages exist" here would be the duplicate defect the house rules
// forbid. So every source is passed IN, structurally, and this file only FOLDS and LABELS.
//
// THE FOLD IS THE POINT. A page reachable from the nav AND listed as a report AND named in prose is
// ONE thing, and before this it would have been three entries that tie with each other and push the
// real answer off an 8-row list. `byHref` below keeps one entry per destination and merges what each
// source knows about it: the nav supplies the label the viewer actually sees, the report catalogue
// supplies the description, SCREENS supplies the aliases.
//
// ENTITIES CARRY NO HREF, deliberately. A store and a person are real answers to "509 nostrand" and
// "who is working", but the platform has no store page and no person page to open, and inventing an
// href would be worse than the gap (the same honesty convention `ScreenLink` states for the metric
// source of truth). A resolved entity is handed to the assistant as the subject of the question
// instead — `entityOnly` below is what lets the console render it as a fact rather than a link.
//
// PERMISSIONS ARE UPSTREAM. Every source is filtered by its own existing gate before it reaches this
// file: the nav by `canSeeItem`, the reports by `clearedFor`, the roster by the server's own
// `visible_people_keyset`. Filtering here would be a second gate that can disagree with the first.
//
// PURE and import-free apart from the ranking types, so `frontend/prove_search_catalog.mjs` drives
// the real function under Node with no React, no network and no build step.
import type { Searchable, SearchKind } from '@/lib/search-rank'

// ── what each source looks like, structurally ────────────────────────────────────────────────────
// Each type is the SHAPE this file reads, not an import of the registry's own type: the caller
// passes `NAV` and `REPORT_CATEGORIES` straight in, and a field added to either cannot break this.
export type NavSource = { group: string; items: { href: string; label: string }[] }[]
export type ReportSource = { category: string; reports: { href: string; label: string; desc?: string }[] }[]
export type ScreenSource = { href: string; label: string; blurb?: string; aliases?: string[] }[]
/** Every page that exists, as `route-index.searchableRoutes()` emits it. Passed LAST so a page that
 *  also has a nav entry keeps the nav's own wording; a page nav does not list gets its derived label
 *  and whatever aliases were declared for it. */
export type RouteSource = { path: string; label: string; aliases?: string[] }[]
/** A folded store option, exactly as `core.scope.build_store_options` emits it. */
export type StoreSource = { store: string; market?: string | null; also_known_as?: string[] }[]
/** A visible employee, as `GET /storeops/employees/visible` emits it. */
export type PersonSource = { employee_id?: string; name: string; home_store?: string | null; role?: string | null }[]

export type CatalogSources = {
  nav?: NavSource
  reports?: ReportSource
  screens?: ScreenSource
  routes?: RouteSource
  stores?: StoreSource
  people?: PersonSource
}

/** True for an entry that names a thing, not a place — it has no href and must never be rendered as
 *  a link. One predicate so the console, the keyboard handler and the proof agree on the test. */
export function entityOnly(item: Searchable): boolean {
  return !item.href
}

// A destination's path, the query and anchor removed — the same key `rbac.navPath` gates on, so two
// doors into one page (`/hr` and `/hr?tab=employees`) fold to one searchable entry.
function pathOf(href: string): string {
  return String(href || '').split('#')[0].split('?')[0]
}

const clean = (v: unknown): string => String(v == null ? '' : v).trim()

// A nav group whose name says it is where you CONFIGURE something rather than read it. Used only to
// label a hit ("Setting" vs "Page") so the console can group them; it gates nothing.
const SETTING_GROUP = /settings?|admin|config|setup|access|roles/i

export function buildCatalog(src: CatalogSources): Searchable[] {
  const byHref = new Map<string, Searchable>()
  const kindOf = (href: string, group: string): SearchKind =>
    SETTING_GROUP.test(group) || /\/(settings?|admin|config)\b/.test(pathOf(href)) ? 'setting' : 'page'

  const upsert = (href: string, patch: Partial<Searchable> & { label: string; kind: SearchKind }) => {
    const path = pathOf(href)
    if (!path || !clean(patch.label)) return
    const cur = byHref.get(path)
    if (!cur) {
      byHref.set(path, { key: `page:${path}`, kind: patch.kind, label: clean(patch.label), href,
                         category: patch.category, desc: patch.desc, aliases: patch.aliases })
      return
    }
    // MERGE, never replace: whichever source knows a field fills it, and a label already set by the
    // nav wins because that is the words on the viewer's own screen.
    if (patch.category && !cur.category) cur.category = patch.category
    if (patch.desc && !cur.desc) cur.desc = patch.desc
    if (patch.aliases?.length) {
      cur.aliases = Array.from(new Set([...(cur.aliases || []), ...patch.aliases.map(clean).filter(Boolean)]))
    }
    // A report entry upgrades a plain page: it says the destination IS a report.
    if (patch.kind === 'report') cur.kind = 'report'
  }

  for (const g of src.nav || []) {
    for (const it of g.items || []) {
      upsert(it.href, { label: it.label, kind: kindOf(it.href, g.group), category: clean(g.group) })
    }
  }
  for (const cat of src.reports || []) {
    for (const r of cat.reports || []) {
      upsert(r.href, { label: r.label, kind: 'report', category: clean(cat.category), desc: r.desc })
    }
  }
  for (const s of src.screens || []) {
    // SCREENS is the spellings registry: its aliases are what prose calls the screen, which is
    // exactly what somebody types. Its label and blurb only fill gaps the nav left.
    upsert(s.href, { label: s.label, kind: kindOf(s.href, ''), desc: s.blurb, aliases: s.aliases })
  }

  // LAST, deliberately. `upsert` merges rather than replaces, so a page the nav already named keeps
  // that label and only gains the declared aliases — and a page in no other registry becomes its own
  // entry here instead of being unfindable. This ordering is the whole completeness guarantee, and
  // the proof asserts it rather than trusting it.
  for (const r of src.routes || []) {
    upsert(r.path, { label: r.label, kind: kindOf(r.path, ''), aliases: r.aliases })
  }

  const out: Searchable[] = Array.from(byHref.values())

  for (const st of src.stores || []) {
    const label = clean(st.store)
    if (!label) continue
    const aka = (st.also_known_as || []).map(clean).filter(Boolean)
    out.push({
      key: `store:${label.toLowerCase()}`, kind: 'store', label, href: '',
      // The store's OWN other spellings — its bare code when the display is the street address, and
      // the address when it is not. `subtitle` is reserved for a second line of its own identity;
      // a store's display already IS its address, so the code belongs in `aliases`.
      aliases: aka,
      // The market is borrowed, not identity: see the `context` note in search-rank.ts.
      context: clean(st.market) || undefined,
      category: 'Store',
    })
  }
  for (const p of src.people || []) {
    const label = clean(p.name)
    if (!label) continue
    out.push({
      key: `person:${clean(p.employee_id) || label.toLowerCase()}`, kind: 'person', label, href: '',
      aliases: clean(p.employee_id) ? [clean(p.employee_id)] : undefined,
      // Where they work is borrowed from the store, so asking about an ADDRESS must surface the
      // store itself above the people who happen to work there.
      context: clean(p.home_store) || undefined,
      category: clean(p.role) || 'Person',
    })
  }
  return out
}

/** How many of each kind the catalogue holds — for the console's own "searching N things" line and
 *  for the proof, which asserts the fold rather than a total. */
export function catalogCounts(items: Searchable[]): Record<SearchKind, number> {
  const out: Record<SearchKind, number> = { page: 0, report: 0, setting: 0, store: 0, person: 0, customer: 0 }
  for (const it of items || []) out[it.kind] = (out[it.kind] || 0) + 1
  return out
}
