// THE ONE HOME for "which different words mean the same thing" in the platform search
// (owner directive 2026-10-10: *"all modules should be searcable, index and found by susing
// similar words like a search enegine"*).
//
// WHY THIS FILE EXISTS — measured, not assumed. On 2026-10-10 the real registries were loaded and
// the real ranker driven over them. Of **351 findable things, 250 carry a label and nothing else**:
// no description, no alternate spellings. Only the 31 report entries have prose, and only 79 entries
// have any alias at all. So a page was findable only by the words in its own title, and 11 of 30
// everyday words a manager would type found NOTHING:
//
//     headcount · wages · holiday · sick leave · rota · margin · turnover · refund · discount ·
//     bonus · trade in
//
// Every one of those but the last has a page behind it. "wages" missed Payroll, "margin" missed
// Gross Profit, "refund" missed the Void & Return Register, "headcount" missed the Employee Database.
//
// WHY A VOCABULARY AND NOT 250 ALIAS LISTS. The obvious patch is to type alternate spellings onto
// each page. That is 250 lists to maintain, each a SECOND COPY of wording the nav already owns
// (the exact duplicate the house rules forbid — and the reason a NAV page may declare no second
// label at all, see §54.12), every one of them stale the moment a page is renamed, and the next
// report ships with the same hole. It is the patchwork the owner banned on 2026-09-20.
//
// The missing fact is not "what else is this page called". It is **which words mean the same thing**,
// which is a property of the LANGUAGE, not of any page. So it lives here once, and the ranker
// expands what was TYPED through it. One group ("wages = pay = payroll = salary = earnings") makes
// every page about pay findable by all five words — including pages that do not exist yet. The cost
// is O(vocabulary), a few hundred lines that change only when English does, instead of O(pages),
// which grows every time anything ships.
//
// THE RULE THIS FILE MAY NOT BREAK — `search-never-guesses` (§54.10, PR #394). A search that always
// returns its best guess cannot tell a bad match from no match, and the owner was taken to a carrier
// report by exactly that. So a word is mapped here ONLY when the platform really has the thing it
// names. A word with no destination stays unmapped and the console honestly finds nothing — see
// `UNMAPPED` below, which records the words that were considered and deliberately left out, with the
// reason. Mapping "trade in" at Device History would be a guess wearing a vocabulary's clothes.
//
// RULE TWO (config, never code). Nothing here may be a CARRIER, TENANT or PRODUCT name. Every term
// below is ordinary business or retail English that is true for every tenant. A carrier's or a
// tenant's own spelling for something is per-org data and already has its own homes — a store's
// `also_known_as`, the org's Display Labels, the carrier mapping tables — and must never be typed
// into this file. `prove_search_vocabulary.mjs` fails the build on a term that appears in the
// platform's carrier/product vocabulary.
//
// PURE and import-free, so `frontend/prove_search_vocabulary.mjs` drives the real functions under
// bare Node with no React, no network and no build step.

/** One set of spellings that mean the same thing. Order is irrelevant; membership is the fact.
 *  A term may be a PHRASE ("time off", "sick leave") — `phraseHits` finds those on word
 *  boundaries, which is the only way a two-word spelling can be matched at all, because the
 *  tokenizer splits "sick leave" into a content word and a word it has never heard of. */
export type VocabGroup = {
  /** Every spelling, lowercase. The group is symmetric: typing any one finds pages written with any other. */
  readonly terms: readonly string[]
  /** Why this grouping is safe, where that is not obvious from the terms, or which sense was chosen. */
  readonly note?: string
}

// ── THE VOCABULARY ───────────────────────────────────────────────────────────────────────────────
// Grouped by the part of the platform the words belong to, so a reader can check a group against
// the real page labels rather than against their own intuition. Every group below was checked
// against the live nav: each one has at least one real destination whose own text uses one of its
// terms, which `prove_search_vocabulary.mjs` re-checks against the registries on every run — a
// group whose words lead nowhere is a guess, and the build fails on it.
export const VOCABULARY: readonly VocabGroup[] = [
  // ── people and pay ─────────────────────────────────────────────────────────────────────────────
  { terms: ['pay', 'payroll', 'wage', 'wages', 'salary', 'salaries', 'earnings', 'paycheck',
            'paycheque', 'compensation', 'remuneration', 'take home'],
    note: 'Payroll, Employees & Pay, Total Comp, Payroll Expenses. "comp" is NOT here: it is the '
        + 'platform\'s own abbreviation for compensation AND for the carrier compensation report, '
        + 'so it would pull pay queries onto carrier pages.' },
  { terms: ['employee', 'employees', 'staff', 'personnel', 'headcount', 'head count', 'workforce',
            'people', 'worker', 'workers', 'team', 'teams'],
    note: 'Employees, Employee Database, My Team, Workforce, People (add employees).' },
  { terms: ['rep', 'reps', 'salesperson', 'salespeople', 'sales rep', 'seller', 'sellers',
            'associate', 'associates', 'advisor', 'advisors'],
    note: 'Rep Incentive Report, Rep Coaching, Rep Aliases, Rep to Store Map.' },
  { terms: ['manager', 'managers', 'management', 'supervisor', 'supervisors', 'dm',
            'district manager', 'store manager'],
    note: 'Manager Report Cards, Management Watchdog, Management Review, DM Verify, DM Visit Plan.' },

  // ── time, attendance and scheduling ────────────────────────────────────────────────────────────
  { terms: ['schedule', 'schedules', 'scheduling', 'rota', 'rotas', 'shift', 'shifts', 'timetable',
            'shift pattern'],
    note: 'Schedule, Shift Swaps, Shift Extensions, Staffing Heat Map. "roster" is deliberately '
        + 'absent: on this platform a roster is WHO a person may see, not when they work.' },
  { terms: ['time off', 'timeoff', 'holiday', 'holidays', 'vacation', 'annual leave', 'leave',
            'absence', 'absences', 'pto', 'sick', 'sick leave', 'sickness', 'sick day'],
    note: 'Time Off. Sickness and holiday are both booked as time off here, so they are one group; '
        + 'splitting them would make "sick leave" find nothing.' },
  { terms: ['attendance', 'lateness', 'late', 'tardy', 'tardiness', 'punctuality', 'absenteeism',
            'no show', 'noshow'],
    note: 'Attendance Exceptions, Lateness %.' },
  { terms: ['time clock', 'timeclock', 'clock in', 'clock out', 'clocking', 'punch', 'punches',
            'hours', 'worked hours', 'timesheet', 'timesheets'],
    note: 'Time Clock, Time-clock Permissions, Hours Approval, Hours Budget.' },

  // ── money: the top line ────────────────────────────────────────────────────────────────────────
  { terms: ['revenue', 'turnover', 'takings', 'income', 'top line', 'topline', 'gross sales',
            'sales volume', 'p l', 'pnl', 'p and l', 'profit and loss', 'income statement',
            'statement', 'statements'],
    note: 'P&L Statement, Gross Profit, Sales Report, Financial Analysis. The P&L spellings are '
        + 'here because the statement IS where revenue is read on this platform and no page is '
        + 'titled "Revenue" — without them "revenue" could reach no financial statement at all. '
        + 'The bare word "statement" is here for a blunt reason worth recording: the tokenizer drops '
        + 'one-letter words, so the title "P&L Statement" reduces to the single token "statement" '
        + 'and nothing else in it is searchable. "statement" is therefore DELIBERATELY ambiguous on '
        + 'this platform — it also belongs to the invoice group — and the lookup unions both, which '
        + 'is the honest behaviour for a word that really does mean two things here. '
        + '"turnover" is mapped to its REVENUE sense (British usage), not to staff turnover: this '
        + 'platform measures the latter as retention, which has its own group.' },
  { terms: ['profit', 'profits', 'profitability', 'margin', 'margins', 'markup', 'gross profit',
            'gp', 'net profit', 'bottom line', 'bottomline', 'p l', 'pnl', 'profit and loss'],
    note: 'Gross Profit, P&L Statement, Profit Centers, Spiff Impact, Financial Analysis. Shares '
        + 'the P&L spellings with the revenue group above: a statement carries both, and a term may '
        + 'belong to more than one group — the lookup unions them.' },
  { terms: ['expense', 'expenses', 'cost', 'costs', 'spend', 'spending', 'outgoings', 'overhead',
            'overheads'],
    note: 'Store Expenses, Closing Expenses, Payroll Expenses, Cost Centers, Expense Categories.' },
  { terms: ['tax', 'taxes', 'sales tax', 'vat', 'duty'],
    note: 'Tax Collected, Payroll (Tax).' },

  // ── money: what a person earns on top ──────────────────────────────────────────────────────────
  { terms: ['commission', 'commissions', 'incentive', 'incentives', 'bonus', 'bonuses', 'spiff',
            'spiffs', 'kicker', 'reward', 'rewards', 'payout', 'payouts'],
    note: 'Commission Ledger, Incentive Plans, Spiff Impact, Payout Schedules, Rep Incentive Report. '
        + 'One group because on this platform all of them are the same money reaching a person: the '
        + 'engine books a spiff and a bonus onto commission lines.' },
  { terms: ['chargeback', 'chargebacks', 'clawback', 'clawbacks', 'withholding', 'recoup',
            'recovery', 'reversal', 'reversals', 'deduction', 'deductions'],
    note: 'Commission Withholding, Chargebacks & Fraud, Appeal Recovery.' },
  { terms: ['residual', 'residuals', 'recurring revenue', 'trail', 'renewal'],
    note: 'Residual per Subscriber.' },

  // ── money: what comes back the other way ───────────────────────────────────────────────────────
  { terms: ['refund', 'refunds', 'return', 'returns', 'void', 'voids', 'voided', 'cancellation',
            'cancelled', 'credit note'],
    note: 'Void & Return Register, Watchdog voids and returns, Processor Debits & Credits.' },
  { terms: ['discount', 'discounts', 'waived', 'waiver', 'waived fee', 'markdown', 'concession',
            'promo', 'promotion', 'promotions', 'offer', 'offers'],
    note: 'Watchdog waived fees, Pricing Hotsheet, Price Compare. A money-off concept the platform '
        + 'reports on as a waived or promotional amount.' },
  { terms: ['rebate', 'rebates', 'reimbursement', 'reimbursements', 'claim', 'claims'],
    note: 'IMEI Rebates, Rebate Received, Vendor Rebate History, Reimbursement per Line.' },

  // ── cash and the daily close ───────────────────────────────────────────────────────────────────
  { terms: ['cash', 'till', 'drawer', 'float', 'petty cash', 'cash on hand', 'banking',
            'bank deposit', 'deposit', 'deposits'],
    note: 'Store Cash on Hand, Cash Pickup, Cash Deposit Recon, Cash Recon, Cash Setup.' },
  { terms: ['closing', 'close', 'end of day', 'eod', 'day end', 'cash up', 'cashup',
            'daily closing'],
    note: 'Submit Closing, Daily Closing Dashboard, Management Review, DM Verify.' },
  { terms: ['reconciliation', 'recon', 'reconcile', 'reconciled', 'tie out', 'tieout', 'balancing',
            'variance', 'discrepancy', 'discrepancies'],
    note: 'Reconciliation, 3-Way Tender Recon, Card Settlement Recon, Commission Discrepancy, '
        + 'Pay Discrepancy, Inventory Recon.' },
  { terms: ['tender', 'tenders', 'payment method', 'card', 'cards', 'credit card', 'settlement'],
    note: 'Tender Setup, X-Tender Recon, 3-Way Tender Recon, Card Settlement Recon.' },

  // ── things on the shelf ────────────────────────────────────────────────────────────────────────
  { terms: ['inventory', 'stock', 'stock on hand', 'on hand', 'shelf', 'warehouse', 'goods'],
    note: 'Inventory, Inventory Aging, Inventory Values, On-Inventory by Store, Inventory Recon.' },
  { terms: ['device', 'devices', 'handset', 'handsets', 'phone', 'phones', 'unit', 'units',
            'box', 'boxes'],
    note: 'Device History, Handset COGS, Device Cost Recon, Missing Phones, Product Mix & Ports.' },
  { terms: ['accessory', 'accessories', 'attachment', 'attach', 'attach rate', 'add on', 'addon',
            'add ons'],
    note: 'Accessory Targets, Accessory Target Allocation, Accessory Recon, Accessory Flags, '
        + 'Accessory Cost Audit, Accessory Definition.' },
  { terms: ['purchase order', 'po', 'pos order', 'ordering', 'procurement', 'requisition',
            'restock', 're order', 'reorder'],
    note: 'Purchase Orders, Supply Orders, Supply Cart, Special Orders, Price Compare.' },
  { terms: ['vendor', 'vendors', 'supplier', 'suppliers', 'distributor', 'distributors',
            'wholesaler'],
    note: 'Vendors, Supply Vendors, Distributors, Forecasting & Vendor Payables.' },
  { terms: ['invoice', 'invoices', 'bill', 'bills', 'statement', 'statements', 'payable',
            'payables', 'owed', 'due'],
    note: 'Invoices Due, Device Payable as at a date, Weekly Owed-to-Distributor, '
        + 'Current Monetary Liabilities.' },

  // ── the customer side ──────────────────────────────────────────────────────────────────────────
  { terms: ['customer', 'customers', 'client', 'clients', 'subscriber', 'subscribers',
            'account holder', 'buyer'],
    note: 'Customers, Customer Lookup, Residual per Subscriber.' },
  { terms: ['lead', 'leads', 'prospect', 'prospects', 'enquiry', 'enquiries', 'inquiry',
            'opportunity', 'opportunities', 'pipeline', 'funnel'],
    note: 'Leads, Log a Lead, Pipeline Board, My Follow-ups, Autopay Opportunity.' },
  { terms: ['activation', 'activations', 'new line', 'new lines', 'upgrade', 'upgrades',
            'sale', 'sales', 'sold'],
    note: 'Activations, Activation / Rebate Report, Sales Report, Sales Comparison.' },
  { terms: ['port', 'ports', 'port in', 'ported', 'porting', 'port out', 'switcher',
            'number transfer'],
    note: 'Product Mix & Ports, Watchdog port-outs. A network-neutral industry term for moving a '
        + 'telephone number between operators, so it names no carrier.' },
  { terms: ['churn', 'attrition', 'retention', 'cancel', 'cancels', 'disconnect', 'disconnects',
            'deactivation', 'lapsed'],
    note: 'Retention Analysis, Watchdog port-outs and churn.' },
  { terms: ['financing', 'finance plan', 'instalment', 'instalments', 'installment',
            'installments', 'credit', 'lease', 'leasing'],
    note: 'Financing, instalment payouts, Insurance & Leases, device lending.' },

  // ── keeping the place honest ───────────────────────────────────────────────────────────────────
  { terms: ['flag', 'flags', 'alert', 'alerts', 'exception', 'exceptions', 'finding', 'findings',
            'issue', 'issues', 'warning', 'warnings'],
    note: 'Flags, Accessory Flags, Attendance Exceptions, Management Watchdog.' },
  { terms: ['fraud', 'theft', 'shrinkage', 'shrink', 'abuse', 'suspicious', 'tampering'],
    note: 'Chargebacks & Fraud, Management Watchdog, Void & Return Register.' },
  { terms: ['audit', 'audit trail', 'log', 'logs', 'history', 'trail', 'who changed',
            'change log', 'activity log'],
    note: 'Access Log, Operator Audit Trail, Sign-in-as Audit, Failure Logs, Accessory Cost Audit, '
        + 'Device History.' },
  { terms: ['approval', 'approvals', 'approve', 'sign off', 'signoff', 'authorise', 'authorize',
            'pending approval'],
    note: 'Approvals, Hours Approval, Shift Approvals.' },
  { terms: ['target', 'targets', 'goal', 'goals', 'quota', 'quotas', 'budget', 'budgets',
            'objective', 'objectives'],
    note: 'Daily Targets, My Targets, Accessory Targets, Target Settings, Hours Budget, '
        + 'Accessory Target Allocation.' },
  { terms: ['kpi', 'kpis', 'metric', 'metrics', 'measure', 'measures', 'scorecard', 'scorecards',
            'performance'],
    note: 'Failing KPIs, KPI Metrics, KPI Definitions, Manager Report Cards.' },

  // ── running the platform ───────────────────────────────────────────────────────────────────────
  { terms: ['setting', 'settings', 'config', 'configuration', 'setup', 'preferences', 'options',
            'admin'],
    note: 'All Settings, POS Settings, Target Settings, Cash Setup, Store Setup, Employee Setup.' },
  { terms: ['permission', 'permissions', 'role', 'roles', 'access', 'rbac', 'entitlement',
            'who can see', 'rights'],
    note: 'Roles & Access, Time-clock Permissions, Security Settings.' },
  { terms: ['upload', 'uploads', 'import', 'imports', 'ingest', 'ingestion', 'load', 'feed',
            'feeds', 'intake'],
    note: 'Upload Files, Upload Wizard, Import, Import Health, FTP Auto-Import, Auto-Import, '
        + 'Ingest Guard, Sales Feed Recon.' },
  { terms: ['mapping', 'mappings', 'map', 'match', 'matching', 'translate', 'crosswalk',
            'alias', 'aliases'],
    note: 'All Mappings, Store Matching, Column Mapping, Item / Model Mapping, Carrier Mapping, '
        + 'Rep Aliases, GP Category Map.' },
  { terms: ['integration', 'integrations', 'connector', 'connectors', 'api', 'sync',
            'third party'],
    note: 'Integrations, Connectors, Payment Processor Sync, Metrics Rep/Store Sync.' },
  { terms: ['notification', 'notifications', 'notify', 'digest', 'digests', 'email', 'emails',
            'reminder', 'reminders', 'recipients'],
    note: 'Notify, Report Recipients, HR Communications.' },
  { terms: ['ticket', 'tickets', 'helpdesk', 'help desk', 'support', 'case', 'cases',
            'raise a ticket'],
    note: 'Tickets, Raise a Ticket, Helpdesk Dashboard, Support Console, Fix Requests.' },
  { terms: ['store', 'stores', 'location', 'locations', 'branch', 'branches', 'shop', 'shops',
            'site', 'sites'],
    note: 'Store Setup, Store Matching, Store Visits, Rep to Store Map, On-Inventory by Store.' },
  { terms: ['company', 'companies', 'tenant', 'tenants', 'organisation', 'organization', 'org',
            'entity', 'entities'],
    note: 'Companies, Companies (Tenants), Org Structure, Employee Org Chart, Business Types.' },
  { terms: ['report', 'reports', 'reporting', 'statement of', 'analysis', 'analytics',
            'dashboard', 'dashboards'],
    note: 'Report Center, Reports Index, POS Reports, CRM Reports, Custom Report, '
        + 'Financial Analysis.' },
  { terms: ['training', 'onboarding', 'walkthrough', 'walk through', 'tutorial', 'guide',
            'how to', 'documentation', 'docs', 'help'],
    note: 'Training Center, Onboarding Checklist, Walk-throughs, Help Docs, Setup Wizard.' },
  { terms: ['camera', 'cameras', 'cctv', 'video', 'footage', 'surveillance', 'vision'],
    note: 'Camera Setup, Live Cameras, Vision Dashboard, Floor Activity.' },
  { terms: ['traffic', 'footfall', 'foot traffic', 'visitors', 'walk ins', 'walkins',
            'door count', 'busy'],
    note: 'Traffic & Heat Map, Busy Hours, Staffing Heat Map, Peer Sales Comparison.' },
  { terms: ['review', 'reviews', 'rating', 'ratings', 'feedback', 'star rating', 'google review'],
    note: 'Google Reviews, Reviews Setup, Productivity & Reviews.' },
  { terms: ['royalty', 'royalties', 'franchise fee', 'franchise'],
    note: 'Royalty Report, Royalty vs Daily Sales.' },
  { terms: ['asset', 'assets', 'ledger', 'book value', 'depreciation', 'capital'],
    note: 'Assets Dashboard, Balance Sheet, Inventory Values, and the book of owned stock.' },
  { terms: ['event', 'events', 'promotion day', 'roadshow', 'pop up', 'popup', 'campaign',
            'campaigns', 'marketing'],
    note: 'Plan an Event, Events Dashboard, Sales from Events, Marketing Settings.' },
  { terms: ['referral', 'referrals', 'refer', 'referred', 'introduction'],
    note: 'New Referral, Referrals, Referral Dashboard.' },
  { terms: ['coaching', 'coach', 'development', 'one to one', 'improvement plan', 'action plan',
            'performance plan'],
    note: 'Rep Coaching, Coaching, Action Plan, Productivity Insights.' },
  { terms: ['forecast', 'forecasting', 'projection', 'projected', 'prediction', 'outlook',
            'what if', 'whatif', 'scenario'],
    note: 'Forecasting & Vendor Payables, What-If Analysis, What Would I Make?' },
]

// ── WORDS DELIBERATELY NOT MAPPED ────────────────────────────────────────────────────────────────
// The honest half of a vocabulary. Each of these was typed, found nothing, and was LEFT finding
// nothing, because the platform has no page that answers it. Mapping one to its nearest-looking
// page would be the `search-never-guesses` defect (§54.10) re-introduced through a side door: the
// console would stop saying "I did not find that" and start handing over a confident wrong page.
//
// This is a REGISTER, not a to-do list. An entry leaves it when the platform gains the thing, not
// when someone finds a page that is roughly about it.
export const UNMAPPED: Readonly<Record<string, string>> = {
  'trade in': 'No page records a device taken in part-exchange. Device History is the nearest and '
            + 'is about a device the store SOLD, so mapping it would answer a different question.',
  'tip': 'Tips are not captured anywhere on this platform.',
  'mileage': 'No expense category for travel exists; Store Expenses is per-store, not per-journey.',
  'uniform': 'Not tracked. Supply Ordering is for consumables and fixtures.',
  'appraisal': 'No performance-review record exists. Rep Coaching is a prompt, not a review cycle.',
  'grievance': 'No HR case management. HR Communications is one-way messaging.',
  'maternity': 'Statutory leave types are not modelled; Time Off has no leave-type breakdown.',
  'pension': 'Not modelled in payroll: there is no employer-contribution or scheme record '
           + 'anywhere, so Payroll Expenses would answer a different question.',
}
// 'insurance claim' was in this register and the proof (§G1) threw it out: "insurance" really does
// reach Insurance & Leases and "claim" really does reach the rebate group, so the word was never
// finding nothing and had no business being registered as if it were. Recorded here because the
// mistake is the instructive kind — a register of what cannot be found goes stale the moment the
// platform grows, which is exactly why §G1 re-checks every entry against the live catalogue on
// every run rather than trusting the comment beside it.

// ── LOOKUP ───────────────────────────────────────────────────────────────────────────────────────
const normTerm = (s: string) => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()

/** Single-word term -> every OTHER term of every group it belongs to, PHRASES INCLUDED. Built once.
 *
 *  A phrase is kept as an expansion TARGET even though it can never be a typed token, and that is
 *  deliberate: roughly half this platform's page titles are two words ("Time Off", "Gross Profit",
 *  "Purchase Orders"), and a scorer compares a candidate against the whole label as well as against
 *  its words — so the phrase 'time off' matches the title "Time Off" exactly while neither 'time'
 *  nor 'off' alone identifies it. Dropping phrases here cost the obvious answer on the first run:
 *  "holiday" surfaced Time Clock above Time Off, and "margin" surfaced GP Category Map above Gross
 *  Profit, in both cases because only a weak single word could reach the right page.
 *
 *  Only SINGLE words are keys, because only a single word arrives as a token; `phraseHits` is what
 *  recognises a phrase on the typed side. */
const BY_WORD: Map<string, Set<string>> = (() => {
  const m = new Map<string, Set<string>>()
  for (const g of VOCABULARY) {
    const all = g.terms.map(normTerm).filter(Boolean)
    for (const t of all) {
      if (t.includes(' ')) continue
      let set = m.get(t)
      if (!set) { set = new Set(); m.set(t, set) }
      for (const s of all) if (s !== t) set.add(s)
    }
  }
  return m
})()

/** Every multi-word term, longest first, with the single words its group contributes. Longest
 *  first so "sick leave" wins over "leave" and the more specific phrase decides. */
const PHRASES: { phrase: string; words: string[] }[] = (() => {
  const out: { phrase: string; words: string[] }[] = []
  for (const g of VOCABULARY) {
    const all = g.terms.map(normTerm).filter(Boolean)
    for (const t of all) {
      if (t.includes(' ')) out.push({ phrase: t, words: all.filter(x => x !== t) })
    }
  }
  out.sort((a, b) => b.phrase.length - a.phrase.length || a.phrase.localeCompare(b.phrase))
  return out
})()

/** The other words that mean the same as `t`. Empty for a word the vocabulary has never heard of,
 *  and empty for a digit run — "509" means 509 and nothing else. */
export function synonymsOf(t: string): string[] {
  const k = normTerm(t)
  if (!k || /\d/.test(k)) return []
  return Array.from(BY_WORD.get(k) || [])
}

/** The terms contributed by every multi-word spelling the query contains, matched on WORD
 *  BOUNDARIES so "leaver" never counts as "leave". This is what lets "sick leave", "time off",
 *  "head count" and "attach rate" find anything at all. Phrases are included in what is
 *  contributed, for the same reason `BY_WORD` keeps them: a two-word page title needs a two-word
 *  candidate to match it exactly. */
export function phraseHits(q: string): string[] {
  const hay = ' ' + normTerm(q) + ' '
  const out = new Set<string>()
  for (const { phrase, words } of PHRASES) {
    if (hay.includes(' ' + phrase + ' ')) for (const w of words) out.add(w)
  }
  return Array.from(out)
}

/** Is this a word the vocabulary deliberately leaves finding nothing? The console may say so
 *  instead of shrugging; the ranker does not consult it, because an unmapped word must behave
 *  exactly as any other unknown word. */
export function unmappedReason(q: string): string | undefined {
  const k = normTerm(q)
  for (const [term, why] of Object.entries(UNMAPPED)) if (k === normTerm(term)) return why
  return undefined
}
