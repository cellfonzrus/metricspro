'use client'
// Device History Lookup — admin/employee page surface (commission-16). The same self-contained
// <DeviceHistoryLookup> component is intended to also mount as an employee-portal widget (see the
// mod-people coordination note in the commission handoff). Org-scoped, DISPLAY only.
import { useAuth } from '@/lib/auth-context'
import { hasDataGrant } from '@/lib/rbac'
import DeviceHistoryLookup from './DeviceHistoryLookup'

export default function DeviceHistoryPage() {
  // The month-wide IMEI Rebate Reconciliation report has NO DEFAULT ACCESS (owner directive
  // 2026-07-29). Only advertise it to someone who can actually open it — a cross-link into a lock
  // note is a dead end. The page's own gate is the enforcement; this is just discovery hygiene.
  const { permissions } = useAuth()
  const canImeiRebates = hasDataGrant(permissions, 'imei_rebates')
  return (
    <div style={{ maxWidth: 640, margin: '0 auto', padding: '4px 0 32px' }}>
      <h1 style={{ fontSize: 20, fontWeight: 800, margin: '0 0 4px' }}>Device History Lookup</h1>
      <div style={{ fontSize: 13, color: 'var(--text3)', marginBottom: 16 }}>
        Look up any customer device by IMEI or phone number — see whether we sold it, when it activated,
        how long it&apos;s been active, and (for admins) the commission &amp; rebate we&apos;ve received on it.
        {canImeiRebates && <>
          {' '}Looking for a whole month at once? <a href="/commcalc/imei-rebates" style={{ color: 'var(--accent, #2563eb)' }}>
          IMEI Rebate Reconciliation</a> lists every IMEI activated in a period and the rebate against it —
          including the ones with none. (It reads the same activation sources as this lookup.)
        </>}
      </div>
      <DeviceHistoryLookup />
    </div>
  )
}
