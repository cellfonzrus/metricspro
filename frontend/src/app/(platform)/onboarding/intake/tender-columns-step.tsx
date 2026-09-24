'use client'
// ═══════════════════════════════════════════════════════════════════════════════════════════════
// TENANT ONBOARDING — Stage 2, step 2.5b: "Which columns are tender types, and what kind of payment is
// each?" (owner 2026-09-21: "sales by invoice report also has the tender types on the report, need to
// capture that as well — tender types is in columns").
//
// A sales-by-invoice export carries ONE AMOUNT COLUMN PER TENDER TYPE, and which columns those are is
// not fixed (a card brand, its non-integrated twin, a debit PIN column, a vendor rebate applied as
// payment …). This step lists every money column of the file that is not an invoice field, with the
// role the platform proposes from the words in the header — a tender with its canonical class (the ONE
// tender vocabulary the closing recon uses), a tax component, or nothing — the Σ over the file, and the
// tie: the tender columns must add up to the invoice totals. The person confirms or changes each column;
// the decisions ride the auto-saved draft and the commit remembers them for next month.
//
// This component classifies nothing and stores nothing of its own: the proposal, the vocabulary and the
// Σ come from the backend's analyze payload; the decisions go back as `tender_columns`. No carrier, POS
// vendor, card brand or tenant is named here (RULE TWO).
// ═══════════════════════════════════════════════════════════════════════════════════════════════
import { card, note, inp, btn, primary, ghost, mono, money, num } from './intake-shared'

export type TenderVocab = { key: string; label: string; recon_class: string; closing_axis: boolean; folds_to: string | null }
export type TenderColumn = {
  header: string; role: 'tender' | 'tax' | 'ignore' | string; tender_class: string | null; keyed_manually: boolean
  tax_total?: boolean; provenance: string; sum: number; cells: number; nonzero: number
}
export type TenderColumnsBlock = {
  step: string; columns: TenderColumn[]; confirmed: TenderColumn[]; tenders: TenderColumn[]; taxes: TenderColumn[]
  unplaced: { header: string; sum: number; cells: number; nonzero: number }[]; errors: string[]; scanned: number
  tax_header: string | null; vocab: TenderVocab[]; roles: string[]; earlier_choices: number
}
export type TenderDecision = { role: 'tender' | 'tax' | 'ignore'; tender_class?: string | null; keyed_manually?: boolean }
export type TenderTie = { our_total: number; file_total: number | null; file_total_source: string; difference: number | null; match: boolean | null; words: string } | null
export type TenderNumbers = {
  rows: number; distinct_txns: number; sum_amount: number; sum_invoice_total: number | null; sum_net_sales: number; sum_gp: number; sum_tax: number | null
  tenders: { rows: number; sum: number; customer_sum?: number; by_class: Record<string, number>; by_label: Record<string, number>; invoices_with_tenders: number
    not_customer?: { sum: number; by_label: Record<string, number>; invoices: number }
    difference: number | null; invoices_off: number; invoices_off_sample: { trans_id: string; invoice_total: number; tenders: number; difference: number }[]; words: string; match: boolean | null }
  tax: { sum: number | null; components: Record<string, number>; component_rows: number; components_sum: number; words: string }
  tie_field?: string
}

const ROLE_LABEL: Record<string, string> = { tender: 'a tender (how the invoice was paid)', tax: 'a tax component', ignore: 'not a tender — leave it' }

export type TenderBasisInfo = { basis: string; source: string; label: string; upload_kind: string; screen: string; bases: { key: string; label: string }[] }

export function TenderColumnsStep({ block, numbers, tie, decisions, setDecisions, basis, setBasis, basisInfo, busy, canRecheck, recheck, onBack, onNext, saveUi }: {
  block: TenderColumnsBlock | null; numbers: TenderNumbers | null; tie: TenderTie; decisions: Record<string, TenderDecision>
  setDecisions: (d: Record<string, TenderDecision>) => void
  basis: string; setBasis: (b: string) => void; basisInfo: TenderBasisInfo | null
  busy: boolean; canRecheck: boolean; recheck: () => Promise<unknown>
  onBack: () => void; onNext: () => void; saveUi: React.ReactNode
}) {
  if (!block) {
    return (
      <div style={card}>
        <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>2.5b — Tender types and tax columns</h2>
        <p style={note}>Nothing to check yet — read the file at 2.1 first.</p>
        <div style={{ display: 'flex', gap: 8, marginTop: 10 }}><button style={ghost} onClick={onBack}>← Back</button></div>
      </div>
    )
  }
  const vocabLabel = (k: string | null) => block.vocab.find(v => v.key === k)?.label || k || '—'
  const gateOf = (k: string | null) => block.vocab.find(v => v.key === k)?.recon_class || ''
  const rows: TenderColumn[] = [
    ...block.columns,
    ...block.unplaced.filter(u => !block.columns.some(c => c.header === u.header)).map(u => ({ header: u.header, role: 'ignore', tender_class: null, keyed_manually: false, provenance: 'not proposed — no rule placed it', sum: u.sum, cells: u.cells, nonzero: u.nonzero })),
  ]
  const decide = (h: string, patch: Partial<TenderDecision>, current: TenderColumn) => {
    const base: TenderDecision = decisions[h] || { role: current.role as TenderDecision['role'], tender_class: current.tender_class, keyed_manually: current.keyed_manually }
    setDecisions({ ...decisions, [h]: { ...base, ...patch } })
  }
  const ok = tie ? tie.match === true : null
  return (
    <div style={card}>
      <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>2.5b — Which columns are tender types, and what kind of payment is each?</h2>
      <p style={{ ...note, marginBottom: 8 }}>
        This export has one amount column per way of paying. Below is every money column that is not an invoice field, with the role we read from its header:
        a tender with its kind of payment, a tax component, or nothing. Confirm or change each; the kinds are the same ones the closing cash / card reconciliation uses, so a card column here is a card column there.
        A column the register keyed by hand (a &quot;non-integrated&quot; twin) is the same kind of payment, flagged.
        {block.earlier_choices > 0 && <> Your earlier choices for this file&apos;s columns were applied ({block.earlier_choices}).</>}
      </p>
      {!!block.errors.length && <div style={{ ...card, borderColor: '#ef4444', background: 'rgba(239,68,68,.06)', fontSize: 13, marginBottom: 10 }}><b>Fix before confirming:</b><ul style={{ margin: '4px 0 0 18px' }}>{block.errors.map((e, i) => <li key={i}>{e}</li>)}</ul></div>}
      <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse', marginBottom: 10 }}>
        <thead><tr style={{ color: 'var(--text2)' }}><th align="left">Column</th><th align="right" style={{ width: 120 }}>Σ in file</th><th align="right" style={{ width: 90 }}>Invoices</th><th align="left" style={{ width: 210 }}>Role</th><th align="left" style={{ width: 220 }}>Kind of payment</th><th align="left" style={{ width: 110 }}>Keyed by hand</th><th align="left">Where this came from</th></tr></thead>
        <tbody>
          {rows.map(c => {
            const d = decisions[c.header]
            const role = d?.role ?? c.role
            const cls = d ? (d.tender_class ?? null) : c.tender_class
            const keyed = d ? !!d.keyed_manually : c.keyed_manually
            const isTotalTax = !!c.tax_total
            return (
              <tr key={c.header} style={{ borderTop: '1px solid var(--border)', verticalAlign: 'top' }}>
                <td style={{ padding: '6px 4px', fontWeight: 600 }}>{c.header}</td>
                <td style={{ ...mono, padding: '6px 4px' }}>{money(c.sum)}</td>
                <td style={{ ...mono, padding: '6px 4px' }}>{num(c.nonzero)}</td>
                <td style={{ padding: '6px 4px' }}>
                  {isTotalTax ? <span style={note}>the invoice&apos;s total tax (mapped at 2.3)</span> : (
                    <select value={role} onChange={e => decide(c.header, { role: e.target.value as TenderDecision['role'], tender_class: e.target.value === 'tender' ? (cls || block.vocab[0]?.key) : null }, c)} style={{ ...inp, padding: '4px 6px', width: '100%' }}>
                      {block.roles.map(r => <option key={r} value={r}>{ROLE_LABEL[r] || r}</option>)}
                    </select>
                  )}
                </td>
                <td style={{ padding: '6px 4px' }}>
                  {role === 'tender' && !isTotalTax ? (
                    <select value={cls || ''} onChange={e => decide(c.header, { role: 'tender', tender_class: e.target.value }, c)} style={{ ...inp, padding: '4px 6px', width: '100%' }}>
                      <option value="">— pick —</option>
                      {block.vocab.map(v => <option key={v.key} value={v.key}>{v.label} ({v.recon_class})</option>)}
                    </select>
                  ) : <span style={note}>{role === 'tax' ? (isTotalTax ? 'total tax' : 'tax component, kept with its label') : '—'}</span>}
                </td>
                <td style={{ padding: '6px 4px' }}>{role === 'tender' && !isTotalTax ? <label style={{ fontSize: 13 }}><input type="checkbox" checked={keyed} onChange={e => decide(c.header, { role: 'tender', tender_class: cls, keyed_manually: e.target.checked }, c)} style={{ marginRight: 5 }} />yes</label> : <span style={note}>—</span>}</td>
                <td style={{ padding: '6px 4px', ...note, fontSize: 12 }}>{d ? 'confirmed by you' : c.provenance}{role === 'tender' && cls ? <> · counts as <b>{gateOf(cls)}</b> beside the register&apos;s X-report</> : null}</td>
              </tr>
            )
          })}
          {rows.length === 0 && <tr><td colSpan={7} style={{ ...note, padding: 8 }}>Every money column in this file is an invoice field — nothing to declare.</td></tr>}
        </tbody>
      </table>
      {numbers && (
        <div style={{ ...card, background: 'var(--bg,transparent)', fontSize: 13, marginBottom: 10 }}>
          <div><b>Do the customer's payments add up to the invoice totals?</b> {numbers.tenders.words}.</div>
          <div style={{ ...note, marginTop: 4 }}>
            Σ customer payments {money(numbers.tenders.customer_sum ?? numbers.tenders.sum)} on {num(numbers.tenders.invoices_with_tenders)} invoice(s)
            {!!numbers.tenders.not_customer?.sum && <>{' · '}paid by the vendor / a promotion (lands beside, not part of the invoice total) {money(numbers.tenders.not_customer.sum)} on {num(numbers.tenders.not_customer.invoices)} invoice(s)</>}
            {' · '}Σ invoice total {money(numbers.sum_invoice_total)}
            {numbers.tenders.difference !== null && <> · difference <span style={{ color: ok ? '#16a34a' : '#ef4444', fontWeight: 700 }}>{money(numbers.tenders.difference)}</span></>}
            {' · '}by kind: {Object.entries(numbers.tenders.by_class).map(([k, v]) => `${vocabLabel(k)} ${money(v)}`).join(' · ') || 'none'}
          </div>
          {numbers.tenders.invoices_off > 0 && <div style={{ ...note, fontSize: 12, marginTop: 4, color: '#b45309' }}>
            {num(numbers.tenders.invoices_off)} invoice(s) whose customer payments do not add up (first {numbers.tenders.invoices_off_sample.length}): {numbers.tenders.invoices_off_sample.map(o => `${o.trans_id} total ${money(o.invoice_total)} vs tenders ${money(o.tenders)} (${money(o.difference)})`).join(' · ')}
            — fix the columns above, or attest the difference with a reason at 2.5.
          </div>}
          <div style={{ marginTop: 6 }}><b>Tax:</b> {numbers.tax.words}{numbers.tax.component_rows > 0 && <> — {num(numbers.tax.component_rows)} component row(s) land with their label</>}.</div>
        </div>
      )}
      <div style={{ ...note, fontSize: 12, marginBottom: 12 }}>
        On confirm, one row lands per invoice and tender column with an amount, carrying its kind of payment; the columns you confirmed are remembered for this file&apos;s next upload. In Stage 4 the tender split per store and day is shown beside the register&apos;s X-report where one exists.
      </div>
      {/* THE TENDER BASIS (owner 2026-09-21: "nothing on cash collected either") — what Cash Collected and the cash / card and
          deposit reconciliations read per store-day: the register's X-report (the default), these invoice tenders, or the
          X-report when one exists else the invoice tenders. One per-company setting, saved on confirm, shown on every closing page. */}
      <div style={{ ...card, background: 'var(--bg,transparent)', fontSize: 13, marginBottom: 12 }}>
        <div style={{ fontWeight: 700, marginBottom: 4 }}>Do you also upload a daily cash register / X-report?</div>
        <div style={{ ...note, marginBottom: 6 }}>Cash Collected and the cash / card reconciliation read ONE tender split per store and day. If you do not upload an X-report, the invoice tenders you declared above become that basis.
          {basisInfo && <> Today this company reads <b>{basisInfo.label}</b> ({basisInfo.source}).</>}</div>
        {(basisInfo?.bases || []).map(b => (
          <label key={b.key} style={{ display: 'block', fontSize: 13, marginBottom: 3 }}>
            <input type="radio" name="tender_basis" checked={basis === b.key} onChange={() => setBasis(b.key)} style={{ marginRight: 6 }} />
            {b.key === 'x_report' ? 'Yes, every day — keep reading the X-report' : b.key === 'invoice' ? 'No — the invoice tenders are my cash-collected basis' : 'Sometimes — read the X-report when one exists for the store-day, else the invoice tenders'}
            <span style={{ ...note, fontSize: 11 }}> ({b.label})</span>
          </label>
        ))}
        {!basis && <div style={{ ...note, fontSize: 11 }}>Leave unanswered to keep the current setting.</div>}
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
        <button style={ghost} onClick={onBack}>← Back</button>
        <button style={btn} disabled={busy || !canRecheck} onClick={() => recheck()}>Re-check with these decisions</button>
        <button style={primary} disabled={busy || block.errors.length > 0} onClick={onNext}>Confirm →</button>
        {saveUi}
      </div>
    </div>
  )
}
