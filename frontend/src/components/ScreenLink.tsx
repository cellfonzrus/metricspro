'use client'
// ── ONE mechanism: when copy NAMES a screen, that name is a LINK ─────────────────────────────────
// Owner directive 2026-09-08: *"for dm verify it shows 'Asad Umar is still assigned as this store's
// closer but is no longer an employee — clear the assignment under Cash Setup.' — need to have a link
// for Cash setup if the option is presented for any menu"*.
//
// Telling somebody to "clear the assignment under Cash Setup" and leaving them to find Cash Setup is
// the defect. This file is the ONE way the fleet fixes it — there is deliberately no second one:
//
//   <ScreenLink to="cash_setup" />          inline link inside hand-written JSX copy
//   <LinkedText text={someNote} />          linkify a STRING (backend-authored notes included)
//   <Signpost to="sales_tax" />             the standalone button/affordance form
//
// WHY a frontend linkifier and not a structured `href` on every backend payload: a backend note is a
// sentence ("… clear the assignment under Cash Setup."), and the destination is already named inside
// it. Adding a parallel `href`/`link_label` field to every note-producing endpoint would (a) touch
// dozens of routers — several of which are owned by other agents right now — and (b) create a SECOND
// source of truth for "where does this screen live", which is exactly the drift the house rules
// forbid. The screen→href map already exists once, in `lib/rbac.ts` NAV; this reads it.
//
// RBAC — every link gates ITSELF, the same way `SalesTaxRateLink` always has: `canSeeItem` over the
// destination's own NAV entry, the SAME predicate the sidebar uses. A link can therefore never
// advertise a page its viewer would be bounced out of. Inline links degrade to PLAIN TEXT for such a
// viewer (deleting the words would maim the sentence); the standalone `Signpost` renders nothing.
//
// RULE TWO: nothing here branches on carrier/tenant/product. The registry is screen NAMES → the NAV
// hrefs that already exist.
import Link from 'next/link'
import { Fragment, useCallback, useMemo } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { useAuth } from '@/lib/auth-context'
import { NAV, canSeeItem } from '@/lib/rbac'

// ── THE REGISTRY ────────────────────────────────────────────────────────────────────────────────
// One row per destination that user-facing copy actually names. `href` MUST be (or start with) a NAV
// href — that is what makes the self-gate possible. `aliases` are the exact spellings prose uses,
// including breadcrumb forms; they are matched case-insensitively, longest-first.
export type ScreenKey =
  | 'cash_setup' | 'tender_setup' | 'store_matching' | 'roles_access' | 'sales_tax' | 'pos_settings'
  | 'employees' | 'store_setup' | 'sales_report_settings' | 'connectors' | 'vision_settings'
  | 'comm_onboarding' | 'menu_layout' | 'dashboard_designer' | 'target_settings'
  | 'org_structure' | 'billing_usage'

export type ScreenDest = {
  /** Where the reader is sent. May carry a #anchor; the gate uses the path half. */
  href: string
  /** How the link reads when copy did not supply its own words. */
  label: string
  /** Tooltip — what the reader is being sent there to DO. */
  blurb: string
  /** Spellings that appear in prose. Longest match wins. */
  aliases: string[]
}

export const SCREENS: Record<ScreenKey, ScreenDest> = {
  cash_setup: {
    href: '/closing/cash-config', label: 'Cash Setup',
    blurb: 'Cash Setup: closing deadline + gate, the assigned closer per store, alert recipients, cash-aging',
    aliases: ['Cash Setup', 'Closing → Cash Setup'],
  },
  tender_setup: {
    href: '/closing/tender-config', label: 'Tender Setup',
    blurb: 'Closing → Tender Setup: map each POS tender label onto a tender class so its dollars reconcile',
    aliases: ['Closing → Tender Config', 'Closing → Tender Setup', 'Tender Config', 'Tender Setup'],
  },
  store_matching: {
    href: '/commcalc/store-match', label: 'Store Matching',
    blurb: 'Store Matching: point a raw store name from a data feed at the store it really is',
    aliases: ['Store Matching', 'store-matching', 'Mapping → Store Matching'],
  },
  roles_access: {
    href: '/admin/roles', label: 'Roles & Access',
    blurb: 'Roles & Access: roles, per-module and per-report permissions, logins, Employee ID links',
    aliases: ['Roles & Access', 'Administration → Roles', 'Roles &amp; Access'],
  },
  sales_tax: {
    href: '/pos/settings#sales-tax', label: 'POS Settings → Sales Tax',
    blurb: 'POS Settings → Sales Tax: set a rate for one store, a whole market, or the company',
    aliases: ['POS Settings → Sales Tax'],
  },
  pos_settings: {
    href: '/pos/settings', label: 'POS Settings',
    blurb: 'POS Settings: register, tax and receipt configuration',
    aliases: ['POS Settings', 'POS Configuration'],
  },
  employees: {
    href: '/storeops/employees', label: 'Employees',
    blurb: 'Employees: the StoreOps roster — home store, Employee ID, active/inactive',
    aliases: ['Employees module', 'the Employees module'],
  },
  store_setup: {
    href: '/storeops/setup/stores', label: 'Store Setup',
    blurb: 'Store Setup: the store roster — code, address, market',
    aliases: ['Store Setup', 'Settings → Stores'],
  },
  sales_report_settings: {
    href: '/commcalc/sales-report', label: 'Sales Report',
    blurb: 'Sales Report: the ⚙ settings panel where classification / accessory definitions are set',
    aliases: [
      'Sales Report → ⚙️ Classification settings', 'Sales Report → ⚙ Classification settings',
      'Sales Report → Classification settings', 'Sales Report → Accessory settings',
      'Classification settings',
    ],
  },
  connectors: {
    href: '/commcalc/connectors', label: 'Connectors',
    blurb: 'Connectors: which feed supplies each data set, and whether it derives automatically',
    aliases: ['Connectors → Sales Transactions', 'Connectors'],
  },
  vision_settings: {
    href: '/vision/settings', label: 'Vision Settings',
    blurb: 'Vision Settings: turn the module on and configure cameras',
    aliases: ['Vision → Settings', 'Vision Settings'],
  },
  comm_onboarding: {
    href: '/commcalc/onboarding', label: 'Onboarding Wizard',
    blurb: 'Onboarding Wizard: add carriers and the rest of the tenant setup',
    aliases: ['Onboarding → Carrier', 'Onboarding Wizard'],
  },
  menu_layout: {
    href: '/admin/menu', label: 'Menu Layout',
    blurb: 'Menu Layout: rename, reorder and hide menu items',
    aliases: ['Menu Designer', 'Menu Layout'],
  },
  dashboard_designer: {
    href: '/admin/dashboards', label: 'Dashboard Designer',
    blurb: 'Dashboard Designer: arrange the tiles on a hub dashboard',
    aliases: ['Configuration → Dashboard Designer', 'Dashboard Designer'],
  },
  target_settings: {
    href: '/commcalc/targets/settings', label: 'Target Settings',
    blurb: 'Target Settings: set the monthly targets each store and rep is measured against',
    aliases: ['Target Settings'],
  },
  org_structure: {
    href: '/admin/org', label: 'Org Structure',
    blurb: 'Org Structure: the reporting tree — which unit each manager and employee sits in',
    aliases: ['Org Structure'],
  },
  billing_usage: {
    href: '/admin/billing-usage', label: 'Billing — Usage & Pricing',
    blurb: 'Billing — Usage & Pricing: your company\'s plan, metered usage and statements',
    aliases: ['Billing — Usage & Pricing', 'Billing Usage & Pricing'],
  },
  // NOT registered: "Metric Source of Truth". Copy on Cash Recon (Management) names it, but no such
  // page exists anywhere in NAV or under app/ — inventing an href would be worse than the gap. It is
  // reported to the owner as a named destination with nothing behind it.
}

/** The NAV href a destination gates against (strip #anchor and any query). */
function gateHref(href: string): string {
  return String(href || '').split('#')[0].split('?')[0]
}

// ── THE GATE ────────────────────────────────────────────────────────────────────────────────────
/**
 * Can this viewer open `href`? Mirrors the sidebar's gate exactly (`canSeeItem` over that page's own
 * NAV entry). RBAC applies only while login is enforced and a session exists — the open app shows
 * everything, exactly as the sidebar does. An href with no NAV entry is NOT linkable: there is no
 * menu to gate against, so we refuse rather than guess (that gap is a `rbac.ts` fix, not a runtime one).
 */
export function useCanOpen(): (href: string) => boolean {
  const { permissions, session, rbacEnabled } = useAuth()
  const byHref = useMemo(() => {
    const m = new Map<string, any>()
    for (const g of NAV) for (const it of g.items) if (!m.has(it.href)) m.set(it.href, it)
    return m
  }, [])
  return useCallback((href: string) => {
    const item = byHref.get(gateHref(href))
    if (!item) return false
    if (rbacEnabled === false || !session) return true
    return canSeeItem(permissions, item)
  }, [byHref, permissions, session, rbacEnabled])
}

/** Convenience for a single destination. */
export function useCanOpenScreen(to: ScreenKey): boolean {
  const can = useCanOpen()
  return can(SCREENS[to].href)
}

const LINK_STYLE: CSSProperties = { color: 'inherit', fontWeight: 700, textDecoration: 'underline' }

// ── INLINE LINK ─────────────────────────────────────────────────────────────────────────────────
/**
 * The screen's name, as a link. For a viewer who could not open it, the SAME words render as plain
 * text — the sentence still makes sense, and nobody is pointed at a 403.
 */
export default function ScreenLink(
  { to, children, style }: { to: ScreenKey; children?: ReactNode; style?: CSSProperties },
) {
  const dest = SCREENS[to]
  const allowed = useCanOpenScreen(to)
  const body = children ?? dest.label
  if (!allowed) return <b>{body}</b>
  return (
    <Link href={dest.href} title={dest.blurb} style={{ ...LINK_STYLE, ...style }}>{body}</Link>
  )
}

// ── STANDALONE AFFORDANCE ───────────────────────────────────────────────────────────────────────
/**
 * The button form (what `SalesTaxRateLink` has always been). Renders NOTHING for a viewer who may not
 * open the target — a standalone button is not part of a sentence, so hiding it loses nothing.
 */
export function Signpost(
  { to, label, title, icon, style }:
  { to: ScreenKey; label?: string; title?: string; icon?: string; style?: CSSProperties },
) {
  const dest = SCREENS[to]
  const allowed = useCanOpenScreen(to)
  if (!allowed) return null
  return (
    <Link href={dest.href} className="btn btn-secondary" title={title || dest.blurb}
      style={{ fontSize: 12, padding: '5px 10px', whiteSpace: 'nowrap', ...style }}>
      {icon ? `${icon} ` : ''}{label || dest.label}
    </Link>
  )
}

// ── LINKIFY A STRING ────────────────────────────────────────────────────────────────────────────
// The half that matters for BACKEND-authored prose. A backend note cannot carry JSX, and it should
// not have to: it already names the screen. This scans the sentence for a registered spelling and
// turns that spelling into a (self-gating) link, leaving every other character untouched.
type Alias = { key: ScreenKey; alias: string }
const ALIASES: Alias[] = Object.entries(SCREENS)
  .flatMap(([key, d]) => d.aliases.map(alias => ({ key: key as ScreenKey, alias })))
  // longest first, so "Closing → Tender Config" wins over "Tender Config"
  .sort((a, b) => b.alias.length - a.alias.length)

const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
// One pass, one regex. `\b` would fail against "→"/"&", so the boundary is done by hand: a match may
// not be preceded or followed by a word character (so "Employees module" inside a longer word is not
// hit, but "…under Cash Setup." is).
const ALIAS_RE = new RegExp(`(?<![A-Za-z0-9])(${ALIASES.map(a => esc(a.alias)).join('|')})(?![A-Za-z0-9])`, 'gi')
const KEY_BY_ALIAS = new Map<string, ScreenKey>(ALIASES.map(a => [a.alias.toLowerCase(), a.key]))

/** Split a string into plain runs and destination hits. Exported for the DB-free harness. */
export function splitScreenMentions(text: string): Array<{ text: string; screen?: ScreenKey }> {
  const src = String(text ?? '')
  if (!src) return []
  const out: Array<{ text: string; screen?: ScreenKey }> = []
  let last = 0
  ALIAS_RE.lastIndex = 0
  for (let m = ALIAS_RE.exec(src); m; m = ALIAS_RE.exec(src)) {
    const key = KEY_BY_ALIAS.get(m[1].toLowerCase())
    if (!key) continue
    if (m.index > last) out.push({ text: src.slice(last, m.index) })
    out.push({ text: m[1], screen: key })
    last = m.index + m[1].length
  }
  if (last < src.length) out.push({ text: src.slice(last) })
  return out
}

/**
 * Render a prose string with every named destination linked. Use this wherever a NOTE/hint/error
 * string produced elsewhere (the backend, a shared constant) is printed verbatim.
 */
export function LinkedText({ text, style }: { text?: string | null; style?: CSSProperties }) {
  const parts = useMemo(() => splitScreenMentions(text || ''), [text])
  return (
    <>
      {parts.map((p, i) => p.screen
        ? <ScreenLink key={i} to={p.screen} style={style}>{p.text}</ScreenLink>
        : <Fragment key={i}>{p.text}</Fragment>)}
    </>
  )
}
