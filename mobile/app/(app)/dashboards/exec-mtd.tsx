import React, { useState } from 'react'
import { RefreshControl, ScrollView, StyleSheet } from 'react-native'
import { useQuery } from '@tanstack/react-query'

import { useAuth } from '@/auth/AuthContext'
import { atLeast, scopeOf } from '@/lib/scope'
import { getExecMtd, type ExecRow } from '@/api/reports'
import { money, count } from '@/lib/format'
import { currentPeriod } from '@/lib/period'
import { Screen, Loading, ErrorView, EmptyState, Body } from '@/components/ui'
import { BarRow, Kpi, KpiGrid, PeriodSwitcher, SectionTitle } from '@/components/dash'
import { colors, spacing } from '@/theme'

const rowLabel = (r: ExecRow) => r.label || r.employee || r.store || r.name || '—'

// Executive MTD — activations and the full sales mix, by store and by rep. Reads the same engine the
// web Exec MTD page uses; by_location/by_employee each carry {rows, total}.
export default function ExecMtdScreen() {
  const { me } = useAuth()
  const [period, setPeriod] = useState(currentPeriod())
  const q = useQuery({ queryKey: ['dash', 'exec-mtd', period], queryFn: () => getExecMtd(period),
    enabled: atLeast(scopeOf(me), 'market') })

  if (!atLeast(scopeOf(me), 'market')) {
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
        <PeriodSwitcher period={period} onChange={setPeriod} />
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
            {stores.length === 0 ? <Body dim>No store activity this period.</Body> :
              stores.map((r, i) => (
                <BarRow key={i} label={rowLabel(r)} value={Number(r.total_activation) || 0}
                  valueText={count(r.total_activation)} max={maxStore} tone="info" right={money(r.amount)} />
              ))}

            <SectionTitle>Top reps · activations</SectionTitle>
            {reps.length === 0 ? <Body dim>No rep activity this period.</Body> :
              reps.map((r, i) => (
                <BarRow key={i} label={rowLabel(r)} value={Number(r.total_activation) || 0}
                  valueText={count(r.total_activation)} max={maxRep} tone="good" />
              ))}

            {q.data?.activation_source ? <Body dim>Source: {String(q.data.activation_source)}.</Body> : null}
          </>
        )}
      </ScrollView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  c: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xxl },
})
