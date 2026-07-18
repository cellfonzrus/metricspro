// Pure export-payload builder for the Device History Lookup (RULE FOUR §3c).
// Kept React-free + side-effect-free so it is unit-provable (see
// frontend/scratchpad/prove_device_history_export.mjs).
//
// GATED-COLUMN GUARANTEE (the clause the task asks us to prove): the per-period COMMISSION and
// REBATE money rows are added to the export ONLY when `res.commission_visible && res.money` — the
// exact same condition the on-screen money table renders under. The backend is the source of truth:
// when the caller lacks the `device_commission` grant it returns `money_locked` and NO `money`
// object, so those rows are never even present in `res` on the client. The export therefore cannot
// leak a gated money row — there is literally nothing to export. A granted caller gets the money
// sheets; a non-granted caller gets only the ungated device/sale/tenure sheet.
import { type ExportColumn } from '@/lib/export'

export type ExportSheet = { name: string; columns: ExportColumn[]; rows: any[] }
export type DeviceHistoryExport = { title: string; filename: string; sheets: ExportSheet[] }

const kv: ExportColumn[] = [
  { header: 'Field', get: (r: any) => r.k },
  { header: 'Value', get: (r: any) => r.v },
]
const moneyCols: ExportColumn[] = [
  { header: 'Period', get: (r: any) => r.period || '—' },
  { header: 'Detail', get: (r: any) => r.label || '' },
  { header: 'Amount', money: true, get: (r: any) => r.amount },
]

// Turn a lookup response into the export sheets. `res` is the /commcalc/device-history payload.
export function buildDeviceHistorySheets(res: any): ExportSheet[] {
  if (!res) return []
  const d = res.device || {}
  const t = res.tenure || {}
  const p = res.prompt || {}
  const pp = res.purchase_price || {}
  const ag = res.aging || {}
  const bill = ag.billing || {}

  // ── Sheet 1: ungated device / sale / tenure + aging & purchase summary (always) ──
  // Aging + purchase price are UNGATED (owner directive) so they ride in the always-exported sheet.
  const info: { k: string; v: any }[] = [
    { k: 'Query', v: res.query ?? '' },
    { k: 'Prompt', v: [p.icon, p.text].filter(Boolean).join(' ') },
    { k: 'Sold by us', v: d && (d.phone_model || d.mdn || d.imei) ? 'Yes' : 'No' },
    { k: 'Phone model', v: d.phone_model || '—' },
    { k: 'Date sold', v: d.sold_date || '—' },
    { k: 'Sale price', v: d.sale_price != null ? d.sale_price : '' },
    { k: 'Store', v: d.store || '—' },
    { k: 'Sold by', v: d.salesperson || '—' },
    { k: 'Contract', v: d.contract_type || '—' },
    { k: 'MDN / IMEI', v: [d.mdn, d.imei].filter(Boolean).join(' · ') || '—' },
    { k: 'Our purchase price', v: pp.found ? pp.amount : '—' },
    { k: 'Purchase price source', v: pp.found ? `${pp.label} · ${pp.source}` : (pp.provenance || '—') },
    { k: 'Acquired (inventory)', v: ag.acquired_date || '—' },
    { k: 'Inventory store', v: ag.store || '—' },
    { k: ag.is_sold ? 'Days on inventory (at sale)' : 'Current age (unsold)', v: ag.days_on_inventory != null ? ag.days_on_inventory : '—' },
    { k: 'Aging bucket', v: ag.bucket ? `${ag.bucket.label} (${ag.bucket.range})` : (ag.found ? '—' : 'no inventory record') },
    { k: 'PayGo date', v: bill.payg_date || '—' },
    { k: 'Billing Friday', v: bill.billing_friday || '—' },
    { k: 'Activated (residual)', v: t.activation_period || '—' },
    { k: 'Months active', v: t.months_active != null ? `${t.months_active} (${t.basis || 'residual months'})` : '—' },
    { k: 'Last seen', v: t.last_seen_period || '—' },
  ]
  const sheets: ExportSheet[] = [{ name: 'Device & tenure', columns: kv, rows: info }]

  // ── Sheets 2+3: GATED money rows — present ONLY when the backend granted them ──
  if (res.commission_visible && res.money) {
    const m = res.money
    if (m.kind === 'ma') {
      // MA-fed (Total / VidaPay): one per-period row carrying every money component (paid-to-dealer).
      const maCols: ExportColumn[] = [
        { header: 'Period', get: (r: any) => r.period || '—' },
        { header: 'M1–M6 spiffs', money: true, get: (r: any) => r.spiff_total },
        { header: 'Rebate', money: true, get: (r: any) => r.rebate },
        { header: 'Equipment margin', money: true, get: (r: any) => r.margin_total },
        { header: 'Plan MRC (info)', money: true, get: (r: any) => r.mrc_net_discount },
        { header: 'Line status', get: (r: any) => r.line_status || '' },
      ]
      const maRows = [...(m.periods || []), {
        period: 'Subtotals', spiff_total: m.spiff?.subtotal || 0, rebate: m.rebate?.subtotal || 0,
        margin_total: m.margin?.subtotal || 0, mrc_net_discount: m.mrc?.subtotal || 0, line_status: '',
      }]
      sheets.push({ name: 'MA commission', columns: maCols, rows: maRows })
      sheets.push({
        name: 'Total', columns: kv,
        rows: [{ k: 'Grand total (paid to dealer)', v: m.grand_total || 0 }],
      })
    } else {
      const commissionRows = [...(m.commission?.rows || []),
        { period: '', label: 'Commission subtotal', amount: m.commission?.subtotal || 0 }]
      const rebateRows = [...(m.rebate?.rows || []),
        { period: '', label: 'Rebate subtotal', amount: m.rebate?.subtotal || 0 }]
      sheets.push({ name: 'Commission', columns: moneyCols, rows: commissionRows })
      sheets.push({ name: 'Rebate', columns: moneyCols, rows: rebateRows })
      sheets.push({
        name: 'Total', columns: kv,
        rows: [{ k: 'Grand total', v: m.grand_total || 0 }],
      })
    }
  }
  return sheets
}

export function buildDeviceHistoryExport(res: any): DeviceHistoryExport {
  const q = String(res?.query || 'lookup').replace(/[^\w]+/g, '_').toLowerCase()
  return {
    title: 'Device History',
    filename: `device_history_${q}`,
    sheets: buildDeviceHistorySheets(res),
  }
}
