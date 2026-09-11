import React, { useState } from 'react'
import { RefreshControl, ScrollView, StyleSheet } from 'react-native'
import { useQuery } from '@tanstack/react-query'

import { useAuth } from '@/auth/AuthContext'
import { getEmployeeDashboard } from '@/api/reports'
import { money } from '@/lib/format'
import { currentPeriod } from '@/lib/period'
import { Screen, Loading, ErrorView, EmptyState, Body } from '@/components/ui'
import { Kpi, KpiGrid, PeriodSwitcher, SectionTitle, StatRow } from '@/components/dash'
import { colors, spacing } from '@/theme'

// My performance — the caller's personal commission, tier and KPI progress. Access is enforced
// server-side (#13): a rep only ever sees their own record. employee_id comes from /core/me.
export default function MyPerformanceScreen() {
  const { me } = useAuth()
  const employeeId = (me?.user?.employee_id as string | undefined) || ''
  const [period, setPeriod] = useState(currentPeriod())

  const q = useQuery({
    queryKey: ['dash', 'me', employeeId, period],
    queryFn: () => getEmployeeDashboard(employeeId, period),
    enabled: !!employeeId,
  })

  if (!employeeId) {
    return (
      <Screen>
        <EmptyState title="No employee record" subtitle="Your login isn't linked to an employee, so there's no personal performance to show." />
      </Screen>
    )
  }

  const comm = (q.data?.commission || {}) as Record<string, unknown>
  const kpisMet = Number(comm.kpis_met)
  const totalKpis = Number(comm.total_kpis)

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={styles.c}
        refreshControl={<RefreshControl refreshing={q.isFetching} onRefresh={q.refetch} tintColor={colors.primary} />}
      >
        <PeriodSwitcher period={period} onChange={setPeriod} />
        {q.isLoading ? (
          <Loading label="Loading your numbers…" />
        ) : q.isError ? (
          <ErrorView message={(q.error as Error)?.message || 'Could not load your dashboard.'} onRetry={q.refetch} />
        ) : (
          <>
            <KpiGrid>
              <Kpi label="Commission" value={money(comm.total_payout)} tone="good" />
              <Kpi label="Tier" value={comm.tier != null ? String(comm.tier) : '—'} tone="info" />
              <Kpi
                label="KPIs met"
                value={Number.isFinite(kpisMet) && Number.isFinite(totalKpis) ? `${kpisMet}/${totalKpis}` : '—'}
                tone={Number.isFinite(kpisMet) && kpisMet >= totalKpis ? 'good' : 'warn'}
              />
            </KpiGrid>

            {q.data?.employee ? (
              <>
                <SectionTitle>Details</SectionTitle>
                <StatRow label="Name" value={q.data.employee.name || '—'} />
                <StatRow label="Store" value={q.data.employee.store || '—'} />
                <StatRow label="Role" value={q.data.employee.role || '—'} />
                {q.data.employee.pay_rate != null ? (
                  <StatRow label="Pay rate" value={money(q.data.employee.pay_rate, { cents: true })} />
                ) : null}
              </>
            ) : null}

            <Body dim>Only your own record is shown — personal dashboards are private to you.</Body>
          </>
        )}
      </ScrollView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  c: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xxl },
})
