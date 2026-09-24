'use client'
// "Clean up duplicate customers" — the one-time cleanup of customers created before the matcher (index §30.16):
// same-name duplicates merged into the first created, placeholder bill-tos ('Walk In') detached from their sales.
// Preview first (writes nothing), then Apply with the previewed count — the backend refuses a count that no longer
// matches the plan. Every merge can be undone with Un-merge. Backend: POST /pos/customers/dedupe (gated
// pos_customers_merge; needs migration 1017).
import { useState, type CSSProperties } from 'react'
import { api } from '@/lib/client'

type Merge = { from: string; from_number?: number | null; into: string; into_number?: number | null; name: string }
type Detach = { id: string; name: string; cust_number?: number | null }
type Plan = { merges: Merge[]; detach: Detach[]; skipped: { name: string; customers: number; why: string }[]; count: number; ready: boolean; words: string[] }

const box: CSSProperties = { marginBottom: 14, padding: '10px 12px', border: '1px dashed var(--border)', borderRadius: 6, fontSize: 13 }

export default function DedupeCard({ onDone }: { onDone: () => void }) {
  const [plan, setPlan] = useState<Plan | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  const preview = async () => {
    setBusy(true); setErr(''); setMsg('')
    try { setPlan(await api('/api/v1/pos/customers/dedupe', { method: 'POST', body: JSON.stringify({ dry_run: true }) })) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Could not read the customers') }
    finally { setBusy(false) }
  }

  const apply = async () => {
    if (!plan || !confirm(`Apply ${plan.count} change(s)? Each merge can be undone with Un-merge on the customer.`)) return
    setBusy(true); setErr(''); setMsg('')
    try {
      const r: Plan & { failed?: { name: string; words: string[] }[] } =
        await api('/api/v1/pos/customers/dedupe', { method: 'POST', body: JSON.stringify({ dry_run: false, confirm_count: plan.count }) })
      setMsg(r.words.join(' '))
      if (r.failed?.length) setErr(r.failed.map(f => `${f.name}: ${f.words.join(' ')}`).join(' · '))
      setPlan(null)
      onDone()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Could not apply the cleanup') }
    finally { setBusy(false) }
  }

  // the plan's last sentence speaks to the API caller ("send this again with confirm_count …"); the button says it here
  const words = (plan?.words || []).filter(w => !w.startsWith('to apply,'))

  return (
    <div style={box}>
      <div style={{ fontWeight: 700 }}>Clean up duplicate customers</div>
      <div style={{ color: 'var(--text2)', margin: '4px 0 8px' }}>
        Merges customers saved more than once under the same name, and detaches placeholder names like &quot;Walk In&quot; from their sales.
        Preview first — nothing changes until you click Apply.
      </div>
      <button className="btn btn-secondary" disabled={busy} onClick={preview}>{busy && !plan ? 'Reading…' : plan ? 'Preview again' : 'Preview the cleanup'}</button>
      {err && <div style={{ color: '#9b1c1c', marginTop: 8 }}>{err}</div>}
      {msg && <div style={{ color: '#0a7d33', marginTop: 8 }}>✓ {msg}</div>}
      {plan && (
        <div style={{ marginTop: 10 }}>
          {words.map(w => <div key={w}>• {w}</div>)}
          {plan.merges.length > 0 && (
            <div style={{ marginTop: 6 }}>
              <b>Merge:</b>
              {plan.merges.map(m => <div key={m.from}>{m.name} #{m.from_number ?? '?'} → #{m.into_number ?? '?'}</div>)}
            </div>
          )}
          {plan.detach.length > 0 && (
            <div style={{ marginTop: 6 }}>
              <b>Detach:</b> {plan.detach.map(d => `${d.name} #${d.cust_number ?? '?'}`).join(', ')}
            </div>
          )}
          {plan.count > 0 && plan.ready && (
            <button className="btn btn-primary" style={{ marginTop: 8 }} disabled={busy} onClick={apply}>{busy ? 'Applying…' : `Apply ${plan.count} change(s)`}</button>
          )}
        </div>
      )}
    </div>
  )
}
