import React, { useState } from 'react'
import { RefreshControl, ScrollView, StyleSheet } from 'react-native'
import { useQuery } from '@tanstack/react-query'

import { useAuth } from '@/auth/AuthContext'
import { atLeast, scopeOf } from '@/lib/scope'
import { getOverview, getOverviewNarrative, type OverviewScope } from '@/api/reports'
import { money, pct } from '@/lib/format'
import { currentPeriod } from '@/lib/period'
import { Screen, Loading, ErrorView, EmptyState, Body } from '@/components/ui'
import { Kpi, KpiGrid, PeriodSwitcher, SectionTitle, StatRow } from '@/components/dash'
import { colors, spacing } from '@/theme'

const isPl = (s: OverviewScope) => s.net_income != null || s.revenue != null

// P&L / Accounts — revenue, gross profit and net income across the business. Same account/overview
// endpoint the web Accounts dashboard reads; the /narrative gives a ready margin figure.
export default function PnlScreen() {
  const { me } = useAuth()
  const [period, setPeriod] = useState(currentPeriod())
  const allowed = atLeast(scopeOf(me), 'market')
  const q = useQuery({ queryKey: ['dash', 'overview', period], queryFn: () => getOverview(period), enabled: allowed })
  const nq = useQuery({ queryKey: ['dash', 'overview-narr', period], queryFn: () => getOverviewNarrative(period), enabled: allowed })

  if (!allowed) {
    return <Screen><EmptyState title="No access" subtitle="P&L needs company-wide reporting access." /></Screen>
  }

  const scopes = q.data?.scopes || []
  const consolidated = scopes.find((s) => s.scope_key === 'consolidated') || scopes.find(isPl)
  const facts = (nq.data?.facts || {}) as Record<string, unknown>
  const margin = facts.margin != null ? Number(facts.margin) : (consolidated?.revenue
    ? ((consolidated.net_income || 0) / (consolidated.revenue || 1)) * 100 : undefined)
  const perCompany = scopes.filter((s) => s.scope_key !== 'consolidated' && isPl(s))

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={styles.c}
        refreshControl={<RefreshControl refreshing={q.isFetching} onRefresh={() => { q.refetch(); nq.refetch() }} tintColor={colors.primary} />}
      >
        <PeriodSwitcher period={period} onChange={setPeriod} />
        {q.isLoading ? (
          <Loading label="Loading P&L…" />
        ) : q.isError ? (
          <ErrorView message={(q.error as Error)?.message || 'Could not load the P&L.'} onRetry={q.refetch} />
        ) : !consolidated ? (
          <EmptyState title="Not computed yet" subtitle="No P&L has been computed for this period." />
        ) : (
          <>
            <KpiGrid>
              <Kpi label="Revenue" value={money(consolidated.revenue)} tone="info" />
              <Kpi label="Gross profit" value={money(consolidated.gross_profit)} tone="good" />
              <Kpi label="Net income" value={money(consolidated.net_income)} tone={(consolidated.net_income || 0) >= 0 ? 'good' : 'bad'} />
              <Kpi label="Net margin" value={margin != null ? pct(margin, 1) : '—'} />
            </KpiGrid>

            {q.data && !q.data.computed ? (
              <Body dim>Showing the latest available figures — this period isn&apos;t fully closed yet.</Body>
            ) : null}

            {perCompany.length > 0 ? (
              <>
                <SectionTitle>By company</SectionTitle>
                {perCompany.map((s) => (
                  <StatRow key={s.scope_key} label={s.scope_label} value={money(s.net_income)}
                    tone={(s.net_income || 0) >= 0 ? 'good' : 'bad'} />
                ))}
              </>
            ) : null}
          </>
        )}
      </ScrollView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  c: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xxl },
})
