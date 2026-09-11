import React from 'react'
import { RefreshControl, ScrollView, StyleSheet } from 'react-native'
import { useQuery } from '@tanstack/react-query'

import { getPosKpis } from '@/api/reports'
import { money, count } from '@/lib/format'
import { Screen, Loading, ErrorView, Body } from '@/components/ui'
import { Kpi, KpiGrid, SectionTitle } from '@/components/dash'
import { colors, spacing } from '@/theme'

// Store KPIs — live store sales (today / week / month) + stock on hand. Server scopes to the caller's
// own stores (pos/reports/kpis), so it's safe for any store-level user. No period: it's "now".
export default function StoreKpisScreen() {
  const q = useQuery({ queryKey: ['dash', 'pos-kpis'], queryFn: getPosKpis })

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={styles.c}
        refreshControl={<RefreshControl refreshing={q.isFetching} onRefresh={q.refetch} tintColor={colors.primary} />}
      >
        {q.isLoading ? (
          <Loading label="Loading store KPIs…" />
        ) : q.isError ? (
          <ErrorView message={(q.error as Error)?.message || 'Could not load KPIs.'} onRetry={q.refetch} />
        ) : (
          <>
            <SectionTitle>Sales</SectionTitle>
            <KpiGrid>
              <Kpi label="Today" value={money(q.data?.today.total)} sub={`${count(q.data?.today.count)} sales`} tone="good" />
              <Kpi label="This week" value={money(q.data?.week.total)} sub={`${count(q.data?.week.count)} sales`} />
              <Kpi label="This month" value={money(q.data?.month.total)} sub={`${count(q.data?.month.count)} sales`} tone="info" />
            </KpiGrid>

            <SectionTitle>Catalog & stock</SectionTitle>
            <KpiGrid>
              <Kpi label="Customers" value={count(q.data?.customers)} />
              <Kpi label="Products" value={count(q.data?.products)} />
              <Kpi label="Units in stock" value={count(q.data?.in_stock_units)} />
            </KpiGrid>

            <Body dim>Counts and totals are in your store&apos;s local time, across the stores you cover.</Body>
          </>
        )}
      </ScrollView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  c: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xxl },
})
