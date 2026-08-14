import React from 'react'
import { Alert, Linking, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native'
import { Stack, useLocalSearchParams } from 'expo-router'
import { useMutation, useQuery } from '@tanstack/react-query'

import { getLead, logActivity, moveStage } from '@/api/crm'
import { queryClient } from '@/api/query'
import { Badge, Body, Button, Card, ErrorView, H2, Loading, Screen } from '@/components/ui'
import { colors, font, radius, spacing } from '@/theme'

export default function LeadDetail() {
  const { leadId } = useLocalSearchParams<{ leadId: string }>()

  const q = useQuery({
    queryKey: ['crm', 'lead', leadId],
    queryFn: () => getLead(String(leadId)),
    enabled: !!leadId,
  })

  const stageMut = useMutation({
    mutationFn: (stageId: string) => moveStage(String(leadId), stageId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['crm'] })
      q.refetch()
    },
    onError: (e) => Alert.alert('Could not move stage', e instanceof Error ? e.message : 'Try again.'),
  })

  const activityMut = useMutation({
    mutationFn: (kind: string) => logActivity(String(leadId), { kind }),
    onSuccess: () => q.refetch(),
    onError: (e) => Alert.alert('Could not log', e instanceof Error ? e.message : 'Try again.'),
  })

  if (q.isLoading)
    return (
      <Screen>
        <Loading />
      </Screen>
    )
  if (q.isError || !q.data)
    return (
      <Screen>
        <ErrorView message={(q.error as Error)?.message ?? 'Failed to load lead'} onRetry={q.refetch} />
      </Screen>
    )

  const { lead, stages = [], activities = [] } = q.data
  const name =
    lead.display_name || [lead.first_name, lead.last_name].filter(Boolean).join(' ') || `Lead #${lead.lead_no}`

  const call = () => {
    if (lead.phone) Linking.openURL(`tel:${lead.phone}`)
  }
  const text = () => {
    if (lead.phone) Linking.openURL(`sms:${lead.phone}`)
  }

  return (
    <Screen>
      <Stack.Screen options={{ title: `Lead #${lead.lead_no}` }} />
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.head}>
          <Text style={styles.name}>{name}</Text>
          <Badge
            label={lead.priority.toUpperCase()}
            color={lead.priority === 'hot' ? colors.hot : lead.priority === 'warm' ? colors.warm : colors.cold}
          />
        </View>

        <Card style={{ gap: spacing.xs }}>
          <Row label="Stage" value={lead.stage_name ?? '—'} />
          <Row label="Phone" value={lead.phone ?? '—'} />
          <Row label="Email" value={lead.email ?? '—'} />
          <Row label="Store" value={lead.store_code ?? '—'} />
          <Row label="Value" value={lead.value_estimate ? `$${Number(lead.value_estimate).toLocaleString()}` : '—'} />
          <Row label="Source" value={lead.source_name ?? '—'} />
          <Row label="Interest" value={lead.interest_name ?? '—'} />
        </Card>

        {lead.phone ? (
          <View style={styles.contactRow}>
            <View style={{ flex: 1 }}>
              <Button title="Call" onPress={call} />
            </View>
            <View style={{ flex: 1 }}>
              <Button title="Text" variant="secondary" onPress={text} />
            </View>
          </View>
        ) : null}

        {stages.length > 0 && (
          <>
            <H2>Move stage</H2>
            <View style={styles.chips}>
              {stages.map((s) => {
                const on = s.id === lead.stage_id
                return (
                  <Pressable
                    key={s.id}
                    disabled={on || stageMut.isPending}
                    onPress={() => stageMut.mutate(s.id)}
                    style={[styles.chip, on && styles.chipOn]}
                  >
                    <Text style={[styles.chipText, on && styles.chipTextOn]}>{s.name}</Text>
                  </Pressable>
                )
              })}
            </View>
          </>
        )}

        <H2>Log activity</H2>
        <View style={styles.chips}>
          {['call', 'note', 'visit', 'email'].map((k) => (
            <Pressable
              key={k}
              disabled={activityMut.isPending}
              onPress={() => activityMut.mutate(k)}
              style={styles.chip}
            >
              <Text style={styles.chipText}>+ {k}</Text>
            </Pressable>
          ))}
        </View>

        {activities.length > 0 && (
          <>
            <H2>Recent activity</H2>
            <Card style={{ gap: spacing.sm }}>
              {activities.slice(0, 10).map((a: any) => (
                <View key={a.id} style={styles.activity}>
                  <Text style={styles.activityKind}>{a.kind}</Text>
                  {a.body ? <Body dim>{a.body}</Body> : null}
                  <Text style={styles.activityTime}>
                    {a.created_at ? new Date(a.created_at).toLocaleString() : ''}
                  </Text>
                </View>
              ))}
            </Card>
          </>
        )}
      </ScrollView>
    </Screen>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={styles.rowValue}>{value}</Text>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { padding: spacing.lg, gap: spacing.md, paddingBottom: spacing.xxl },
  head: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  name: { color: colors.text, fontSize: font.h2, fontWeight: '800', flex: 1 },
  row: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 2, gap: spacing.md },
  rowLabel: { color: colors.textDim, fontSize: font.small },
  rowValue: { color: colors.text, fontSize: font.body, fontWeight: '600', flexShrink: 1, textAlign: 'right' },
  contactRow: { flexDirection: 'row', gap: spacing.md },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  chip: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  chipOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { color: colors.text, fontSize: font.small, fontWeight: '600' },
  chipTextOn: { color: colors.primaryText },
  activity: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border, paddingTop: spacing.sm },
  activityKind: { color: colors.text, fontSize: font.body, fontWeight: '700', textTransform: 'capitalize' },
  activityTime: { color: colors.textDim, fontSize: font.tiny, marginTop: 2 },
})
