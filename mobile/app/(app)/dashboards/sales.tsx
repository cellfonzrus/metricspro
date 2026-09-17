import React, { useMemo, useState } from 'react'
import { RefreshControl, ScrollView, StyleSheet } from 'react-native'
import { useQuery } from '@tanstack/react-query'

import { useAuth } from '@/auth/AuthContext'
import { atLeast, scopeOf } from '@/lib/scope'
import { getSalesReport, getFilterOptions } from '@/api/reports'
import { money, count } from '@/lib/format'
import { monthOf, inRange } from '@/lib/daterange'
import { Screen, Loading, ErrorView, EmptyState, Body } from '@/components/ui'
import { Kpi, KpiGrid, SectionTitle, StatRow } from '@/components/dash'
import { FilterBar, defaultFilter, type FilterState } from '@/components/FilterBar'
import { colors, spacing } from '@/theme'

// Sales report — transactions, revenue and gross profit. The endpoint returns one row per
// (store, rep, day) for the month; the date range, store, market and rep filters are applied
// CLIENT-SIDE here and the KPI totals are recomputed from the filtered rows. Defaults to TODAY.
const NUM = ['txns', 'lines', 'activations', 'byod', 'upgrades', 'swaps', 'accessory_rev', 'revenue', 'gp'] as const
type SalesRow = Record<string, unknown>

export default function SalesScreen() {
  const { me } = useAuth()
  const allowed = atLeast(scopeOf(me), 'market')
  const [filter, setFilter] = useState<FilterState>(() => defaultFilter('today'))
  const period = monthOf(filter.range.to)

  const opts = useQuery({ queryKey: ['filter-options'], queryFn: getFilterOptions, enabled: allowed })
  const q = useQuery({ queryKey: ['dash', 'sales', period], queryFn: () => getSalesReport(period), enabled: allowed })

  // client-side filter + recompute totals from the visible rows
  const { totals, shown } = useMemo(() => {
    const rows = (q.data?.rows || []) as SalesRow[]
    const f = rows.filter((r) =>
      inRange(r.trans_date, filter.range) &&
      (filter.stores.length === 0 || filter.stores.includes(String(r.store))) &&
      (filter.markets.length === 0 || filter.markets.includes(String(r.market))) &&
      (filter.reps.length === 0 || filter.reps.includes(String(r.salesperson))))
    const t: Record<string, number> = {}
    for (const k of NUM) t[k] = f.reduce((s, r) => s + (Number(r[k]) || 0), 0)
    return { totals: t, shown: f.length }
  }, [q.data, filter])

  if (!allowed) {
    return <Screen><EmptyState title="No access" subtitle="The sales report needs company-wide reporting access." /></Screen>
  }

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={styles.c}
        refreshControl={<RefreshControl refreshing={q.isFetching} onRefresh={q.refetch} tintColor={colors.primary} />}
      >
        <FilterBar value={filter} onChange={setFilter} options={opts.data ?? null}
          show={{ date: true, stores: true, markets: true, reps: true }} />

        {q.isLoading ? (
          <Loading label="Loading sales…" />
        ) : q.isError ? (
          <ErrorView message={(q.error as Error)?.message || 'Could not load the sales report.'} onRetry={q.refetch} />
        ) : (
          <>
            <KpiGrid>
              <Kpi label="Revenue" value={money(totals.revenue)} tone="info" />
              <Kpi label="Gross profit" value={money(totals.gp)} tone="good" />
              <Kpi label="Transactions" value={count(totals.txns)} />
              <Kpi label="Activations" value={count(totals.activations)} />
            </KpiGrid>

            <SectionTitle>Mix</SectionTitle>
            <StatRow label="Line items" value={count(totals.lines)} />
            <StatRow label="BYOD" value={count(totals.byod)} />
            <StatRow label="Upgrades" value={count(totals.upgrades)} />
            <StatRow label="Swaps" value={count(totals.swaps)} />
            <StatRow label="Accessory revenue" value={money(totals.accessory_rev)} />
            <StatRow label="Revenue" value={money(totals.revenue)} strong tone="info" />
            <StatRow label="Gross profit" value={money(totals.gp)} strong tone="good" />

            <Body dim>
              {shown === 0
                ? 'No sales match this filter. Try a wider date range or clear a filter.'
                : `${count(shown)} store/rep/day rows in this view.`}
            </Body>
          </>
        )}
      </ScrollView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  c: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xxl },
})
