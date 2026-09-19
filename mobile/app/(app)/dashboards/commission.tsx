import React, { useMemo, useState } from 'react'
import { RefreshControl, ScrollView, StyleSheet } from 'react-native'
import { useQuery } from '@tanstack/react-query'

import { useAuth } from '@/auth/AuthContext'
import { atLeast, scopeOf } from '@/lib/scope'
import { getCommissions, getFilterOptions, type CommissionRow } from '@/api/reports'
import { money, count } from '@/lib/format'
import { currentPeriod } from '@/lib/period'
import { Screen, Loading, ErrorView, EmptyState, Body } from '@/components/ui'
import { BarRow, Kpi, KpiGrid, PeriodSwitcher, SectionTitle } from '@/components/dash'
import { FilterBar, defaultFilter, type FilterState } from '@/components/FilterBar'
import { colors, spacing } from '@/theme'

const repLabel = (r: CommissionRow) => r.rep || r.name || r.employee_id || '—'

// Commission payouts — incentive payout by rep and the company total. The endpoint is month-only, so
// the month stepper picks the period; Market / Rep filtering is applied CLIENT-SIDE (each row carries
// a server-resolved `market`) and the totals recompute from the visible rows.
export default function CommissionScreen() {
  const { me } = useAuth()
  const allowed = atLeast(scopeOf(me), 'market')
  const [period, setPeriod] = useState(currentPeriod())
  const [filter, setFilter] = useState<FilterState>(() => defaultFilter('month'))

  const opts = useQuery({ queryKey: ['filter-options'], queryFn: getFilterOptions, enabled: allowed })
  const q = useQuery({ queryKey: ['dash', 'commissions', period], queryFn: () => getCommissions(period), enabled: allowed })

  const { rows, totalPayout } = useMemo(() => {
    const all = (q.data || []).filter((r) =>
      (filter.markets.length === 0 || filter.markets.includes(String((r as CommissionRow).market ?? ''))) &&
      (filter.reps.length === 0 || filter.reps.includes(String(repLabel(r)))))
      .slice().sort((a, b) => (Number(b.total_payout) || 0) - (Number(a.total_payout) || 0))
    return { rows: all, totalPayout: all.reduce((s, r) => s + (Number(r.total_payout) || 0), 0) }
  }, [q.data, filter])

  if (!allowed) {
    return <Screen><EmptyState title="No access" subtitle="Commission payouts need company-wide reporting access." /></Screen>
  }

  const top = rows.slice(0, 12)
  const maxPay = Math.max(1, ...top.map((r) => Number(r.total_payout) || 0))

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={styles.c}
        refreshControl={<RefreshControl refreshing={q.isFetching} onRefresh={q.refetch} tintColor={colors.primary} />}
      >
        <PeriodSwitcher period={period} onChange={setPeriod} />
        <FilterBar value={filter} onChange={setFilter} options={opts.data ?? null}
          show={{ date: false, markets: true, reps: true }} />

        {q.isLoading ? (
          <Loading label="Loading commissions…" />
        ) : q.isError ? (
          <ErrorView message={(q.error as Error)?.message || 'Could not load commissions.'} onRetry={q.refetch} />
        ) : (
          <>
            <KpiGrid>
              <Kpi label="Total payout" value={money(totalPayout)} tone="good" />
              <Kpi label="Reps paid" value={count(rows.length)} tone="info" />
            </KpiGrid>

            <SectionTitle>Top reps · payout</SectionTitle>
            {top.length === 0 ? <Body dim>No commission rows for this filter.</Body> :
              top.map((r, i) => {
                const met = Number(r.kpis_met)
                const tot = Number(r.total_kpis)
                const kpi = Number.isFinite(met) && Number.isFinite(tot) ? `${met}/${tot} KPIs` : undefined
                return (
                  <BarRow key={i} label={repLabel(r)} value={Number(r.total_payout) || 0}
                    valueText={money(r.total_payout)} max={maxPay} tone="good" right={kpi} />
                )
              })}
            {rows.length > top.length ? <Body dim>Showing top {top.length} of {rows.length} reps.</Body> : null}
          </>
        )}
      </ScrollView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  c: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xxl },
})
