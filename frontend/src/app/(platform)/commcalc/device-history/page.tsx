'use client'
// Device History Lookup — admin/employee page surface (commission-16). The same self-contained
// <DeviceHistoryLookup> component is intended to also mount as an employee-portal widget (see the
// mod-people coordination note in the commission handoff). Org-scoped, DISPLAY only.
import DeviceHistoryLookup from './DeviceHistoryLookup'

export default function DeviceHistoryPage() {
  return (
    <div style={{ maxWidth: 640, margin: '0 auto', padding: '4px 0 32px' }}>
      <h1 style={{ fontSize: 20, fontWeight: 800, margin: '0 0 4px' }}>Device History Lookup</h1>
      <div style={{ fontSize: 13, color: 'var(--text3)', marginBottom: 16 }}>
        Look up any customer device by IMEI or phone number — see whether we sold it, when it activated,
        how long it&apos;s been active, and (for admins) the commission &amp; rebate we&apos;ve received on it.
      </div>
      <DeviceHistoryLookup />
    </div>
  )
}
