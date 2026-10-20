'use client'
// THE print-format picker (owner 2026-10-19, verbatim: "all stores should have the option to print
// the print in the required format from the print receipt screen as there are receipts from the
// thermal printer also"). ONE component, used by the till's completion modal and by the Sale Log
// reprint, so a counter cannot be offered a format in one place and not the other.
//
// WHAT IT DOES NOT DO: it invents no formats and no paper widths. Both lists come from the server's
// own receipt vocabulary (`layout_options` / `paper_options` on the receipt response), so the screen
// can never offer something the renderer cannot produce. Nothing here is saved — the choice applies
// to THIS print, and the next one starts again from the store's own declared setting.
import type { PrintChoice, SaleReceipt } from '@/lib/pos-receipt'

const rowStyle: React.CSSProperties = { display: 'flex', gap: 8, alignItems: 'flex-end' }
const labelStyle: React.CSSProperties = { fontSize: 11, color: 'var(--text3)', marginBottom: 3 }
const selectStyle: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)',
  borderRadius: 6, padding: '6px 8px', fontSize: 12, width: '100%',
}

export function PrintFormatPicker({ view, choice, onChange, disabled }: {
  /** What the server resolved for this sale — null while it is still being read. */
  view: SaleReceipt | null
  choice: PrintChoice
  onChange: (next: PrintChoice) => void
  disabled?: boolean
}) {
  if (!view) return null
  // The selects show what will actually print: the request when the cashier made one, otherwise
  // whatever the store or the company template resolved to.
  const layout = String(choice.layout || view.layout)
  const paper = String(choice.paper_width_mm ?? view.paper_width_mm)
  const destination = String(choice.destination || view.destination)
  return (
    <div style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8, padding: 12, marginBottom: 12, textAlign: 'left' }}>
      <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 6 }}>Print this receipt as:</div>
      {/* WHERE IT GOES (§POS-04). Both routes are offered on every receipt — the dialog every
          store has always had, and the printer itself where the counter runs the helper. The store's
          own setting only decides which one is selected first. */}
      {view.destination_options.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <div style={labelStyle}>Send to</div>
          <select value={destination} disabled={disabled} style={selectStyle}
            onChange={e => onChange({ ...choice, destination: e.target.value })}>
            {view.destination_options.map(o => (
              <option key={String(o.value)} value={String(o.value)}>{o.label}</option>
            ))}
          </select>
        </div>
      )}
      <div style={rowStyle}>
        <div style={{ flex: 2, minWidth: 0 }}>
          <div style={labelStyle}>Format</div>
          <select value={layout} disabled={disabled} style={selectStyle}
            onChange={e => onChange({ ...choice, layout: e.target.value })}>
            {view.layout_options.map(o => (
              <option key={String(o.value)} value={String(o.value)}>{o.label}</option>
            ))}
          </select>
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={labelStyle}>Paper</div>
          <select value={paper} disabled={disabled} style={selectStyle}
            onChange={e => onChange({ ...choice, paper_width_mm: e.target.value })}>
            {view.paper_options.map(o => (
              <option key={String(o.value)} value={String(o.value)}>{o.label}</option>
            ))}
          </select>
        </div>
      </div>
      {/* The store's own words for its printer, so staff can see what the slip is sized for. The
          driver itself is Windows' (or the Mac's) — the browser prints through it. */}
      <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6, lineHeight: 1.5 }}>
        {destination === 'direct'
          ? (view.printer_label
            ? `It goes straight to ${view.printer_label} and cuts, with no dialog.`
            : 'It goes straight to this counter\u2019s receipt printer and cuts, with no dialog.')
          : view.printer_label
            ? `This store prints on ${view.printer_label}. Choose the printer itself in the print dialog.`
            : 'No receipt printer is recorded for this store yet — add one in POS → Settings → Receipt Content & Printing so staff know which it is.'}
      </div>
      {view.layout_note && (
        <div style={{ fontSize: 11, color: 'var(--amber)', marginTop: 4, lineHeight: 1.5 }}>{view.layout_note}</div>
      )}
    </div>
  )
}
