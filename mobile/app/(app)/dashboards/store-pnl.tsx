import React, { useState } from 'react'
import { RefreshControl, ScrollView, StyleSheet } from 'react-native'
import { useQuery } from '@tanstack/react-query'

import { useAuth } from '@/auth/AuthContext'
import { atLeast, scopeOf } from '@/lib/scope'
import { getGpReport, type GpStoreRow } from '@/api/reports'
import { money, pct } from '@/lib/format'
import { currentPeriod } from '@/lib/period'
import { Screen, Loading, ErrorView, EmptyState, Body } from '@/components/ui'
import { BarRow, Kpi, KpiGrid, PeriodSwitcher, SectionTitle } from '@/components/dash'
import { colors, spacing } from '@/theme'

const storeLabel = (r: GpStoreRow) => r.store_name || r.store || r.store_code || '—'
// attainment may arrive as a ratio (0.85) or a percent (85). Normalise small magnitudes to percent.
const attainPct = (v: unknown) => {
  const n = Number(v)
  if (!Number.isFinite(n)) return undefined
  return Math.abs(n) <= 3 ? n * 100 : n
}

// Store P&L — net profit and target attainment, store by store (commcalc/gp). `totals` carries the
// company roll-up; each store row has net_profit + net_profit_attainment.
export default function StorePnlScreen() {
  const { me } = useAuth()
  const [period, setPeriod] = useState(currentPeriod())
  const q = useQuery({ queryKey: ['dash', 'gp', period], queryFn: () => getGpReport(period),
    enabled: atLeast(scopeOf(me), 'market') })

  if (!atLeast(scopeOf(me), 'market')) {
    return <Screen><EmptyState title="No access" subtitle="Store P&L needs company-wide reporting access." /></Screen>
  }

  const t = q.data?.totals || {}
  const rows = (q.data?.store_rows || []).slice()
    .sort((a, b) => (Number(b.net_profit) || 0) - (Number(a.net_profit) || 0))
  const maxNp = Math.max(1, ...rows.map((r) => Number(r.net_profit) || 0))

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={styles.c}
        refreshControl={<RefreshControl refreshing={q.isFetching} onRefresh={q.refetch} tintColor={colors.primary} />}
      >
        <PeriodSwitcher period={period} onChange={setPeriod} />
        {q.isLoading ? (
          <Loading label="Loading store P&L…" />
        ) : q.isError ? (
          <ErrorView message={(q.error as Error)?.message || 'Could not load store P&L.'} onRetry={q.refetch} />
        ) : (
          <>
            <KpiGrid>
              <Kpi label="Total revenue" value={money(t.total_rev)} tone="info" />
              <Kpi label="Net profit" value={money(t.net_profit)} tone={(Number(t.net_profit) || 0) >= 0 ? 'good' : 'bad'} />
              <Kpi label="Commission" value={money(t.comm)} />
              <Kpi label="Rep pay" value={money(t.rep_pay)} />
            </KpiGrid>

            <SectionTitle>Net profit by store</SectionTitle>
            {rows.length === 0 ? <Body dim>No store rows this period.</Body> :
              rows.map((r, i) => {
                const att = attainPct(r.net_profit_attainment)
                return (
                  <BarRow key={i} label={storeLabel(r)} value={Number(r.net_profit) || 0}
                    valueText={money(r.net_profit)} max={maxNp}
                    tone={(Number(r.net_profit) || 0) >= 0 ? 'good' : 'bad'}
                    right={att != null ? `${pct(att)} of target` : undefined} />
                )
              })}
          </>
        )}
      </ScrollView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  c: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xxl },
})
