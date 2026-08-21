'use client'
// POS module — POS Settings (ported from the standalone pos-system app's /settings
// page). Phase 1: config engine + Sales Tax + Receipt Template. Phase 2: Dealer
// Codes, Carrier Portals, and Service Plans (the plans CRUD is new — the standalone
// app had no UI for its empty service_plans catalog). Still skipped from the source:
// receipt sample upload (storage bucket not ported) and the client-side permission
// gate — RBAC is enforced server-side via the `pos_settings` permission and 403s
// surface inline in each section.
//
// The page owns the pos_settings rows so the config engine's inheritance badges and
// the Sales Tax rule (the `tax_applied_on` key) always read the same data.
import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import type { PosSettingRow } from '@/lib/pos-config'
import PosConfigSection, { type PosStore } from '@/components/pos/PosConfigSection'
import TaxCodesSection from '@/components/pos/TaxCodesSection'
import ReceiptTemplateSection from '@/components/pos/ReceiptTemplateSection'
import DealerCodesSection from '@/components/pos/DealerCodesSection'
import CarrierPortalsSection from '@/components/pos/CarrierPortalsSection'
import ServicePlansSection from '@/components/pos/ServicePlansSection'

export default function PosSettingsPage() {
  const [stores, setStores] = useState<PosStore[]>([])
  const [rows, setRows] = useState<PosSettingRow[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')

  // No store_code filter: the badges need every store's overrides, not just one scope's.
  const reloadSettings = useCallback(async () => {
    const r = await api('/api/v1/pos/settings')
    setRows((r.settings || []) as PosSettingRow[])
  }, [])

  useEffect(() => {
    (async () => {
      const problems: string[] = []
      try {
        const s = await apiCached('/api/v1/storeops/stores', LOOKUP)
        setStores(Array.isArray(s) ? (s as PosStore[]) : [])
      } catch (err: any) { problems.push(`stores: ${err?.message || err}`) }
      try {
        await reloadSettings()
      } catch (err: any) { problems.push(`POS settings: ${err?.message || err}`) }
      setLoadError(problems.length > 0 ? `Some settings failed to load — ${problems.join('; ')}` : '')
      setLoading(false)
    })()
  }, [reloadSettings])

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>⚙️ POS Settings</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Register rules, sales tax, and receipts — org-wide defaults with per-store overrides
        </p>
      </div>

      <PosConfigSection stores={stores} rows={rows} loading={loading} loadError={loadError} reload={reloadSettings} />
      <TaxCodesSection stores={stores} rows={rows} onSettingsChanged={reloadSettings} />
      <ReceiptTemplateSection />
      <DealerCodesSection stores={stores} />
      <CarrierPortalsSection />
      <ServicePlansSection />
    </div>
  )
}
