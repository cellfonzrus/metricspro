import React, { useState } from 'react'
import { Alert, FlatList, Pressable, StyleSheet, Text, View } from 'react-native'
import { useRouter } from 'expo-router'
import { useQuery } from '@tanstack/react-query'

import { completeTask, listLeads, listTasks, type CrmTask, type Lead } from '@/api/crm'
import { queryClient } from '@/api/query'
import { Badge, Body, EmptyState, ErrorView, Loading, Screen } from '@/components/ui'
import { OfflineBanner } from '@/components/OfflineBanner'
import { colors, font, radius, spacing } from '@/theme'

type Tab = 'leads' | 'tasks'

const priorityColor = (p: Lead['priority']) =>
  p === 'hot' ? colors.hot : p === 'warm' ? colors.warm : colors.cold

export default function Crm() {
  const [tab, setTab] = useState<Tab>('leads')
  const router = useRouter()
  return (
    <Screen>
      <OfflineBanner />
      <View style={styles.segment}>
        <Seg label="Leads" active={tab === 'leads'} onPress={() => setTab('leads')} />
        <Seg label="Tasks" active={tab === 'tasks'} onPress={() => setTab('tasks')} />
      </View>
      {tab === 'leads' ? <LeadsList /> : <TasksList />}
      {tab === 'leads' && (
        <Pressable style={styles.fab} onPress={() => router.push('/crm/new')} accessibilityLabel="New lead">
          <Text style={styles.fabText}>+ New lead</Text>
        </Pressable>
      )}
    </Screen>
  )
}

function LeadsList() {
  const router = useRouter()
  const leads = useQuery({ queryKey: ['crm', 'leads', 'open'], queryFn: () => listLeads({ status: 'open' }) })

  if (leads.isLoading) return <Loading label="Loading leads…" />
  if (leads.isError)
    return <ErrorView message={(leads.error as Error)?.message ?? 'Failed'} onRetry={leads.refetch} />

  const rows = leads.data?.leads ?? []
  if (rows.length === 0) return <EmptyState title="No open leads" subtitle="New leads will show up here." />

  return (
    <FlatList
      data={rows}
      keyExtractor={(l) => l.id}
      contentContainerStyle={styles.list}
      onRefresh={leads.refetch}
      refreshing={leads.isFetching}
      renderItem={({ item }) => (
        <Pressable style={styles.card} onPress={() => router.push(`/crm/${item.id}`)}>
          <View style={styles.cardHead}>
            <Text style={styles.name} numberOfLines={1}>
              {item.display_name || [item.first_name, item.last_name].filter(Boolean).join(' ') || `Lead #${item.lead_no}`}
            </Text>
            <Badge label={item.priority.toUpperCase()} color={priorityColor(item.priority)} />
          </View>
          <Body dim>
            {[item.stage_name, item.phone, item.store_code].filter(Boolean).join(' · ') || '—'}
          </Body>
          {item.value_estimate ? (
            <Text style={styles.value}>${Number(item.value_estimate).toLocaleString()}</Text>
          ) : null}
        </Pressable>
      )}
    />
  )
}

function TasksList() {
  const router = useRouter()
  const tasks = useQuery({ queryKey: ['crm', 'tasks', 'open'], queryFn: () => listTasks({ scope: 'open' }) })

  const onComplete = async (t: CrmTask) => {
    try {
      await completeTask(t.id)
      queryClient.invalidateQueries({ queryKey: ['crm'] })
    } catch (e) {
      Alert.alert('Could not complete', e instanceof Error ? e.message : 'Try again.')
    }
  }

  if (tasks.isLoading) return <Loading label="Loading tasks…" />
  if (tasks.isError)
    return <ErrorView message={(tasks.error as Error)?.message ?? 'Failed'} onRetry={tasks.refetch} />

  const rows = tasks.data?.tasks ?? []
  if (rows.length === 0) return <EmptyState title="All caught up" subtitle="No open tasks." />

  return (
    <FlatList
      data={rows}
      keyExtractor={(t) => t.id}
      contentContainerStyle={styles.list}
      onRefresh={tasks.refetch}
      refreshing={tasks.isFetching}
      renderItem={({ item }) => (
        <View style={styles.card}>
          <View style={styles.cardHead}>
            <Text style={styles.name} numberOfLines={1}>
              {item.title}
            </Text>
            {item.is_overdue ? (
              <Badge label="OVERDUE" color={colors.danger} />
            ) : item.is_today ? (
              <Badge label="TODAY" color={colors.warning} />
            ) : null}
          </View>
          {item.lead_name ? (
            <Pressable onPress={() => item.lead_id && router.push(`/crm/${item.lead_id}`)}>
              <Body dim>
                {item.lead_name}
                {item.lead_phone ? ` · ${item.lead_phone}` : ''}
              </Body>
            </Pressable>
          ) : null}
          <View style={styles.taskActions}>
            <Pressable style={styles.doneBtn} onPress={() => onComplete(item)}>
              <Text style={styles.doneText}>✓ Complete</Text>
            </Pressable>
          </View>
        </View>
      )}
    />
  )
}

function Seg({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  return (
    <Pressable style={[styles.seg, active && styles.segOn]} onPress={onPress}>
      <Text style={[styles.segText, active && styles.segTextOn]}>{label}</Text>
    </Pressable>
  )
}

const styles = StyleSheet.create({
  segment: { flexDirection: 'row', gap: spacing.sm, padding: spacing.lg, paddingBottom: spacing.sm },
  seg: {
    flex: 1,
    paddingVertical: spacing.sm,
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    alignItems: 'center',
    backgroundColor: colors.surface,
  },
  segOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  segText: { color: colors.text, fontSize: font.body, fontWeight: '700' },
  segTextOn: { color: colors.primaryText },
  list: { paddingHorizontal: spacing.lg, paddingBottom: spacing.xxl, gap: spacing.sm },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    padding: spacing.md,
    gap: spacing.xs,
  },
  cardHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm },
  name: { color: colors.text, fontSize: font.body, fontWeight: '700', flex: 1 },
  value: { color: colors.success, fontSize: font.body, fontWeight: '700' },
  taskActions: { flexDirection: 'row', marginTop: spacing.xs },
  doneBtn: { backgroundColor: colors.surfaceAlt, borderRadius: radius.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  doneText: { color: colors.success, fontSize: font.small, fontWeight: '700' },
  fab: {
    position: 'absolute',
    right: spacing.lg,
    bottom: spacing.lg,
    backgroundColor: colors.primary,
    borderRadius: radius.pill,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    shadowColor: '#000',
    shadowOpacity: 0.3,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 4,
  },
  fabText: { color: colors.primaryText, fontSize: font.body, fontWeight: '800' },
})
