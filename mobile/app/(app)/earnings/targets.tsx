import React from 'react'
import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native'
import { Stack, useLocalSearchParams } from 'expo-router'
import { useQuery } from '@tanstack/react-query'

import { getTargetCalendar, localToday, type TargetCategory } from '@/api/earnings'
import { Badge, Body, Card, EmptyState, ErrorView, H2, Loading, Screen } from '@/components/ui'
import { colors, font, radius, spacing } from '@/theme'

const CAT_LABEL: Record<string, string> = {
  accessories: 'Accessories',
  activations: 'Activations',
  byod: 'BYOD',
  handset: 'Handsets',
  hsi: 'Home Internet',
}
const label = (k: string) => CAT_LABEL[k] ?? k.charAt(0).toUpperCase() + k.slice(1)

function ProgressBar({ pct, color = colors.primary }: { pct: number; color?: string }) {
  const clamped = Math.max(0, Math.min(100, pct))
  return (
    <View style={styles.track}>
      <View style={[styles.fill, { width: `${clamped}%`, backgroundColor: color }]} />
    </View>
  )
}

function fmt(n: number, unit: string) {
  if (unit === '$') return `$${Number(n || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}`
  return `${Number(n || 0).toLocaleString('en-US', { maximumFractionDigits: 1 })}`
}

export default function Targets() {
  const { period, store, rep } = useLocalSearchParams<{ period: string; store: string; rep: string }>()

  const cal = useQuery({
    queryKey: ['earnings', 'targets', period, store, rep],
    queryFn: () =>
      getTargetCalendar({ period: String(period), store_code: String(store), rep: String(rep), today: localToday() }),
    enabled: !!period && !!store && !!rep,
  })

  if (cal.isLoading)
    return (
      <Screen>
        <Loading label="Loading targets…" />
      </Screen>
    )
  if (cal.isError || !cal.data)
    return (
      <Screen>
        <ErrorView message={(cal.error as Error)?.message ?? 'Failed to load targets'} onRetry={cal.refetch} />
      </Screen>
    )

  const data = cal.data
  const cats = Object.entries(data.categories ?? {}) as [string, TargetCategory][]
  const convRate = data.conversion?.rep?.rate ?? data.conversion?.store?.rate
  const convTarget = data.conversion?.rep?.target ?? data.conversion?.store?.target

  return (
    <Screen>
      <Stack.Screen options={{ title: `Targets · ${store}` }} />
      <ScrollView
        contentContainerStyle={styles.container}
        refreshControl={<RefreshControl refreshing={cal.isFetching} onRefresh={cal.refetch} tintColor={colors.primary} />}
      >
        <Body dim>
          {rep} · {store} · {data.period}
          {data.has_schedule === false ? ' · no schedule to weight from' : ''}
        </Body>

        {cats.length === 0 ? (
          <EmptyState title="No targets set" subtitle="No target categories are configured for this store/period." />
        ) : (
          cats.map(([key, c]) => {
            const attainment = c.monthly ? (c.achieved_mtd / c.monthly) * 100 : 0
            const tone = attainment >= 100 ? colors.success : attainment >= 60 ? colors.warning : colors.danger
            return (
              <Card key={key} style={{ gap: spacing.sm }}>
                <View style={styles.rowBetween}>
                  <Text style={styles.catTitle}>{label(key)}</Text>
                  <Text style={[styles.pct, { color: tone }]}>{attainment.toFixed(0)}%</Text>
                </View>
                <ProgressBar pct={attainment} color={tone} />
                <View style={styles.rowBetween}>
                  <Body dim>
                    {fmt(c.achieved_mtd, c.unit)} of {fmt(c.monthly, c.unit)}
                  </Body>
                  <Body dim>{fmt(c.need, c.unit)} to go</Body>
                </View>
                <View style={styles.metaRow}>
                  {c.today_target ? (
                    <Badge label={`Need today: ${fmt(c.today_target, c.unit)}`} color={colors.primary} />
                  ) : null}
                  {c.pace ? <Badge label={`Pace: ${fmt(c.pace, c.unit)}/day`} color={colors.textDim} /> : null}
                  <Badge label={`${c.open_days_left} days left`} color={colors.textDim} />
                </View>
              </Card>
            )
          })
        )}

        {convRate != null && (
          <>
            <H2>Conversion</H2>
            <Card style={{ gap: spacing.sm }}>
              <View style={styles.rowBetween}>
                <Text style={styles.catTitle}>Box / bill-pay rate</Text>
                <Text
                  style={[
                    styles.pct,
                    { color: convTarget != null && convRate >= convTarget ? colors.success : colors.warning },
                  ]}
                >
                  {(convRate * (convRate <= 1 ? 100 : 1)).toFixed(0)}%
                </Text>
              </View>
              {convTarget != null ? <Body dim>Target {(convTarget * (convTarget <= 1 ? 100 : 1)).toFixed(0)}%</Body> : null}
              {data.conversion?.rep?.below_store ? (
                <Body dim>You&apos;re below the store average — focus on attaching a plan to each box.</Body>
              ) : null}
            </Card>
          </>
        )}

        <Body dim>
          Targets are weighted by your scheduled hours and update as sales post. &quot;Need today&quot; is what
          keeps you on pace for the month.
        </Body>
      </ScrollView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  container: { padding: spacing.lg, gap: spacing.md, paddingBottom: spacing.xxl },
  rowBetween: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  catTitle: { color: colors.text, fontSize: font.body, fontWeight: '700' },
  pct: { fontSize: font.h3, fontWeight: '800' },
  track: { height: 10, borderRadius: radius.pill, backgroundColor: colors.surfaceAlt, overflow: 'hidden' },
  fill: { height: 10, borderRadius: radius.pill },
  metaRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
})
