'use client'

/**
 * MONTHLY SALES BASIS — derive console (mod-commission, 2026-08-01).
 *
 * The daily B2B email feed lands in `daily_sales_feed`. The month a closed period is PAID from is
 * `raw_sales`, which is DERIVED from that feed. Until now the derivation asked the wall clock for its
 * period, so at 00:00 on the 1st it moved to the new month and never looked back — while the feed kept
 * finalizing the old one for hours (luxelink, 2026-08-01: 45 July transactions delivered after midnight,
 * never derived, missing from every July report and unpaid in a July recompute).
 *
 * This page is the two things that were missing: somewhere to SEE that gap for a period, and a
 * click-path to CLOSE it. Preview first (dry run, writes nothing), then re-derive.
 */
import { useEffect, useState, useCallback } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

type Cfg = { enabled: boolean; days: number; retain: number | null }
type CfgResp = {
  org_id: string; config: Cfg; default: Cfg; max_days: number; today: string
  window_open: boolean; current_period: string; prior_period: string
  next_run_periods: string[]; grace_note: string
}
type Status = {
  period: string; feed_trans: number; monthly_trans: number; feed_lines: number; monthly_lines: number
  has_feed: boolean; missing_in_monthly: number; missing_in_daily: number; sample_missing: string[]
  capped: boolean; is_closed_month: boolean; grace_window_open: boolean; grace_config: Cfg
  auto_derive_enabled: boolean; action: string | null
}

const card: React.CSSProperties = { padding: 16, marginBottom: 14 }
const fin: React.CSSProperties = {
  padding: '6px 9px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13,
  background: 'var(--surface)',
}
const lbl: React.CSSProperties = { fontSize: 12, color: 'var(--text3)', display: 'block', marginBottom: 3 }

function Tile({ label, value, tone }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div style={{ padding: '10px 14px', border: '1px solid var(--border)', borderRadius: 8, minWidth: 140 }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</div>
      <div style={{ fontSize: 21, fontWeight: 700, color: tone || 'var(--text)' }}>{value}</div>
    </div>
  )
}

export default function SalesDerivePage() {
  const { period, setPeriod, periods } = usePeriod()
  const [cfg, setCfg] = useState<CfgResp | null>(null)
  const [draft, setDraft] = useState<Cfg | null>(null)
  const [status, setStatus] = useState<Status | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [preview, setPreview] = useState<any>(null)

  const loadCfg = useCallback(async () => {
    try {
      const r: CfgResp = await api(`/api/v1/commcalc/sales/derive-config?org_id=${ORG_ID}`)
      setCfg(r); setDraft(r.config)
    } catch (e: any) { setErr(e?.message || 'could not load the derive window') }
  }, [])

  const loadStatus = useCallback(async (p: string) => {
    setLoading(true); setErr(''); setPreview(null)
    try {
      setStatus(await api(`/api/v1/commcalc/sales/derive-status?org_id=${ORG_ID}&period=${encodeURIComponent(p)}`))
    } catch (e: any) { setErr(e?.message || 'could not read the derive status') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { loadCfg() }, [loadCfg])
  useEffect(() => { loadStatus(period) }, [period, loadStatus])

  async function saveCfg() {
    if (!draft) return
    setBusy('cfg'); setMsg(''); setErr('')
    try {
      const r: CfgResp = await api(`/api/v1/commcalc/sales/derive-config?org_id=${ORG_ID}`, {
        method: 'PUT',
        body: JSON.stringify({ enabled: draft.enabled, days: Number(draft.days), retain: draft.retain }),
      })
      setCfg(r); setDraft(r.config); setMsg('✅ Saved.')
    } catch (e: any) { setErr(e?.message || 'save failed') }
    finally { setBusy('') }
  }

  async function derive(commit: boolean) {
    setBusy(commit ? 'commit' : 'preview'); setMsg(''); setErr('')
    try {
      const r = await api(
        `/api/v1/commcalc/sales/promote-feed?org_id=${ORG_ID}&period=${encodeURIComponent(period)}&dry_run=${commit ? 'false' : 'true'}`,
        { method: 'POST' })
      setPreview(r)
      if (commit) {
        setMsg(r?.skipped
          ? `⚠️ Nothing written — ${r.skipped}`
          : `✅ ${period} re-derived: ${r?.written ?? 0} line(s), ${r?.result_trans ?? 0} transaction(s). Now re-calculate ${period} so the new sales are paid.`)
        loadStatus(period)
      }
    } catch (e: any) { setErr(e?.message || 'derive failed') }
    finally { setBusy('') }
  }

  const gap = status?.missing_in_monthly || 0
  const behind = gap > 0

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔄 Monthly sales basis</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Commissions for a closed month are calculated from the monthly basis, which is derived from the daily sales feed.
          This page shows whether a month&apos;s basis is in step with its feed — and re-derives it when it is not.
        </p>
      </div>

      {/* ── universal period filter (RULE FIVE) ──────────────────────────────────────────────── */}
      <div className="card" style={{ ...card, display: 'flex', gap: 14, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <div>
          <label style={lbl}>Period</label>
          <select style={fin} value={period} onChange={e => setPeriod(e.target.value)}>
            {periods.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        {cfg && (
          <div style={{ fontSize: 12, color: 'var(--text3)', paddingBottom: 6 }}>
            Today {cfg.today}. The next automatic run covers{' '}
            <strong>{cfg.next_run_periods.join(' + ')}</strong>
            {cfg.window_open ? ' (month-boundary grace window OPEN).' : '.'}
          </div>
        )}
      </div>

      {err && <div className="card" style={{ ...card, color: '#b42318' }}>❌ {err}</div>}
      {msg && <div className="card" style={{ ...card, fontSize: 13 }}>{msg}</div>}

      {/* ── the gap for the selected period ──────────────────────────────────────────────────── */}
      <div className="card" style={card}>
        <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 10px' }}>{period} — feed vs basis</h2>
        {loading && <div style={{ fontSize: 13, color: 'var(--text3)' }}>Counting…</div>}
        {!loading && status && (
          <>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
              <Tile label="Daily feed" value={status.feed_trans.toLocaleString()} />
              <Tile label="Monthly basis" value={status.monthly_trans.toLocaleString()} />
              <Tile label="In feed, not in basis" value={gap.toLocaleString()} tone={behind ? '#b42318' : '#16794a'} />
              <Tile label="In basis, not in feed" value={status.missing_in_daily.toLocaleString()} />
            </div>
            {!status.has_feed && (
              <div style={{ fontSize: 13, color: 'var(--text2)' }}>
                No daily-feed rows for {period}. There is nothing to derive from, so the basis is left exactly as it is —
                a month with no feed is never rebuilt from an empty one.
              </div>
            )}
            {status.has_feed && !behind && (
              <div style={{ fontSize: 13, color: '#16794a' }}>
                ✅ Every transaction the feed delivered for {period} is in the monthly basis.
              </div>
            )}
            {status.has_feed && behind && (
              <div style={{ fontSize: 13, color: '#b42318' }}>
                ⚠️ {gap.toLocaleString()} transaction(s) reached the daily feed and never reached the basis.
                {status.is_closed_month
                  ? ' This month is closed, so those sales are missing from every report for it and would not be paid by a recompute.'
                  : ' This month is still open; the next automatic run should pick them up.'}
                {status.sample_missing?.length > 0 && (
                  <div style={{ marginTop: 6, fontSize: 12, color: 'var(--text3)' }}>
                    e.g. transaction {status.sample_missing.slice(0, 6).join(', ')}
                    {gap > 6 ? ' …' : ''} — see <a href={`/commcalc/sales-recon`}>Sales Feed Recon</a> for the full list.
                  </div>
                )}
              </div>
            )}
            {status.capped && (
              <div style={{ marginTop: 6, fontSize: 12, color: '#b45309' }}>
                Note: this period exceeded the scan cap, so the counts above are a lower bound.
              </div>
            )}
            {!status.auto_derive_enabled && (
              <div style={{ marginTop: 6, fontSize: 12, color: '#b45309' }}>
                Automatic derivation is switched OFF for this tenant (Connectors → Sales Transactions is set to manual),
                so nothing will build this basis on its own.
              </div>
            )}

            <div style={{ display: 'flex', gap: 8, marginTop: 14, flexWrap: 'wrap' }}>
              <button className="btn btn-secondary" style={{ fontSize: 13 }} disabled={!!busy} onClick={() => derive(false)}>
                {busy === 'preview' ? 'Previewing…' : '👁 Preview re-derive (writes nothing)'}
              </button>
              <button className="btn" style={{ fontSize: 13 }} disabled={!!busy || !status.has_feed}
                      onClick={() => { if (confirm(`Re-derive the ${period} sales basis from the daily feed?\n\nThis rebuilds the monthly basis for ${period}. It does NOT recalculate commissions — run Calculate for ${period} afterwards.`)) derive(true) }}>
                {busy === 'commit' ? 'Re-deriving…' : `🔄 Re-derive ${period}`}
              </button>
            </div>
            <div style={{ marginTop: 6, fontSize: 12, color: 'var(--text3)' }}>
              Re-deriving never changes anybody&apos;s pay by itself — it refreshes the sales the calculation reads.
              Run Calculate for {period} afterwards.
            </div>
          </>
        )}
      </div>

      {/* ── preview / result ─────────────────────────────────────────────────────────────────── */}
      {preview && (
        <div className="card" style={card}>
          <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 8px' }}>
            {preview.dry_run ? 'Preview' : 'Result'} — {preview.period}
          </h2>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <Tile label="Feed lines" value={(preview.feed_lines ?? 0).toLocaleString()} />
            <Tile label="Basis lines now" value={(preview.existing_lines ?? 0).toLocaleString()} />
            <Tile label="Basis lines after" value={(preview.result_lines ?? 0).toLocaleString()} />
            <Tile label="Transactions after" value={(preview.result_trans ?? 0).toLocaleString()} />
          </div>
          {preview.skipped && (
            <div style={{ marginTop: 10, fontSize: 13, color: '#b45309' }}>Skipped — {preview.skipped}</div>
          )}
        </div>
      )}

      {/* ── the grace window (RULE TWO: config, not a hard-coded 3) ──────────────────────────── */}
      <div className="card" style={card}>
        <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 4px' }}>Month-boundary grace window</h2>
        <p style={{ fontSize: 13, color: 'var(--text2)', margin: '0 0 12px' }}>
          The sales feed keeps finalizing a month for a while after midnight on the 1st. For this many days into a new
          month, the automatic derivation also re-derives the month that just closed, so late transactions still reach
          the basis they are paid from.
        </p>
        {draft && cfg && (
          <div style={{ display: 'flex', gap: 16, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
              <input type="checkbox" checked={!!draft.enabled}
                     onChange={e => setDraft({ ...draft, enabled: e.target.checked })} />
              Re-derive the previous month after rollover
            </label>
            <div>
              <label style={lbl}>Days after rollover (0 = off, max {cfg.max_days})</label>
              <input style={{ ...fin, width: 90 }} type="number" min={0} max={cfg.max_days}
                     value={draft.days} onChange={e => setDraft({ ...draft, days: Number(e.target.value) })} />
            </div>
            <div>
              <label style={lbl}>Shrink guard for these runs</label>
              <select style={fin} value={draft.retain == null ? '' : String(draft.retain)}
                      onChange={e => setDraft({ ...draft, retain: e.target.value === '' ? null : Number(e.target.value) })}>
                <option value="">Normal (85%)</option>
                <option value="0.95">Strict (95%)</option>
                <option value="1">Never lose a line (100%)</option>
              </select>
            </div>
            <button className="btn" style={{ fontSize: 13 }} disabled={busy === 'cfg'} onClick={saveCfg}>
              {busy === 'cfg' ? 'Saving…' : 'Save window'}
            </button>
          </div>
        )}
        {cfg && (
          <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text3)' }}>
            Default when nothing is saved: {cfg.default.enabled ? 'on' : 'off'}, {cfg.default.days} day(s).
            Runs made by this window are labelled “{cfg.grace_note}” in the upload history.
            Set 100% if you hand-upload the authoritative monthly file for closed months.
          </div>
        )}
      </div>
    </div>
  )
}
