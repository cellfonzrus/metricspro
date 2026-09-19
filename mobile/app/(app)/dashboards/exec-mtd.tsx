import React, { useState } from 'react'
import { RefreshControl, ScrollView, StyleSheet } from 'react-native'
import { useQuery } from '@tanstack/react-query'

import { useAuth } from '@/auth/AuthContext'
import { atLeast, scopeOf } from '@/lib/scope'
import { getExecMtd, getFilterOptions, type ExecRow } from '@/api/reports'
import { money, count } from '@/lib/format'
import { monthOf } from '@/lib/daterange'
import { Screen, Loading, ErrorView, EmptyState, Body } from '@/components/ui'
import { BarRow, Kpi, KpiGrid, SectionTitle } from '@/components/dash'
import { FilterBar, defaultFilter, type FilterState } from '@/components/FilterBar'
import { colors, spacing } from '@/theme'

const rowLabel = (r: ExecRow) => r.label || r.employee || r.store || r.name || '—'

// Executive MTD — activations and the full sales mix, by store and by rep. Filters (store / market /
// rep) and the date range are applied SERVER-SIDE by the exec-mtd endpoint; the period is the month
// the range ends in. Defaults to month-to-date.
export default function ExecMtdScreen() {
  const { me } = useAuth()
  const allowed = atLeast(scopeOf(me), 'market')
  const [filter, setFilter] = useState<FilterState>(() => defaultFilter('month'))
  const period = monthOf(filter.range.to)

  const opts = useQuery({ queryKey: ['filter-options'], queryFn: getFilterOptions, enabled: allowed })
  const q = useQuery({
    queryKey: ['dash', 'exec-mtd', period, filter.range, filter.stores, filter.markets, filter.reps],
    queryFn: () => getExecMtd(period, {
      dateFrom: filter.range.from, dateTo: filter.range.to,
      stores: filter.stores, markets: filter.markets, reps: filter.reps,
    }),
    enabled: allowed,
  })

  if (!allowed) {
    return <Screen><EmptyState title="No access" subtitle="Executive MTD needs company-wide reporting access." /></Screen>
  }

  const total = q.data?.by_location?.total || {}
  const stores = (q.data?.by_location?.rows || []).slice()
    .sort((a, b) => (Number(b.total_activation) || 0) - (Number(a.total_activation) || 0)).slice(0, 8)
  const reps = (q.data?.by_employee?.rows || []).slice()
    .sort((a, b) => (Number(b.total_activation) || 0) - (Number(a.total_activation) || 0)).slice(0, 8)
  const maxStore = Math.max(1, ...stores.map((r) => Number(r.total_activation) || 0))
  const maxRep = Math.max(1, ...reps.map((r) => Number(r.total_activation) || 0))

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={styles.c}
        refreshControl={<RefreshControl refreshing={q.isFetching} onRefresh={q.refetch} tintColor={colors.primary} />}
      >
        <FilterBar value={filter} onChange={setFilter} options={opts.data ?? null}
          show={{ date: true, stores: true, markets: true, reps: true }} />

        {q.isLoading ? (
          <Loading label="Loading Executive MTD…" />
        ) : q.isError ? (
          <ErrorView message={(q.error as Error)?.message || 'Could not load Executive MTD.'} onRetry={q.refetch} />
        ) : (
          <>
            <KpiGrid>
              <Kpi label="Activations" value={count(total.total_activation)} tone="good" />
              <Kpi label="Phones" value={count(total.total_phones)} />
              <Kpi label="Sales" value={money(total.amount)} tone="info" />
              <Kpi label="Accessories" value={money(total.acc_sales)} />
            </KpiGrid>

            <SectionTitle>Top stores · activations</SectionTitle>
            {stores.length === 0 ? <Body dim>No store activity for this filter.</Body> :
              stores.map((r, i) => (
                <BarRow key={i} label={rowLabel(r)} value={Number(r.total_activation) || 0}
                  valueText={count(r.total_activation)} max={maxStore} tone="info" right={money(r.amount)} />
              ))}

            <SectionTitle>Top reps · activations</SectionTitle>
            {reps.length === 0 ? <Body dim>No rep activity for this filter.</Body> :
              reps.map((r, i) => (
                <BarRow key={i} label={rowLabel(r)} value={Number(r.total_activation) || 0}
                  valueText={count(r.total_activation)} max={maxRep} tone="good" />
              ))}
          </>
        )}
      </ScrollView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  c: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xxl },
})
