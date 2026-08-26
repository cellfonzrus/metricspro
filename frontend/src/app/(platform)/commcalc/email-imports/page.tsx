'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import { api, apiUpload } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { WhereAreMyRowsButton } from '../_lib/UploadTracePanel'
import { SweepStatusCell, summarizeSweepRun } from '../_lib/sweepOutcome'
import EntityPicker from '@/components/EntityPicker'

// Generic email (IMAP) inbox sweep — sibling of the FTP sweep. Configure a mailbox (host/creds) and
// attachment-filename → upload-type patterns; the backend polls the inbox on a schedule and routes
// each matching attachment to the right parser. For B2B Soft (or any vendor) that EMAILS report files.
const BUILTIN_TYPES = ['sales', 'daily_sales', 'payment_detail', 'mi_report', 'dlar_rep', 'dlar_store', 'comp_report', 'catalog', 'inventory_aging', 'x_report', 'ma_commission', 'ma_daily_tx', 'ma_fulfillment']
const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 13 }
const lbl: React.CSSProperties = { fontSize: 12, fontWeight: 600, display: 'block', marginBottom: 2 }

// One-click IMAP presets so a user can add a Gmail/Yahoo/Outlook/etc. mailbox without knowing servers.
const PROVIDERS: Record<string, { label: string; imap_host: string; imap_port: number; use_ssl: boolean; hint?: string }> = {
  custom:  { label: 'Custom / other (enter manually)', imap_host: '', imap_port: 993, use_ssl: true },
  gmail:   { label: 'Gmail / Google Workspace', imap_host: 'imap.gmail.com', imap_port: 993, use_ssl: true, hint: 'Gmail needs an App Password (Google Account → Security → 2-Step Verification → App passwords) — not your normal password. Paste the 16-character code (the displayed spaces are fine, they’re stripped automatically). Username = the full @gmail address.' },
  outlook: { label: 'Outlook / Hotmail / Live / MSN', imap_host: 'outlook.office365.com', imap_port: 993, use_ssl: true, hint: 'Microsoft accounts with 2FA need an App Password (account.microsoft.com → Security → Advanced security options).' },
  yahoo:   { label: 'Yahoo Mail', imap_host: 'imap.mail.yahoo.com', imap_port: 993, use_ssl: true, hint: 'Yahoo requires an App Password (Account Info → Account Security → Generate app password).' },
  aol:     { label: 'AOL Mail', imap_host: 'imap.aol.com', imap_port: 993, use_ssl: true, hint: 'AOL requires an App Password (Account Security → Generate app password).' },
  icloud:  { label: 'iCloud Mail', imap_host: 'imap.mail.me.com', imap_port: 993, use_ssl: true, hint: 'iCloud requires an app-specific password (appleid.apple.com → Sign-In and Security).' },
  zoho:    { label: 'Zoho Mail', imap_host: 'imap.zoho.com', imap_port: 993, use_ssl: true },
  gmx:     { label: 'GMX', imap_host: 'imap.gmx.com', imap_port: 993, use_ssl: true },
}
const providerOf = (host: string) =>
  Object.keys(PROVIDERS).find(k => PROVIDERS[k].imap_host && PROVIDERS[k].imap_host === (host || '')) || 'custom'

export default function EmailImportsPage() {
  // A brand-new mailbox starts with the standard b2bsoft rules — an empty rules list silently
  // matches NOTHING ("0/0 ingested" with reports sitting in the inbox), which bit the Total setup.
  const DEFAULT_RULES = [
    { pattern: '*Sales*Transaction*Details*', upload_type: 'daily_sales', note: 'daily B2B sales export (use the "for Metrics pro" custom report — full columns)' },
    { pattern: '*Inventory*Aging*', upload_type: 'inventory_aging', note: 'b2bsoft inventory aging → Asset / Inventory Recon' },
    { pattern: '*X-Report*', upload_type: 'x_report', note: 'POS X-report tender summary → Daily Closing cash/credit recon' },
  ]
  const BLANK = { imap_port: 993, use_ssl: true, mailbox: 'INBOX', since_days: 14, patterns: DEFAULT_RULES as any[], frequency: 'daily', hour: 7 }
  const [cfg, setCfg] = useState<any>({ account: 'default', ...BLANK })
  const [accounts, setAccounts] = useState<any[]>([])
  const [pwd, setPwd] = useState('')
  const [test, setTest] = useState<any>(null)
  const [processed, setProcessed] = useState<any[]>([])
  const [health, setHealth] = useState<any>(null)   // per-day ingest health (mig 200)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [upPeriod, setUpPeriod] = useState('')   // period for a manual custom-sheet upload (e.g. "August 2026")
  const [upBusy, setUpBusy] = useState('')        // report_key currently uploading
  const [sources, setSources] = useState<any[]>([])
  const [srcReady, setSrcReady] = useState(true)
  const [srcDraft, setSrcDraft] = useState<any>(null)   // add/edit form for a data-source login
  const [srcMsg, setSrcMsg] = useState('')
  const [proxyTest, setProxyTest] = useState<any>(null) // { proxy, direct, routed_through_proxy, is_us, summary }
  const [proxyBusy, setProxyBusy] = useState(false)
  // 🔧 What the pull saw — the per-report outcome + the portal's own report vocabulary (mig 242).
  const [pullDiag, setPullDiag] = useState<any>(null)

  async function testProxy() {
    const px = (srcDraft?.proxy_url || '').trim()
    if (!px) { setProxyTest({ summary: 'Enter a proxy URL first (http://user:pass@host:port).' }); return }
    setProxyBusy(true); setProxyTest(null)
    try {
      const r = await api('/api/v1/commcalc/data-source/test-proxy', { method: 'POST', body: JSON.stringify({ proxy_url: px }) })
      setProxyTest(r)
    } catch (e: any) { setProxyTest({ summary: '❌ ' + (e?.message || e) }) }
    finally { setProxyBusy(false) }
  }
  const [carriers, setCarriers] = useState<any[]>([])
  const [distributors, setDistributors] = useState<any[]>([])
  const [twoFa, setTwoFa] = useState<any>(null)     // { source, hint } while a 2FA code is needed
  const [code, setCode] = useState('')
  const [authBusy, setAuthBusy] = useState('')
  const [shotView, setShotView] = useState<any>(null)   // { label, loading, src, at, note } — last login screenshot
  const [live, setLive] = useState<any>(null)           // { source } while the 🔴 Live-login modal is open
  const [liveState, setLiveState] = useState<any>(null) // { phase, message, shot, seq } — driven by the ~300ms frame poll
  const [liveCode, setLiveCode] = useState('')
  const [liveBusy, setLiveBusy] = useState(false)
  const [liveFocused, setLiveFocused] = useState(false) // true while the live view has keyboard focus (drives the focus ring)
  const liveSeqRef = useRef(0)                           // last frame seq we've shown — sent as ?since= so we only fetch a NEW JPEG
  const liveViewRef = useRef<HTMLDivElement | null>(null) // the focusable live-view container (keyboard + wheel target)
  const wheelTsRef = useRef(0)                           // throttle stamp so a scroll gesture doesn't flood the input queue
  const [customTypes, setCustomTypes] = useState<any[]>([])   // self-serve custom sheets (mig 099)
  const [newSheet, setNewSheet] = useState('')
  const [viewer, setViewer] = useState<any>(null)             // { report_key, label } while viewing data
  const [viewData, setViewData] = useState<any>(null)

  // Load all of the tenant's mailboxes (multi-mailbox = mig 075); keep or select one in the editor.
  const refresh = useCallback((keepAccount?: string) => {
    api('/api/v1/commcalc/email-sweep/accounts').then((r: any) => {
      const list: any[] = r.accounts || []
      setAccounts(list)
      setCfg((cur: any) => {
        const want = keepAccount ?? cur?.account
        const found = list.find(a => a.account === want) || list[0]
        return found ? { ...found, patterns: found.patterns || [] } : cur
      })
    }).catch(() => {
      api('/api/v1/commcalc/email-sweep/config').then((c: any) => setCfg({ ...c, patterns: c.patterns || [] })).catch(() => {})
    })
    api('/api/v1/commcalc/email-sweep/processed').then((p: any) => setProcessed(p || [])).catch(() => {})
    api('/api/v1/commcalc/data-sources').then((r: any) => { setSources(r.sources || []); setSrcReady(r.ready !== false) }).catch(() => {})
    apiCached('/api/v1/commcalc/carriers', LOOKUP).then((r: any) => setCarriers(r || [])).catch(() => {})
    api('/api/v1/commcalc/distributors').then((r: any) => setDistributors(Array.isArray(r) ? r : (r?.distributors || []))).catch(() => {})
    api('/api/v1/commcalc/custom-import-types').then((r: any) => setCustomTypes(Array.isArray(r) ? r : [])).catch(() => {})
  }, [])
  useEffect(() => { refresh() }, [refresh])

  // Per-day ingest health for the selected mailbox — reloads when the mailbox or its import history changes.
  useEffect(() => {
    const acct = cfg.account || 'default'
    api(`/api/v1/commcalc/email-sweep/ingest-health?account=${encodeURIComponent(acct)}&days=14`)
      .then((r: any) => setHealth(r)).catch(() => setHealth(null))
  }, [cfg.account, processed])

  const set = (patch: any) => setCfg((c: any) => ({ ...c, ...patch }))
  const setPat = (i: number, patch: any) => setCfg((c: any) => ({ ...c, patterns: c.patterns.map((p: any, j: number) => j === i ? { ...p, ...patch } : p) }))
  const addPat = () => setCfg((c: any) => ({ ...c, patterns: [...(c.patterns || []), { pattern: '', upload_type: 'daily_sales', note: '' }] }))
  const delPat = (i: number) => setCfg((c: any) => ({ ...c, patterns: c.patterns.filter((_: any, j: number) => j !== i) }))
  const knownTypeKeys = new Set([...BUILTIN_TYPES, ...customTypes.map((c: any) => c.report_key)])

  const body = () => ({ ...cfg, password: pwd || undefined })

  // Live-login low-latency frame poll: hit /live-login/frame?since=<seq> (~300ms). The backend returns a
  // NEW JPEG only when its frame seq advanced past `since` (else a tiny unchanged payload) — so we swap the
  // <img> only on a real frame change while phase/message stay fresh every tick. Coalesces the CDP screencast
  // (or the ~300ms screenshot fallback) the worker pumps into one cheap poll.
  const refreshFrame = useCallback(async () => {
    const id = live?.source?.id
    if (!id) return
    try {
      const r: any = await api(`/api/v1/commcalc/data-sources/${id}/live-login/frame?since=${liveSeqRef.current}`)
      if (!r) return
      if (r.phase === 'idle' && !r.shot) return   // session not up yet — keep the local 'starting' state, don't flash idle
      liveSeqRef.current = r.seq ?? liveSeqRef.current
      setLiveState((p: any) => {
        const next: any = { ...(p || {}), phase: r.phase, message: r.message, seq: r.seq }
        if (r.changed && r.shot) next.shot = r.shot   // only replace the frame when a newer one actually arrived
        return next
      })
    } catch { /* keep the last frame on a transient poll error */ }
  }, [live?.source?.id])

  useEffect(() => {
    const id = live?.source?.id
    if (!id) return
    liveSeqRef.current = 0
    refreshFrame()
    const iv = setInterval(refreshFrame, 300)   // ~3.3 polls/s — matches the worker's frame pump cadence
    return () => clearInterval(iv)
  }, [live?.source?.id, refreshFrame])

  function pickAccount(acct: string) {
    const a = accounts.find(x => x.account === acct)
    if (a) { setCfg({ ...a, patterns: a.patterns || [] }); setPwd(''); setTest(null); setMsg('') }
  }
  function addMailbox() {
    const key = (prompt('Short key for the new mailbox (letters/numbers, e.g. "total"):', '') || '').trim().toLowerCase().replace(/[^a-z0-9_]/g, '')
    if (!key) return
    if (accounts.some(a => a.account === key)) { setMsg('That mailbox key already exists — pick it from the list.'); return }
    setPwd(''); setTest(null); setMsg('New mailbox — fill in the details and Save.')
    setCfg({ account: key, label: '', ...BLANK, enabled: false })
  }
  async function delMailbox() {
    if (!cfg.account || cfg.account === 'default') return
    if (!confirm(`Delete mailbox "${cfg.label || cfg.account}" and its import history?`)) return
    try { await api(`/api/v1/commcalc/email-sweep/account/${encodeURIComponent(cfg.account)}`, { method: 'DELETE' }); setMsg('🗑️ Deleted.'); refresh('default') }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  async function save(acknowledge = false) {
    setBusy('save')
    try {
      const payload = acknowledge ? { ...body(), acknowledge_cross_org: true } : body()
      const r: any = await api('/api/v1/commcalc/email-sweep/config', { method: 'PUT', body: JSON.stringify(payload) })
      // MISFILE GUARD: the backend refuses to persist an ENABLED save when the same address is already
      // enabled under another tenant (both would ingest the same inbox). Confirm-to-override.
      if (r && r.ok === false && r.warning === 'cross_org_mailbox') {
        const others = (r.conflicts || []).map((c: any) => c.label || c.account || c.org_id).join(', ')
        if (confirm(`⚠️ MISFILE RISK\n\n${r.message}\n\nAlready enabled under: ${others}\n\nSave anyway? Both tenants will then ingest this inbox.`)) { setBusy(''); return save(true) }
        setMsg('⚠️ Not saved — this mailbox is enabled under another tenant (cross-tenant misfile risk).'); return
      }
      setPwd(''); setMsg(r?.warning === 'cross_org_mailbox' ? '✅ Saved · ⚠️ note: this address is also configured under another tenant.' : '✅ Saved.'); refresh(r.account || cfg.account)
    }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy('') }
  }

  // One-click: bring this mailbox's filename rules + schedule to the b2bsoft POS standard (mig 200).
  // Strictly additive on the backend — adds any missing standard rule, fills blank defaults, seeds the
  // report registry; never clobbers creds or an existing rule. The tenant still enters host + password.
  async function applyStandard() {
    setBusy('apply')
    try {
      const r: any = await api(`/api/v1/commcalc/pos-profiles/b2bsoft/apply?account=${encodeURIComponent(cfg.account || 'default')}`, { method: 'POST', body: '{}' })
      setMsg(r?.ok
        ? `✅ Applied the b2bsoft standard — ${r.rules_added} rule(s) added (${r.rules_total} total)${r.reports_seeded ? `, ${r.reports_seeded} report(s) registered` : ''}.${r.needs_credentials ? ' Now enter the IMAP host + password below and enable it.' : ''}`
        : `❌ ${r?.error || 'could not apply the standard profile'}`)
      refresh(cfg.account)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy('') }
  }
  async function testConn() {
    setBusy('test'); setTest(null)
    try {
      const r: any = await api('/api/v1/commcalc/email-sweep/test', { method: 'POST', body: JSON.stringify(body()) }); setTest(r)
      if (!(cfg.patterns || []).some((p: any) => (p.pattern || '').trim())) setMsg(`⚠️ Connected (${r.count} message(s)) — but NO filename rules are configured below, so nothing will ever import. Add a rule like *Sales*Transaction*Details* → daily sales, then Save.`)
      else if (r.count > 0 && r.matched_attachments === 0) setMsg(`⚠️ Connected — ${r.count} message(s) found but 0 attachments match your rules. Check the attachment names listed below and adjust the rule patterns.`)
      else setMsg(`✅ Connected — ${r.count} recent message(s), ${r.matched_attachments} matching attachment(s).`)
    }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy('') }
  }
  async function runNow() {
    setBusy('run')
    try {
      const r: any = await api(`/api/v1/commcalc/email-sweep/run-now?account=${encodeURIComponent(cfg.account || 'default')}`, { method: 'POST', body: '{}' })
      const guardSkips = (r.files || []).filter((f: any) => f.status === 'skipped' && String(f.skipped || '').startsWith('price_guard'))
      // A PARTIAL price-guard ingest comes back status='ok' (rows saved) with skipped='price_guard_partial'.
      const partials = (r.files || []).filter((f: any) => f.skipped === 'price_guard_partial')
      // Everything else worth saying — 0-row refusals with their reason, files read but carrying no
      // ingestable rows (`empty`), types with no importer (`ignored`), errors, download failures,
      // retries, and a failure to record the sweep's OWN history row. Shared with the FTP page so the
      // two sweeps report identically.
      const extra = summarizeSweepRun(r)
      const partNote = partials.length ? ` · ⚠️ ${partials.length} partial (ingested fresh day(s), kept existing data for degraded day(s))` : ''
      setMsg(!r.ok ? `❌ ${r.error}`
        : r.ingested > 0 ? `✅ Ingested ${r.ingested} attachment(s).${extra ? ' · ' + extra : ''}${partNote}`
        : guardSkips.length ? `⚠️ 0 ingested — ${guardSkips.length} file(s) refused by the price guard: a degraded/price-less export arrived and the fuller data already stored for that day was kept. Re-send the full "Sales Transaction Details" (with Ext Price + GP). · ${extra}`
        : extra ? `⚠️ 0 ingested — ${extra}`
        : !(cfg.patterns || []).some((p: any) => (p.pattern || '').trim())
          ? '⚠️ 0 ingested — this mailbox has NO filename rules, so nothing can match. Add the rules below and Save.'
          : '⚠️ 0 ingested — nothing new: matched files already imported OK (errored/refused ones auto-retry), or none match your rules. Use Test connection to see the attachment names + which rule they hit.')
      refresh(cfg.account)
    }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy('') }
  }

  // Suggest a filename rule from an attachment name — recognizes the standard b2bsoft/portal reports,
  // else builds a glob from the distinctive tokens. Turns a "no pattern" attachment into a saved rule
  // in ONE click (no hand-typed globs), then re-tests so the user sees it match immediately.
  function suggestRule(name: string): { pattern: string; upload_type: string } {
    const low = (name || '').toLowerCase()
    const known: [RegExp, string, string][] = [
      [/sales.*transaction.*details/, '*Sales*Transaction*Details*', 'daily_sales'],
      [/inventory.*aging/, '*Inventory*Aging*', 'inventory_aging'],
      [/x.?report/, '*X-Report*', 'x_report'],
      [/commission.*detail/, '*Commission*Details*', 'ma_commission'],
      [/daily.*tx/, '*Daily*Tx*', 'ma_daily_tx'],
      [/fulfillment/, '*Fulfillment*', 'ma_fulfillment'],
      [/payment.*detail/, '*Payment*Detail*', 'payment_detail'],
    ]
    for (const [re, pat, ut] of known) if (re.test(low)) return { pattern: pat, upload_type: ut }
    const base = (name || '').replace(/\.[a-z0-9]+$/i, '')
    const toks = base.split(/[^a-zA-Z0-9]+/).filter(t => t.length > 2).slice(0, 3)
    return { pattern: toks.length ? '*' + toks.join('*') + '*' : '*' + base + '*', upload_type: 'daily_sales' }
  }
  async function addRuleFor(name: string) {
    const s = suggestRule(name)
    const next = [...(cfg.patterns || []), { pattern: s.pattern, upload_type: s.upload_type, note: 'added from Test connection' }]
    setCfg((c: any) => ({ ...c, patterns: next }))
    setBusy('save')
    try {
      await api('/api/v1/commcalc/email-sweep/config', { method: 'PUT', body: JSON.stringify({ ...body(), patterns: next }) })
      const r: any = await api('/api/v1/commcalc/email-sweep/test', { method: 'POST', body: JSON.stringify({ ...body(), patterns: next }) })
      setTest(r)
      setMsg(`✅ Rule added: ${s.pattern} → ${s.upload_type}. ${r.matched_attachments || 0} attachment(s) now match — click “Run now” to import.`)
    } catch (e: any) { setMsg('Could not add rule: ' + (e?.message || e)) }
    finally { setBusy('') }
  }

  // Self-serve custom sheets (mig 099): add a report by name, then route a filename pattern to its key.
  const reloadCustom = () => api('/api/v1/commcalc/custom-import-types').then((r: any) => setCustomTypes(Array.isArray(r) ? r : [])).catch(() => {})
  async function addCustomSheet() {
    const label = newSheet.trim()
    if (!label) return
    try {
      const r: any = await api('/api/v1/commcalc/custom-import-types', { method: 'POST', body: JSON.stringify({ label }) })
      setNewSheet(''); await reloadCustom()
      setMsg(`✅ Added custom sheet "${r.label}" (key: ${r.report_key}). Now add a filename pattern above that routes to it.`)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function delCustomSheet(rk: string, label: string) {
    if (!confirm(`Remove custom sheet "${label}"? (Imported data is kept unless you also purge it.)`)) return
    const purge = confirm('Also DELETE all captured rows for this sheet?\n\nOK = delete the data too · Cancel = keep the data')
    try {
      await api(`/api/v1/commcalc/custom-import-types/${encodeURIComponent(rk)}?purge=${purge}`, { method: 'DELETE' })
      await reloadCustom()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function openViewer(c: any) {
    setViewer(c); setViewData(null)
    try { const r: any = await api(`/api/v1/commcalc/custom-import/${encodeURIComponent(c.report_key)}`); setViewData(r) }
    catch (e: any) { setViewData({ error: e?.message || String(e) }) }
  }
  // Manual upload for a custom sheet — for the MTD file (and daily files) when you don't want to wait for
  // the email sweep. POSTs straight to /upload/<report_key>, the SAME capture the sweep uses, so the row
  // lands in raw_custom_import and the report's dataset (Activations / Bill Payments / Sales by Product)
  // lights up. `period` scopes the capture: a re-upload of the same period REPLACES it (the b2b MTD export
  // is cumulative, so re-uploading the latest MTD file is correct); leave it blank to capture by filename.
  async function uploadCustom(rk: string, label: string, file: File | null | undefined) {
    if (!file) return
    const per = (upPeriod || '').trim()
    setUpBusy(rk)
    try {
      const form = new FormData(); form.append('file', file)
      const path = `/api/v1/commcalc/upload/${encodeURIComponent(rk)}${per ? `?period=${encodeURIComponent(per)}` : ''}`
      const r: any = await apiUpload(path, form)
      setMsg(`✅ Uploaded "${file.name}" to ${label} — ${r?.saved ?? 0} rows captured${per ? ` for ${per}` : ''}.`)
      await reloadCustom()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setUpBusy('') }
  }

  async function saveSource() {
    if (!srcDraft) return
    setSrcMsg('')
    try {
      await api('/api/v1/commcalc/data-sources', { method: 'PUT', body: JSON.stringify(srcDraft) })
      setSrcDraft(null); setSrcMsg('✅ Saved.')
      const r: any = await api('/api/v1/commcalc/data-sources'); setSources(r.sources || [])
    } catch (e: any) { setSrcMsg('❌ ' + (e?.message || e)) }
  }
  async function delSource(s: any) {
    if (!confirm(`Delete login "${s.label || s.username || s.processor}"?`)) return
    try { await api(`/api/v1/commcalc/data-sources/${s.id}`, { method: 'DELETE' }); const r: any = await api('/api/v1/commcalc/data-sources'); setSources(r.sources || []) }
    catch (e: any) { setSrcMsg('❌ ' + (e?.message || e)) }
  }
  // A pull that imported NOTHING is never a ✅. Before 2026-07-27 this rendered
  // "✅ pulled 0 rows across 0 report(s)" — a green tick on a connector that had never delivered a
  // single row, which is exactly what the owner reported as "shows logged in but imports nothing".
  function pullMsg(s: any, r: any) {
    const delivered = r?.delivered !== undefined ? !!r.delivered : Number(r?.rows_ingested || 0) > 0
    const text = r?.status || r?.error || (delivered ? 'Pulled.' : 'Nothing imported.')
    return delivered ? `✅ ${text}` : `⚠️ ${text}`
  }
  // ── PORTAL COOLDOWN (mig 244) ─────────────────────────────────────────────────────────────────
  // Owner report 2026-07-27: VidaPay answered "you have too many requests, and have been temporarily
  // blocked". A HUMAN may still try during a cooldown — but never by accident. The backend returns
  // { blocked:true, requires_confirm:true, warning } instead of acting, and the second, deliberate
  // click re-sends the SAME call with ?confirm=true. Retrying into an active block extends it, which
  // is exactly what happened on the 27th.
  function blockTime(v: any): string {
    if (!v) return 'later'
    try { return new Date(v).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) } catch { return String(v) }
  }
  function confirmBlocked(r: any): boolean {
    const warn = r?.warning || 'The portal has rate-limited us. Another attempt may extend the block.'
    return confirm(`⛔ ${warn}\n\n${r?.block_reason || ''}`.trim())
  }
  async function runSource(s: any, confirmed = false) {
    setSrcMsg('⏳ Pulling…')
    let zero = false
    try {
      const q = confirmed ? '?confirm=true' : ''
      const r: any = await api(`/api/v1/commcalc/data-sources/${s.id}/run${q}`, { method: 'POST', body: '{}' })
      if (r.blocked && r.requires_confirm) {
        setSrcMsg(`⛔ ${r.error}`)
        try { const l: any = await api('/api/v1/commcalc/data-sources'); setSources(l.sources || []) } catch { /* keep */ }
        if (confirmBlocked(r)) return runSource(s, true)
        return
      }
      if (r.needs_2fa) { setSrcMsg(`🔒 ${r.error}`); setTwoFa({ source: s, hint: null }) }
      else if (!r.ok) setSrcMsg(`⚠️ ${r.error}`)
      else { setSrcMsg(pullMsg(s, r)); zero = !(r?.delivered !== undefined ? r.delivered : Number(r?.rows_ingested || 0) > 0) }
    } catch (e: any) { setSrcMsg('❌ ' + (e?.message || e)) }
    try { const r: any = await api('/api/v1/commcalc/data-sources'); setSources(r.sources || []) } catch { /* keep list */ }
    if (zero) openPullDiag(s)   // 0 rows ⇒ put the WHY on screen without making anyone hunt for it
  }
  // Operator escape hatch: lift a cooldown by hand (import-admin only). Deliberately worded as a last
  // resort — clearing a cooldown and retrying into a LIVE block is what escalated the 27th.
  async function clearBlock(s: any) {
    if (!confirm('Only do this if you KNOW the portal has released us.\n\nIf it is still blocking, the '
      + 'next attempt re-arms the cooldown for LONGER. Lift it anyway?')) return
    try {
      const r: any = await api(`/api/v1/commcalc/data-sources/${s.id}/clear-block`, { method: 'POST', body: '{}' })
      setSrcMsg('✅ ' + (r?.message || 'Cooldown lifted.'))
    } catch (e: any) { setSrcMsg('❌ ' + (e?.message || e)) }
    try { const r: any = await api('/api/v1/commcalc/data-sources'); setSources(r.sources || []) } catch { /* keep */ }
  }
  // The durable "what the last pull saw" record (mig 242): every report the pull tried, why each one
  // failed, and the report names this portal's own dropdown offers — the vocabulary to fix Report
  // mapping with. Serves the module's own calibration strategy, which until now had no last mile.
  async function openPullDiag(s: any) {
    setPullDiag({ id: s.id, label: s.label || s.username || s.processor, loading: true })
    try {
      const r: any = await api(`/api/v1/commcalc/data-sources/${s.id}/pull-diagnostic`)
      setPullDiag({ id: s.id, label: s.label || s.username || s.processor, loading: false, ...r })
    } catch (e: any) {
      setPullDiag({ id: s.id, label: s.label || s.username || s.processor, loading: false, note: '❌ ' + (e?.message || e) })
    }
  }
  // Interactive portal login: submit the stored 3 creds → land on the 2FA challenge → the operator
  // enters the code from their email/SMS → the authenticated session is saved for scheduled pulls.
  async function startLogin(s: any, confirmed = false) {
    setAuthBusy(s.id); setSrcMsg('🔐 Signing in…'); setCode('')
    try {
      const q = confirmed ? '?confirm=true' : ''
      const r: any = await api(`/api/v1/commcalc/data-sources/${s.id}/login/start${q}`, { method: 'POST', body: '{}' })
      if (r.blocked && r.requires_confirm) {
        setSrcMsg(`⛔ ${r.error}`); setAuthBusy('')
        if (confirmBlocked(r)) return startLogin(s, true)
        return
      }
      if (r.status === 'authenticated') { setSrcMsg('✅ ' + r.message); setTwoFa(null); setAuthBusy('') }
      else if (r.status === 'needs_2fa') { setSrcMsg('📩 ' + r.message); setTwoFa({ source: s, hint: r.two_fa_hint }); setAuthBusy('') }
      else { setSrcMsg('⏳ ' + (r.message || 'Logging in…')); pollLogin(s.id) }   // 'authenticating' → poll the row
    } catch (e: any) { setSrcMsg('❌ ' + (e?.message || e)); setTwoFa(null); setAuthBusy('') }
    try { const r: any = await api('/api/v1/commcalc/data-sources'); setSources(r.sources || []) } catch { /* keep */ }
  }
  // The login runs in the background (Playwright through the proxy is slow); poll the row until it flips.
  async function pollLogin(id: string) {
    for (let i = 0; i < 40; i++) {           // ~2 min (40 × 3s)
      await new Promise(res => setTimeout(res, 3000))
      let row: any
      try { const r: any = await api('/api/v1/commcalc/data-sources'); setSources(r.sources || []); row = (r.sources || []).find((x: any) => x.id === id) } catch { continue }
      if (!row) continue
      const st = row.auth_status
      if (st === 'needs_2fa') { setSrcMsg('📩 ' + (row.auth_message || 'Enter your 2FA code.')); setTwoFa({ source: row, hint: row.two_fa_hint }); setAuthBusy(''); return }
      if (st === 'authenticated') { setSrcMsg('✅ ' + (row.auth_message || 'Logged in — session saved.')); setTwoFa(null); setAuthBusy(''); return }
      if (st === 'error') { setSrcMsg('❌ ' + (row.auth_message || 'Login failed.')); setTwoFa(null); setAuthBusy(''); return }
    }
    setSrcMsg('⌛ Still logging in — give it a moment and refresh; the proxy may be slow.'); setAuthBusy('')
  }
  async function verify2fa() {
    if (!twoFa || !code.trim()) return
    setAuthBusy('verify'); setSrcMsg('🔐 Verifying code…')
    try {
      const r: any = await api(`/api/v1/commcalc/data-sources/${twoFa.source.id}/login/verify`, { method: 'POST', body: JSON.stringify({ code: code.trim() }) })
      setSrcMsg('✅ ' + r.message); setTwoFa(null); setCode('')
    } catch (e: any) { setSrcMsg('❌ ' + (e?.message || e)) }
    finally { setAuthBusy(''); try { const r: any = await api('/api/v1/commcalc/data-sources'); setSources(r.sources || []) } catch { /* keep */ } }
  }
  // "What did the headless browser SEE?" — the backend stores a JPEG of the last page the Playwright
  // login landed on (2FA challenge / bot-wall / portal error). Visual debugging beats text diagnostics.
  async function openShot(s: any) {
    const label = s?.label || s?.username || s?.processor || 'login'
    setShotView({ label, loading: true })
    try {
      const r: any = await api(`/api/v1/commcalc/data-sources/${s.id}/login/screenshot`)
      setShotView({ label, loading: false, src: r?.shot || null, at: r?.at || null, note: r?.note || null, id: s.id })
    } catch (e: any) { setShotView({ label, loading: false, src: null, note: '❌ ' + (e?.message || e), id: s.id }) }
  }
  // 🔴 Live login: ONE persistent browser stays open from login through code entry, so the 2FA code is
  // sent ONCE and the operator's code goes into the SAME live page (fixes the "code sent twice" resend).
  // The operator watches a live screenshot stream and submits / resends / cancels against that session.
  async function startLive(s: any, confirmed = false) {
    liveSeqRef.current = 0
    setLive({ source: s }); setLiveCode(''); setLiveBusy(true)
    setLiveState({ phase: 'starting', message: 'Starting the live session…', shot: null, seq: 0 })
    try {
      const q = confirmed ? '?confirm=true' : ''
      const r: any = await api(`/api/v1/commcalc/data-sources/${s.id}/live-login/start${q}`, { method: 'POST', body: '{}' })
      if (r?.blocked && r?.requires_confirm) {
        // Do NOT start a browser yet: close the modal, warn, and let a SECOND deliberate click through.
        setLive(null); setLiveState(null); setLiveBusy(false); setSrcMsg(`⛔ ${r.error}`)
        try { const l: any = await api('/api/v1/commcalc/data-sources'); setSources(l.sources || []) } catch { /* keep */ }
        if (confirmBlocked(r)) return startLive(s, true)
        return
      }
      if (r?.blocked) setSrcMsg('⛔ ' + (r?.message || 'The portal has blocked us — the automatic pull is suppressed.'))
    }
    catch (e: any) { setLiveState({ phase: 'error', message: '❌ ' + (e?.message || e), shot: null }) }
    finally { setLiveBusy(false) }
  }
  async function submitLive() {
    if (!live?.source?.id || !liveCode.trim()) return
    setLiveBusy(true)
    try { await api(`/api/v1/commcalc/data-sources/${live.source.id}/live-login/submit`, { method: 'POST', body: JSON.stringify({ code: liveCode.trim() }) }); setLiveCode('') }
    catch (e: any) { setLiveState((p: any) => ({ ...(p || {}), message: '❌ ' + (e?.message || e) })) }
    finally { setLiveBusy(false) }
  }
  async function resendLive() {
    if (!live?.source?.id) return
    try { await api(`/api/v1/commcalc/data-sources/${live.source.id}/live-login/resend`, { method: 'POST', body: '{}' }) } catch { /* the state poll shows the outcome */ }
  }
  // ── Human-driven live view: forward raw input to the SAME live page (high-priority /input queue). ──
  // The first input pauses the backend's auto-drive for the rest of pre-auth (the human wins). All coords
  // are NORMALIZED (0..1 of the streamed image) — the backend multiplies by the live viewport (DPR-proof).
  async function sendInput(ev: any) {
    const id = live?.source?.id
    if (!id) return
    try { await api(`/api/v1/commcalc/data-sources/${id}/live-login/input`, { method: 'POST', body: JSON.stringify(ev) }) }
    catch { /* the frame poll shows the outcome */ }
  }
  // Right after an input, pull a few quick frames so the result appears without waiting for the next tick.
  function nudgeFrames(delays = [140, 380, 800]) {
    for (const d of delays) setTimeout(() => { refreshFrame() }, d)
  }
  function normXY(e: React.MouseEvent<HTMLImageElement>) {
    const r = e.currentTarget.getBoundingClientRect()
    return {
      x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
    }
  }
  async function clickLive(e: React.MouseEvent<HTMLImageElement>) {
    if (!live?.source?.id) return
    liveViewRef.current?.focus()          // so subsequent typing/scroll lands in the live view
    const { x, y } = normXY(e)
    await sendInput({ type: 'click', x, y }); nudgeFrames()
  }
  async function dblClickLive(e: React.MouseEvent<HTMLImageElement>) {
    if (!live?.source?.id) return
    liveViewRef.current?.focus()
    const { x, y } = normXY(e)
    await sendInput({ type: 'dblclick', x, y }); nudgeFrames()
  }
  // Keyboard: while the live view is focused, printable chars → type; named/combo keys → key press
  // (Enter/Backspace/Tab/Arrow*/Escape/Delete etc. map 1:1 to Playwright key names; modifiers form a combo).
  async function keyLive(e: React.KeyboardEvent) {
    if (!live?.source?.id) return
    const k = e.key
    if (['Shift', 'Control', 'Alt', 'Meta', 'CapsLock', 'Dead', 'Process', 'Unidentified'].includes(k)) return
    e.preventDefault()
    if (k.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      await sendInput({ type: 'type', text: k })
    } else {
      const mods = [e.ctrlKey && 'Control', e.altKey && 'Alt', e.metaKey && 'Meta'].filter(Boolean) as string[]
      const key = k.length === 1 ? k.toUpperCase() : k
      await sendInput({ type: 'key', key: mods.length ? [...mods, key].join('+') : key })
    }
    nudgeFrames([120, 340])
  }
  // Scroll: forward the wheel delta (throttled) so the human can reach off-screen portal controls.
  function wheelLive(e: React.WheelEvent) {
    if (!live?.source?.id) return
    const now = Date.now()
    if (now - wheelTsRef.current < 110) return
    wheelTsRef.current = now
    sendInput({ type: 'scroll', deltaY: e.deltaY }); nudgeFrames([160, 420])
  }
  async function closeLive(cancel = true) {
    const id = live?.source?.id
    if (id && cancel && liveState?.phase !== 'authenticated') {
      try { await api(`/api/v1/commcalc/data-sources/${id}/live-login/cancel`, { method: 'POST', body: '{}' }) } catch { /* best-effort */ }
    }
    setLive(null); setLiveState(null); setLiveCode('')
    try { const r: any = await api('/api/v1/commcalc/data-sources'); setSources(r.sources || []) } catch { /* keep list */ }
  }
  function authBadge(s: any) {
    const st = s.auth_status || 'unconfigured'
    const map: Record<string, { t: string; c: string; b: string }> = {
      authenticated: { t: '✅ Connected', c: '#166534', b: '#dcfce7' },
      authenticating: { t: '⏳ Logging in…', c: '#1e40af', b: '#dbeafe' },
      needs_2fa: { t: '🔒 Needs 2FA', c: '#9a3412', b: '#ffedd5' },
      error: { t: '⚠️ Login error', c: '#991b1b', b: '#fee2e2' },
      unconfigured: { t: '○ Not connected', c: 'var(--text3)', b: 'var(--surface2)' },
    }
    const m = map[st] || map.unconfigured
    const exp = s.session_expires_at ? new Date(s.session_expires_at) : null
    // ⛔ THE COOLDOWN CHIP. A blocked login used to render "✅ Connected" over a misleading error
    // ("session expired" / "report not listed"), which is precisely what made a human keep retrying
    // into an active block. `blocked` is computed server-side (_strip_source_pw) so the page never has
    // to reason about clock skew; pre-migration-244 it is simply absent and nothing renders.
    return (
      <span>
        <span style={{ display: 'inline-block', padding: '1px 7px', borderRadius: 999, fontSize: 11, fontWeight: 700, color: m.c, background: m.b }}>{m.t}</span>
        {st === 'authenticated' && exp && !s.blocked && <span style={{ color: 'var(--text3)', fontSize: 11, marginLeft: 6 }}>until {exp.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</span>}
        {s.blocked && (
          <div style={{ marginTop: 4, padding: '4px 7px', borderRadius: 6, background: '#fee2e2', border: '1px solid #fecaca', color: '#991b1b', maxWidth: 260, whiteSpace: 'normal' }}>
            <div style={{ fontWeight: 700, fontSize: 11 }}>⛔ Portal temporarily blocked us — next automatic attempt {blockTime(s.blocked_until)}</div>
            {s.block_reason && <div style={{ fontSize: 10, marginTop: 2, fontWeight: 400 }}>{s.block_reason}</div>}
            <div style={{ fontSize: 10, marginTop: 2, fontWeight: 400 }}>Don&apos;t retry — another attempt usually extends the block. Nothing is lost; it imports on the next automatic attempt.</div>
            <button className="btn btn-secondary" style={{ fontSize: 10, padding: '1px 6px', marginTop: 4 }} onClick={() => clearBlock(s)}>Lift cooldown (only if the portal released us)</button>
          </div>
        )}
      </span>
    )
  }

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📧 Email Auto-Import</h1>
          <WhereAreMyRowsButton />
        </div>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Poll a mailbox a vendor (e.g. B2B Soft) emails reports to, and route each attachment to its upload parser.
          Add <strong>more than one mailbox</strong> when reports arrive in different inboxes (e.g. the B2B feed at one
          address, Total Wireless at another) — each has its own creds, patterns and schedule.
          Swept a file but a page shows nothing? <strong>Where are my rows?</strong> traces every ingest (incl. which org it landed in).
        </p>
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 14 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12, paddingBottom: 12, borderBottom: '1px solid var(--border)' }}>
          <label style={{ ...lbl, marginBottom: 0 }}>Mailbox</label>
          <select style={sel} value={cfg.account || 'default'} onChange={e => pickAccount(e.target.value)}>
            {accounts.length === 0 && <option value={cfg.account || 'default'}>{cfg.label || cfg.account || 'default'}</option>}
            {accounts.map(a => <option key={a.account} value={a.account}>{(a.label || a.account)}{a.username ? ` — ${a.username}` : ''}{a.enabled ? '' : ' (off)'}</option>)}
            {cfg.account && !accounts.some(a => a.account === cfg.account) && <option value={cfg.account}>{(cfg.label || cfg.account)} — new (unsaved)</option>}
          </select>
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={addMailbox}>＋ Add mailbox</button>
          <button className="btn btn-secondary" style={{ fontSize: 12 }} disabled={busy === 'apply'} onClick={applyStandard} title="Set this mailbox's filename rules + schedule to the standard b2bsoft profile. Adds any missing rule and fills blank defaults; never clobbers your host/credentials or an existing rule.">{busy === 'apply' ? 'Applying…' : '✨ Apply b2bsoft standard'}</button>
          {cfg.account && cfg.account !== 'default' && <button className="btn btn-secondary" style={{ fontSize: 12, color: '#dc2626' }} onClick={delMailbox}>Delete this mailbox</button>}
          <span style={{ fontSize: 11, color: 'var(--text3)' }}>key: <code>{cfg.account || 'default'}</code></span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 12 }}>
          <div><label style={lbl}>Label (friendly name)</label><input style={{ ...sel, width: '100%' }} placeholder="Total Wireless" value={cfg.label || ''} onChange={e => set({ label: e.target.value })} /></div>
          <div style={{ gridColumn: '1 / -1' }}>
            <label style={lbl}>Email provider</label>
            <select style={{ ...sel, width: '100%', maxWidth: 340 }} value={providerOf(cfg.imap_host)}
              onChange={e => { const k = e.target.value; const p = PROVIDERS[k]; if (k === 'custom') { set({ imap_host: '' }) } else if (p) { set({ imap_host: p.imap_host, imap_port: p.imap_port, use_ssl: p.use_ssl }) } }}>
              {Object.entries(PROVIDERS).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
            </select>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 3 }}>Pick a provider to auto-fill the server + port — then just enter the email + password below.</div>
            {PROVIDERS[providerOf(cfg.imap_host)]?.hint && (
              <div style={{ fontSize: 11, color: '#b45309', marginTop: 3 }}>💡 {PROVIDERS[providerOf(cfg.imap_host)].hint}</div>
            )}
          </div>
          <div><label style={lbl}>IMAP host</label><input style={{ ...sel, width: '100%' }} placeholder="imap.example.com" value={cfg.imap_host || ''} onChange={e => set({ imap_host: e.target.value })} /></div>
          <div><label style={lbl}>Port</label><input style={{ ...sel, width: '100%' }} value={cfg.imap_port || 993} onChange={e => set({ imap_port: Number(e.target.value) || 993 })} /></div>
          <div><label style={lbl}>Username</label><input style={{ ...sel, width: '100%' }} placeholder="the inbox email address" value={cfg.username || ''} onChange={e => set({ username: e.target.value })} /></div>
          <div><label style={lbl}>Password {cfg.has_password && <span style={{ color: '#16794a' }}>✓ saved</span>}</label><input type="password" style={{ ...sel, width: '100%' }} placeholder={cfg.has_password ? 'saved — leave blank to keep' : 'mailbox password'} value={pwd} onChange={e => setPwd(e.target.value)} />
            {cfg.has_password && !pwd && <div style={{ fontSize: 10.5, color: 'var(--text3)', marginTop: 2 }}>The password stays saved — this field is blank for security, not because it was lost.</div>}</div>
          <div><label style={lbl}>Mailbox</label><input style={{ ...sel, width: '100%' }} placeholder="INBOX" value={cfg.mailbox || ''} onChange={e => set({ mailbox: e.target.value })} /></div>
          <div><label style={lbl}>From filter (optional)</label><input style={{ ...sel, width: '100%' }} placeholder="b2bsoft.com" value={cfg.from_filter || ''} onChange={e => set({ from_filter: e.target.value })} /></div>
          <div><label style={lbl}>Security</label>
            <label style={{ fontSize: 12, display: 'block' }}><input type="checkbox" checked={cfg.use_ssl !== false} onChange={e => set({ use_ssl: e.target.checked })} /> SSL (993)</label>
            <span style={{ fontSize: 11, color: 'var(--text3)' }}>off = STARTTLS (143)</span>
          </div>
          <div><label style={lbl}>Scan last N days</label><input style={{ ...sel, width: '100%' }} value={cfg.since_days ?? 14} onChange={e => set({ since_days: Number(e.target.value) || 14 })} /></div>
          <div><label style={lbl}>Schedule</label>
            <select style={{ ...sel, width: '100%' }} value={cfg.frequency || 'daily'} onChange={e => set({ frequency: e.target.value })}><option value="hourly">hourly</option><option value="daily">daily</option><option value="weekly">weekly</option></select>
          </div>
          <div><label style={lbl}>Hour (0–23)</label><input style={{ ...sel, width: '100%' }} value={cfg.hour ?? 7} onChange={e => set({ hour: Number(e.target.value) || 0 })} /></div>
          <div><label style={lbl}>Auto-run</label><label style={{ fontSize: 12 }}><input type="checkbox" checked={!!cfg.enabled} onChange={e => set({ enabled: e.target.checked })} /> enabled</label></div>
        </div>
        {cfg.has_password && /auth/i.test(String(cfg.last_status || '')) && (
          <div style={{ marginTop: 10, padding: '8px 12px', borderRadius: 8, background: 'var(--surface2)', border: '1px solid var(--border)', fontSize: 12, color: 'var(--text2)' }}>
            ⚠️ The mail server <b>rejected the last automatic login</b>{cfg.last_run_at ? ` (${new Date(cfg.last_run_at).toLocaleString()})` : ''}.
            Your saved password was <b>not</b> lost — mail hosts sometimes temporarily block frequent logins, and the next
            scheduled run usually recovers on its own. Use <b>Test connection</b> to check right now; only re-enter the
            password if the test also fails with it.
          </div>
        )}

        <div style={{ marginTop: 14, fontWeight: 600, fontSize: 13 }}>Attachment filename → upload type</div>
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 4 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>{['Filename pattern (glob)', 'Routes to', 'Note', ''].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
          <tbody>
            {(cfg.patterns || []).map((p: any, i: number) => (
              <tr key={i}>
                <td style={cell}><input style={{ ...sel, width: '100%' }} placeholder="*Sales*Transaction*Details*" value={p.pattern || ''} onChange={e => setPat(i, { pattern: e.target.value })} /></td>
                <td style={cell}><select style={sel} value={p.upload_type} onChange={e => setPat(i, { upload_type: e.target.value })}>
                  <optgroup label="Built-in">{BUILTIN_TYPES.map((u: string) => <option key={u} value={u}>{u}</option>)}</optgroup>
                  {customTypes.length > 0 && (
                    <optgroup label="Custom sheets">{customTypes.map((c: any) => <option key={c.report_key} value={c.report_key}>{c.label + ' (' + c.report_key + ')'}</option>)}</optgroup>
                  )}
                  {p.upload_type && !knownTypeKeys.has(p.upload_type) && <option value={p.upload_type}>{p.upload_type}</option>}
                </select></td>
                <td style={cell}><input style={{ ...sel, width: '100%' }} placeholder="optional" value={p.note || ''} onChange={e => setPat(i, { note: e.target.value })} /></td>
                <td style={cell}><button className="btn btn-secondary" style={{ fontSize: 12, color: '#dc2626' }} onClick={() => delPat(i)}>✕</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        <button className="btn btn-secondary" style={{ fontSize: 12, marginTop: 6 }} onClick={addPat}>+ Add pattern</button>

        <div style={{ marginTop: 14, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <button className="btn btn-primary" disabled={busy === 'save'} onClick={() => save()}>Save</button>
          <button className="btn btn-secondary" disabled={busy === 'test'} onClick={testConn}>{busy === 'test' ? 'Testing…' : 'Test connection'}</button>
          <button className="btn btn-secondary" disabled={busy === 'run'} onClick={runNow}>{busy === 'run' ? 'Running…' : 'Run now'}</button>
          {cfg.last_status && <span style={{ fontSize: 12, color: 'var(--text3)' }}>Last: {cfg.last_status} {cfg.last_run_at ? `· ${new Date(cfg.last_run_at).toLocaleString()}` : ''}</span>}
          {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
        </div>
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 14 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>🧩 Custom import sheets</div>
        <p style={{ color: 'var(--text2)', fontSize: 13, margin: '0 0 10px' }}>
          Add your own report (e.g. B2B <b>Sales Trend</b>) with no code. Name it here, then add a filename pattern above
          that routes to its key — every matching attachment is captured as-is and viewable below. Needs migration <b>099_custom_import.sql</b>.
        </p>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 }}>
          <input style={{ ...sel, minWidth: 240 }} placeholder="New sheet name, e.g. Sales Trend" value={newSheet}
            onChange={e => setNewSheet(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') addCustomSheet() }} />
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={addCustomSheet}>＋ Add sheet</button>
        </div>
        {/* Manual upload — for the MTD file (and daily files) when you don't want to wait for the email sweep.
            Set the period this file is FOR, then Upload on the sheet's row. Re-uploading the same period
            replaces it (the b2b MTD export is cumulative, so re-uploading the latest MTD is correct). */}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 }}>
          <span style={{ fontSize: 12, color: 'var(--text2)' }}>Manual upload period:</span>
          <input style={{ ...sel, minWidth: 160 }} placeholder="e.g. August 2026" value={upPeriod}
            onChange={e => setUpPeriod(e.target.value)} />
          <span style={{ fontSize: 12, color: 'var(--text3)' }}>then click <b>Upload</b> on a sheet below (leave blank to capture by filename).</span>
        </div>
        {customTypes.length > 0 ? (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>{['Sheet', 'Key (use in a pattern)', 'Captured rows', 'Manual upload', ''].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
            <tbody>
              {customTypes.map((c: any) => (
                <tr key={c.report_key} style={{ borderTop: '1px solid var(--border)', fontSize: 13 }}>
                  <td style={{ padding: '6px 8px', fontWeight: 600 }}>{c.label}</td>
                  <td style={{ padding: '6px 8px' }}><code>{c.report_key}</code></td>
                  <td style={{ padding: '6px 8px' }}>{c.rows || 0}</td>
                  <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>
                    <label className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px', cursor: 'pointer', opacity: upBusy === c.report_key ? 0.6 : 1 }}>
                      {upBusy === c.report_key ? 'Uploading…' : '⬆ Upload file'}
                      <input type="file" accept=".csv,.txt,.xlsx,.xls" style={{ display: 'none' }} disabled={upBusy === c.report_key}
                        onChange={e => { const f = e.target.files?.[0]; uploadCustom(c.report_key, c.label, f); e.currentTarget.value = '' }} />
                    </label>
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                    <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px' }} onClick={() => openViewer(c)}>👁 View data</button>{' '}
                    <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px', color: '#dc2626' }} onClick={() => delCustomSheet(c.report_key, c.label)}>✕</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div style={{ fontSize: 13, color: 'var(--text3)' }}>No custom sheets yet — add one above to auto-import any new report the vendor emails.</div>}
      </div>

      {test && (
        <div className="card" style={{ padding: 14, marginBottom: 14 }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>Recent messages ({test.count})</div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <tbody>
              {(test.messages || []).map((m: any, i: number) => (
                <tr key={i}>
                  <td style={{ ...cell, fontSize: 12 }}><div style={{ fontWeight: 600 }}>{m.subject || '(no subject)'}</div><div style={{ color: 'var(--text3)' }}>{m.from} · {m.date}</div></td>
                  <td style={cell}>
                    {(m.attachments || []).length === 0 ? (
                      <div>
                        <span style={{ color: 'var(--text3)', fontSize: 12 }}>no importable file extracted</span>
                        {(m.parts || []).length > 0 && (
                          <div style={{ marginTop: 4, fontSize: 11, color: 'var(--text3)' }}>
                            <div style={{ fontWeight: 600 }}>message contains:</div>
                            {(m.parts || []).map((p: any, k: number) => (
                              <div key={k}>· {p.filename} — {p.content_type}{p.size ? ` · ${(p.size / 1024).toFixed(0)} KB` : ''}{p.disposition && p.disposition !== '(none)' ? ` · ${p.disposition}` : ''}</div>
                            ))}
                          </div>
                        )}
                      </div>
                    ) :
                      (m.attachments || []).map((a: any, j: number) => (
                        <div key={j} style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                          <span>{a.name}</span>
                          {a.matches
                            ? <span className="badge" style={{ background: '#16794a', color: '#fff', fontSize: 11 }}>→ {a.matches}</span>
                            : <>
                                <span style={{ color: 'var(--text3)' }}>no pattern</span>
                                <button disabled={busy === 'save'} onClick={() => addRuleFor(a.name)} className="btn btn-secondary" style={{ fontSize: 10, padding: '1px 7px' }}>＋ Add rule</button>
                              </>}
                        </div>
                      ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Per-day ingest health (mig 200): answers "is my file ingesting?" with 3 distinct honest states ── */}
      {health && (
        <div className="card" style={{ padding: 16, marginBottom: 14 }}>
          <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>🩺 Ingest health — last {health.window_days} days</div>
          <p style={{ color: 'var(--text2)', fontSize: 12, margin: '0 0 10px' }}>
            Per-day sales-feed coverage for this mailbox. <b style={{ color: '#16794a' }}>ingested</b> = priced rows landed ·{' '}
            <b style={{ color: '#b45309' }}>zero-priced</b> = rows landed but every Ext Price is $0 (a degraded/price-less export) ·{' '}
            <b style={{ color: '#dc2626' }}>missing</b> = no file delivered/ingested that day (b2bsoft didn’t send it, or the guard refused it).
          </p>
          {health.cross_org_warning && (
            <div style={{ padding: 10, marginBottom: 10, background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 8, fontSize: 12.5 }}>
              ⚠️ <b>Misfile risk:</b> this mailbox address is also configured under another tenant
              {health.cross_org_conflicts?.some((c: any) => c.enabled) ? ' and ENABLED there' : ''} — both tenants would ingest the same emails. Keep it enabled under only ONE tenant.
            </div>
          )}
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 10, fontSize: 12 }}>
            {([['ok', 'ingested clean', '#16794a'], ['partial', 'partial', '#b45309'], ['refused', 'refused (guard)', '#b45309'], ['parse_skip', '0-row parse', '#b45309'], ['error', 'errored', '#dc2626']] as [string, string, string][]).map(([k, lbl, c]) => (
              <span key={k} style={{ color: c }}>{lbl}: <b>{health.recent_counts?.[k] ?? 0}</b></span>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
            {(health.days || []).map((d: any) => {
              const bg = d.state === 'ingested' ? '#dcfce7' : d.state === 'zero_priced' ? '#fef3c7' : '#fee2e2'
              const bd = d.state === 'ingested' ? '#86efac' : d.state === 'zero_priced' ? '#fde68a' : '#fecaca'
              return (
                <div key={d.date} title={`${d.date}: ${d.rows} row(s), ${d.priced} priced, $${d.amount} — ${d.state}`}
                  style={{ background: bg, border: `1px solid ${bd}`, borderRadius: 6, padding: '4px 6px', fontSize: 10.5, minWidth: 58, textAlign: 'center' }}>
                  <div style={{ fontWeight: 600 }}>{String(d.date).slice(5)}</div>
                  <div style={{ color: 'var(--text3)' }}>{d.state === 'missing' ? '—' : `${d.priced}✓`}</div>
                </div>
              )
            })}
          </div>
          {(health.days || []).every((d: any) => d.state === 'missing') && (
            <div style={{ marginTop: 8, fontSize: 12, color: '#b45309' }}>
              No sales ingested for any day in the window. Check: mailbox Enabled, the connection works (use <b>Test connection</b> — a saved password is kept even though the field shows blank), a rule for <code>*Sales*Transaction*Details*</code>, and that b2bsoft is actually delivering the report to this address.
            </div>
          )}
        </div>
      )}

      <div className="card" style={{ padding: 0 }}>
        <div style={{ padding: '10px 14px', fontWeight: 600, fontSize: 13 }}>Recently imported</div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <tbody>
            {processed.map(p => (
              <tr key={p.id}>
                <td style={cell}>{p.filename}</td>
                <td style={{ ...cell, fontSize: 11, color: 'var(--text3)' }}>{p.account && p.account !== 'default' ? p.account : ''}</td>
                <td style={{ ...cell, fontSize: 12 }}>{p.upload_type}</td>
                {/* Shared with the FTP-Imports page so the two can never disagree. Keeps the existing
                    ok-with-caveat (amber) and skipped (amber, verbatim reason) behaviour and adds the
                    two statuses the else-branch used to render as a bare red ✕: `empty` (read fine,
                    carried no ingestable rows — terminal, so this row is the only record it arrived)
                    and `ignored` (no importer for that report). */}
                <td style={cell}><SweepStatusCell row={p} /></td>
                <td style={{ ...cell, fontSize: 11, color: 'var(--text3)' }}>{p.processed_at ? new Date(p.processed_at).toLocaleString() : ''}</td>
              </tr>
            ))}
            {processed.length === 0 && <tr><td style={{ ...cell, color: 'var(--text3)', textAlign: 'center', padding: 24 }} colSpan={5}>Nothing imported yet.</td></tr>}
          </tbody>
        </table>
      </div>
      {/* ── Payment-processor sources (mig 083): distributor → processor → LOGINS registry ── */}
      <div id="portal-logins" className="card" style={{ padding: 16, marginTop: 16, scrollMarginTop: 80 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 4 }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>📡 Payment-processor sources</div>
          <div style={{ flex: 1 }} />
          <a href="/commcalc/report-mappings" className="btn btn-secondary" style={{ fontSize: 12 }} title="Which portal report lands in which table + column mapping — configurable, not hard-coded">🗺️ Report mapping</a>
          <a href="/commcalc/ma-upload" className="btn btn-secondary" style={{ fontSize: 12 }} title="Upload MA report files by hand, per carrier — the parallel track to the live portal pull">⬆️ Manual upload</a>
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setSrcDraft({ processor: 'b2bsoft', portal_url: 'https://wsreports.b2bsoft.com', enabled: false })}>＋ Add b2bsoft (sales)</button>
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setSrcDraft({ processor: 'vidapay', enabled: false })}>＋ Add login</button>
        </div>
        <p style={{ color: 'var(--text2)', fontSize: 13, margin: '0 0 10px' }}>
          Every portal login your commission data comes from — a company can have several distributors, two
          processors per distributor, and two logins per processor (all stores for one carrier usually sit under one
          login). Add each login here; everything pulled lands combined in one database, stamped with its source.
          Until a processor&apos;s portal scraper is wired, its reports still import automatically via the mailbox rules
          above (MA Commission Details / MA Daily Tx / MA Fulfillment) or the Data Imports page.
        </p>
        {!srcReady && <div style={{ padding: 10, marginBottom: 10, background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 8, fontSize: 13 }}>⚠️ Run migration <b>083_total_processor_sources.sql</b> in Supabase to enable this registry.</div>}
        {srcMsg && <div style={{ fontSize: 13, marginBottom: 8 }}>{srcMsg}</div>}

        {sources.length > 0 && (
          <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 10 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>{['Label', 'Processor', 'Distributor', 'Carrier', 'Login', 'Status', ''].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
            <tbody>
              {sources.map((s: any) => (
                <tr key={s.id} style={{ borderTop: '1px solid var(--border)', fontSize: 13 }}>
                  <td style={{ padding: '6px 8px', fontWeight: 600 }}>{s.label || '—'}{!s.enabled && <span style={{ fontSize: 11, color: '#b45309' }}> (off)</span>}</td>
                  <td style={{ padding: '6px 8px' }}>{s.processor}</td>
                  <td style={{ padding: '6px 8px', color: 'var(--text3)' }}>{distributors.find((d: any) => d.id === s.distributor_id)?.name || '—'}</td>
                  <td style={{ padding: '6px 8px', color: 'var(--text3)' }}>{carriers.find((c: any) => c.id === s.carrier_id)?.name || '—'}</td>
                  <td style={{ padding: '6px 8px', color: 'var(--text3)' }}>{s.account_id ? `${s.account_id} / ` : ''}{s.username || '—'}{s.has_password ? ' 🔑' : ''}</td>
                  <td style={{ padding: '6px 8px', fontSize: 12 }}>
                    {authBadge(s)}
                    {/* "Connected" describes the LOGIN. Whether anything was IMPORTED is a separate
                        fact, and it is the one that matters — so a 0-row pull is amber here, never a
                        green success line under a green chip. */}
                    {s.last_status && <div style={{ color: s.last_pull_delivered === false ? '#9a3412' : 'var(--text3)', background: s.last_pull_delivered === false ? '#fff7ed' : undefined, borderRadius: 6, padding: s.last_pull_delivered === false ? '3px 6px' : undefined, fontSize: 11, marginTop: 2, maxWidth: 240, whiteSpace: 'normal' }}>{s.last_status}</div>}
                    {s.has_pull_diag && <button className="btn btn-secondary" style={{ fontSize: 11, padding: '1px 7px', marginTop: 4 }} onClick={() => openPullDiag(s)}>🔧 What the pull saw</button>}
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {['vidapay', 'total_access', 'b2bsoft', 'b2b'].includes((s.processor || '').toLowerCase()) && (
                      <><button className="btn btn-secondary" title="Watchable LIVE login: one browser stays open from login through the 2FA code — the code is sent ONCE (no re-send). Best for VidaPay / Total Access." style={{ fontSize: 12, padding: '3px 9px', color: '#dc2626', fontWeight: 700 }} onClick={() => startLive(s)}>🔴 Live login</button>{' '}</>
                    )}
                    <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px' }} disabled={authBusy === s.id} onClick={() => startLogin(s)}>{authBusy === s.id ? '…' : (s.auth_status === 'authenticated' ? '🔁 Re-auth' : '🔐 Log in')}</button>{' '}
                    <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px' }} onClick={() => runSource(s)}>▶ Pull now</button>{' '}
                    <button className="btn btn-secondary" title="See the page the headless login browser last saw (2FA screen / bot-wall / error)" style={{ fontSize: 12, padding: '3px 9px' }} onClick={() => openShot(s)}>📷</button>{' '}
                    <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px' }} onClick={() => setSrcDraft({ ...s, password: '' })}>Edit</button>{' '}
                    <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px', color: '#dc2626' }} onClick={() => delSource(s)}>✕</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {sources.length === 0 && srcReady && <div style={{ fontSize: 13, color: 'var(--text3)', marginBottom: 8 }}>No processor logins yet — add the VidaPay / Total Access login(s) for Total, one row per login.</div>}

        {srcDraft && (
          <div style={{ border: '1px dashed var(--border)', borderRadius: 8, padding: 12 }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 10, marginBottom: 10 }}>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Label<br />
                <input style={{ width: '100%', padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, marginTop: 4 }} placeholder="VidaPay — login 1 (NY stores)" value={srcDraft.label || ''} onChange={e => setSrcDraft({ ...srcDraft, label: e.target.value })} /></label>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Processor<br />
                {/* RULE THREE §3b: pick a KNOWN processor (mis-filing a tenant/processor routes data wrong);
                    a genuinely new processor stays available via the explicit create affordance. */}
                <div style={{ marginTop: 4 }}>
                  <EntityPicker
                    options={(() => { const o = ['vidapay', 'total_access', 'b2bsoft', 'epay', 'other'].map(p => ({ id: p, label: p })); if (srcDraft.processor && !o.some(x => x.id === srcDraft.processor)) o.unshift({ id: srcDraft.processor, label: srcDraft.processor }); return o })()}
                    value={srcDraft.processor || null} allowCreate width="100%"
                    onChange={proc => { const patch: any = { processor: proc || '' }; if (proc === 'b2bsoft' && !srcDraft.portal_url) patch.portal_url = 'https://wsreports.b2bsoft.com'; setSrcDraft({ ...srcDraft, ...patch }) }}
                    onCreate={proc => { const patch: any = { processor: proc }; if (proc === 'b2bsoft' && !srcDraft.portal_url) patch.portal_url = 'https://wsreports.b2bsoft.com'; setSrcDraft({ ...srcDraft, ...patch }) }}
                    placeholder="pick or type a processor…" ariaLabel="Processor" />
                </div></label>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Distributor<br />
                <select style={{ width: '100%', padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, marginTop: 4 }} value={srcDraft.distributor_id || ''} onChange={e => setSrcDraft({ ...srcDraft, distributor_id: e.target.value })}>
                  <option value="">—</option>
                  {distributors.map((d: any) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select></label>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Carrier<br />
                <select style={{ width: '100%', padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, marginTop: 4 }} value={srcDraft.carrier_id || ''} onChange={e => setSrcDraft({ ...srcDraft, carrier_id: e.target.value })}>
                  <option value="">—</option>
                  {carriers.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select></label>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Portal URL<br />
                <input style={{ width: '100%', padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, marginTop: 4 }} placeholder="https://…" value={srcDraft.portal_url || ''} onChange={e => setSrcDraft({ ...srcDraft, portal_url: e.target.value })} /></label>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Account ID<br />
                <input style={{ width: '100%', padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, marginTop: 4 }} placeholder="VidaPay Account ID" value={srcDraft.account_id || ''} onChange={e => setSrcDraft({ ...srcDraft, account_id: e.target.value })} /></label>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>User ID<br />
                <input style={{ width: '100%', padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, marginTop: 4 }} value={srcDraft.username || ''} onChange={e => setSrcDraft({ ...srcDraft, username: e.target.value })} /></label>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Password{srcDraft.id ? ' (blank = keep saved)' : ''}<br />
                <input type="password" style={{ width: '100%', padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, marginTop: 4 }} value={srcDraft.password || ''} onChange={e => setSrcDraft({ ...srcDraft, password: e.target.value })} /></label>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Egress proxy (optional)<br />
                <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
                  <input style={{ flex: 1, padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13 }} placeholder="http://user:pass@host:port" value={srcDraft.proxy_url || ''} onChange={e => setSrcDraft({ ...srcDraft, proxy_url: e.target.value })} />
                  <button type="button" className="btn btn-secondary" style={{ fontSize: 12, whiteSpace: 'nowrap' }} disabled={proxyBusy} onClick={testProxy}>{proxyBusy ? 'Testing…' : '🧪 Test proxy'}</button>
                </div></label>
            </div>
            {proxyTest && (
              <div style={{ margin: '0 0 8px', padding: '8px 10px', borderRadius: 8, fontSize: 12.5,
                background: proxyTest.proxy?.ok ? (proxyTest.routed_through_proxy && proxyTest.is_us ? '#ecfdf5' : '#fffbeb') : '#fef2f2',
                border: `1px solid ${proxyTest.proxy?.ok ? (proxyTest.routed_through_proxy && proxyTest.is_us ? '#a7f3d0' : '#fde68a') : '#fecaca'}` }}>
                <div style={{ fontWeight: 600 }}>{proxyTest.summary}</div>
                {proxyTest.proxy?.ok && (
                  <div style={{ color: 'var(--text2)', marginTop: 3 }}>
                    Egress IP <b>{proxyTest.proxy.ip}</b> · {proxyTest.proxy.city || '?'}, {proxyTest.proxy.region || '?'} {proxyTest.proxy.country || '?'} · {proxyTest.proxy.org || ''} · {proxyTest.proxy.elapsed_ms}ms
                    {proxyTest.direct?.ip && <span style={{ color: 'var(--text3)' }}> · (server’s own IP: {proxyTest.direct.ip})</span>}
                  </div>
                )}
              </div>
            )}
            <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 8px' }}>
              💡 If Log in returns “Something doesn&apos;t look right” / an anti-bot page, the portal is blocking the
              server&apos;s datacenter IP. Enter a <b>residential / allow-listed proxy</b> above to route the login
              through it (leave blank otherwise).
            </p>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
                <input type="checkbox" checked={!!srcDraft.enabled} onChange={e => setSrcDraft({ ...srcDraft, enabled: e.target.checked })} /> Enabled (auto-pull once the scraper is wired)</label>
              {/* mig 242 — a signed-in session that expires before anything is pulled is worthless. */}
              <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }} title="As soon as a live login succeeds, pull this login's reports on that same trusted browser. Turn off only if you want to sign in without importing.">
                <input type="checkbox" checked={srcDraft.auto_pull_after_login !== false} onChange={e => setSrcDraft({ ...srcDraft, auto_pull_after_login: e.target.checked })} /> Pull reports right after login</label>
              <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }} title="How many months back each pull covers (each report's own cap still applies).">
                Months back
                <input type="number" min={1} max={12} value={srcDraft.months_back ?? 2} onChange={e => setSrcDraft({ ...srcDraft, months_back: Number(e.target.value) || 2 })} style={{ width: 56, padding: '4px 6px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }} /></label>
              <div style={{ flex: 1 }} />
              <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setSrcDraft(null)}>Cancel</button>
              <button className="btn btn-primary" style={{ fontSize: 12 }} onClick={saveSource}>💾 Save login</button>
            </div>
            <p style={{ fontSize: 12, color: 'var(--text3)', margin: '8px 0 0' }}>
              <b>b2bsoft (daily Sales Transaction Details):</b> processor <code>b2bsoft</code>, Portal URL
              <code>https://wsreports.b2bsoft.com</code>, fill User ID + Password (Account ID optional), Save, then
              click <b>🔐 Log in</b> in the table above and enter the 2-factor code when prompted. b2bsoft usually
              blocks the server&apos;s datacenter IP, so set a <b>residential / allow-listed proxy</b> above first —
              otherwise Log in returns an anti-bot page. The signed-in session is saved and reused (~90 days) so
              sales stops relying on the email feed.<br /><br />
              For VidaPay / Total Access: fill Account ID + User ID + Password, Save, then click <b>🔐 Log in</b> in the
              table above. The portal will text/email a 2-factor code — enter it when prompted. The signed-in session
              is saved and reused for scheduled pulls; when it expires the status shows <b>🔒 Needs 2FA</b> and you just
              log in again. Credentials are never hard-coded — they live only in this form.
            </p>
          </div>
        )}
      </div>

      {/* ── Custom-sheet data viewer ── */}
      {viewer && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50 }} onClick={() => setViewer(null)}>
          <div className="card" style={{ padding: 18, width: 900, maxWidth: '94vw', maxHeight: '86vh', overflow: 'auto' }} onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <div style={{ fontWeight: 700, fontSize: 15 }}>👁 {viewer.label} <span style={{ color: 'var(--text3)', fontWeight: 400, fontSize: 12 }}>({viewer.report_key})</span></div>
              <div style={{ flex: 1 }} />
              <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setViewer(null)}>Close</button>
            </div>
            {!viewData ? <div style={{ color: 'var(--text3)', fontSize: 13 }}>Loading…</div>
              : viewData.error ? <div style={{ color: '#dc2626', fontSize: 13 }}>❌ {viewData.error}</div>
              : (viewData.rows || []).length === 0 ? <div style={{ color: 'var(--text3)', fontSize: 13 }}>No data captured yet for this sheet. Add a matching filename pattern above and run the sweep.</div>
              : (
                <div style={{ overflowX: 'auto' }}>
                  <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 6 }}>{viewData.count} row(s){viewData.periods?.length ? ` · periods: ${viewData.periods.join(', ')}` : ''} · showing first 500</div>
                  <table style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: '100%' }}>
                    <thead><tr style={{ background: 'var(--surface2)' }}>{(viewData.columns || []).map((c: string) => <th key={c} style={{ textAlign: 'left', padding: '5px 8px', whiteSpace: 'nowrap', borderBottom: '1px solid var(--border)' }}>{c}</th>)}</tr></thead>
                    <tbody>
                      {(viewData.rows || []).slice(0, 500).map((row: any, i: number) => (
                        <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                          {(viewData.columns || []).map((c: string) => <td key={c} style={{ padding: '4px 8px', whiteSpace: 'nowrap' }}>{String(row[c] ?? '')}</td>)}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
          </div>
        </div>
      )}

      {/* ── 2FA challenge modal: entered after 🔐 Log in reaches the verification step ── */}
      {twoFa && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50 }} onClick={() => { if (!authBusy) { setTwoFa(null); setCode('') } }}>
          <div className="card" style={{ padding: 22, width: 380, maxWidth: '92vw' }} onClick={e => e.stopPropagation()}>
            <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 6 }}>🔒 Two-factor verification</div>
            <p style={{ fontSize: 13, color: 'var(--text2)', margin: '0 0 12px' }}>
              Enter the verification code the portal just sent{twoFa.hint ? <> to <b>{twoFa.hint}</b></> : ' to you'} for
              login <b>{twoFa.source?.label || twoFa.source?.username || twoFa.source?.processor}</b>.
            </p>
            <input autoFocus inputMode="numeric" value={code} onChange={e => setCode(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') verify2fa() }}
              placeholder="123456" style={{ width: '100%', padding: '10px 12px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 18, letterSpacing: 3, textAlign: 'center', marginBottom: 12 }} />
            <details style={{ marginBottom: 12, fontSize: 12, color: 'var(--text2)' }}>
              <summary style={{ cursor: 'pointer', fontWeight: 600 }}>Not receiving the code?</summary>
              <div style={{ marginTop: 6, lineHeight: 1.6 }}>
                The portal sends the code to the phone/email registered <b>on that portal account</b>{twoFa.hint ? <> — it shows <b>{twoFa.hint}</b></> : ''}, not to this device. If you don’t get it:
                <ul style={{ margin: '6px 0 0 16px', padding: 0 }}>
                  <li>Confirm the destination above is a phone/email <b>you control</b>. If it’s stale/someone else’s, log into the portal directly (normal browser) and update the 2-factor contact in its <b>account / security settings</b>, or have that person relay the code.</li>
                  <li>For an <b>email</b> code, check <b>spam/junk</b>. For <b>SMS</b>, make sure the number can receive short-code texts.</li>
                  <li>If the portal uses an <b>authenticator app</b> (no text is sent), open that app and enter the current 6-digit code.</li>
                  <li>Each <b>↻ Resend</b> sends a <b>new</b> code and voids the previous one — use the latest, and don’t press Log in repeatedly.</li>
                </ul>
              </div>
            </details>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', alignItems: 'center' }}>
              <button className="btn btn-secondary" style={{ fontSize: 13 }} disabled={authBusy === 'verify'} onClick={() => startLogin(twoFa.source)}>↻ Resend</button>
              <button className="btn btn-secondary" title="See the exact 2FA screen the headless browser is on" style={{ fontSize: 13 }} onClick={() => openShot(twoFa.source)}>📷</button>
              <div style={{ flex: 1 }} />
              <button className="btn btn-secondary" style={{ fontSize: 13 }} disabled={!!authBusy} onClick={() => { setTwoFa(null); setCode('') }}>Cancel</button>
              <button className="btn btn-primary" style={{ fontSize: 13 }} disabled={authBusy === 'verify' || !code.trim()} onClick={verify2fa}>{authBusy === 'verify' ? 'Verifying…' : 'Verify'}</button>
            </div>
          </div>
        </div>
      )}

      {/* ── 🔴 LIVE login: one persistent browser, watched via a streamed screenshot; code sent ONCE ── */}
      {live && (() => {
        const ph = liveState?.phase || 'starting'
        const showCode = ph === 'awaiting_code' || (ph === 'error' && liveState?.shot) || ph === 'verifying'
        const done = ph === 'authenticated'
        const badge: Record<string, { t: string; c: string; b: string }> = {
          starting: { t: '⏳ Starting…', c: '#1e40af', b: '#dbeafe' },
          login: { t: '⏳ Signing in…', c: '#1e40af', b: '#dbeafe' },
          human_action: { t: '🧑 Your turn — solve the check', c: '#9a3412', b: '#ffedd5' },
          awaiting_code: { t: '🔒 Enter the code', c: '#9a3412', b: '#ffedd5' },
          verifying: { t: '⏳ Verifying…', c: '#1e40af', b: '#dbeafe' },
          action_needed: { t: '👆 Click Next in the view', c: '#9a3412', b: '#ffedd5' },
          authenticated: { t: '✅ Signed in', c: '#166534', b: '#dcfce7' },
          pulling: { t: '⏳ Pulling reports…', c: '#1e40af', b: '#dbeafe' },
          error: { t: '⚠️ Error', c: '#991b1b', b: '#fee2e2' },
          cancelled: { t: '○ Cancelled', c: 'var(--text3)', b: 'var(--surface2)' },
          idle: { t: '○ Idle', c: 'var(--text3)', b: 'var(--surface2)' },
        }
        const bd = badge[ph] || badge.starting
        return (
          <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 60 }} onClick={() => closeLive(true)}>
            <div className="card" style={{ padding: 18, width: 720, maxWidth: '95vw', maxHeight: '92vh', overflow: 'auto' }} onClick={e => e.stopPropagation()}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <div style={{ fontWeight: 700, fontSize: 15 }}>🔴 Live login — <span style={{ fontWeight: 400 }}>{live.source?.label || live.source?.username || live.source?.processor}</span></div>
                <span style={{ display: 'inline-block', padding: '1px 8px', borderRadius: 999, fontSize: 11, fontWeight: 700, color: bd.c, background: bd.b }}>{bd.t}</span>
                <div style={{ flex: 1 }} />
                <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => closeLive(true)}>Close</button>
              </div>
              <p style={{ fontSize: 12.5, color: 'var(--text2)', margin: '0 0 10px' }}>{liveState?.message || 'Starting…'}</p>
              {ph === 'human_action' ? (
                <div style={{ fontSize: 12.5, color: '#9a3412', margin: '0 0 6px', fontWeight: 700, padding: '8px 10px', borderRadius: 8, background: '#fff7ed', border: '1px solid #fdba74' }}>
                  🧑 Human check detected. <b>Solve the “I’m not a robot” box directly in the live view below</b>, then click the portal’s <b>Sign in</b> button. You’re driving this login now — clicks, typing and scrolling all go straight to the live browser.
                </div>
              ) : !done && liveState?.shot && (
                <div style={{ fontSize: 11.5, color: '#9a3412', margin: '0 0 6px', fontWeight: 600 }}>
                  👆 Click, type and scroll directly on the screen below to drive the live browser (click once to focus it, then type — e.g. press the portal’s <b>Next</b>/<b>Sign in</b> button).
                </div>
              )}
              <div
                ref={liveViewRef}
                tabIndex={done ? -1 : 0}
                onKeyDown={done ? undefined : keyLive}
                onWheel={done ? undefined : wheelLive}
                onFocus={() => setLiveFocused(true)}
                onBlur={() => setLiveFocused(false)}
                style={{ position: 'relative', minHeight: 220, background: 'var(--surface2)', borderRadius: 8, border: `2px solid ${(!done && liveFocused) ? '#f97316' : 'var(--border)'}`, display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 12, outline: 'none' }}>
                {liveState?.shot
                  // eslint-disable-next-line @next/next/no-img-element
                  ? <img src={liveState.shot} alt="Live login screen" draggable={false} onClick={done ? undefined : clickLive} onDoubleClick={done ? undefined : dblClickLive} style={{ maxWidth: '100%', borderRadius: 6, cursor: done ? 'default' : 'crosshair', userSelect: 'none' }} />
                  : <div style={{ color: 'var(--text3)', fontSize: 13, padding: 30 }}>Opening the portal in a live browser… the screen appears here in a moment.</div>}
                {!done && liveState?.shot && (
                  <div style={{ position: 'absolute', top: 6, right: 8, fontSize: 10.5, fontWeight: 600, padding: '1px 7px', borderRadius: 999, color: liveFocused ? '#9a3412' : 'var(--text3)', background: liveFocused ? '#ffedd5' : 'var(--surface)', border: '1px solid var(--border)' }}>
                    {liveFocused ? '⌨ typing here' : 'click to type here'}
                  </div>)}
              </div>
              {done ? (() => {
                // SIGNED IN IS NOT IMPORTED. The reports are now pulled automatically the moment the
                // login succeeds (mig 242 auto_pull_after_login), and this block reports THAT outcome —
                // a 0-row pull is amber with the next step, never a green "session saved" full stop.
                const p = liveState?.pull || {}
                const green = p.ran && p.delivered
                const amber = p.ran && p.delivered === false
                return (
                  <div style={{ padding: '10px 12px', borderRadius: 8, background: amber ? '#fff7ed' : '#dcfce7', color: amber ? '#9a3412' : '#166534', fontSize: 13, border: amber ? '1px solid #fdba74' : undefined }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                      <span style={{ fontWeight: 700 }}>
                        {green ? `✅ Signed in and imported ${p.rows ?? ''} row(s).`
                          : amber ? '⚠️ Signed in — but nothing was imported.'
                            : liveState?.auto_pull === false ? '✅ Signed in — the session is saved (automatic pull is switched off for this login).'
                              : '✅ Signed in — the session is saved and reused until it expires.'}
                      </span>
                      <div style={{ flex: 1 }} />
                      {(green || amber) && <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => live?.source && openPullDiag(live.source)}>🔧 What the pull saw</button>}
                      <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={() => closeLive(false)}>Done</button>
                    </div>
                    {p.ran && p.status && <div style={{ marginTop: 6, fontSize: 12.5, whiteSpace: 'normal' }}>{p.status}</div>}
                    {amber && (
                      <div style={{ marginTop: 6, fontSize: 12 }}>
                        {p.reason === 'report_not_listed' && p.options?.length > 0
                          ? <>This portal&apos;s Reports list actually offers: <b>{p.options.slice(0, 8).join(', ')}</b>. Put those exact names on <a href="/commcalc/report-mappings">Report mapping</a>, then press ▶ Pull now.</>
                          : p.reason === 'no_reports_page'
                            ? <>The pull could not open the portal&apos;s Reports page from where the login landed. Open <b>🔧 What the pull saw</b> — it lists the menu links this login really has.</>
                            : ['results_never_rendered', 'run_control_missing', 'export_link_missing', 'export_download_failed', 'report_select_missing'].includes(p.reason)
                              /* NOT a naming problem and NOT "the portal has no data" — the reports could not be
                                 scraped. Sending the operator to Report mapping here is what wasted a day. */
                              ? <>The portal <b>was reached and the reports were selected</b>, but their results could not be scraped (<code>{p.reason}</code>). This is <b>not</b> a statement that the portal has no data, and the report names are fine. Open <b>🔧 What the pull saw</b> for the per-report detail.</>
                              : p.reason === 'portal_reported_empty'
                                ? <>The portal ran the reports and displayed its own “no records” message — there genuinely is nothing to import for this window.</>
                                : <>Open <b>🔧 What the pull saw</b> for the per-report detail, or fix the report names on <a href="/commcalc/report-mappings">Report mapping</a>.</>}
                      </div>
                    )}
                  </div>
                )
              })() : showCode ? (
                <div>
                  <input autoFocus inputMode="numeric" value={liveCode} onChange={e => setLiveCode(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') submitLive() }} placeholder="123456"
                    style={{ width: '100%', padding: '10px 12px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 18, letterSpacing: 3, textAlign: 'center', marginBottom: 10 }} />
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <button className="btn btn-secondary" style={{ fontSize: 13 }} disabled={liveBusy} onClick={resendLive}>↻ Resend</button>
                    <span style={{ fontSize: 11.5, color: 'var(--text3)' }}>The code is sent ONCE to this same live browser — enter the latest code; Resend voids the previous one.</span>
                    <div style={{ flex: 1 }} />
                    <button className="btn btn-primary" style={{ fontSize: 13 }} disabled={liveBusy || !liveCode.trim() || ph === 'verifying'} onClick={submitLive}>{ph === 'verifying' ? 'Verifying…' : 'Submit code'}</button>
                  </div>
                </div>
              ) : ph === 'action_needed' ? (
                <div style={{ padding: '10px 12px', borderRadius: 8, background: '#ffedd5', color: '#9a3412', fontSize: 13 }}>
                  ✅ Your code was accepted — just <b>click the blue Next button</b> on the screen above to finish trusting this device. (The click is sent straight to the live browser.)
                </div>
              ) : ph === 'human_action' ? (
                <div style={{ padding: '10px 12px', borderRadius: 8, background: '#ffedd5', color: '#9a3412', fontSize: 13 }}>
                  🧑 <b>Solve the “I’m not a robot” check in the live view above</b>, then click the portal’s <b>Sign in</b> button. Everything you click, type or scroll goes straight to the live browser — take your time, nothing is auto-submitted past the check.
                </div>
              ) : (
                <div style={{ fontSize: 12.5, color: 'var(--text3)' }}>{ph === 'error' || ph === 'cancelled' ? 'Close this and try again, or use 🔐 Log in as a fallback.' : 'Watch the live screen above — the code box appears here once the portal challenges.'}</div>
              )}
            </div>
          </div>
        )
      })()}

      {/* ── 🔧 What the pull saw — the last pull's per-report outcome + the portal's own vocabulary ── */}
      {pullDiag && (() => {
        const d = pullDiag.diag || null
        const opts: string[] = (d?.calibration?.portal_report_options || d?.probe?.report_options || [])
        const navs: any[] = (d?.probe?.nav_links || [])
        return (
          <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 60 }} onClick={() => setPullDiag(null)}>
            <div className="card" style={{ padding: 18, width: 880, maxWidth: '94vw', maxHeight: '90vh', overflow: 'auto' }} onClick={e => e.stopPropagation()}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
                <div style={{ fontWeight: 700, fontSize: 15 }}>🔧 What the pull saw — <span style={{ fontWeight: 400 }}>{pullDiag.label}</span></div>
                {pullDiag.at && <span style={{ color: 'var(--text3)', fontSize: 12 }}>{new Date(pullDiag.at).toLocaleString()}</span>}
                <div style={{ flex: 1 }} />
                <a href="/commcalc/report-mappings" className="btn btn-secondary" style={{ fontSize: 12 }}>🗺️ Report mapping</a>
                <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setPullDiag(null)}>Close</button>
              </div>
              {pullDiag.loading ? <div style={{ color: 'var(--text3)', fontSize: 13 }}>Loading…</div> : (<>
                {pullDiag.note && <div style={{ padding: 10, marginBottom: 10, background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 8, fontSize: 13 }}>{pullDiag.note}</div>}
                {d && (
                  <div style={{ padding: '10px 12px', borderRadius: 8, marginBottom: 12, fontSize: 13, background: d.delivered ? '#dcfce7' : '#fff7ed', color: d.delivered ? '#166534' : '#9a3412', border: d.delivered ? undefined : '1px solid #fdba74' }}>
                    <b>{d.delivered ? `✅ Imported ${d.rows_ingested ?? 0} row(s).` : '⚠️ This pull imported nothing.'}</b>
                    <div style={{ marginTop: 4, whiteSpace: 'normal' }}>{d.status}</div>
                    {d.reports_page_reachable === false && <div style={{ marginTop: 4 }}>The portal&apos;s <b>Reports page was not reachable</b> from the page this login landed on — see the menu links below.</div>}
                  </div>
                )}
                {opts.length > 0 && (
                  <div style={{ marginBottom: 12 }}>
                    <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 4 }}>Reports this portal login actually offers ({opts.length})</div>
                    <p style={{ fontSize: 12, color: 'var(--text2)', margin: '0 0 6px' }}>These are the names in the portal&apos;s own dropdown. A report whose <b>display name</b> on Report mapping is not one of these can never be selected — copy the right name across.</p>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      {opts.map((o, i) => <code key={i} style={{ fontSize: 12, background: 'var(--surface2)', padding: '2px 7px', borderRadius: 6 }}>{o}</code>)}
                    </div>
                  </div>
                )}
                {(d?.reports || []).length > 0 && (
                  <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 12 }}>
                    <thead><tr style={{ background: 'var(--surface2)' }}>{['Report', 'Target table', 'Rows', 'Outcome'].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
                    <tbody>
                      {d.reports.map((r: any, i: number) => {
                        // HONESTY (2026-07-28): a report that was never submitted, or whose grid never
                        // rendered, is NOT "ran, returned no rows" — the backend now sends the exact
                        // outcome sentence, and match_debug carries the repr() of both names so an
                        // invisible-character mismatch is visible instead of self-contradicting.
                        const md = r.match_debug || (r.name_match || {}).debug || null
                        const tone = r.rows_ingested ? '#166534' : (r.ok ? 'var(--text2)' : '#9a3412')
                        return (
                        <tr key={i} style={{ borderTop: '1px solid var(--border)', fontSize: 12.5 }}>
                          <td style={{ padding: '6px 8px', fontWeight: 600 }}>{r.report_key}</td>
                          <td style={{ padding: '6px 8px', color: 'var(--text3)' }}>{r.target_table || '—'}</td>
                          <td style={{ padding: '6px 8px' }}>{r.rows_ingested ?? 0}</td>
                          <td style={{ padding: '6px 8px', color: tone, whiteSpace: 'normal' }}>
                            {r.rows_ingested ? `imported ${r.months_covered?.length || 0} month(s)` : (r.outcome || r.error || (r.ok ? 'ran, returned no rows' : 'failed'))}
                            {r.calibration && !r.rows_ingested && <span style={{ color: '#b45309' }}> · params not calibrated yet</span>}
                            {r.name_match?.tier && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 3 }}>name matched the portal’s <b>{r.name_match.tier}</b> spelling <code>{r.name_match.matched}</code> (invisible characters normalised)</div>}
                            {md?.wanted?.repr && (
                              <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 3, fontFamily: 'ui-monospace, monospace' }}>
                                wanted {md.wanted.repr}{md.wanted.odd_chars?.length ? ` [${md.wanted.odd_chars.join(', ')}]` : ''}
                                {md.nearest_offered?.repr && <> · closest offered {md.nearest_offered.repr}{md.nearest_offered.odd_chars?.length ? ` [${md.nearest_offered.odd_chars.join(', ')}]` : ''}{typeof md.similarity === 'number' ? ` · ${Math.round(md.similarity * 100)}% alike` : ''}</>}
                              </div>
                            )}
                            {r.options_changed && <div style={{ fontSize: 11, color: '#b45309', marginTop: 3 }}>⚠ the portal’s report list CHANGED between the start of the pull and this report’s turn — the page state moved, the name may be fine.</div>}
                          </td>
                        </tr>
                      )})}
                    </tbody>
                  </table>
                )}
                {navs.length > 0 && (
                  <details>
                    <summary style={{ fontSize: 13, fontWeight: 700, cursor: 'pointer' }}>Menu links this login has ({navs.length})</summary>
                    <p style={{ fontSize: 12, color: 'var(--text2)', margin: '6px 0' }}>Captured from the signed-in page. If none of these looks like a Reports menu, this login may not have report access at all.</p>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      {navs.slice(0, 60).map((l: any, i: number) => <span key={i} style={{ fontSize: 11.5, background: 'var(--surface2)', padding: '2px 7px', borderRadius: 6 }}>{l.t}</span>)}
                    </div>
                  </details>
                )}
                {d?.probe?.url && <p style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 10 }}>Seen at <code>{d.probe.url}</code>{d.probe.title ? ` · “${d.probe.title}”` : ''} · {d.probe.frames || 1} frame(s). No password or field value is ever recorded here.</p>}
              </>)}
            </div>
          </div>
        )
      })()}

      {/* ── Login-screenshot viewer: the page the headless Playwright browser last saw for a source ── */}
      {shotView && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 60 }} onClick={() => setShotView(null)}>
          <div className="card" style={{ padding: 18, width: 860, maxWidth: '94vw', maxHeight: '90vh', overflow: 'auto' }} onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <div style={{ fontWeight: 700, fontSize: 15 }}>📷 What the browser saw — <span style={{ fontWeight: 400 }}>{shotView.label}</span></div>
              {shotView.at && <span style={{ color: 'var(--text3)', fontSize: 12 }}>{new Date(shotView.at).toLocaleString()}</span>}
              <div style={{ flex: 1 }} />
              {shotView.id && <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => openShot({ id: shotView.id, label: shotView.label })}>↻ Refresh</button>}
              <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setShotView(null)}>Close</button>
            </div>
            {shotView.loading ? <div style={{ color: 'var(--text3)', fontSize: 13 }}>Loading…</div>
              : shotView.src ? (
                <>
                  <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 8px' }}>
                    This is the page the <b>headless login browser</b> was on when it last stopped (2FA challenge,
                    bot-wall, or error). If a code was never sent, this shows the button/choice the portal is waiting on.
                  </p>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={shotView.src} alt="Last login screenshot" style={{ maxWidth: '100%', border: '1px solid var(--border)', borderRadius: 8 }} />
                </>
              )
              : <div style={{ color: 'var(--text3)', fontSize: 13 }}>{shotView.note || 'No screenshot available yet.'}</div>}
          </div>
        </div>
      )}
    </div>
  )
}
