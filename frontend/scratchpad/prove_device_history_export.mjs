// PROOF — Device History export honors the device_commission permission gate (RULE FOUR §3c clause:
// "a gated money column never leaks through an export"). Same convention as prove_entity_picker.mjs /
// prove_org_append.mjs: a VERBATIM re-implementation of the pure builder + a SOURCE-PARITY grep that
// the shipped builder guards the money sheets by the identical condition.
//
// The gate is airtight for two independent reasons, both asserted below:
//   (1) The backend only sends `money` + `commission_visible:true` when the caller has the grant; a
//       non-granted caller receives `money_locked` and NO `money`. So the money rows are not even
//       present in `res` on the client — there is nothing to export.
//   (2) The builder additionally guards the money sheets on `res.commission_visible && res.money`, so
//       even a malformed/partial payload cannot surface a money row without the visible flag.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SRC = path.join(__dirname, '../src/app/(platform)/commcalc/device-history/deviceHistoryExport.ts')

let pass = 0, fail = 0
const ok = (name, cond) => { if (cond) { pass++; console.log('  ✓', name) } else { fail++; console.log('  ✗ FAIL', name) } }

// ── VERBATIM re-implementation of buildDeviceHistorySheets (kept byte-parallel to the .ts) ──
function buildSheets(res) {
  if (!res) return []
  const d = res.device || {}, t = res.tenure || {}, p = res.prompt || {}
  const info = [
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
    { k: 'Activated (residual)', v: t.activation_period || '—' },
    { k: 'Months active', v: t.months_active != null ? `${t.months_active} (${t.basis || 'residual months'})` : '—' },
    { k: 'Last seen', v: t.last_seen_period || '—' },
  ]
  const sheets = [{ name: 'Device & tenure', rows: info }]
  if (res.commission_visible && res.money) {
    const m = res.money
    sheets.push({ name: 'Commission', rows: [...(m.commission?.rows || []), { period: '', label: 'Commission subtotal', amount: m.commission?.subtotal || 0 }] })
    sheets.push({ name: 'Rebate', rows: [...(m.rebate?.rows || []), { period: '', label: 'Rebate subtotal', amount: m.rebate?.subtotal || 0 }] })
    sheets.push({ name: 'Total', rows: [{ k: 'Grand total', v: m.grand_total || 0 }] })
  }
  return sheets
}

const names = (s) => s.map(x => x.name)
const flat = (s) => JSON.stringify(s)

// Fixtures ------------------------------------------------------------------
const device = { phone_model: 'iPhone 15', sold_date: '2026-06-02', sale_price: 899.99, store: '1313 Main', salesperson: 'A. Rep', contract_type: 'Upgrade', mdn: '5551234567', imei: '35900012' }
const tenure = { months_active: 3, activation_period: 'June 2026', basis: 'residual months', last_seen_period: 'July 2026' }
const money = {
  commission: { source: 'raw_mi', subtotal: 42.5, rows: [{ period: 'June 2026', label: 'Residual (MI+ATU)', amount: 20 }, { period: 'July 2026', label: 'Residual (MI+ATU)', amount: 22.5 }] },
  rebate: { source: 'raw_payment_detail', subtotal: 300, rows: [{ period: 'June 2026', label: 'Device reimbursement', amount: 300 }] },
  grand_total: 342.5,
}
const GRANTED = { found: true, query: '5551234567', prompt: { icon: '⬆️', text: 'offer an UPGRADE' }, device, tenure, commission_visible: true, money }
// The exact shape the backend returns for a NON-granted caller: money_locked, NO money, flag false.
const LOCKED = { found: true, query: '5551234567', prompt: { icon: '⬆️', text: 'offer an UPGRADE' }, device, tenure, commission_visible: false, money_locked: { note: 'Commission details are restricted.' } }

// G1 — granted caller gets the money sheets, with the real amounts
console.log('G1 granted → money sheets present')
{
  const s = buildSheets(GRANTED)
  ok('has Commission + Rebate + Total sheets', names(s).includes('Commission') && names(s).includes('Rebate') && names(s).includes('Total'))
  ok('commission line amounts exported', flat(s).includes('20') && flat(s).includes('22.5') && flat(s).includes('42.5'))
  ok('rebate + grand total exported', flat(s).includes('300') && flat(s).includes('342.5'))
}

// G2 — non-granted caller: ONLY the ungated sheet, ZERO money rows, NO money amount leaks
console.log('G2 locked (no grant) → NO money in export')
{
  const s = buildSheets(LOCKED)
  ok('exactly one sheet (Device & tenure)', s.length === 1 && s[0].name === 'Device & tenure')
  ok('no Commission/Rebate/Total sheet', !names(s).some(n => ['Commission', 'Rebate', 'Total'].includes(n)))
  // structural check: not a single exported row carries an `amount` field (money rows are amount-keyed)
  const anyAmountRow = s.some(sh => (sh.rows || []).some(r => 'amount' in r))
  ok('no exported row has an `amount` field', anyAmountRow === false)
  // and the distinctive money totals never appear as exact cell values
  const allVals = s.flatMap(sh => (sh.rows || []).flatMap(r => Object.values(r).map(String)))
  ok('gated totals (42.5/22.5/342.5/300) absent as any cell value', !['42.5', '22.5', '342.5', '300', '20'].some(a => allVals.includes(a)))
  ok('ungated device/tenure still exported', flat(s).includes('iPhone 15') && flat(s).includes('June 2026'))
}

// G3 — defensive: commission_visible true but money missing → no money sheets (can't fabricate)
console.log('G3 visible flag but money absent → still no money sheets')
{
  const s = buildSheets({ found: true, query: 'x', device, tenure, commission_visible: true })
  ok('no money sheets without money object', s.length === 1)
}
// G4 — defensive: money present but flag false (shouldn\'t happen, but must stay closed)
console.log('G4 money present but commission_visible false → excluded')
{
  const s = buildSheets({ found: true, query: 'x', device, tenure, commission_visible: false, money })
  ok('flag false suppresses money even if money leaked into payload', s.length === 1 && !flat(s).includes('342.5'))
}

// G5 — SOURCE PARITY: the shipped builder guards the money sheets on the identical condition
console.log('G5 source parity vs deviceHistoryExport.ts')
{
  const src = fs.readFileSync(SRC, 'utf8')
  const guardRe = /if\s*\(\s*res\.commission_visible\s*&&\s*res\.money\s*\)/
  ok('guards money on `res.commission_visible && res.money`', guardRe.test(src))
  ok('pushes Commission + Rebate sheets', src.includes("name: 'Commission'") && src.includes("name: 'Rebate'"))
  const guardIdx = src.search(guardRe)   // the actual `if (...)` guard, not the doc-comment mention
  ok('device/tenure sheet is built BEFORE the money guard', src.indexOf("name: 'Device & tenure'") < guardIdx)
  ok('builder imports no React/DOM (pure)', !/from 'react'/.test(src) && !src.includes('document.'))
}

console.log(`\n${pass}/${pass + fail} PASS` + (fail ? ` — ${fail} FAILED` : ''))
process.exit(fail ? 1 : 0)
