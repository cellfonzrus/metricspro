// RBAC: nav definition (module-tagged) + access helpers. Permissions come from
// storeops.roles.permissions (resolved by the backend /core/me for the logged-in user).
export type Scope = 'all' | 'market' | 'store' | 'self'
// SCHEDULING reach — deliberately SEPARATE from `scope` (which is the REPORTING span). See
// backend app/core/scope.py for the full rationale. Short version: one store-grant set used to
// answer both "whose numbers may this person see?" (must be narrow) and "whom may this person put
// on a shift?" (must be wide, because employees move around), so an operator who wanted a DM to
// schedule a borrowed rep had to grant that DM every store — silently widening their reporting
// access too. 'org' (DEFAULT) = the employee roster/picker is span-exempt, which is what every
// scheduling surface already does today via /storeops/employees?all_company=true; 'span' = the old
// coupling, for tenants that want the roster locked to the reporting span.
export type SchedulingReach = 'org' | 'span'
export type Permissions = {
  modules?: Record<string, boolean>
  reports?: Record<string, boolean>   // per-AREA report access (separate from the operational module)
  data?: Record<string, boolean>      // per-KEY sensitive-data grants (e.g. carrier_residual) — see DATA_GRANTS
  pages?: Record<string, boolean>     // per-FUNCTION override (by nav href): explicit true/false wins over module
  scope?: Scope                       // REPORTING span (whose numbers) — NOT scheduling reach
  scheduling_reach?: SchedulingReach  // SCHEDULING reach (whom you may schedule); default 'org'
  home?: string
  impersonate?: boolean               // "Sign in as an employee" — DEFAULT-DENY, no bypass (see below)
}
// ── Admin "view as employee" (owner directive 2026-08-06) ────────────────────────────────────────
// MIRROR of backend `app.modules.core.impersonation_api.can_impersonate` — KEEP IN SYNC.
//
// This is the ONE permission in the product with NO bypass path. Deliberately:
//   • `isSuperAdmin` does NOT grant it — a platform super-admin still has to be given it;
//   • `scope: 'all'` does NOT grant it (unlike DATA_GRANTS / settings areas, which default-open for
//     company-wide roles);
//   • nothing seeds it onto any role, and seeded role modules are forward-only anyway.
// So on the day this ships, nobody can sign in as anybody until an administrator consciously ticks
// "Sign in as an employee" on a role at /admin/roles. Entering someone else's session is not the kind
// of capability that should arrive switched on because a role happened to be called "admin".
export function canImpersonate(perms: Permissions | undefined): boolean {
  return (perms as any)?.impersonate === true
}
// MIRROR of backend app/core/scope.scheduling_reach() — KEEP IN SYNC. Unknown/absent/garbage → 'org',
// which is byte-identical to today's behaviour for every existing role.
export function schedulingReach(perms: Permissions | undefined): SchedulingReach {
  const v = String((perms as any)?.scheduling_reach || '').trim().toLowerCase()
  return v === 'span' ? 'span' : 'org'
}
// True when a scheduling roster / employee-picker read may ignore the reporting span.
export function rosterSpanExempt(perms: Permissions | undefined): boolean {
  return schedulingReach(perms) === 'org'
}

// ── THE GRANT MODEL (owner rulings #5 / #6 / #7, 2026-08-08) ─────────────────────────────────────
// MIRROR of backend app/core/scope.py — KEEP IN SYNC.
//
// A person's REPORTING span comes from TWO independent grants that used to be fused into one
// undifferentiated set:
//
//   'market' — storeops.app_users.market   → every store in those markets
//   'store'  — storeops.app_users.store_code / .store_codes → those stores
//
// #6, verbatim: "if it is slected then it is granted of not then separate them and let the managers
// assign it as required". A market on a manager's record IS a market grant — it was selected, so it
// is granted, and nothing here strips it. What was wrong is that the two were welded: with one set
// coming out, nobody could see WHICH grant produced WHICH store. Live today: all 13 Luxelink
// `store_manager` logins (a scope-'store' role) also carry `market = Chicago`/`NY`, so each spans
// their whole market — 26 store codes against a store grant of 1. `grantWidening()` is what lets the
// Roles page SAY that out loud, so narrowing a person stays a deliberate click by the owner.
export type GrantKind = 'market' | 'store'
export const GRANT_KINDS: GrantKind[] = ['market', 'store']
export type GrantBreakdown = {
  market?: { granted?: string[]; codes?: string[]; unresolved?: string[]; per_market?: Record<string, string[]> }
  store?: { granted?: string[]; codes?: string[]; unresolved?: string[] }
  market_widens_beyond_store_scope?: boolean
  own_store?: string[]
  own_store_why?: string
}
// Does this person's MARKET grant reach past what their role's scope tier is for? Returns null when
// there is nothing to say. Advisory only — it never changes access, it explains it.
export function grantWidening(scope: Scope | undefined, g: GrantBreakdown | undefined):
  { markets: string[]; marketStores: number; ownStores: number } | null {
  if (!g) return null
  const s = scope || 'all'
  const markets = g.market?.granted || []
  const marketStores = (g.market?.codes || []).length
  if (!(s === 'store' || s === 'self') || !markets.length || !marketStores) return null
  return { markets, marketStores, ownStores: (g.store?.codes || []).length }
}
// A grant value that names nothing real. Ruling #5: these are shown, never silently honoured —
// live examples are `Floating`, `3738 26th Street` and the `15` fragment in `market = "15, NYC, LI"`.
export function deadGrants(g: GrantBreakdown | undefined): string[] {
  return [...(g?.market?.unresolved || []), ...(g?.store?.unresolved || [])]
}

// ── #7 "they shoudl see their own store" — the ADOPTION REGISTRY, not a widening ─────────────────
// A scope-'self' person resolves to an EMPTY store keyset, which every span-filtered read treats as
// deny-all. Ruling #7 says a rep must resolve to their OWN store instead. That resolution is
// deliberately OPT-IN PER SURFACE on the backend (`reporting_span_codes(..., self_own_store=True)`),
// because ~54 reads share the same primitive and a global flip would hand every rep the payroll,
// hours and colleagues' commission at their store.
//
// THE PAIRED RULE, and it is not optional: a surface listed here shows STORE-LEVEL data. If it also
// carries a per-employee pay / commission / compensation / PII column, that column is filtered to
// the caller's own employee_id (`self_employee_ids()`), so a rep sees their own row and nobody
// else's. Adding a surface to this list without that filter is a payroll leak between colleagues.
export const SELF_OWN_STORE_SURFACES: { path: string; note: string }[] = [
  { path: '/commcalc/targets/my', note: 'My Targets — store-level target vs achieved for the rep\'s own store. Per-rep rows are their own only.' },
  { path: '/commcalc/commissions', note: 'My Commission — already self-bypassed by rep name; the rep\'s own rows only.' },
]
export function selfSurfaceAdopted(path: string): boolean {
  return SELF_OWN_STORE_SURFACES.some(s => path === s.path || path.startsWith(s.path + '/'))
}

// Report (analytical) pages are gated by a per-area `reports` permission that is SEPARATE from the
// operational module, so e.g. a market manager can run schedules/targets but see no reports.
// Operational pages (targets entry, schedule, My Team, time clock…) are NOT listed here.
export const REPORT_AREAS: { key: string; label: string }[] = [
  { key: 'commissions', label: 'Commission reports' },
  { key: 'asset', label: 'Asset reports' },
  { key: 'vip', label: 'Distributor reports' },
  { key: 'accounts', label: 'Accounting reports (P&L / BS)' },
  { key: 'storeops', label: 'StoreOps reports (hours / payroll)' },
  { key: 'closing', label: 'Daily Closing reports' },
]
// Exact report pages (a bare path that is a report but whose tree also holds non-reports).
const REPORT_EXACT: Record<string, string> = { '/commcalc': 'commissions', '/reports': '*' }
// Report page TREES (prefix → area), boundary-matched; longest prefix wins.
const REPORT_TREES: [string, string][] = [
  ['/commcalc/exec', 'commissions'], ['/commcalc/reports', 'commissions'], ['/commcalc/gp', 'commissions'],
  ['/commcalc/coaching', 'commissions'], ['/commcalc/sales-analyzer', 'commissions'],
  ['/commcalc/sales-comparison', 'commissions'],
  ['/commcalc/comp-trend', 'commissions'], ['/commcalc/flags', 'commissions'], ['/commcalc/chargebacks', 'commissions'],
  ['/commcalc/discrepancy', 'commissions'], ['/commcalc/sales-recon', 'commissions'],
  ['/commcalc/asset', 'asset'], ['/commcalc/vip', 'vip'], ['/accounts', 'accounts'],
  ['/storeops/reports', 'storeops'], ['/storeops/reviews', 'storeops'],
  ['/storeops/payroll', 'storeops'], ['/storeops/payroll-tax', 'storeops'],
  ['/closing/recon', 'closing'],
]
// The report area for a path, or null if it's an operational (non-report) page.
export function reportAreaForPath(path: string): string | null {
  if (REPORT_EXACT[path]) return REPORT_EXACT[path]
  let best: string | null = null, bestLen = -1
  for (const [pre, area] of REPORT_TREES) {
    if ((path === pre || path.startsWith(pre + '/')) && pre.length > bestLen) { best = area; bestLen = pre.length }
  }
  return best
}
// May this user see report area `area`? Explicit `reports` config wins; otherwise default by scope —
// company-wide ('all') leadership keeps reports, everyone else (market/store/self) gets none. So a
// market manager has NO default report access, while admins/execs keep theirs, with no re-seeding.
export function hasReport(perms: Permissions, area: string): boolean {
  if (isSuperAdmin(perms)) return true
  const r = perms.reports
  if (r && Object.keys(r).length) {
    return area === '*' ? Object.values(r).some(Boolean) : !!r[area]
  }
  return (perms.scope || 'all') === 'all'
}

// ── Sensitive-data grants (separate from module/report access) ────────────────────────────────────
// Some data is carrier/finance-sensitive and gated by its OWN per-key grant, independent of whether a
// role can open the operational module. `carrier_residual` is the first: the raw_mi-derived carrier
// residual reports (backend commcalc `_can_view_carrier_residual`, commission-0 P1) are visible to
// everyone by default (tenant setting residual_visibility='all'), but a tenant that flips it to
// 'permissioned' requires this grant. Company-wide leadership (scope 'all') and admins always pass;
// grant it here to let a scoped manager see it too. Register a new sensitive-data key by adding a row
// AND gating the matching backend read (same contract as REPORT_AREAS + settings).
export const DATA_GRANTS: { key: string; label: string; help?: string }[] = [
  { key: 'carrier_residual', label: 'Carrier residual (raw carrier data)',
    help: 'Raw carrier/processor residual reports (raw_mi-derived). Only enforced when the tenant sets residual visibility to "permissioned".' },
  { key: 'device_commission', label: 'Device history commission amounts',
    help: 'Per-period commission & rebate $ on the Device History Lookup (backend commcalc `_can_view_device_commission`). DEFAULT-CLOSED — admin-only until granted; the device history / prompts / tenure stay visible to everyone regardless.' },
  { key: 'imei_rebates', label: 'IMEI rebate reconciliation report',
    help: 'Access to /commcalc/imei-rebates — the whole report, not just the $ (backend commcalc `_can_view_imei_rebates`). DEFAULT-CLOSED — admin-only until granted; money columns additionally ride the carrier-residual gate when the tenant sets residual visibility to "permissioned".' },
  { key: 'residual_per_sub', label: 'Residual per Subscriber report',
    help: 'Access to /accounts/residual-per-sub. DEFAULT-CLOSED — admin-only until granted.' },
  { key: 'account_trends', label: 'Trends report (all metrics)',
    help: 'Access to /accounts/trends. DEFAULT-CLOSED — admin-only until granted.' },
  { key: 'ma_handset_cogs', label: 'Marketplace handset COGS report',
    help: 'Access to /commcalc/ma-handsets — the whole report (lines, quantities and handset costs), not just the totals (backend commcalc `_can_view_ma_handset_cogs`). DEFAULT-CLOSED — admin-only until granted.' },
  { key: 'device_cost_recon', label: 'Device cost reconciliation',
    help: 'Access to /commcalc/device-cost-recon — every device-cost row from all four sources at once (marketplace purchase price, consignment/VIP billing, POS-derived cost, inventory unit cost) plus the policy delta preview (backend commcalc `_can_view_device_cost_recon`). DEFAULT-CLOSED — admin-only until granted. Strictly MORE sensitive than the per-source reports it reconciles.' },
  { key: 'whatif_employee_payout', label: 'What-If — Employee Payout scenario',
    help: 'The 🎯 Employee Payout tab of /commcalc/whatif: the per-carrier payout template (Boost components or the carrier\'s Commission-Plan rules/tiers) and the projector. DEFAULT-CLOSED — admin-only until granted.' },
  { key: 'whatif_byod_residual', label: 'What-If — BYOD → Residuals',
    help: 'The 📶 BYOD → Residuals tab of /commcalc/whatif. DEFAULT-CLOSED — admin-only until granted; the raw carrier-residual money additionally rides "carrier_residual" when the tenant sets residual visibility to "permissioned".' },
  { key: 'whatif_accessory_corr', label: 'What-If — Accessories ↔ BYOD ↔ Revenue',
    help: 'The 🔗 correlation tab of /commcalc/whatif (per store/period BYOD activations vs accessory revenue vs total revenue). DEFAULT-CLOSED — admin-only until granted.' },
  { key: 'customer_360', label: 'Customer lookup (full customer history by phone)',
    help: 'Access to /crm/lookup — type a phone number and see everything we know about that customer: purchases, devices, plan, tickets and CRM history (backend crm `customer_360_allowed`). DEFAULT-CLOSED — admin / company-wide only until granted. A tenant can open it to everyone by turning OFF "lookup requires permission" in CRM Settings. Every lookup is written to the audit trail either way.' },
  { key: 'customer_360_financial', label: 'Customer lookup — money columns',
    help: 'The $ inside a customer lookup: margin, cost, extended price, lifetime value (backend crm `customer_360_financial_allowed`). DEFAULT-CLOSED with NO tenant toggle. Without it the lookup still shows what/when/where/who-sold-it — the money is listed as withheld, never shown as zero.' },
  { key: 'whatif_carrier_income', label: 'What-If — Company Payout / Carrier Income',
    help: 'The 💵 Company Payout / Carrier Income tab of /commcalc/whatif — what the carrier / master-agent pays the COMPANY. DEFAULT-CLOSED — admin-only until granted; also rides "carrier_residual" when the tenant sets residual visibility to "permissioned".' },
]
// Frontend mirror of backend commcalc `_can_view_carrier_residual` — KEEP IN SYNC. Super-admins /
// company-wide ('all') roles / admins always pass; otherwise the grant is honored under either the
// `data` bucket (what the roles UI writes) or a `modules` key of the same name (backend also accepts it).
export function hasDataGrant(perms: Permissions, key: string): boolean {
  if (isSuperAdmin(perms)) return true
  if ((perms.scope || 'all') === 'all') return true
  if (perms.modules?.[key]) return true
  if (perms.data?.[key]) return true
  return false
}

// `cap` (optional) is a tenant CAPABILITY gate, separate from RBAC: the sidebar hides the item only when
// the tenant's capability is explicitly false (e.g. asset_lending=false → no consignment distributor).
// Unknown/true → shown, so it never hides anything by default. RBAC (module/scopes) still applies first.
export type NavItem = { href: string; label: string; icon: string; module: string; scopes?: Scope[]; cap?: string }
// A named sub-category INSIDE a group (owner directive 2026-08-12 — roadmap #5). Sub-groups are a
// LAYOUT-level concept only: the built-in NAV literal below stays structurally two-level, so a
// newly-shipped item still lands in its group with no code change and no tenant re-configuration.
// INVARIANT: `NavGroup.items` always carries EVERY item in the group, including the ones a sub claims.
// The search index and the active-group detection in (platform)/layout.tsx both read `.items`, so
// widening it would change what they see. `subs` is therefore an ADDITIONAL view over the same objects
// and the renderer treats anything no sub claims as loose (rendered directly under the group header).
export type NavSub = { name: string; items: NavItem[] }
export type NavGroup = { group: string; module: string; items: NavItem[]; subs?: NavSub[] }

// scopes (when present) further restricts an item to those scope tiers, e.g. settings = admin only.
// ── NAV taxonomy (reorganized 2026-06-28) ──────────────────────────────────────────────
// Grouping is purely an information-architecture concern: a group renders if ANY of its
// items passes canSeeItem(), which keys on item.module — so regrouping/relabeling here is a
// ZERO-RBAC-CHANGE operation as long as each item keeps its existing `module` + `scopes`.
// group.module is a representative tag only (not a gate). The old single /commcalc menu was a
// catch-all spanning Commissions / Finance / Assets / VIP / Targets / Integrations; it's split
// into those real domains below. Pages keep their URLs (a deeper re-home is a separate phase).
export const NAV: NavGroup[] = [
  // ── ORDER (owner directive 2026-08-10): Point of Sale FIRST, Reports LAST. Group order in this
  // array IS the sidebar order — applyNavLayout() walks `defaultOrder` (this array) before appending
  // any tenant-created or Reports-directory groups, so moving a block here moves it in the sidebar.
  // POS module (mig 724/725) — the point-of-sale port (Phase 1: register, customers, inventory,
  // settings). Activations/vendors/POs/reports arrive in Phase 2; see pos-system INTEGRATION_PLAN.md.
  { group: 'Point of Sale', module: 'pos', items: [
    // Setup wizard (mig 733, owner directive 2026-08-09). FIRST in the group deliberately: a tenant
    // whose POS is not configured is redirected here by (platform)/pos/layout.tsx, and this entry is
    // how they get BACK to it afterwards. Scoped 'all' + 'market' — a store-scoped cashier is not the
    // person who defines the tenant's departments and tax rates.
    { href: '/pos/onboarding', label: 'Setup Wizard', icon: '🛠️', module: 'pos', scopes: ['all', 'market'] },
    { href: '/pos/sales', label: 'Register', icon: '🛒', module: 'pos', scopes: ['all', 'market', 'store'] },
    { href: '/pos/customers', label: 'Customers', icon: '👤', module: 'pos', scopes: ['all', 'market', 'store'] },
    { href: '/pos/inventory', label: 'Inventory', icon: '📦', module: 'pos', scopes: ['all', 'market', 'store'] },
    { href: '/pos/products', label: 'Products & Services', icon: '🏷️', module: 'pos', scopes: ['all', 'market', 'store'] },
    { href: '/pos/activations', label: 'Activations', icon: '📱', module: 'pos', scopes: ['all', 'market', 'store'] },
    { href: '/pos/vendors', label: 'Vendors', icon: '🏭', module: 'pos', scopes: ['all', 'market'] },
    { href: '/pos/reports', label: 'POS Reports', icon: '📈', module: 'pos', scopes: ['all', 'market'] },
    { href: '/pos/import', label: 'Import', icon: '📥', module: 'pos', scopes: ['all'] },
    { href: '/pos/settings', label: 'POS Settings', icon: '⚙️', module: 'pos', scopes: ['all', 'market'] },
  ]},
  // CRM (mig 800, owner directive 2026-08-12) — the sales pipeline + follow-up system + the
  // phone-number Customer 360. Placed directly after Point of Sale: it is the surface a rep touches
  // BEFORE a sale exists, and the register is where it ends up.
  // 'Customer Lookup' carries no extra nav scope on purpose — the real gate is the server-side
  // `customer_360` data grant (default-closed), so widening the nav here cannot widen access.
  { group: 'CRM', module: 'crm', items: [
    { href: '/crm', label: 'Dashboard', icon: '🎯', module: 'crm', scopes: ['all', 'market', 'store'] },
    { href: '/crm/my-followups', label: 'My Follow-ups', icon: '🔔', module: 'crm' },
    { href: '/crm/leads', label: 'Leads', icon: '📇', module: 'crm', scopes: ['all', 'market', 'store'] },
    { href: '/crm/leads/new', label: 'Log a Lead', icon: '➕', module: 'crm' },
    { href: '/crm/pipeline', label: 'Pipeline Board', icon: '🗂️', module: 'crm', scopes: ['all', 'market', 'store'] },
    { href: '/crm/lookup', label: 'Customer Lookup', icon: '🔎', module: 'crm' },
    { href: '/crm/agencies', label: 'Outside Agencies', icon: '🤝', module: 'crm', scopes: ['all', 'market'] },
    { href: '/crm/reports', label: 'CRM Reports', icon: '📈', module: 'crm', scopes: ['all', 'market'] },
    { href: '/crm/settings', label: 'CRM Settings', icon: '⚙️', module: 'crm', scopes: ['all'] },
  ]},
  // Referral (mig 850, owner directive 2026-08-13) — QR-code customer referrals + activation-gated,
  // approval-gated commission. Placed after CRM: it is a sibling top-of-funnel surface (a rep hands a
  // referrer a QR before any sale exists). Same shape as the CRM block, so regrouping/relabeling is a
  // ZERO-RBAC-CHANGE move as long as each item keeps its `module: 'referral'` + scopes.
  // Approvals is scoped ['all','market']: approving a payout is a manager act, and the backend
  // (_can_approve) is the real gate — the nav scope only decides who sees the tab. Settings is 'all'
  // only, like every other module's config surface.
  { group: 'Referral', module: 'referral', items: [
    { href: '/referral', label: 'Dashboard', icon: '🎁', module: 'referral', scopes: ['all', 'market', 'store'] },
    { href: '/referral/new', label: 'New Referral', icon: '➕', module: 'referral', scopes: ['all', 'market', 'store'] },
    { href: '/referral/list', label: 'Referrals', icon: '📇', module: 'referral', scopes: ['all', 'market', 'store'] },
    { href: '/referral/approvals', label: 'Approvals', icon: '✅', module: 'referral', scopes: ['all', 'market'] },
    { href: '/referral/settings', label: 'Referral Settings', icon: '⚙️', module: 'referral', scopes: ['all'] },
  ]},
  { group: 'Commissions', module: 'commissions', items: [
      { href: '/commcalc/pay-simulator', label: 'What Would I Make?', icon: '🎚️', module: 'commissions' },
    { href: '/commcalc', label: 'Dashboard', icon: '📊', module: 'commissions' },
    { href: '/commcalc/sales-report', label: 'Sales Report', icon: '🧾', module: 'commissions' },
    { href: '/commcalc/sales-comparison', label: 'Sales Comparison', icon: '📈', module: 'commissions' },
    { href: '/commcalc/custom-report', label: 'Custom Report', icon: '🧩', module: 'commissions' },
    { href: '/commcalc/exec', label: 'Owner Overview', icon: '🏆', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/exec/mtd', label: 'Executive MTD', icon: '📅', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/reports', label: 'Rep Commission Report', icon: '📋', module: 'commissions' },
    { href: '/commcalc/kpi', label: 'KPI Metrics', icon: '🎯', module: 'commissions' },
    { href: '/commcalc/device-history', label: 'Device History', icon: '📱', module: 'commissions' },
    { href: '/commcalc/ma-handsets', label: 'Handset COGS', icon: '📦', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/device-cost-recon', label: 'Device Cost Recon', icon: '🧮', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/productivity', label: 'Productivity & Reviews', icon: '🏅', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/coaching', label: 'Rep Coaching', icon: '🎓', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/sales-analyzer', label: 'Sales Analyzer', icon: '📉', module: 'commissions', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/whatif', label: 'What‑If Analysis', icon: '🔮', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/comp-trend', label: 'Total Compensation', icon: '📡', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/commission-ledger', label: 'Commission Ledger', icon: '🧾', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/ma-commission', label: 'Total Processor', icon: '📡', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/ma-overview-recon', label: 'MA Overview cross-check', icon: '🧾', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/commission-legs', label: 'Commission Legs (M1 / M2–M12)', icon: '🧩', module: 'commissions', scopes: ['all'] },
    // DM GATE — OWNER DIRECTIVE 2026-08-07, verbatim: "all flags need to be fed thru the dm, so yes
    // route it thru the dm and then visible to the scoped user." Flags now carries the SAME scope gate
    // its Chargebacks & Fraud sibling below has always had (admin 'all' + DM 'market'), so a flag
    // reaches a store/self-scoped user THROUGH their DM instead of straight off the raw list. Until
    // now Flags was the only review-queue row in this group with no `scopes`, and its only backstop
    // was the soft `commissions` REPORT-AREA gate — which an admin defeats the moment they tick
    // "Commission reports" for a store role, silently handing that role the un-reviewed queue.
    // Shape byte-identical to the chargebacks row: an EXACT per-function grant still lifts it, exactly
    // as it lifts chargebacks (that override is an admin's deliberate act, not the default posture).
    { href: '/commcalc/flags', label: 'Flags', icon: '🚩', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/chargebacks', label: 'Chargebacks & Fraud', icon: '🔻', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/accessory-flags', label: 'Accessory Flags', icon: '🔖', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/accessory-cost-audit', label: 'Accessory Cost Audit', icon: '🧾', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/expected-commission', label: 'Expected vs Earned', icon: '⏳', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/daily-commission', label: 'Daily Commission', icon: '📅', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/training', label: 'Training Center', icon: '🎓', module: 'targets' },
    { href: '/commcalc/discrepancy', label: 'Pay Discrepancy', icon: '⚠️', module: 'commissions' },
    { href: '/commcalc/imei-rebates', label: 'IMEI Rebates', icon: '🔁', module: 'commissions' },
    { href: '/commcalc/recovery', label: 'Appeal Recovery', icon: '💰', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/sales-recon', label: 'Sales Feed Recon', icon: '🔁', module: 'commissions', scopes: ['all', 'market'] },
    // Agency (Master/Sub-Agent) console — config + billing, admin/owner scope only (NEEDS CORE for
    // agency-phase1). Intentionally NOT in REPORT_DIRECTORY: it is a config+invoicing surface, not a report.
    { href: '/commcalc/agency', label: 'Agency', icon: '🏢', module: 'commissions', scopes: ['all'] },
  ]},
  // ── Commission Payout Plans ────────────────────────────────────────────────────────────────
  // ONE home for HOW reps get paid, per carrier. 'Overview' maps each enabled carrier to the engine
  // that actually pays it (Boost KPI-tier rates vs configurable Commission Plans / Payout Schedules).
  // Boost Rates is carrier-gated to Boost tenants (NAV_CARRIERS) so a Total-only tenant never sees the
  // hardcoded Boost tiers. Regroup is a ZERO-RBAC-CHANGE move — every item keeps its module + scopes.
  { group: 'Commission Payout Plans', module: 'commissions', items: [
    { href: '/commcalc/payout-plans', label: 'Overview', icon: '💳', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/commission-plans', label: 'Commission Plans', icon: '🧮', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/plan-installments', label: 'Multi‑Month Installments', icon: '🗓️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/payout-schedules', label: 'Payout Schedules', icon: '📆', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/settings', label: 'Boost Rates (KPI‑tier)', icon: '⚙️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/carrier-mapping', label: 'Carrier Mapping', icon: '📡', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/commission-category-map', label: 'Category → Bucket Map', icon: '🗺️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/ma-product-class', label: 'MA Product Name Classification', icon: '🏷️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/accessory-definition', label: 'Accessory Definition', icon: '🎧', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/commission-import', label: 'Import Wizard', icon: '🪄', module: 'commissions', scopes: ['all'] },
  ]},
  { group: 'Targets & Coaching', module: 'targets', items: [
    { href: '/commcalc/targets', label: 'Daily Targets', icon: '📈', module: 'targets', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/financing', label: 'Financing', icon: '💳', module: 'commissions', scopes: ['all', 'market', 'store'] },
    // Autopay opportunity (owner 2026-08-12). Sits in Targets & Coaching, not Commissions: it states
    // revenue NOT collected and is a thing a rep is COACHED to fix, not a payout anyone is owed.
    { href: '/commcalc/atu-opportunity', label: 'Autopay Opportunity', icon: '🔁', module: 'targets', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/targets/action-plan', label: 'Action Plan', icon: '✅', module: 'targets', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/targets/accessories', label: 'Accessory Targets', icon: '🔖', module: 'targets', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/targets/rep-map', label: 'Rep → Store Map', icon: '🗺️', module: 'targets', scopes: ['all', 'market'] },
    { href: '/commcalc/targets/settings', label: 'Target Settings', icon: '🎚️', module: 'targets', scopes: ['all'] },
    { href: '/commcalc/targets/my', label: 'My Targets', icon: '🙋', module: 'targets' },
    { href: '/employee', label: 'Employee Dashboard', icon: '🧑‍💼', module: 'targets' },
  ]},
  { group: 'Finance', module: 'accounts', items: [
    { href: '/accounts', label: 'Dashboard', icon: '💼', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/trends', label: 'Trends', icon: '📊', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/pl', label: 'P&L Statement', icon: '📈', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/commcalc/gp', label: 'Gross Profit', icon: '💰', module: 'commissions' },
    { href: '/commcalc/expenses', label: 'Store Expenses', icon: '🏪', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/tax-collected', label: 'Tax Collected', icon: '🧾', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/accounts/balance-sheet', label: 'Balance Sheet', icon: '⚖️', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/inventory', label: 'Inventory Values', icon: '📦', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/recon', label: 'Reconciliation', icon: '🔎', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/residual-per-sub', label: 'Residual per Subscriber', icon: '📉', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/journal', label: 'Journal', icon: '📒', module: 'accounts', scopes: ['all'] },
    { href: '/accounts/companies', label: 'Companies', icon: '🏢', module: 'accounts', scopes: ['all'] },
  ]},
  { group: 'Assets', module: 'asset', items: [
    { href: '/commcalc/asset', label: 'Asset Ledger', icon: '📦', module: 'asset' },
    // MA / VidaPay marketplace-purchase orders (mig 207). First-class nav entry per mod-asset NEEDS CORE
    // [asset-10] — was reachable only via a button on the VIP-styled landing. carrier-gated to Total in
    // NAV_CARRIERS (the one asset page that applies to luxelink/Total, and only to them).
    { href: '/commcalc/asset/marketplace-purchases', label: 'Marketplace Purchases', icon: '🛒', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/dashboard', label: 'Charges Dashboard', icon: '📊', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/owed-weekly', label: 'Weekly Owed-to-Distributor', icon: '📅', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/aging', label: 'Inventory Aging', icon: '⏳', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/missing-phones', label: 'Missing Phones', icon: '📵', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/aging-rebate', label: 'Aging · Rebate Received', icon: '💵', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/on-inventory', label: 'On-Inventory by Store', icon: '🏬', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/payables', label: 'Forecasting & Vendor Payables', icon: '📱', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/borrowed', label: 'Borrowed / Lending', icon: '🔁', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/lending', label: 'Asset Lending (PayGo)', icon: '📲', module: 'asset', scopes: ['all', 'market'], cap: 'asset_lending' },
    { href: '/commcalc/asset/charges/rma', label: 'RMA', icon: '↩️', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/inventory-recon', label: 'Inventory Recon', icon: '🔎', module: 'asset', scopes: ['all', 'market'] },
    // Purchase Orders (mig 301) — proposed PO → receiving → sold tally → unsold aging. First-class nav
    // entry per mod-asset NEEDS CORE [asset-11] (was reachable only via a button on /commcalc/asset).
    // Carrier-NEUTRAL by design (buying/receiving is universal) → deliberately NOT in NAV_CARRIERS.
    // scopes ['all','market'] mirrors every sibling asset item: the PO endpoints are ORG-scoped, not
    // store-scoped, so a store-scoped user would see other stores' POs. If store-level receiving is
    // wanted, mod-asset should store-scope the reads first and then file for the scope widening.
    { href: '/commcalc/asset/purchase-orders', label: 'Purchase Orders', icon: '📦', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/hotsheet-recon', label: 'Pricing Hotsheet', icon: '🏷️', module: 'commissions', scopes: ['all', 'market'] },
  ]},
  { group: 'Distributors', module: 'vip', items: [
    { href: '/commcalc/distributors', label: 'Distributors', icon: '🏬', module: 'vip', scopes: ['all', 'market'] },
    { href: '/commcalc/vip', label: 'Distributor · Invoices', icon: '🧾', module: 'vip' },
    { href: '/commcalc/vip/paygo', label: 'Distributor · PayGo / Asset Lending', icon: '📲', module: 'vip', scopes: ['all', 'market'], cap: 'asset_lending' },
    { href: '/commcalc/vip/sweep', label: 'Distributor · Sweep', icon: '🧹', module: 'vip', scopes: ['all'] },
  ]},
  { group: 'Workforce', module: 'storeops', items: [
    { href: '/storeops', label: 'Dashboard', icon: '🏠', module: 'storeops' },
    { href: '/storeops/schedule', label: 'Schedule', icon: '📅', module: 'storeops' },
    { href: '/storeops/timeoff', label: 'Time Off', icon: '🌴', module: 'storeops' },
    { href: '/storeops/swaps', label: 'Shift Swaps', icon: '🔄', module: 'storeops' },
    { href: '/storeops/shift-extensions', label: 'Shift Extensions', icon: '⏱️', module: 'storeops', scopes: ['all', 'market', 'store'] },
    { href: '/storeops/timeclock-permissions', label: 'Time-clock Permissions', icon: '⏳', module: 'storeops', scopes: ['all', 'market', 'store'] },
    { href: '/storeops/hours-budget', label: 'Hours Budget', icon: '📊', module: 'storeops', scopes: ['all', 'market', 'store'] },
    { href: '/storeops/timeclock', label: 'Time Clock', icon: '⏱️', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/attendance', label: 'Attendance Exceptions', icon: '🚨', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/employees', label: 'Employees', icon: '👥', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/team', label: 'My Team', icon: '🫂', module: 'storeops', scopes: ['all', 'market', 'store'] },
    { href: '/storeops/visits', label: 'Store Visits', icon: '📝', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/visits/settings', label: 'Visit Checklist', icon: '🧾', module: 'storeops', scopes: ['all'] },
    { href: '/storeops/reviews', label: 'Google Reviews', icon: '⭐', module: 'storeops', scopes: ['all', 'market', 'store'] },
    { href: '/storeops/reviews/config', label: 'Reviews Setup', icon: '⚙️', module: 'storeops', scopes: ['all'] },
    { href: '/storeops/reports', label: 'Reports', icon: '📋', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/admin', label: 'Admin', icon: '🛠️', module: 'storeops', scopes: ['all', 'market'] },
  ]},
  { group: 'Payroll & HR', module: 'storeops', items: [
    { href: '/hr/people', label: 'People (add employees)', icon: '🧑‍💼', module: 'hr', scopes: ['all', 'market'] },
    { href: '/hr/onboarding', label: 'Onboarding Checklist', icon: '🧩', module: 'hr', scopes: ['all', 'market'] },
    { href: '/hr/compliance', label: 'Compliance', icon: '📋', module: 'hr', scopes: ['all', 'market'] },
    { href: '/hr/employee-database', label: 'Employee Database', icon: '🗄️', module: 'hr', scopes: ['all', 'market'] },
    { href: '/hr/letters', label: 'HR Communications', icon: '✉️', module: 'hr', scopes: ['all', 'market'] },
    { href: '/hr', label: 'HR · Total Comp', icon: '📊', module: 'hr', scopes: ['all', 'market'] },
    { href: '/hr/payroll-expenses', label: 'Payroll Expenses', icon: '💼', module: 'hr', scopes: ['all', 'market'] },
    { href: '/storeops/payroll', label: 'Payroll', icon: '💵', module: 'storeops', scopes: ['all', 'market'] },
    // Weekly hours approval (mig 431, owner directive 2026-08-10). Scoped 'all' + 'market' + 'store':
    // the DM ('market') is the FIRST gate and must reach it, and a store manager standing in for an
    // absent DM sees only their own span anyway (the read is span-scoped server-side). Same shape as
    // its Payroll sibling, so it adds no permission surface an existing role did not already have.
    { href: '/storeops/payroll/approvals', label: 'Hours Approval', icon: '✅', module: 'storeops', scopes: ['all', 'market', 'store'] },
    // Payer registry — admin-only config ('all'), like every other "who receives money" setting.
    { href: '/storeops/payroll/payers', label: 'Who Pays Payroll', icon: '🏦', module: 'storeops', scopes: ['all'] },
    { href: '/storeops/payroll-tax', label: 'Payroll (Tax)', icon: '🧾', module: 'storeops', scopes: ['all', 'market'] },
  ]},
  { group: 'Daily Closing', module: 'closing', items: [
    { href: '/closing', label: 'Dashboard', icon: '🧾', module: 'closing', scopes: ['all', 'market', 'store'] },
    { href: '/closing/submit', label: 'Submit Closing', icon: '➕', module: 'closing' },
    { href: '/closing/verify', label: 'DM Verify', icon: '✅', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/management', label: 'Management Review', icon: '🛡️', module: 'closing' },
    { href: '/closing/recon', label: 'Reconciliation', icon: '🔎', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/tender-recon', label: 'X-Tender Recon', icon: '🧾', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/tender-recon-3way', label: '3-Way Tender Recon', icon: '🧮', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/accessory-recon', label: 'Accessory Recon', icon: '🔖', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/pickup', label: 'Cash Pickup', icon: '💵', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/envelope-payout', label: 'Envelope Payouts', icon: '💸', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/store-cash-on-hand', label: 'Store Cash on Hand', icon: '🏦', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/expenses-report', label: 'Closing Expenses', icon: '📋', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/epay-recon', label: 'ePay Bank-Deposit Recon', icon: '🏦', module: 'closing', scopes: ['all', 'market'] },
    // Cash Deposit Recon + Deposit Categories (mig 509) — nav entries per mod-retail-ops NEEDS CORE
    // 2026-08-05 (both pages were reachable only via direct links on the Closing dashboard / ePay recon).
    // Same `module: 'closing'` key + scope tiers as every sibling above, so NO role re-seeding is needed
    // ([[seeded-role-modules-forward-only]]): any role that already has the closing module sees them, and
    // neither href falls under a REPORT_TREES prefix (`/closing/recon` boundary-matches only itself), so
    // there is no extra report-area gate — identical gating to Closing Expenses / Expense Categories.
    { href: '/closing/deposit-recon', label: 'Cash Deposit Recon', icon: '💵', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/cash-config', label: 'Cash Setup', icon: '⚙️', module: 'closing', scopes: ['all'] },
    { href: '/closing/tender-config', label: 'Tender Setup', icon: '🧾', module: 'closing', scopes: ['all'] },
    { href: '/closing/expense-categories', label: 'Expense Categories', icon: '🗂️', module: 'closing', scopes: ['all'] },
    { href: '/closing/deposit-categories', label: 'Deposit Categories', icon: '🗂️', module: 'closing', scopes: ['all'] },
    { href: '/closing/envelope-config', label: 'Envelope Payout Setup', icon: '⚙️', module: 'closing', scopes: ['all'] },
    { href: '/closing/imports', label: 'Auto-Import', icon: '🔄', module: 'closing', scopes: ['all'] },
  ]},
  { group: 'Integrations & Imports', module: 'commissions', items: [
    { href: '/commcalc/connectors', label: 'Connectors', icon: '🔌', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/onboarding', label: 'Onboarding Wizard', icon: '🚀', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/implementation', label: 'Implementation Wizard', icon: '🧭', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/upload', label: 'Upload Files', icon: '📁', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/upload/wizard', label: 'Upload Wizard', icon: '🧭', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/carrier-comm-file', label: 'Carrier Comm File → Table', icon: '📑', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/ftp-imports', label: 'FTP Auto-Import', icon: '🔁', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/email-imports', label: 'Email & Portal Logins (2FA)', icon: '📨', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/epay/sweep', label: 'Payment Processor Sync', icon: '🧹', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/dlar/sweep', label: 'Metrics Rep/Store Sync', icon: '🧹', module: 'commissions', scopes: ['all'] },
  ]},
  { group: 'Mapping', module: 'commissions', items: [
    { href: '/commcalc/mapping', label: 'All Mappings', icon: '🗂️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/store-match', label: 'Store Matching', icon: '🏬', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/column-mapping', label: 'Column Mapping', icon: '🧩', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/target-fields', label: 'Custom Target Fields', icon: '🧱', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/gp-category-map', label: 'GP Category Map', icon: '💰', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/item-mapping', label: 'Item / Model Mapping', icon: '🧩', module: 'commissions', scopes: ['all'] },
    // Catalog Categories (migs 230/231) — the per-tenant category-override layer on top of the uploaded
    // product catalog; feeds catalog-driven accessory classification. Nav entry per mod-commission
    // NEEDS CORE (catalog-accessory-byod). Filed under Mapping, not Commissions: it is a mapping/config
    // surface like GP Category Map / Item Mapping, so it is also intentionally NOT in REPORT_DIRECTORY
    // (config pages are excluded) and has no report-area gate. Carrier-NEUTRAL — the page accepts BOTH
    // the house (product-ID) and TOTAL (UPC) catalog files, so it must NOT go in NAV_CARRIERS.
    { href: '/commcalc/catalog', label: 'Catalog Categories', icon: '🏷️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/rep-aliases', label: 'Rep Aliases', icon: '🔗', module: 'commissions', scopes: ['all'] },
  ]},
  { group: 'Notify', module: 'notify', items: [
    { href: '/notify', label: 'Notify', icon: '📤', module: 'notify' },
    { href: '/notify/report-recipients', label: 'Report Recipients', icon: '📬', module: 'notify' },
  ]},
  { group: 'Helpdesk', module: 'helpdesk', items: [
    { href: '/helpdesk', label: 'Tickets', icon: '🎫', module: 'helpdesk' },
    { href: '/helpdesk/new', label: 'Raise a Ticket', icon: '➕', module: 'helpdesk' },
    { href: '/helpdesk/dashboard', label: 'Dashboard', icon: '📊', module: 'helpdesk', scopes: ['all', 'market', 'store'] },
    { href: '/helpdesk/settings', label: 'Settings', icon: '⚙️', module: 'helpdesk', scopes: ['all'] },
    { href: '/remediation', label: 'Auto-Remediation', icon: '🤖', module: 'helpdesk', scopes: ['all', 'market'] },
  ]},
  // Tech Support (mig 715) — the HOUSE support team's cross-tenant console + per-page help docs. Gated on
  // module 'support' (NOT 'admin'): support agents need not be admins. The console's backend endpoints are
  // additionally house-gated + super-admin-only cross-tenant, so a tenant user granted the module still
  // can't read another tenant's cases.
  { group: 'Support', module: 'support', items: [
    { href: '/admin/support', label: 'Support Console', icon: '🎧', module: 'support', scopes: ['all', 'market'] },
    { href: '/admin/support/failures', label: 'Fleet Failure Triage', icon: '🩺', module: 'support', scopes: ['all', 'market'] },
    { href: '/admin/support/fix-requests', label: 'Fix Requests', icon: '🛠️', module: 'support', scopes: ['all', 'market'] },
    { href: '/admin/support/docs', label: 'Help Docs', icon: '📚', module: 'support', scopes: ['all'] },
  ]},
  { group: 'Configuration', module: 'admin', items: [
    { href: '/configurations', label: 'All Settings', icon: '⚙️', module: 'admin' },
    { href: '/admin/tenants', label: 'Companies (Tenants)', icon: '🏢', module: 'admin' },
    { href: '/admin/tenant-settings', label: 'Pay Period & Work-Week', icon: '📅', module: 'admin' },
    { href: '/admin/billing', label: 'Billing (Tenants)', icon: '💳', module: 'admin' },
    { href: '/admin/roles', label: 'Roles & Access', icon: '🔐', module: 'admin' },
    { href: '/admin/security', label: 'Security Settings', icon: '🛡️', module: 'admin' },
    // "Sign in as an employee" audit log + policy (mig 730, owner directive 2026-08-06). Tagged
    // module 'admin' with NO `scopes`, byte-identical in shape to its /admin/security sibling: an
    // existing admin role already carries modules.admin, so this nav line adds NO new permission
    // surface and needs no SEED_VERSION bump. The ABILITY to impersonate is a completely separate,
    // DEFAULT-DENY grant (`permissions.impersonate`, canImpersonate below) that nothing seeds; this
    // page is only where an admin reads the trail and tunes the session length. Every backend
    // endpoint behind it gates independently.
    { href: '/admin/impersonation', label: 'Sign-in-as Audit', icon: '🕵️', module: 'admin' },
    { href: '/admin/org', label: 'Org Structure', icon: '🌳', module: 'admin' },
    { href: '/admin/org-chart', label: 'Employee Org Chart', icon: '👥', module: 'admin' },
    { href: '/admin/labels', label: 'Display Labels', icon: '🏷️', module: 'admin' },
    { href: '/admin/menu', label: 'Menu Layout', icon: '🧭', module: 'admin' },
    { href: '/failures', label: 'Failure Logs', icon: '🩺', module: 'admin' },
    // Import Health (mig 717) — the universal import-freshness registry behind the admin login popup.
    // module 'admin' + no `scopes`, IDENTICAL to its Failure Logs sibling: an existing admin role already
    // carries modules.admin, so this adds no new permission surface and needs no SEED_VERSION bump.
    // Deliberately NOT in REPORT_DIRECTORY — it edits import schedules, and the directory excludes
    // config/entry surfaces by design (one line to add if the operator prefers it listed).
    { href: '/admin/import-health', label: 'Import Health', icon: '📡', module: 'admin' },
    { href: '/admin/training', label: 'Walk-throughs', icon: '🎓', module: 'admin' },
    { href: '/admin/whats-new', label: "What's New", icon: '✨', module: 'admin' },
    // Auto-Fix Pipeline board (mig 718) — the fix-request registry + AI token/$ accounting. Tagged
    // module 'admin' with NO `scopes`, byte-identical in shape to its /admin/tenants sibling: the PAGE
    // itself is super-admin-only (it renders an explainer for anyone else, and every backend endpoint
    // 403s a non-super-admin independently), so this nav line adds no new permission surface and needs
    // no SEED_VERSION bump for roles. Deliberately NOT a new module key: it is a platform surface, not
    // a billable tenant module.
    { href: '/admin/fix-requests', label: 'Auto-Fix Pipeline', icon: '🛠️', module: 'admin' },
  ]},
  // Reports LAST (owner directive 2026-08-10) — the Report Center directory sits at the foot of the
  // sidebar, immediately above the per-category report groups applyNavLayout() appends after it.
  { group: 'Reports', module: 'targets', items: [
    { href: '/reports', label: 'Report Center', icon: '📊', module: 'targets' },
  ]},
]

// Per-item override: `group` = the item's PRIMARY group (a MOVE); `also` = ADDITIONAL groups the SAME
// item also appears in (DUPLICATE links to the same href — never a second permission surface); `hidden`
// removes it everywhere. `groups` = admin-created group names that may have no items yet (kept so the
// designer can show an empty group); the sidebar ignores empty groups. BACKWARD-COMPATIBLE: a legacy
// layout carrying only `{group?, hidden?}` behaves exactly as before (`also`/`groups` absent → no-op).
// `hideReportsDirectory` (optional, per-tenant) suppresses the built-in Reports directory (below) for a
// tenant that doesn't want the duplicate categorized copies. Default OFF (every tenant gets the directory).
// It flows through the existing nav-layout JSON; wiring a designer toggle for it is an optional follow-up.
export type NavLayout = {
  // `sub` nests the item under a named sub-category within its group. Empty/absent ⇒ loose, as today.
  items?: Record<string, { group?: string; sub?: string; hidden?: boolean; also?: string[] }>
  groups?: string[]
  // Explicit drag-and-drop ordering. Each is OPTIONAL and ADDITIVE: anything a list does not name keeps
  // its natural (code-default) position, after the named entries. All three absent ⇒ applyNavLayout
  // returns exactly what it returned before sub-categories existed.
  groupOrder?: string[]                     // sidebar order of groups
  subOrder?: Record<string, string[]>       // group  → order of its sub-category names
  itemOrder?: Record<string, string[]>      // group  → order of its item hrefs
  hideReportsDirectory?: boolean
}

// ── Reports directory (OWNER DIRECTIVE 2026-07-17) ────────────────────────────────────────────────
// Every report/list/dashboard surface ALSO appears — as a DUPLICATE entry — under a categorized
// "Reports · <Category>" area, WITHOUT leaving its own module group. This is a CODE-LEVEL DEFAULT
// (no per-tenant rows, no migration): applyNavLayout injects each surviving report item into its
// category, reusing the SAME NavItem object AFTER access filtering, so every copy is byte-identically
// RBAC/carrier/entitlement-gated (never a second permission surface). A tenant whose role lacks module X
// therefore sees NO X entry in Reports either; a tenant admin can still hide/move any item on /admin/menu
// (a hide removes it from Reports too), or suppress the whole area via `hideReportsDirectory`.
// ONLY analytical reports + operational lists/dashboards are listed here — pure config/mapping/import/
// settings/entry pages are intentionally EXCLUDED (they are inputs, not reports; same exemption as
// RULES FOUR/FIVE). Category ORDER below is the sidebar order (categories render after the module groups).
export const REPORT_CATEGORIES: { key: string; label: string }[] = [
  { key: 'sales',    label: 'Reports · Sales' },
  { key: 'comm',     label: 'Reports · Commissions & Pay' },
  { key: 'targets',  label: 'Reports · Targets & Coaching' },
  { key: 'assets',   label: 'Reports · Assets & Inventory' },
  { key: 'finance',  label: 'Reports · Finance & Accounting' },
  { key: 'payroll',  label: 'Reports · Payroll & HR' },
  { key: 'ops',      label: 'Reports · Store Operations' },
  { key: 'admin',    label: 'Reports · Admin & System' },
]
// href → category KEY. Ordered by category (drives intra-area order). A duplicate is only rendered when
// the SAME href survives access filtering in a module group, so this map never widens visibility.
export const REPORT_DIRECTORY: [string, string][] = [
  // Sales
  ['/commcalc/sales-report', 'sales'], ['/commcalc/custom-report', 'sales'],
  ['/commcalc/sales-comparison', 'sales'],
  ['/commcalc/sales-analyzer', 'sales'], ['/commcalc/sales-recon', 'sales'],
  ['/crm/reports', 'sales'],
  // Commissions & Pay
  ['/commcalc', 'comm'], ['/commcalc/exec', 'comm'], ['/commcalc/exec/mtd', 'comm'],
  ['/commcalc/reports', 'comm'], ['/commcalc/comp-trend', 'comm'], ['/commcalc/commission-ledger', 'comm'],
  ['/commcalc/ma-commission', 'comm'], ['/commcalc/ma-overview-recon', 'comm'], ['/commcalc/financing', 'comm'],
  ['/commcalc/commission-legs', 'comm'],
  ['/commcalc/device-history', 'comm'], ['/commcalc/whatif', 'comm'],
  ['/commcalc/discrepancy', 'comm'], ['/commcalc/recovery', 'comm'], ['/commcalc/flags', 'comm'],
  ['/commcalc/chargebacks', 'comm'], ['/commcalc/accessory-flags', 'comm'],
  ['/commcalc/accessory-cost-audit', 'comm'], ['/commcalc/accessory-definition', 'comm'],
  ['/commcalc/expected-commission', 'comm'], ['/commcalc/daily-commission', 'comm'],
  // Targets & Coaching
  ['/commcalc/targets', 'targets'], ['/commcalc/targets/action-plan', 'targets'],
  ['/commcalc/atu-opportunity', 'targets'],
  ['/commcalc/targets/accessories', 'targets'], ['/commcalc/targets/my', 'targets'],
  ['/commcalc/kpi', 'targets'], ['/commcalc/productivity', 'targets'], ['/commcalc/coaching', 'targets'],
  // Assets & Inventory (incl. Distributors/VIP consignment)
  ['/commcalc/asset', 'assets'], ['/commcalc/asset/dashboard', 'assets'], ['/commcalc/asset/owed-weekly', 'assets'],
  ['/commcalc/asset/aging', 'assets'], ['/commcalc/asset/missing-phones', 'assets'],
  ['/commcalc/asset/aging-rebate', 'assets'], ['/commcalc/asset/on-inventory', 'assets'],
  ['/commcalc/asset/borrowed', 'assets'], ['/commcalc/asset/lending', 'assets'],
  ['/commcalc/asset/charges/rma', 'assets'], ['/commcalc/asset/inventory-recon', 'assets'],
  ['/commcalc/asset/hotsheet-recon', 'assets'], ['/commcalc/asset/marketplace-purchases', 'assets'],
  ['/commcalc/payables', 'assets'],
  ['/commcalc/distributors', 'assets'], ['/commcalc/vip', 'assets'], ['/commcalc/vip/paygo', 'assets'],
  // Finance & Accounting
  ['/accounts', 'finance'], ['/accounts/trends', 'finance'], ['/accounts/pl', 'finance'],
  ['/accounts/balance-sheet', 'finance'], ['/accounts/inventory', 'finance'], ['/accounts/recon', 'finance'],
  ['/accounts/residual-per-sub', 'finance'], ['/accounts/journal', 'finance'],
  ['/commcalc/gp', 'finance'], ['/commcalc/expenses', 'finance'], ['/commcalc/tax-collected', 'finance'],
  // Payroll & HR
  ['/hr', 'payroll'], ['/hr/people', 'payroll'], ['/hr/onboarding', 'payroll'], ['/hr/compliance', 'payroll'],
  ['/hr/payroll-expenses', 'payroll'], ['/storeops/payroll', 'payroll'], ['/storeops/payroll-tax', 'payroll'],
  ['/admin/org-chart', 'payroll'],
  // Store Operations (workforce + daily closing)
  ['/storeops', 'ops'], ['/storeops/schedule', 'ops'], ['/storeops/timeoff', 'ops'], ['/storeops/swaps', 'ops'],
  ['/storeops/shift-extensions', 'ops'], ['/storeops/timeclock-permissions', 'ops'], ['/storeops/hours-budget', 'ops'], ['/storeops/timeclock', 'ops'],
  ['/storeops/attendance', 'ops'],
  ['/storeops/employees', 'ops'], ['/storeops/team', 'ops'], ['/storeops/visits', 'ops'], ['/storeops/reports', 'ops'],
  ['/closing', 'ops'], ['/closing/verify', 'ops'], ['/closing/management', 'ops'], ['/closing/recon', 'ops'],
  ['/closing/tender-recon', 'ops'], ['/closing/tender-recon-3way', 'ops'], ['/closing/accessory-recon', 'ops'],
  ['/closing/pickup', 'ops'], ['/closing/epay-recon', 'ops'],
  // Admin & System
  ['/failures', 'admin'], ['/helpdesk', 'admin'], ['/helpdesk/dashboard', 'admin'], ['/remediation', 'admin'],
  ['/admin/tenants', 'admin'],
  // The fix-request board IS a report surface (status board + rollup tile + full ReportShell export set),
  // so it belongs in the directory — same treatment as its super-admin-only sibling /admin/tenants, and
  // unlike the pure config surfaces (Import Health, Roles & Access), which are deliberately excluded.
  // NOTE: do not name those config hrefs literally here — prove_import_health_nav.mjs asserts the
  // directory block does not CONTAIN that string, and a mention in a comment would trip it.
  ['/admin/fix-requests', 'admin'],
]
const REPORT_CATEGORY_LABEL: Record<string, string> = Object.fromEntries(REPORT_CATEGORIES.map(c => [c.key, c.label]))

// Apply a per-org sidebar layout (admin config) ON TOP of the already-filtered groups: move an item to
// a different group, DUPLICATE it into additional groups (`also`), or hide it. Items with no override
// keep their built-in group. Default group order is preserved; a brand-new group name lands at the end.
// A DUPLICATE reuses the SAME NavItem object — and applyNavLayout runs AFTER access filtering, so an item
// only exists here if it passed canSeeItem/carrierOK/capOK, meaning every copy is identically RBAC-gated.
// Returns groups ready to render. Empty groups (a created group with no items) are dropped from the
// sidebar; `layout.groups` is intentionally ignored here (it exists only for the designer's persistence).
export function applyNavLayout(groups: NavGroup[], layout?: NavLayout): NavGroup[] {
  const ov = layout?.items
  const moduleByGroup: Record<string, string> = {}
  const defaultOrder: string[] = []
  groups.forEach(g => { if (!(g.group in moduleByGroup)) { moduleByGroup[g.group] = g.module; defaultOrder.push(g.group) } })
  const targets: { group: string; it: NavItem }[] = []
  // Global (group|href) dedup so no href renders twice in one group — protects the built-in Reports
  // directory from ever double-placing an item a tenant already duplicated there via `also`.
  const placedGH = new Set<string>()
  const push = (group: string, it: NavItem) => {
    const k = group + '|' + it.href
    if (placedGH.has(k)) return
    placedGH.add(k); targets.push({ group, it })
  }
  const surviving = new Map<string, NavItem>()   // href → item that passed access filtering (for the directory)
  for (const g of groups) for (const it of g.items) {
    const o = ov?.[it.href]
    if (o?.hidden) continue
    surviving.set(it.href, it)
    const primary = (o?.group && o.group.trim()) || g.group
    push(primary, it)
    // Additional placements (duplicates). Dedup within one item so the same href never renders twice in
    // one group (React-key + visual dup), and never re-adds its own primary group.
    if (o?.also && o.also.length) {
      for (const a of o.also) { const ag = (a || '').trim(); if (ag) push(ag, it) }
    }
  }
  // Built-in Reports directory (code-level default, EVERY tenant unless opted out). Each surviving report
  // href is duplicated into its category — SAME object, so identical gating. Iterated in REPORT_DIRECTORY
  // order (grouped by category) so categories appear in REPORT_CATEGORIES order after the module groups.
  if (!layout?.hideReportsDirectory) {
    for (const [href, catKey] of REPORT_DIRECTORY) {
      const it = surviving.get(href)
      const cat = REPORT_CATEGORY_LABEL[catKey]
      if (it && cat) { moduleByGroup[cat] = moduleByGroup[cat] || it.module; push(cat, it) }
    }
  }
  const seen = new Set<string>(); const order: string[] = []
  defaultOrder.forEach(g => { if (targets.some(t => t.group === g)) { order.push(g); seen.add(g) } })
  targets.forEach(t => { if (!seen.has(t.group)) { order.push(t.group); seen.add(t.group) } })
  // Stable "listed first, in the listed order; everything else keeps its natural order after them".
  // Used for groups, sub-names and items alike. An EMPTY/absent list returns the input untouched, which
  // is what keeps the no-layout path byte-identical to the pre-sub-category behaviour.
  const rank = <T,>(arr: T[], want: string[] | undefined, key: (t: T) => string): T[] => {
    if (!want || !want.length) return arr
    const nat = new Map(arr.map((t, i) => [key(t), i]))
    return [...arr].sort((a, b) => {
      const ia = want.indexOf(key(a)), ib = want.indexOf(key(b))
      if (ia === -1 && ib === -1) return (nat.get(key(a)) ?? 0) - (nat.get(key(b)) ?? 0)
      if (ia === -1) return 1
      if (ib === -1) return -1
      return ia - ib
    })
  }
  const ordered = rank(order, layout?.groupOrder, g => g)
  return ordered.map(group => {
    const items = rank(targets.filter(t => t.group === group).map(t => t.it), layout?.itemOrder?.[group], it => it.href)
    // Sub-categories. An item nests when ITS OWN override names a `sub`; a sub therefore cannot capture
    // an item the admin never assigned, and an unknown/stale sub name simply reappears as its own
    // sub-category rather than swallowing the item. `items` above is left complete on purpose.
    const subOf = new Map<string, string>()
    for (const it of items) { const s = (ov?.[it.href]?.sub || '').trim(); if (s) subOf.set(it.href, s) }
    let subs: NavSub[] | undefined
    if (subOf.size) {
      const names: string[] = []
      for (const it of items) { const s = subOf.get(it.href); if (s && !names.includes(s)) names.push(s) }
      subs = rank(names, layout?.subOrder?.[group], n => n)
        .map(name => ({ name, items: items.filter(it => subOf.get(it.href) === name) }))
    }
    return {
      group,
      module: moduleByGroup[group] || (targets.find(t => t.group === group)?.it.module || ''),
      items,
      // Spread so the key is ABSENT (not `undefined`) when the group has no sub-categories — the
      // returned object stays shape-identical to the old one for every untouched tenant.
      ...(subs ? { subs } : {}),
    }
  }).filter(g => g.items.length > 0)
}

export function moduleForPath(path: string): string {
  if (path.startsWith('/reports')) return 'targets'
  if (path.startsWith('/admin/support')) return 'support'   // tech-support console (mig 715), gated on 'support' not 'admin'
  if (path.startsWith('/admin')) return 'admin'
  if (path.startsWith('/configurations')) return 'admin'
  if (path.startsWith('/employee')) return 'targets'
  if (path.startsWith('/storeops')) return 'storeops'
  if (path.startsWith('/hr')) return 'hr'
  if (path.startsWith('/closing')) return 'closing'
  if (path.startsWith('/accounts')) return 'accounts'
  if (path.startsWith('/notify')) return 'notify'
  if (path.startsWith('/helpdesk')) return 'helpdesk'
  if (path.startsWith('/crm')) return 'crm'
  if (path.startsWith('/remediation')) return 'helpdesk'
  if (path.startsWith('/commcalc/targets')) return 'targets'
  if (path.startsWith('/commcalc/asset')) return 'asset'
  if (path.startsWith('/commcalc/vip')) return 'vip'
  if (path.startsWith('/commcalc')) return 'commissions'
  return 'commissions'
}

// The module that GOVERNS a path = the nav item whose href is the longest boundary-matched prefix
// (exact wins). This is the SAME source the sidebar gates on (canSeeItem keys off item.module), so the
// guard (canAccessPath) and the sidebar can never disagree about which module a page belongs to.
// Some items are placed in an information-architecture group whose URL prefix implies a DIFFERENT
// module than the item's real one (e.g. `/commcalc/payables` is an Asset feature, `/commcalc/distributors`
// a Distributor/vip feature, `/commcalc/asset/hotsheet-recon` a Commissions pricing page). Path-prefix
// derivation (moduleForPath) got those wrong, so a tab the sidebar SHOWED under module X was gated by
// the guard under module Y → clicking it failed canAccessPath and bounced to the dashboard. Returns null
// for paths no nav item governs (deep sub-pages of a module whose root isn't itself a nav href) → the
// caller falls back to moduleForPath.
export function navModuleForPath(path: string): string | null {
  let best: string | null = null, bestLen = -1
  for (const g of NAV) {
    for (const it of g.items) {
      if ((path === it.href || path.startsWith(it.href + '/')) && it.href.length > bestLen) {
        best = it.module; bestLen = it.href.length
      }
    }
  }
  return best
}

// A super-admin (role-management rights) implicitly has EVERY module. This keeps newly
// added modules (e.g. Accounts, added after the roles were seeded) visible to admins
// without re-seeding each role's permissions JSONB. Non-admin roles still need the flag.
export function isSuperAdmin(perms: Permissions): boolean {
  return !!perms?.modules?.admin
}

// May this user see the ADMIN ATTENTION popup / indicator (overdue imports, pending mappings,
// duplicate-data signals)? MIRROR of backend `core.import_health.can_view_attention` — KEEP IN SYNC.
// It reuses the EXISTING admin-ish concept (no parallel gate is invented): an explicit per-page override
// for /admin/import-health wins, then the `admin` module, then company-wide scope. A non-admin gets
// `false` here and the component renders nothing; the backend 403s them independently.
export function canSeeAttention(perms: Permissions): boolean {
  const ov = perms?.pages?.['/admin/import-health']
  if (typeof ov === 'boolean') return ov
  if (isSuperAdmin(perms)) return true
  return (perms?.scope || 'all') === 'all'
}

// ── Carrier-scoped nav ──────────────────────────────────────────────────────────────────────────
// Some pages belong to a specific carrier (Boost vs Total). An href listed here shows ONLY when the
// tenant has a matching carrier — UNLESS an admin override says otherwise. Everything not listed is
// generic (all carriers). Currently DLAR-driven reporting (KPI/coaching) lives under Boost; as another
// carrier gets its own DLAR/KPI reporting, add that carrier's code to the relevant hrefs.
export type CarrierRef = { name?: string; code?: string; is_default?: boolean }

// Mirror of backend _resolve_carrier_mode (commcalc/router.py): 'boost' = the legacy verified Boost
// KPI-tier engine; 'plan' = pay ONLY from configurable Commission Plans / Payout Schedules. Conservative
// so Boost tenants are never flipped. KEEP IN SYNC with the backend.
export function carrierMode(carriers: CarrierRef[] | undefined): 'boost' | 'plan' {
  const cs = carriers || []
  const isB = (c: CarrierRef) => /boost/i.test((c.code || '') + ' ' + (c.name || ''))
  if (cs.length === 0) return 'boost'
  const def = cs.find(c => c.is_default)
  if (def) return isB(def) ? 'boost' : 'plan'
  if (cs.some(isB)) return 'boost'
  return 'plan'
}
export const NAV_CARRIERS: Record<string, string[]> = {
  '/commcalc/vip': ['boost'], '/commcalc/vip/paygo': ['boost'], '/commcalc/vip/sweep': ['boost'],
  '/commcalc/distributors': ['boost'], '/commcalc/asset/lending': ['boost'],
  '/commcalc/asset/owed-weekly': ['boost'], '/commcalc/asset/hotsheet-recon': ['boost'],
  // The rest of the VIP(Boost)-financing asset reports (they read the VIP asset_ledger / ePay appeals
  // and are empty for a non-Boost tenant). Added per mod-asset NEEDS CORE [asset-10]. Boost-byte-identical:
  // a Boost tenant HAS the boost carrier → carrierOK stays true → these still show; only a Total-only
  // tenant (luxelink) loses them. Admin can override per item at /admin/labels (caps['carrier:<href>']).
  '/commcalc/asset/dashboard': ['boost'], '/commcalc/asset/aging': ['boost'],
  '/commcalc/asset/missing-phones': ['boost'], '/commcalc/asset/aging-rebate': ['boost'],
  '/commcalc/asset/on-inventory': ['boost'], '/commcalc/asset/charges/rma': ['boost'],
  '/commcalc/kpi': ['boost'], '/commcalc/coaching': ['boost'],
  // MA / VidaPay (T-CETRA) pages — the mirror gate of Boost's ePay pages. Total-processor only.
  // Marketplace Purchases reads commcalc.raw_ma_marketplace_orders (VidaPay MA orders), so it is
  // processor-specific just like ma-commission → gated to Total, NOT ungated. STEWARD JUDGMENT (differs
  // from mod-asset's "leave ungated" note): an ungated item would add a permanently-empty tab to the
  // Boost sidebar, violating "Boost byte-identical"; ['total'] shows it for luxelink/Total (the actual
  // goal) and any admin can widen it per tenant at /admin/labels if a non-Total tenant ever needs it.
  '/commcalc/ma-commission': ['total'],
  '/commcalc/asset/marketplace-purchases': ['total'],
  // Boost Rates page = the hardcoded Boost KPI-tier config; only meaningful for Boost tenants. A
  // Total-only tenant (e.g. luxelink) never sees it — they configure pay via Commission Plans instead.
  '/commcalc/settings': ['boost'],
}
// Carrier gate: admin per-item override wins (caps['carrier:<href>'] true/false); else a carrier-scoped
// item shows only when the tenant has a matching carrier. No carrier chosen yet → hide nothing.
export function carrierOK(href: string, tenantCarriers: CarrierRef[] | undefined, caps: Record<string, boolean | null>): boolean {
  const ov = caps['carrier:' + href]
  if (ov === true) return true
  if (ov === false) return false
  const need = NAV_CARRIERS[href]
  if (!need || need.length === 0) return true
  if (!tenantCarriers || tenantCarriers.length === 0) return true
  const have = tenantCarriers.map(c => (c.code || c.name || '').toLowerCase()).filter(Boolean)
  return need.some(k => have.some(t => t.includes(k) || k.includes(t)))
}

// Pages restricted to management (company-wide leadership by default; DMs excluded), but still
// grantable/revocable per role via a `pages` override — mirrors the backend _can_mgmt_review gate.
const MGMT_ONLY = new Set<string>(['/closing/management'])
export function canManage(perms: Permissions, href: string): boolean {
  if (isSuperAdmin(perms)) return true
  const ov = perms.pages?.[href]
  if (typeof ov === 'boolean') return ov
  return (perms.scope || 'all') === 'all'
}

// Canonical module keys mirror the backend core.module_catalog (mig 700). The frontend historically
// tagged Finance nav + roles with `accounts`; the backend canonical entitlement key is `account`.
// This read-side alias map treats them as ONE module so a grant under either key is honored — no data
// migration of stored role permissions. Behavior-neutral today (no role stores `account`), forward-safe.
export const MODULE_ALIASES: Record<string, string> = { accounts: 'account', account: 'accounts' }
export function moduleGranted(mods: Record<string, boolean> | undefined, key: string): boolean {
  if (!mods) return false
  return !!(mods[key] || mods[MODULE_ALIASES[key]])
}

export function canSeeItem(perms: Permissions, item: NavItem): boolean {
  if (isSuperAdmin(perms)) return true
  if (MGMT_ONLY.has(item.href)) return canManage(perms, item.href)
  // Per-function override wins (either direction) — lets an admin grant/deny each function per role.
  // It is checked BEFORE the item's scope tier: the docs (and the Roles UI) have always promised
  // "these per-function settings override the module/report toggles", but the scope gate used to
  // sit in front and silently veto an explicit grant. An admin who ticked e.g. Rep Coaching for a
  // store-scoped District Manager saw the box stay ticked and the tab never appear (owner report,
  // 2026-08-03). Only an EXACT-href grant can lift a scope gate; an inherited/prefix override
  // cannot, since it was never aimed at this item. Everything else is byte-identical: with no
  // pages[href] entry, or with an entry of `false`, the outcome is exactly what it was before.
  const ov = perms.pages?.[item.href]
  if (item.scopes && !item.scopes.includes(perms.scope || 'all') && ov !== true) return false
  if (typeof ov === 'boolean') return ov
  // Default: operational module gate (alias-aware) + (for report pages) the report-area gate.
  if (!moduleGranted(perms.modules, item.module)) return false
  const area = reportAreaForPath(item.href)
  if (area && !hasReport(perms, area)) return false
  return true
}

// WHY is this nav item hidden for this role? Returns null when it IS visible. Exists so the Roles
// admin UI can explain a hidden function instead of leaving the operator to guess — the 2026-08-03
// "KPI Metrics is allowed for the DM role but doesn't show" / "Rep Coaching doesn't show either"
// reports were both invisible-by-configuration, but nothing on screen said which gate closed.
// Ordered to MATCH canSeeItem exactly; keep the two in lockstep.
export type NavBlockReason =
  | { gate: 'scope'; detail: string }
  | { gate: 'page'; detail: string }
  | { gate: 'module'; detail: string }
  | { gate: 'report'; detail: string }
export function navBlockReason(perms: Permissions, item: NavItem): NavBlockReason | null {
  if (isSuperAdmin(perms)) return null
  if (MGMT_ONLY.has(item.href)) {
    return canManage(perms, item.href) ? null
      : { gate: 'scope', detail: 'management-only page (company-wide scope, or grant it per function)' }
  }
  const ov = perms.pages?.[item.href]
  const scope = perms.scope || 'all'
  if (item.scopes && !item.scopes.includes(scope) && ov !== true) {
    return { gate: 'scope', detail: `built for scope ${item.scopes.join('/')} — this role is "${scope}". Tick this function to grant it anyway.` }
  }
  if (ov === false) return { gate: 'page', detail: 'explicitly denied for this role (untick/tick this function)' }
  if (ov === true) return null
  if (!moduleGranted(perms.modules, item.module)) {
    return { gate: 'module', detail: `the "${item.module}" module is off for this role` }
  }
  const area = reportAreaForPath(item.href)
  if (area && !hasReport(perms, area)) {
    return { gate: 'report', detail: `report area "${area}" is off (Reports column) — separate from the module` }
  }
  return null
}

// The per-function override for a path (exact href, else the longest matching nav-href prefix).
function pageOverrideForPath(perms: Permissions, path: string): boolean | undefined {
  const pages = perms.pages
  if (!pages) return undefined
  if (path in pages) return pages[path]
  let best: boolean | undefined, bestLen = -1
  for (const g of NAV) for (const it of g.items) {
    if ((path === it.href || path.startsWith(it.href + '/')) && it.href.length > bestLen && (it.href in pages)) {
      best = pages[it.href]; bestLen = it.href.length
    }
  }
  return best
}

// Pages a self-scoped (rep) user may always reach, on top of their home.
const SELF_ALLOWED = ['/commcalc/targets/my', '/commcalc/kpi', '/account/password', '/reports', '/helpdesk']

export function canAccessPath(perms: Permissions, path: string): boolean {
  if (path === '/' || path.startsWith('/account/password')) return true
  // Super-admin bypass FIRST — mirrors canSeeItem's own precedence (isSuperAdmin checked before scope).
  // Keeping it ahead of the per-item scope loop guarantees the operator/super-admin (who sees every tab)
  // can never be bounced by a scope-restricted nav item → the sidebar and the guard stay consistent.
  if (isSuperAdmin(perms)) return true
  const scope = perms.scope || 'all'
  if (scope === 'self') {
    const home = perms.home || '/commcalc/targets/my'
    // An EXPLICIT per-function grant reaches a self-scoped rep too. Without this the sidebar shows a
    // page the admin granted (canSeeItem honors `pages[href] === true` for every scope) and the guard
    // then bounces it — the "the tab is there but clicking it does nothing" class. WIDENING ONLY:
    // an explicit `false` is deliberately NOT honored here, so this can never remove access a rep has
    // today (that half stays exactly as shipped).
    if (perms.pages?.[path] === true) return true
    return SELF_ALLOWED.some(p => path.startsWith(p)) || path.startsWith(home)
  }
  // For settings/manager-only sub-pages, honor the matching nav item's scope restriction — unless an
  // EXACT per-function grant lifts it, mirroring canSeeItem's precedence exactly. Without this
  // mirror, a function an admin explicitly granted would render in the sidebar and then be bounced
  // by the guard (sidebar and guard MUST agree; that disagreement is the "clicking it does nothing"
  // class). Byte-identical whenever pages[path] is not exactly `true`.
  const exactOv = perms.pages?.[path]
  for (const g of NAV) {
    for (const it of g.items) {
      if (path === it.href && it.scopes && !it.scopes.includes(scope) && exactOv !== true) return false
    }
  }
  if (MGMT_ONLY.has(path)) return canManage(perms, path)   // management-only pages, DMs excluded
  const ov = pageOverrideForPath(perms, path)   // per-function override wins
  if (typeof ov === 'boolean') return ov
  const area = reportAreaForPath(path)   // report pages need the separate report permission
  if (area && !hasReport(perms, area)) return false
  // Gate on the GOVERNING nav item's module (same source the sidebar uses), not the path-prefix guess —
  // so any tab the sidebar shows is guaranteed reachable. Fall back to moduleForPath for un-navved paths.
  return moduleGranted(perms.modules, navModuleForPath(path) ?? moduleForPath(path))
}

export function homeFor(perms: Permissions): string {
  return perms.home || '/commcalc'
}

// A landing the user can actually open. The configured home may be gated off — e.g. it
// defaults to '/commcalc', which is the report-gated 'commissions' area, so a role without
// commission-report clearance can't enter it. The (platform) guard would then redirect to that
// same home forever (infinite "Redirecting…", presenting as "can't log in"). Fall back to the
// first nav item the user can open, then to the always-allowed password page so we never loop.
export function safeHomeFor(perms: Permissions): string {
  const home = homeFor(perms)
  if (canAccessPath(perms, home)) return home
  for (const g of NAV) {
    for (const it of g.items) {
      if (canSeeItem(perms, it) && canAccessPath(perms, it.href)) return it.href
    }
  }
  return '/account/password'   // canAccessPath() always allows this → guaranteed non-looping
}
