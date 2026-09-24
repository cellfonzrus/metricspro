'use client'
// "Lines the vendor pays" — the per-company rule that makes a receipt rebuilt from the reports print the
// lines a vendor paid (rate-plan rebates, kickers, perks…) at $0.00, as the register prints them (owner
// 2026-09-24: "$0.00 like register"; index §30.14a). The words are PROPOSED from this company's own lines
// — the category words whose lines add up to each invoice's vendor-paid tenders — with the proof, and are
// saved only when confirmed here. Backend: GET/PUT /pos/sales-from-reports/vendor-paid-lines.
import { useState, type CSSProperties } from 'react'
import { api } from '@/lib/client'

type Proof = { invoices: number; tied: number; tied_without_rule: number; lines_matched: number; amount_matched: number; off: number; words: string }
type Evidence = {
  invoices: number; lines: number
  saved: { tokens: string[]; confirmed_by?: string | null; confirmed_at?: string | null }
  saved_proof: Proof | null
  proposal: { tokens: string[]; steps: { token: string; tied: number }[]; proof: Proof } | null
}

const box: CSSProperties = { marginTop: 12, padding: '10px 12px', border: '1px dashed var(--border)', borderRadius: 6, fontSize: 13 }
const money = (n: number) => `$${(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

export default function VendorPaidCard({ from, to }: { from: string; to: string }) {
  const [ev, setEv] = useState<Evidence | null>(null)
  const [picked, setPicked] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  const load = async () => {
    setBusy(true); setErr(''); setMsg('')
    try {
      const r: Evidence = await api(`/api/v1/pos/sales-from-reports/vendor-paid-lines?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`)
      setEv(r)
      setPicked(r.saved.tokens.length ? r.saved.tokens : (r.proposal?.tokens || []))
    } catch (e) { setErr(e instanceof Error ? e.message : 'Could not read the lines') }
    finally { setBusy(false) }
  }

  const save = async (tokens: string[]) => {
    setBusy(true); setErr(''); setMsg('')
    try {
      const r = await api('/api/v1/pos/sales-from-reports/vendor-paid-lines', { method: 'PUT', body: JSON.stringify({ from, to, tokens }) })
      setMsg(r.words || 'saved')
      await load()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Could not save the rule') }
    finally { setBusy(false) }
  }

  const words = Array.from(new Set([...(ev?.proposal?.tokens || []), ...(ev?.saved.tokens || [])]))
  const toggle = (t: string) => setPicked(p => p.includes(t) ? p.filter(x => x !== t) : [...p, t])

  return (
    <div style={box}>
      <div style={{ fontWeight: 700 }}>Lines the vendor pays — print at $0.00 like the register</div>
      <div style={{ color: 'var(--text2)', margin: '4px 0 8px' }}>
        The sales report carries the lines a vendor pays you for (rate-plan rebates, kickers, perks) with their amount; the register prints
        them at $0.00. Pick the category words those lines sit under — we propose them from your own lines, proven by each invoice's
        vendor-paid tender — then save and rebuild. Phones and accessories are never picked (they have a cost).
      </div>
      <button className="btn btn-secondary" disabled={busy} onClick={load}>{busy ? 'Reading…' : ev ? 'Re-check' : 'Show the proposed words'}</button>
      {err && <div style={{ color: '#9b1c1c', marginTop: 8 }}>{err}</div>}
      {msg && <div style={{ color: '#0a7d33', marginTop: 8 }}>✓ {msg}</div>}
      {ev && (
        <div style={{ marginTop: 10 }}>
          <div>{ev.invoices.toLocaleString()} invoice(s) and {ev.lines.toLocaleString()} line(s) in {from} – {to}.</div>
          {ev.saved.tokens.length > 0
            ? <div style={{ marginTop: 4 }}><b>Saved rule:</b> {ev.saved.tokens.map(t => `'${t}'`).join(', ')}{ev.saved_proof && <> — {ev.saved_proof.words}</>}</div>
            : <div style={{ marginTop: 4, color: '#b45309' }}>No rule saved yet — the lines the vendor paid print their amounts on the rebuilt receipts.</div>}
          {ev.proposal && (
            <div style={{ marginTop: 6 }}>
              <b>Proposed from your lines:</b> {ev.proposal.proof.words}
              {ev.proposal.proof.amount_matched ? <> ({ev.proposal.proof.lines_matched.toLocaleString()} line(s), {money(ev.proposal.proof.amount_matched)})</> : null}.
            </div>
          )}
          {words.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, margin: '8px 0' }}>
              {words.map(t => (
                <label key={t} style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                  <input type="checkbox" checked={picked.includes(t)} onChange={() => toggle(t)} /> {t}
                </label>
              ))}
            </div>
          )}
          {!ev.proposal && <div style={{ color: 'var(--text2)', marginTop: 6 }}>Nothing to propose — this period has no invoice with both the invoice upload and its lines.</div>}
          <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
            <button className="btn btn-primary" disabled={busy || picked.length === 0} onClick={() => save(picked)}>Save these words</button>
            {ev.saved.tokens.length > 0 && <button className="btn btn-secondary" disabled={busy} onClick={() => save([])}>Clear the rule</button>}
          </div>
        </div>
      )}
    </div>
  )
}
