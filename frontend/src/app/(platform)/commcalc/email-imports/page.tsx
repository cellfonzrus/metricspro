'use client'
import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/client'

// Generic email (IMAP) inbox sweep — sibling of the FTP sweep. Configure a mailbox (host/creds) and
// attachment-filename → upload-type patterns; the backend polls the inbox on a schedule and routes
// each matching attachment to the right parser. For B2B Soft (or any vendor) that EMAILS report files.
const UPLOAD_TYPES = ['sales', 'daily_sales', 'payment_detail', 'mi_report', 'dlar_rep', 'dlar_store', 'comp_report', 'catalog', 'inventory_aging', 'x_report']
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
  const BLANK = { imap_port: 993, use_ssl: true, mailbox: 'INBOX', since_days: 14, patterns: [] as any[], frequency: 'daily', hour: 7 }
  const [cfg, setCfg] = useState<any>({ account: 'default', ...BLANK })
  const [accounts, setAccounts] = useState<any[]>([])
  const [pwd, setPwd] = useState('')
  const [test, setTest] = useState<any>(null)
  const [processed, setProcessed] = useState<any[]>([])
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')

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
  }, [])
  useEffect(() => { refresh() }, [refresh])

  const set = (patch: any) => setCfg((c: any) => ({ ...c, ...patch }))
  const setPat = (i: number, patch: any) => setCfg((c: any) => ({ ...c, patterns: c.patterns.map((p: any, j: number) => j === i ? { ...p, ...patch } : p) }))
  const addPat = () => setCfg((c: any) => ({ ...c, patterns: [...(c.patterns || []), { pattern: '', upload_type: 'daily_sales', note: '' }] }))
  const delPat = (i: number) => setCfg((c: any) => ({ ...c, patterns: c.patterns.filter((_: any, j: number) => j !== i) }))

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
    try { const r: any = await api('/api/v1/commcalc/email-sweep/test', { method: 'POST', body: JSON.stringify(body()) }); setTest(r); setMsg(`✅ Connected — ${r.count} recent message(s), ${r.matched_attachments} matching attachment(s).`) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy('') }
  }
  async function runNow() {
    setBusy('run')
    try { const r: any = await api(`/api/v1/commcalc/email-sweep/run-now?account=${encodeURIComponent(cfg.account || 'default')}`, { method: 'POST', body: '{}' }); setMsg(r.ok ? `✅ Ingested ${r.ingested} attachment(s).` : `❌ ${r.error}`); refresh(cfg.account) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy('') }
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
                <td style={cell}><select style={sel} value={p.upload_type} onChange={e => setPat(i, { upload_type: e.target.value })}>{UPLOAD_TYPES.map(u => <option key={u} value={u}>{u}</option>)}</select></td>
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
    </div>
  )
}
