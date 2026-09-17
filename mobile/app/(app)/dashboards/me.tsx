import React, { useMemo, useState } from 'react'
import { Modal, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native'
import { useQuery } from '@tanstack/react-query'

import { useAuth } from '@/auth/AuthContext'
import { getEmployeeDashboard, getEmployees, type EmployeeLite } from '@/api/reports'
import { money, count } from '@/lib/format'
import { currentPeriod } from '@/lib/period'
import { Screen, Loading, ErrorView, EmptyState, Body } from '@/components/ui'
import { Kpi, KpiGrid, PeriodSwitcher, SectionTitle, StatRow, BarRow } from '@/components/dash'
import { colors, font, radius, spacing } from '@/theme'

const empName = (e: EmployeeLite): string =>
  e.full_name || e.name || [e.first_name, e.last_name].filter(Boolean).join(' ') || e.employee_id || '—'

// Employee dashboard — one rep's full performance for a period: commission, tier & KPIs, hours vs
// pay, accessory target, report card (flags / chargebacks), priority-sell devices, upcoming shifts,
// and a payout trend. An admin/DM picks which employee to view (their own by default); the server
// enforces that they can only open reps inside their span (#13).
export default function EmployeeDashboardScreen() {
  const { me } = useAuth()
  const ownId = (me?.user?.employee_id as string | undefined) || ''
  const [period, setPeriod] = useState(currentPeriod())
  const [empId, setEmpId] = useState(ownId)
  const [pickerOpen, setPickerOpen] = useState(false)

  const emps = useQuery({ queryKey: ['employees'], queryFn: getEmployees })
  const list: EmployeeLite[] = useMemo(() => {
    const d = emps.data
    const arr = Array.isArray(d) ? d : (d?.employees || [])
    return arr.filter((e) => (e.employee_id || '').toString().trim())
  }, [emps.data])

  // Resolve who to show: explicit pick → own → first visible employee.
  const activeId = empId || ownId || list[0]?.employee_id || ''
  const activeEmp = list.find((e) => e.employee_id === activeId)

  const q = useQuery({
    queryKey: ['dash', 'employee', activeId, period],
    queryFn: () => getEmployeeDashboard(activeId, period),
    enabled: !!activeId,
  })

  if (!ownId && !emps.isLoading && list.length === 0) {
    return (
      <Screen>
        <EmptyState title="No employees to show"
          subtitle="Your login isn't linked to an employee and has no reps in scope." />
      </Screen>
    )
  }

  const d = q.data
  const comm = (d?.commission || {}) as Record<string, unknown>
  const hours = d?.hours || {}
  const rc = d?.report_card || {}
  const kpisMet = Number(comm.kpis_met ?? rc.kpis_met)
  const totalKpis = Number(comm.total_kpis ?? rc.total_kpis)
  const tracking = (d?.commission_tracking || []).slice(-6)
  const maxTrack = Math.max(1, ...tracking.map((t) => Number(t.total_payout) || 0))

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={styles.c}
        refreshControl={<RefreshControl refreshing={q.isFetching} onRefresh={q.refetch} tintColor={colors.primary} />}
      >
        {/* who + period */}
        <Pressable style={styles.who} onPress={() => setPickerOpen(true)}>
          <View style={{ flex: 1 }}>
            <Text style={styles.whoName}>{activeEmp ? empName(activeEmp) : (d?.employee?.name || 'Select employee')}</Text>
            <Text style={styles.whoSub}>
              {(d?.employee?.store || activeEmp?.home_store || activeEmp?.store || '—')}
              {d?.employee?.role ? ` · ${d.employee.role}` : ''}
            </Text>
          </View>
          {list.length > 1 ? <Text style={styles.whoChevron}>▾</Text> : null}
        </Pressable>
        <PeriodSwitcher period={period} onChange={setPeriod} />

        {!activeId ? (
          <EmptyState title="Pick an employee" subtitle="Choose whose dashboard to view." />
        ) : q.isLoading ? (
          <Loading label="Loading dashboard…" />
        ) : q.isError ? (
          <ErrorView message={(q.error as Error)?.message || 'Could not load the dashboard.'} onRetry={q.refetch} />
        ) : (
          <>
            <KpiGrid>
              <Kpi label="Commission" value={money(comm.total_payout ?? rc.commission_earned)} tone="good" />
              <Kpi label="Tier" value={comm.tier != null ? String(comm.tier) : '—'} tone="info" />
              <Kpi label="KPIs met"
                value={Number.isFinite(kpisMet) && Number.isFinite(totalKpis) ? `${kpisMet}/${totalKpis}` : '—'}
                tone={Number.isFinite(kpisMet) && kpisMet >= totalKpis ? 'good' : 'warn'} />
            </KpiGrid>

            {(hours.scheduled_hours != null || hours.actual_hours != null) && (
              <>
                <SectionTitle>Hours &amp; pay</SectionTitle>
                <StatRow label="Scheduled hours" value={count(hours.scheduled_hours)} />
                <StatRow label="Actual hours" value={count(hours.actual_hours)} />
                {hours.pay_rate != null ? <StatRow label="Pay rate" value={money(hours.pay_rate, { cents: true })} /> : null}
                {hours.actual_pay != null ? <StatRow label="Actual pay" value={money(hours.actual_pay)} strong /> : null}
              </>
            )}

            {(d?.targets?.acc_target != null || d?.targets?.acc_comm != null) && (
              <>
                <SectionTitle>Accessory target</SectionTitle>
                <StatRow label="Target" value={money(d?.targets?.acc_target)} />
                <StatRow label="Commission" value={money(d?.targets?.acc_comm)} tone="good" />
              </>
            )}

            {(Number(rc.flags_count) > 0 || Number(rc.chargebacks_count) > 0) && (
              <>
                <SectionTitle>Report card</SectionTitle>
                <StatRow label="Flags" value={count(rc.flags_count)} tone={Number(rc.flags_count) > 0 ? 'warn' : 'default'} />
                <StatRow label="Chargebacks" value={count(rc.chargebacks_count)} tone={Number(rc.chargebacks_count) > 0 ? 'bad' : 'default'} />
                {rc.chargebacks_total != null ? <StatRow label="Chargeback total" value={money(rc.chargebacks_total)} tone="bad" /> : null}
              </>
            )}

            {(d?.phone_priority || []).length > 0 && (
              <>
                <SectionTitle>Priority devices to sell</SectionTitle>
                {(d?.phone_priority || []).slice(0, 8).map((p, i) => (
                  <StatRow key={i} label={p.device_model || p.imei || '—'}
                    value={money(p.net_owed ?? p.owed)} tone="warn" />
                ))}
              </>
            )}

            {(d?.schedule || []).length > 0 && (
              <>
                <SectionTitle>Upcoming shifts</SectionTitle>
                {(d?.schedule || []).slice(0, 5).map((s, i) => (
                  <StatRow key={i} label={`${s.date || '—'}${s.store_code ? ` · ${s.store_code}` : ''}`}
                    value={[s.start_time, s.end_time].filter(Boolean).join('–') || '—'} />
                ))}
              </>
            )}

            {tracking.length > 0 && (
              <>
                <SectionTitle>Payout trend</SectionTitle>
                {tracking.map((t, i) => (
                  <BarRow key={i} label={String(t.period || '—')} value={Number(t.total_payout) || 0}
                    valueText={money(t.total_payout)} max={maxTrack} tone="good"
                    right={t.tier != null ? `Tier ${t.tier}` : undefined} />
                ))}
              </>
            )}

            <Body dim>Pay is shown only where you&apos;re permitted to see it.</Body>
          </>
        )}
      </ScrollView>

      {/* employee picker */}
      <Modal visible={pickerOpen} animationType="slide" transparent onRequestClose={() => setPickerOpen(false)}>
        <Pressable style={styles.backdrop} onPress={() => setPickerOpen(false)}>
          <Pressable style={styles.sheet} onPress={() => {}}>
            <Text style={styles.sheetTitle}>Choose employee</Text>
            <ScrollView style={{ maxHeight: 420 }}>
              {list.map((e) => {
                const on = e.employee_id === activeId
                return (
                  <Pressable key={e.employee_id} style={styles.optRow}
                    onPress={() => { setEmpId(e.employee_id || ''); setPickerOpen(false) }}>
                    <Text style={[styles.optLabel, on && { color: colors.primary, fontWeight: '800' }]} numberOfLines={1}>
                      {on ? '✓ ' : ''}{empName(e)}
                    </Text>
                    <Text style={styles.optSub} numberOfLines={1}>{e.home_store || e.store || ''}</Text>
                  </Pressable>
                )
              })}
              {list.length === 0 ? <Body dim>No employees in your scope.</Body> : null}
            </ScrollView>
          </Pressable>
        </Pressable>
      </Modal>
    </Screen>
  )
}

const styles = StyleSheet.create({
  c: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xxl },
  who: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm,
    backgroundColor: colors.surface, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border, padding: spacing.md,
  },
  whoName: { color: colors.text, fontSize: font.h3, fontWeight: '800' },
  whoSub: { color: colors.textDim, fontSize: font.small },
  whoChevron: { color: colors.textDim, fontSize: font.h3 },
  backdrop: { flex: 1, backgroundColor: '#0008', justifyContent: 'flex-end' },
  sheet: { backgroundColor: colors.surface, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, padding: spacing.lg, gap: spacing.sm },
  sheetTitle: { color: colors.text, fontSize: font.h3, fontWeight: '800', marginBottom: spacing.xs },
  optRow: { paddingVertical: spacing.sm, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  optLabel: { color: colors.text, fontSize: font.body },
  optSub: { color: colors.textDim, fontSize: font.tiny },
})
