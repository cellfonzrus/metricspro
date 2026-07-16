'use client'

// Shared empty-state for the VIP/Boost-financing-specific asset reports (Charges Dashboard, Inventory
// Aging, RMA, charge-group drill-downs). These pages summarize commcalc.asset_ledger — populated ONLY
// by uploading a Distributor Asset_Lending.xlsx (the Boost/VIP device-financing consignment program).
// A tenant not on that program (e.g. a Total-only dealer) has a genuinely EMPTY ledger, and the
// underlying endpoints correctly return a well-formed, all-zero summary (never a 500) — but rendering
// that as "$0.00 Total Loss" / "$0.00 Net Loss" headline tiles reads as "you're doing great" rather
// than "this program doesn't apply to your account", which is misleading (luxelink-parity audit,
// 2026-07-16). This is a DATA-DRIVEN signal (does this org's asset_ledger have any rows at all?), not
// a tenant-name check — any tenant, house org included, sees this exact card before their first
// Asset_Lending.xlsx upload.
//
// `hasLedgerData` should be derived from the already-fetched GET /asset/filter-options response
// (`stores.length > 0`) — every one of these pages already calls that endpoint on mount, so this is a
// zero-extra-request check.
export function NoLedgerData({ title }: { title: string }) {
  return (
    <div className="card" style={{ padding: '36px 28px', textAlign: 'center' }}>
      <div style={{ fontSize: 34, marginBottom: 10 }}>📭</div>
      <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 8 }}>
        No Distributor asset-lending data on this account
      </div>
      <div style={{ color: 'var(--text2)', fontSize: 14, maxWidth: 540, margin: '0 auto 18px', lineHeight: 1.5 }}>
        {title} summarizes the VIP/Boost device-financing (asset-lending) ledger — populated by
        uploading a Distributor <code>Asset_Lending.xlsx</code> export. This account has no such
        ledger loaded, so there is nothing to reconcile here. That&apos;s expected if this account
        doesn&apos;t run that consignment program — not an error.
      </div>
      <div style={{ display: 'flex', gap: 10, justifyContent: 'center', flexWrap: 'wrap' }}>
        <a className="btn" href="/commcalc/asset">← Asset Ledger</a>
        <a className="btn btn-primary" href="/commcalc/asset/marketplace-purchases">🛒 Marketplace Purchases</a>
      </div>
    </div>
  )
}
