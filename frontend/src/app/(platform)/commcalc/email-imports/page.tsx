'use client'
import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/client'

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
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [sources, setSources] = useState<any[]>([])
  const [srcReady, setSrcReady] = useState(true)
  const [srcDraft, setSrcDraft] = useState<any>(null)   // add/edit form for a data-source login
  const [srcMsg, setSrcMsg] = useState('')
  const [carriers, setCarriers] = useState<any[]>([])
  const [distributors, setDistributors] = useState<any[]>([])
  const [twoFa, setTwoFa] = useState<any>(null)     // { source, hint } while a 2FA code is needed
  const [code, setCode] = useState('')
  const [authBusy, setAuthBusy] = useState('')
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
    api('/api/v1/commcalc/carriers').then((r: any) => setCarriers(r || [])).catch(() => {})
    api('/api/v1/commcalc/distributors').then((r: any) => setDistributors(Array.isArray(r) ? r : (r?.distributors || []))).catch(() => {})
    api('/api/v1/commcalc/custom-import-types').then((r: any) => setCustomTypes(Array.isArray(r) ? r : [])).catch(() => {})
  }, [])
  useEffect(() => { refresh() }, [refresh])

  const set = (patch: any) => setCfg((c: any) => ({ ...c, ...patch }))
  const setPat = (i: number, patch: any) => setCfg((c: any) => ({ ...c, patterns: c.patterns.map((p: any, j: number) => j === i ? { ...p, ...patch } : p) }))
  const addPat = () => setCfg((c: any) => ({ ...c, patterns: [...(c.patterns || []), { pattern: '', upload_type: 'daily_sales', note: '' }] }))
  const delPat = (i: number) => setCfg((c: any) => ({ ...c, patterns: c.patterns.filter((_: any, j: number) => j !== i) }))
  const knownTypeKeys = new Set([...BUILTIN_TYPES, ...customTypes.map((c: any) => c.report_key)])

  const body = () => ({ ...cfg, password: pwd || undefined })

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

  async function save() {
    setBusy('save')
    try { const r: any = await api('/api/v1/commcalc/email-sweep/config', { method: 'PUT', body: JSON.stringify(body()) }); setPwd(''); setMsg('✅ Saved.'); refresh(r.account || cfg.account) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy('') }
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
      setMsg(!r.ok ? `❌ ${r.error}`
        : r.ingested > 0 ? `✅ Ingested ${r.ingested} attachment(s).`
        : !(cfg.patterns || []).some((p: any) => (p.pattern || '').trim())
          ? '⚠️ 0 ingested — this mailbox has NO filename rules, so nothing can match. Add the rules below and Save.'
          : '⚠️ 0 ingested — no NEW attachments matched your rules (already-imported files are skipped; use Test connection to see the attachment names in the box).')
      refresh(cfg.account)
    }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy('') }
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
  async function runSource(s: any) {
    setSrcMsg('⏳ Pulling…')
    try {
      const r: any = await api(`/api/v1/commcalc/data-sources/${s.id}/run`, { method: 'POST', body: '{}' })
      if (r.needs_2fa) { setSrcMsg(`🔒 ${r.error}`); setTwoFa({ source: s, hint: null }) }
      else setSrcMsg(r.ok ? `✅ ${r.status || 'Pulled.'}` : `ℹ️ ${r.error}`)
    } catch (e: any) { setSrcMsg('❌ ' + (e?.message || e)) }
    try { const r: any = await api('/api/v1/commcalc/data-sources'); setSources(r.sources || []) } catch { /* keep list */ }
  }
  // Interactive portal login: submit the stored 3 creds → land on the 2FA challenge → the operator
  // enters the code from their email/SMS → the authenticated session is saved for scheduled pulls.
  async function startLogin(s: any) {
    setAuthBusy(s.id); setSrcMsg('🔐 Signing in…'); setCode('')
    try {
      const r: any = await api(`/api/v1/commcalc/data-sources/${s.id}/login/start`, { method: 'POST', body: '{}' })
      if (r.status === 'authenticated') { setSrcMsg('✅ ' + r.message); setTwoFa(null) }
      else { setSrcMsg('📩 ' + r.message); setTwoFa({ source: s, hint: r.two_fa_hint }) }
    } catch (e: any) { setSrcMsg('❌ ' + (e?.message || e)); setTwoFa(null) }
    finally { setAuthBusy(''); try { const r: any = await api('/api/v1/commcalc/data-sources'); setSources(r.sources || []) } catch { /* keep */ } }
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
  function authBadge(s: any) {
    const st = s.auth_status || 'unconfigured'
    const map: Record<string, { t: string; c: string; b: string }> = {
      authenticated: { t: '✅ Connected', c: '#166534', b: '#dcfce7' },
      needs_2fa: { t: '🔒 Needs 2FA', c: '#9a3412', b: '#ffedd5' },
      error: { t: '⚠️ Login error', c: '#991b1b', b: '#fee2e2' },
      unconfigured: { t: '○ Not connected', c: 'var(--text3)', b: 'var(--surface2)' },
    }
    const m = map[st] || map.unconfigured
    const exp = s.session_expires_at ? new Date(s.session_expires_at) : null
    return (
      <span>
        <span style={{ display: 'inline-block', padding: '1px 7px', borderRadius: 999, fontSize: 11, fontWeight: 700, color: m.c, background: m.b }}>{m.t}</span>
        {st === 'authenticated' && exp && <span style={{ color: 'var(--text3)', fontSize: 11, marginLeft: 6 }}>until {exp.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</span>}
      </span>
    )
  }

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📧 Email Auto-Import</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Poll a mailbox a vendor (e.g. B2B Soft) emails reports to, and route each attachment to its upload parser.
          Add <strong>more than one mailbox</strong> when reports arrive in different inboxes (e.g. the B2B feed at one
          address, Total Wireless at another) — each has its own creds, patterns and schedule.
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
          <div><label style={lbl}>IMAP host</label><input style={{ ...sel, width: '100%' }} placeholder="mail.metricspro.tech" value={cfg.imap_host || ''} onChange={e => set({ imap_host: e.target.value })} /></div>
          <div><label style={lbl}>Port</label><input style={{ ...sel, width: '100%' }} value={cfg.imap_port || 993} onChange={e => set({ imap_port: Number(e.target.value) || 993 })} /></div>
          <div><label style={lbl}>Username</label><input style={{ ...sel, width: '100%' }} placeholder="b2b@metricspro.tech" value={cfg.username || ''} onChange={e => set({ username: e.target.value })} /></div>
          <div><label style={lbl}>Password {cfg.has_password && <span style={{ color: '#16794a' }}>(set)</span>}</label><input type="password" style={{ ...sel, width: '100%' }} placeholder={cfg.has_password ? '•••• keep' : 'mailbox password'} value={pwd} onChange={e => setPwd(e.target.value)} /></div>
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
          <button className="btn btn-primary" disabled={busy === 'save'} onClick={save}>Save</button>
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
        {customTypes.length > 0 ? (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>{['Sheet', 'Key (use in a pattern)', 'Captured rows', ''].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
            <tbody>
              {customTypes.map((c: any) => (
                <tr key={c.report_key} style={{ borderTop: '1px solid var(--border)', fontSize: 13 }}>
                  <td style={{ padding: '6px 8px', fontWeight: 600 }}>{c.label}</td>
                  <td style={{ padding: '6px 8px' }}><code>{c.report_key}</code></td>
                  <td style={{ padding: '6px 8px' }}>{c.rows || 0}</td>
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
                    {(m.attachments || []).length === 0 ? <span style={{ color: 'var(--text3)', fontSize: 12 }}>no attachments</span> :
                      (m.attachments || []).map((a: any, j: number) => (
                        <div key={j} style={{ fontSize: 12 }}>{a.name} {a.matches ? <span className="badge" style={{ background: '#16794a', color: '#fff', fontSize: 11 }}>→ {a.matches}</span> : <span style={{ color: 'var(--text3)' }}>no pattern</span>}</div>
                      ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
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
                <td style={cell}>{p.status === 'ok' ? <span style={{ color: '#16794a' }}>✓ {p.rows_saved} rows</span> : <span style={{ color: '#dc2626' }}>✕ {p.detail}</span>}</td>
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
                    {s.last_status && <div style={{ color: 'var(--text3)', fontSize: 11, marginTop: 2, maxWidth: 240, whiteSpace: 'normal' }}>{s.last_status}</div>}
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                    <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px' }} disabled={authBusy === s.id} onClick={() => startLogin(s)}>{authBusy === s.id ? '…' : (s.auth_status === 'authenticated' ? '🔁 Re-auth' : '🔐 Log in')}</button>{' '}
                    <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px' }} onClick={() => runSource(s)}>▶ Pull now</button>{' '}
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
                <input style={{ width: '100%', padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, marginTop: 4 }} list="processors" value={srcDraft.processor || ''} onChange={e => {
                  const proc = e.target.value
                  const patch: any = { processor: proc }
                  if (!srcDraft.portal_url && proc === 'b2bsoft') patch.portal_url = 'https://wsreports.b2bsoft.com'
                  setSrcDraft({ ...srcDraft, ...patch })
                }} />
                <datalist id="processors"><option value="vidapay" /><option value="total_access" /><option value="b2bsoft" /><option value="epay" /><option value="other" /></datalist></label>
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
                <input style={{ width: '100%', padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, marginTop: 4 }} placeholder="http://user:pass@host:port" value={srcDraft.proxy_url || ''} onChange={e => setSrcDraft({ ...srcDraft, proxy_url: e.target.value })} /></label>
            </div>
            <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 8px' }}>
              💡 If Log in returns “Something doesn&apos;t look right” / an anti-bot page, the portal is blocking the
              server&apos;s datacenter IP. Enter a <b>residential / allow-listed proxy</b> above to route the login
              through it (leave blank otherwise).
            </p>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
                <input type="checkbox" checked={!!srcDraft.enabled} onChange={e => setSrcDraft({ ...srcDraft, enabled: e.target.checked })} /> Enabled (auto-pull once the scraper is wired)</label>
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
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', alignItems: 'center' }}>
              <button className="btn btn-secondary" style={{ fontSize: 13 }} disabled={authBusy === 'verify'} onClick={() => startLogin(twoFa.source)}>↻ Resend</button>
              <div style={{ flex: 1 }} />
              <button className="btn btn-secondary" style={{ fontSize: 13 }} disabled={!!authBusy} onClick={() => { setTwoFa(null); setCode('') }}>Cancel</button>
              <button className="btn btn-primary" style={{ fontSize: 13 }} disabled={authBusy === 'verify' || !code.trim()} onClick={verify2fa}>{authBusy === 'verify' ? 'Verifying…' : 'Verify'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
