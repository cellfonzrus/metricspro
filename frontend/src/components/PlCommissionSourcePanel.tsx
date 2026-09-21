'use client'
// ── WHICH SOURCE BOOKS THE P&L's COMMISSION LINES — one panel, every surface (mig 1013) ────────────
// Owner (2026-09-21): "p&l is not showing the commission received, it shows in the commission ledger
// but not populating the p&l - check platform wide not bandaid."
//
// The panel READS one endpoint (GET /commcalc/pl-commission-source: the saved value, the layman options,
// the suggestion with its evidence, the P&L lines the ledger's buckets are linked to, and "this statement
// will show in") and WRITES through the ONE writer for the org's money-policy row
// (PUT /commcalc/commission-settings {pl_commission_source}), then re-reads. A suggestion is OFFERED with
// its reason and applied only when the person clicks it — never silently. No source word, line key or
// href is spelled here: everything comes from the payload; links are ScreenLink keys.
import { useCallback, useEffect, useState, type CSSProperties } from 'react'
import { api, ORG_ID } from '@/lib/client'
import ScreenLink from '@/components/ScreenLink'
import ShowsIn from '@/components/ShowsIn'
import type { ShowsIn as ShowsInPayload, PlLine } from '@/lib/report-kinds'

type Option = { value: string; label: string; blurb: string }
type Payload = {
  value: string; ready: boolean; migration: string; not_ready_note: string | null
  options: Option[]
  suggestion: { value: string | null; why: string }
  evidence: { feeds: Record<string, boolean>; feed_tables_with_rows: string[]; ledger_lines: number
    ledger_lines_truncated: boolean; ledger_periods: { period: string; lines: number }[] }
  pl_link: { lines: PlLine[]; source: string; active: boolean; source_label?: string }
  shows_in: ShowsInPayload
}

const card: CSSProperties = { border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px', background: 'var(--surface, #fff)', marginBottom: 12 }
const note: CSSProperties = { fontSize: 12, color: 'var(--text2)', lineHeight: 1.45 }
const pill: CSSProperties = { display: 'inline-block', fontSize: 11, padding: '1px 8px', borderRadius: 999, border: '1px solid var(--border)', marginRight: 6 }

export default function PlCommissionSourcePanel({ compact = false, lead }: { compact?: boolean; lead?: string }) {
  const [p, setP] = useState<Payload | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<string | null>(null)

  const load = useCallback(async () => {
    try { setP(await api(`/api/v1/commcalc/pl-commission-source?org_id=${ORG_ID}`)); setErr(null) }
    catch (e: any) { setErr(e?.message || 'could not read the P&L commission source') }
  }, [])
  useEffect(() => { load() }, [load])

  const save = async (value: string) => {
    if (!p || value === p.value) return
    setSaving(true); setErr(null)
    try {
      const r = await api(`/api/v1/commcalc/commission-settings?org_id=${ORG_ID}`, { method: 'PUT', body: JSON.stringify({ pl_commission_source: value }) })
      if (r?.pl_commission_source !== value) throw new Error(`saved value read back as '${r?.pl_commission_source}'`)
      setSavedAt(new Date().toLocaleTimeString())
      await load()          // READ BACK — the panel shows what the P&L will read, never what was posted
    } catch (e: any) { setErr(e?.message || 'not saved') }
    finally { setSaving(false) }
  }

  if (!p) return <div style={{ ...card, ...note }} data-pl-commission-source="loading">{err ? <span style={{ color: '#b91c1c' }}>{err}</span> : 'Working out which source books the P&L…'}</div>
  const current = p.options.find(o => o.value === p.value)
  return (
    <div style={card} data-pl-commission-source={p.value}>
      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 2 }}>{lead || 'Which source books the P&L\'s commission lines?'}</div>
      <div style={{ ...note, marginBottom: 8 }}>
        Today: <b>{current?.label || p.value}</b>. {current?.blurb}
      </div>
      {!p.ready && <div style={{ ...note, color: '#9a3412', background: '#fff7ed', border: '1px solid #fdba74', borderRadius: 8, padding: '6px 10px', marginBottom: 8 }}>{p.not_ready_note}</div>}
      <div style={{ display: 'grid', gap: 6, marginBottom: 8 }}>
        {p.options.map(o => (
          <label key={o.value} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 13, cursor: p.ready ? 'pointer' : 'not-allowed', opacity: p.ready ? 1 : 0.6 }}>
            <input type="radio" name="pl_commission_source" value={o.value} checked={p.value === o.value} disabled={!p.ready || saving} onChange={() => save(o.value)} style={{ marginTop: 3 }} />
            <span><b>{o.label}</b>{!compact && <div style={note}>{o.blurb}</div>}</span>
          </label>
        ))}
      </div>
      {p.suggestion?.value && p.suggestion.value !== p.value && (
        <div style={{ ...note, background: 'rgba(37,99,235,.06)', border: '1px solid var(--border)', borderRadius: 8, padding: '6px 10px', marginBottom: 8 }} data-suggestion={p.suggestion.value}>
          <b>Suggested:</b> {p.options.find(o => o.value === p.suggestion.value)?.label}. {p.suggestion.why}{' '}
          <button disabled={!p.ready || saving} onClick={() => save(p.suggestion.value!)} style={{ marginLeft: 6, fontSize: 12, padding: '2px 8px', borderRadius: 6, border: '1px solid var(--border)', background: '#fff', cursor: 'pointer' }}>Use this</button>
        </div>
      )}
      {!p.suggestion?.value && p.suggestion?.why && <div style={{ ...note, marginBottom: 8 }}>{p.suggestion.why}</div>}
      <div style={{ ...note, marginBottom: 6 }}>
        <span style={pill} title={Object.entries(p.evidence.feeds).map(([t, ok]) => `${t}: ${ok ? 'rows' : 'empty'}`).join(' · ')}>feed tables with rows: {p.evidence.feed_tables_with_rows.length ? p.evidence.feed_tables_with_rows.join(', ') : 'none'}</span>
        <span style={pill}>Commission Ledger: {p.evidence.ledger_lines.toLocaleString()}{p.evidence.ledger_lines_truncated ? '+' : ''} line(s){p.evidence.ledger_periods.length ? ` over ${p.evidence.ledger_periods.length} period(s)` : ''}</span>
      </div>
      <ShowsIn info={p.shows_in} lead="A commission statement will show in" compact={compact} style={{ margin: '4px 0' }} />
      <div style={note}>
        Saving takes effect the next time a period is computed; each commission line on the <ScreenLink to="pl_statement">P&L Statement</ScreenLink> shows what the feed tables booked and what the ledger holds, with the difference.
        {saving && <span> · Saving…</span>}{savedAt && !saving && <span> · Saved ✓ {savedAt}</span>}
        {err && <span style={{ color: '#b91c1c' }}> · Not saved — {err}</span>}
      </div>
    </div>
  )
}
