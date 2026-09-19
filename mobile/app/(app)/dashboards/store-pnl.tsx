import React, { useState } from 'react'
import { RefreshControl, ScrollView, StyleSheet } from 'react-native'
import { useQuery } from '@tanstack/react-query'

import { useAuth } from '@/auth/AuthContext'
import { atLeast, scopeOf } from '@/lib/scope'
import { getGpReport, getFilterOptions, type GpStoreRow } from '@/api/reports'
import { money, pct } from '@/lib/format'
import { currentPeriod } from '@/lib/period'
import { Screen, Loading, ErrorView, EmptyState, Body } from '@/components/ui'
import { BarRow, Kpi, KpiGrid, PeriodSwitcher, SectionTitle } from '@/components/dash'
import { FilterBar, defaultFilter, type FilterState } from '@/components/FilterBar'
import { colors, spacing } from '@/theme'

const storeLabel = (r: GpStoreRow) => r.store_name || r.store || r.store_code || '—'
const attainPct = (v: unknown) => {
  const n = Number(v)
  if (!Number.isFinite(n)) return undefined
  return Math.abs(n) <= 3 ? n * 100 : n
}

// Store P&L — net profit and target attainment, store by store (commcalc/gp). Month-only, so the
// month stepper picks the period; the endpoint filters by a SINGLE market server-side (store scoping
// is by the caller's RBAC span).
export default function StorePnlScreen() {
  const { me } = useAuth()
  const allowed = atLeast(scopeOf(me), 'market')
  const [period, setPeriod] = useState(currentPeriod())
  const [filter, setFilter] = useState<FilterState>(() => defaultFilter('month'))
  const market = filter.markets[0] || ''

  const opts = useQuery({ queryKey: ['filter-options'], queryFn: getFilterOptions, enabled: allowed })
  const q = useQuery({ queryKey: ['dash', 'gp', period, market], queryFn: () => getGpReport(period, market), enabled: allowed })

  if (!allowed) {
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
        <FilterBar value={filter} onChange={setFilter} options={opts.data ?? null}
          show={{ date: false, markets: true }} />

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
            {rows.length === 0 ? <Body dim>No store rows for this filter.</Body> :
              rows.map((r, i) => {
                const att = attainPct(r.net_profit_attainment)
                return (
                  <BarRow key={i} label={storeLabel(r)} value={Number(r.net_profit) || 0}
                    valueText={money(r.net_profit)} max={maxNp}
                    tone={(Number(r.net_profit) || 0) >= 0 ? 'good' : 'bad'}
                    right={att != null ? `${pct(att)} of target` : undefined} />
                )
              })}
            {filter.markets.length > 1 ? <Body dim>Showing market “{market}” — this report filters one market at a time.</Body> : null}
          </>
        )}
      </ScrollView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  c: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xxl },
})
