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
// PERMISSION RULES ARE UPSTREAM. The nav is gated by `canSeeItem`, a path by `canAccessPath`, the
// roster by the server's own `visible_people_keyset`. None of those rules lives here: `viewerSources`
// below takes the two predicates as ARGUMENTS, exactly as it takes the registries, so this file asks
// the questions and `rbac.ts` answers them. Re-deciding either answer here would be a second gate
// that can disagree with the first.
//
// WHAT 2026-10-08 ADDED, and why it belongs in this file. The platform had TWO page-search boxes
// with TWO indexes, and only one was derived:
//
//   • ⌘/  (the question bar, `components/AskBar.tsx`) folded six registries through THIS file —
//     among them `route-index.ts`, generated from a walk of `src/app` and build-failing on drift
//     (§54.7). Every page the app has was findable there.
//   • ⌘K  (the sidebar search, `app/(platform)/layout.tsx`) built its own index from `NAV` alone — a
//     hand-curated list with no completeness ratchet — and ranked it with its own private
//     `startsWith / includes / group-includes` ladder.
//
// So 27 in-app pages existed, were reachable, were in the generated registry, and could not be found
// in the box the owner types in: Cash Position, Closing Readiness, Duplicates, the four
// purchase-order screens, Sales Derive, the HR letter queue, Salary Advances and the rest. §54.7
// fixed the class for ⌘/ and left its sibling alone — one surface fixed, the other answering the
// identical question ("which pages exist, and may this viewer open them?") left unfixed. That is the
// patchwork shape the house rules forbid, and the 27 were it decaying in public.
//
// `viewerSources` is the fix: ONE assembly of a viewer's findable world, which both surfaces call,
// in the file that already owned the fold. The GATE (which registries a viewer sees at all) and the
// FOLD (one entry per destination) are each decided once, and `search-rank.rank` remains the one
// answer to "what did they mean" — so the two boxes cannot rank the same words differently or offer
// different destinations.
//
// THREE DIVERGENCES IT COLLAPSES, each measured before it was closed:
//   1. THE COMPLETENESS GAP — ⌘K indexed NAV only. 27 pages unfindable there, 0 unfindable in ⌘/.
//   2. THE NOT-ENFORCED BYPASS — the sidebar showed the full nav while login is not enforced
//      (`open`), the console did not. With an empty permissions payload ⌘K listed 322 destinations
//      and ⌘/ listed 2. One surface believed the app was open and the other did not.
//   3. THE TENANT LAYOUT — the sidebar applied `applyNavLayout` (a tenant's `hidden` rows), the
//      console read raw `TENANT_NAV`, so a page an admin had hidden stayed findable in one box. Zero
//      rows are hidden in any live tenant today, which is exactly why it had to be closed now: a
//      divergence nobody can see is the one that ships.
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
export type RouteSource = { path: string; label?: string; aliases?: string[] }[]
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

  const upsert = (href: string, patch: Partial<Searchable> & { kind: SearchKind }) => {
    const path = pathOf(href)
    if (!path) return
    const cur = byHref.get(path)
    if (!cur) {
      // A label is needed only to CREATE an entry — nothing can be found by a name it does not have.
      // A patch with no label still merges into an entry that already exists, which is how a page the
      // nav names (and which therefore declares no second label) still gains its typed-in aliases.
      if (!clean(patch.label)) return
      byHref.set(path, { key: `page:${path}`, kind: patch.kind, label: clean(patch.label!), href,
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

// ── THE GATE — which registries ONE viewer sees at all, decided once for both surfaces ───────────

/** The shape a nav entry and a report entry share — enough to gate one, nothing more. `NAV` and
 *  `REPORT_CATEGORIES` rows are passed straight in, so a field added to either cannot break this. */
export type ItemLike = { href: string; label: string; module: string; scopes?: readonly string[] }

/** The nav the viewer's own sidebar would render: already through RBAC and `applyNavLayout`, so a
 *  tenant's `hidden` row and its nicknames are honoured. BOTH surfaces pass this same thing now. */
export type NavReg = { group: string; items: ItemLike[] }[]
export type ReportReg = { category: string; reports: ItemLike[] }[]

export type Registries = {
  nav?: NavReg
  reports?: ReportReg
  screens?: ScreenSource
  /** Every page that exists, as `route-index.searchableRoutes()` emits it. */
  routes?: RouteSource
}

export type Viewer = {
  /** TRUE when login is not enforced, i.e. the app is open: the viewer may open everything, so the
   *  registries pass through ungated. This is what the sidebar has always done and the console never
   *  did — one place decides it now. */
  open?: boolean
  /** "May this viewer see this nav/report item" — `rbac.canSeeItem`, partially applied. */
  canSee: (it: ItemLike) => boolean
  /** "May this viewer open this path" — `rbac.canAccessPath`, partially applied. */
  canAccess: (path: string) => boolean
}

/** The entities only the console asks for. The sidebar is a destination jumper and passes none — a
 *  difference in what is SEARCHED, never in how it is gated or ranked. */
export type Entities = { stores?: StoreSource; people?: PersonSource }

/** Assemble the sources for ONE viewer, each registry through its own existing gate. Hand the result
 *  to `buildCatalog`, or call `viewerCatalog` to do both. */
export function viewerSources(reg: Registries, viewer: Viewer, ent: Entities = {}): CatalogSources {
  const canSee = (it: ItemLike) => !!viewer.open || viewer.canSee(it)
  const nav = (reg.nav || [])
    .map(g => ({ group: g.group, items: (g.items || []).filter(canSee) }))
    .filter(g => g.items.length > 0)
  const reports = (reg.reports || [])
    .map(c => ({ category: c.category, reports: (c.reports || []).filter(canSee) }))
    .filter(c => c.reports.length > 0)
  // SCREENS is the spellings registry (`ScreenLink`), and every one of its hrefs IS a nav href —
  // which is what makes it safe: an alias only ever lands on a destination the fold already holds,
  // or on nothing. A screen whose page the viewer may not open is dropped with it.
  const openable = new Set(nav.flatMap(g => g.items.map(it => pathOf(it.href))))
  const screens = (reg.screens || []).filter(sc => openable.has(pathOf(sc.href)))
  // The routes are gated by the one home for "may this viewer open this path" rather than by
  // `canSee`, because a page the nav does not list has no nav item to ask about — which is exactly
  // why the 27 menu-less pages were invisible to a NAV-only index.
  const routes = (reg.routes || []).filter(r => !!viewer.open || viewer.canAccess(pathOf(r.path)))
  return { nav, reports, screens, routes, stores: ent.stores, people: ent.people }
}

/** A viewer's whole findable catalogue — the gate, then the fold. The ONE call both surfaces make,
 *  so neither can assemble a different world. */
export function viewerCatalog(reg: Registries, viewer: Viewer, ent: Entities = {}): Searchable[] {
  return buildCatalog(viewerSources(reg, viewer, ent))
}

/** Destinations only — everything that can be OPENED, the entities dropped. The sidebar search jumps
 *  to a page, so it ranks this; it dereferences `entityOnly` rather than re-deciding what counts as
 *  a place, because "has no href" must mean the same thing in both boxes. */
export function destinations(items: Searchable[]): Searchable[] {
  return (items || []).filter(it => !entityOnly(it))
}

/** How many of each kind the catalogue holds — for the console's own "searching N things" line and
 *  for the proof, which asserts the fold rather than a total. */
export function catalogCounts(items: Searchable[]): Record<SearchKind, number> {
  const out: Record<SearchKind, number> = { page: 0, report: 0, setting: 0, store: 0, person: 0, customer: 0 }
  for (const it of items || []) out[it.kind] = (out[it.kind] || 0) + 1
  return out
}
