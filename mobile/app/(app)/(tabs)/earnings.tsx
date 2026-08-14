import React, { useState } from 'react'
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native'
import { useRouter } from 'expo-router'
import { useQuery } from '@tanstack/react-query'

import { useAuth } from '@/auth/AuthContext'
import { getEmployeeDashboard, type TrackingRow } from '@/api/earnings'
import { Badge, Body, Button, Card, EmptyState, ErrorView, H2, Loading, Screen } from '@/components/ui'
import { OfflineBanner } from '@/components/OfflineBanner'
import { colors, font, radius, spacing } from '@/theme'

const money = (n: number | null | undefined) =>
  `$${Number(n || 0).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`
const money2 = (n: number | null | undefined) =>
  `$${Number(n || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

function ProgressBar({ pct, color = colors.primary }: { pct: number; color?: string }) {
  const clamped = Math.max(0, Math.min(100, pct))
  return (
    <View style={styles.track}>
      <View style={[styles.fill, { width: `${clamped}%`, backgroundColor: color }]} />
    </View>
  )
}

export default function Earnings() {
  const { me } = useAuth()
  const router = useRouter()
  const employeeId = (me?.user?.employee_id as string) || ''
  const [period, setPeriod] = useState<string | undefined>(undefined)

  const dash = useQuery({
    queryKey: ['earnings', 'dashboard', employeeId, period ?? 'current'],
    queryFn: () => getEmployeeDashboard(employeeId, period),
    enabled: !!employeeId,
  })

  if (!employeeId)
    return (
      <Screen>
        <EmptyState
          title="No employee record linked"
          subtitle="Your login isn't linked to an employee record yet. Ask an admin to set your Employee ID in Roles & Access."
        />
      </Screen>
    )

  if (dash.isLoading)
    return (
      <Screen>
        <Loading label="Loading your earnings…" />
      </Screen>
    )
  if (dash.isError || !dash.data)
    return (
      <Screen>
        <ErrorView message={(dash.error as Error)?.message ?? 'Failed to load'} onRetry={dash.refetch} />
      </Screen>
    )

  const d = dash.data
  const comm = d.commission
  const rc = d.report_card
  const earned = rc?.commission_earned ?? comm?.final_payout ?? comm?.total_payout ?? 0
  const kpisMet = rc?.kpis_met ?? comm?.kpis_met ?? 0
  const kpisTotal = rc?.total_kpis ?? comm?.total_kpis ?? 0
  const kpiPct = kpisTotal ? (Number(kpisMet) / Number(kpisTotal)) * 100 : 0

  const accTarget = Number(d.targets?.acc_target ?? comm?.acc_target ?? 0)
  const accComm = Number(d.targets?.acc_comm ?? comm?.acc_comm ?? 0)
  const accPct = accTarget ? (accComm / accTarget) * 100 : 0

  // Period chips from the tracking history (newest first), plus the current/selected period.
  const periods = Array.from(
    new Set([d.period, ...(d.commission_tracking ?? []).map((t) => t.period)].filter(Boolean)),
  ) as string[]
  periods.reverse()

  const canOpenTargets = !!(d.employee?.store && d.employee?.rep_name)

  return (
    <Screen>
      <OfflineBanner />
      <ScrollView
        contentContainerStyle={styles.container}
        refreshControl={
          <RefreshControl refreshing={dash.isFetching} onRefresh={dash.refetch} tintColor={colors.primary} />
        }
      >
        {/* Period selector */}
        {periods.length > 1 && (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
            {periods.map((p) => {
              const on = p === d.period
              return (
                <Pressable key={p} onPress={() => setPeriod(p)} style={[styles.chip, on && styles.chipOn]}>
                  <Text style={[styles.chipText, on && styles.chipTextOn]}>{p}</Text>
                </Pressable>
              )
            })}
          </ScrollView>
        )}

        {/* Commission headline */}
        <Card style={styles.hero}>
          <Text style={styles.heroLabel}>Commission · {d.period}</Text>
          <Text style={styles.heroValue}>{money2(earned)}</Text>
          <View style={styles.heroMeta}>
            {comm?.tier != null ? <Badge label={`Tier ${comm.tier}`} color={colors.primary} /> : null}
            {kpisTotal ? (
              <Badge
                label={`${kpisMet}/${kpisTotal} KPIs`}
                color={kpiPct >= 100 ? colors.success : kpiPct >= 50 ? colors.warning : colors.textDim}
              />
            ) : null}
          </View>
          {kpisTotal ? (
            <View style={{ width: '100%', marginTop: spacing.sm }}>
              <ProgressBar pct={kpiPct} color={kpiPct >= 100 ? colors.success : colors.primary} />
            </View>
          ) : null}
          {!comm ? <Body dim>No commission recorded for this period yet.</Body> : null}
        </Card>

        {/* Accessory target attainment */}
        {(accTarget > 0 || accComm > 0) && (
          <Card style={{ gap: spacing.sm }}>
            <View style={styles.rowBetween}>
              <Text style={styles.cardTitle}>Accessory target</Text>
              <Text style={[styles.pct, { color: accPct >= 100 ? colors.success : colors.text }]}>
                {accPct.toFixed(0)}%
              </Text>
            </View>
            <ProgressBar pct={accPct} color={accPct >= 100 ? colors.success : colors.warning} />
            <View style={styles.rowBetween}>
              <Body dim>Achieved {money(accComm)}</Body>
              <Body dim>Target {money(accTarget)}</Body>
            </View>
          </Card>
        )}

        {/* Targets pace / achievement */}
        <H2>Targets & pace</H2>
        <Card style={{ gap: spacing.sm }}>
          <Body dim>
            {canOpenTargets
              ? 'See your schedule-weighted daily targets, what you need today, and month-to-date attainment by category.'
              : 'Daily target pace becomes available once your store and sales name are on file.'}
          </Body>
          <Button
            title="View target pace"
            variant="secondary"
            disabled={!canOpenTargets}
            onPress={() =>
              router.push({
                pathname: '/earnings/targets',
                params: {
                  period: d.period,
                  store: d.employee.store ?? '',
                  rep: d.employee.rep_name ?? '',
                },
              })
            }
          />
        </Card>

        {/* Report card */}
        <H2>Report card</H2>
        <View style={styles.statGrid}>
          <Stat label="Flags" value={String(rc?.flags_count ?? 0)} tone={rc?.flags_count ? colors.warning : colors.textDim} />
          <Stat
            label="Chargebacks"
            value={String(rc?.chargebacks_count ?? 0)}
            sub={rc?.chargebacks_total ? money2(rc.chargebacks_total) : undefined}
            tone={rc?.chargebacks_count ? colors.danger : colors.textDim}
          />
          <Stat label="Hours (MTD)" value={String(d.hours?.actual_hours ?? 0)} />
          <Stat label="Est. pay" value={money(d.hours?.actual_pay)} />
        </View>

        {/* History */}
        {d.commission_tracking?.length > 0 && (
          <>
            <H2>History</H2>
            <Card style={{ gap: 0 }}>
              {[...d.commission_tracking].reverse().map((t: TrackingRow, i) => (
                <View key={`${t.period}-${i}`} style={[styles.histRow, i === 0 && { borderTopWidth: 0 }]}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.histPeriod}>{t.period}</Text>
                    {t.total_kpis ? (
                      <Body dim>
                        {t.kpis_met ?? 0}/{t.total_kpis} KPIs{t.tier != null ? ` · Tier ${t.tier}` : ''}
                      </Body>
                    ) : null}
                  </View>
                  <Text style={styles.histPay}>{money2(t.total_payout)}</Text>
                </View>
              ))}
            </Card>
          </>
        )}
      </ScrollView>
    </Screen>
  )
}

function Stat({ label, value, sub, tone = colors.text }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={[styles.statValue, { color: tone }]}>{value}</Text>
      {sub ? <Text style={styles.statSub}>{sub}</Text> : null}
    </View>
  )
}

const styles = StyleSheet.create({
  container: { padding: spacing.lg, gap: spacing.md, paddingBottom: spacing.xxl },
  chips: { gap: spacing.sm, paddingVertical: 2 },
  chip: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  chipOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { color: colors.text, fontSize: font.small, fontWeight: '600' },
  chipTextOn: { color: colors.primaryText },
  hero: { alignItems: 'center', gap: spacing.xs, paddingVertical: spacing.xl },
  heroLabel: { color: colors.textDim, fontSize: font.small },
  heroValue: { color: colors.text, fontSize: 40, fontWeight: '900' },
  heroMeta: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.xs },
  rowBetween: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  cardTitle: { color: colors.text, fontSize: font.body, fontWeight: '700' },
  pct: { fontSize: font.h3, fontWeight: '800' },
  track: { height: 10, borderRadius: radius.pill, backgroundColor: colors.surfaceAlt, overflow: 'hidden' },
  fill: { height: 10, borderRadius: radius.pill },
  statGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md },
  stat: {
    width: '47%',
    flexGrow: 1,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    padding: spacing.md,
    gap: 2,
  },
  statLabel: { color: colors.textDim, fontSize: font.small },
  statValue: { fontSize: font.h2, fontWeight: '800' },
  statSub: { color: colors.textDim, fontSize: font.small },
  histRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: spacing.sm,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
  },
  histPeriod: { color: colors.text, fontSize: font.body, fontWeight: '600' },
  histPay: { color: colors.success, fontSize: font.body, fontWeight: '700' },
})
