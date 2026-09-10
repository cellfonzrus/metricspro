import React, { useState } from 'react'
import { RefreshControl, ScrollView, StyleSheet } from 'react-native'
import { useQuery } from '@tanstack/react-query'

import { useAuth } from '@/auth/AuthContext'
import { atLeast, scopeOf } from '@/lib/scope'
import { getSalesReport } from '@/api/reports'
import { money, count } from '@/lib/format'
import { currentPeriod } from '@/lib/period'
import { Screen, Loading, ErrorView, EmptyState, Body } from '@/components/ui'
import { Kpi, KpiGrid, PeriodSwitcher, SectionTitle, StatRow } from '@/components/dash'
import { colors, spacing } from '@/theme'

// Sales report — transactions, revenue and gross profit for the period, from the shared sales
// aggregation the web Sales Report uses (so numbers reconcile with Exec MTD).
export default function SalesScreen() {
  const { me } = useAuth()
  const [period, setPeriod] = useState(currentPeriod())
  const q = useQuery({ queryKey: ['dash', 'sales', period], queryFn: () => getSalesReport(period),
    enabled: atLeast(scopeOf(me), 'market') })

  if (!atLeast(scopeOf(me), 'market')) {
    return <Screen><EmptyState title="No access" subtitle="The sales report needs company-wide reporting access." /></Screen>
  }

  const t = q.data?.totals || {}

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={styles.c}
        refreshControl={<RefreshControl refreshing={q.isFetching} onRefresh={q.refetch} tintColor={colors.primary} />}
      >
        <PeriodSwitcher period={period} onChange={setPeriod} />
        {q.isLoading ? (
          <Loading label="Loading sales…" />
        ) : q.isError ? (
          <ErrorView message={(q.error as Error)?.message || 'Could not load the sales report.'} onRetry={q.refetch} />
        ) : (
          <>
            <KpiGrid>
              <Kpi label="Revenue" value={money(t.revenue)} tone="info" />
              <Kpi label="Gross profit" value={money(t.gp)} tone="good" />
              <Kpi label="Transactions" value={count(t.txns)} />
              <Kpi label="Activations" value={count(t.activations)} />
            </KpiGrid>

            <SectionTitle>Mix</SectionTitle>
            <StatRow label="Line items" value={count(t.lines)} />
            <StatRow label="BYOD" value={count(t.byod)} />
            <StatRow label="Upgrades" value={count(t.upgrades)} />
            <StatRow label="Swaps" value={count(t.swaps)} />
            <StatRow label="Accessory revenue" value={money(t.accessory_rev)} />
            <StatRow label="Revenue" value={money(t.revenue)} strong tone="info" />
            <StatRow label="Gross profit" value={money(t.gp)} strong tone="good" />

            <Body dim>{count((q.data?.rows || []).length)} detail rows in this report.</Body>
          </>
        )}
      </ScrollView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  c: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xxl },
})
