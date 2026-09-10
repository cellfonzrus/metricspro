import React, { useState } from 'react'
import { RefreshControl, ScrollView, StyleSheet } from 'react-native'
import { useQuery } from '@tanstack/react-query'

import { useAuth } from '@/auth/AuthContext'
import { atLeast, scopeOf } from '@/lib/scope'
import { getCommissions, type CommissionRow } from '@/api/reports'
import { money, count } from '@/lib/format'
import { currentPeriod } from '@/lib/period'
import { Screen, Loading, ErrorView, EmptyState, Body } from '@/components/ui'
import { BarRow, Kpi, KpiGrid, PeriodSwitcher, SectionTitle } from '@/components/dash'
import { colors, spacing } from '@/theme'

const repLabel = (r: CommissionRow) => r.rep || r.name || r.employee_id || '—'

// Commission payouts — incentive payout by rep and the company total. commcalc/commissions returns a
// bare list of per-rep rows (no totals object), so we sum total_payout for the headline.
export default function CommissionScreen() {
  const { me } = useAuth()
  const [period, setPeriod] = useState(currentPeriod())
  const q = useQuery({ queryKey: ['dash', 'commissions', period], queryFn: () => getCommissions(period),
    enabled: atLeast(scopeOf(me), 'market') })

  if (!atLeast(scopeOf(me), 'market')) {
    return <Screen><EmptyState title="No access" subtitle="Commission payouts need company-wide reporting access." /></Screen>
  }

  const rows = (q.data || []).slice().sort((a, b) => (Number(b.total_payout) || 0) - (Number(a.total_payout) || 0))
  const totalPayout = rows.reduce((s, r) => s + (Number(r.total_payout) || 0), 0)
  const top = rows.slice(0, 12)
  const maxPay = Math.max(1, ...top.map((r) => Number(r.total_payout) || 0))

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={styles.c}
        refreshControl={<RefreshControl refreshing={q.isFetching} onRefresh={q.refetch} tintColor={colors.primary} />}
      >
        <PeriodSwitcher period={period} onChange={setPeriod} />
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
            {top.length === 0 ? <Body dim>No commission rows for this period.</Body> :
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
