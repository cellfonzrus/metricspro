import React from 'react'
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native'
import { useRouter } from 'expo-router'
import { useQuery } from '@tanstack/react-query'

import { useAuth } from '@/auth/AuthContext'
import { getStatus } from '@/api/timeclock'
import { getSummary } from '@/api/crm'
import { ROADMAP_MODULES, visibleModules } from '@/modules/registry'
import { Body, Card, H2, Screen } from '@/components/ui'
import { OfflineBanner } from '@/components/OfflineBanner'
import { colors, font, radius, spacing } from '@/theme'

export default function Home() {
  const { me } = useAuth()
  const router = useRouter()
  const modules = visibleModules(me)

  const clock = useQuery({ queryKey: ['timeclock', 'status'], queryFn: getStatus })
  const crm = useQuery({ queryKey: ['crm', 'summary'], queryFn: getSummary })

  const refreshing = clock.isFetching || crm.isFetching
  const onRefresh = () => {
    clock.refetch()
    crm.refetch()
  }

  const name = (me?.user?.name || me?.user?.email || 'there') as string
  const firstName = String(name).split(/[ @]/)[0]

  return (
    <Screen>
      <OfflineBanner />
      <ScrollView
        contentContainerStyle={styles.container}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
      >
        <View>
          <Text style={styles.greeting}>Hi {firstName} 👋</Text>
          {me?.user?.org_name ? <Body dim>{me.user.org_name}</Body> : null}
        </View>

        {/* Live status strip */}
        <View style={styles.statRow}>
          <Card style={styles.stat}>
            <Text style={styles.statLabel}>Time Clock</Text>
            <Text style={[styles.statValue, { color: clock.data?.clockedIn ? colors.success : colors.textDim }]}>
              {clock.isLoading ? '—' : clock.data?.clockedIn ? 'On the clock' : 'Clocked out'}
            </Text>
          </Card>
          <Card style={styles.stat}>
            <Text style={styles.statLabel}>Tasks today</Text>
            <Text style={styles.statValue}>{crm.isLoading ? '—' : (crm.data?.tasks_today ?? 0)}</Text>
          </Card>
        </View>

        <H2>Modules</H2>
        <View style={styles.grid}>
          {modules.map((m) => (
            <Pressable key={m.key} style={styles.tile} onPress={() => router.push(m.route as any)}>
              <Text style={styles.tileIcon}>{m.icon}</Text>
              <Text style={styles.tileTitle}>{m.title}</Text>
              <Text style={styles.tileDesc} numberOfLines={2}>
                {m.description}
              </Text>
            </Pressable>
          ))}
        </View>

        <H2>Coming soon</H2>
        <Body dim>Back-office modules are being brought over from the web platform.</Body>
        <View style={styles.grid}>
          {ROADMAP_MODULES.map((m) => (
            <View key={m.id} style={[styles.tile, styles.tileDisabled]}>
              <Text style={[styles.tileIcon, { opacity: 0.5 }]}>{m.icon}</Text>
              <Text style={[styles.tileTitle, { color: colors.textDim }]}>{m.title}</Text>
              <Text style={styles.tileDesc} numberOfLines={2}>
                {m.description}
              </Text>
            </View>
          ))}
        </View>
      </ScrollView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  container: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xxl },
  greeting: { color: colors.text, fontSize: font.h1, fontWeight: '800' },
  statRow: { flexDirection: 'row', gap: spacing.md },
  stat: { flex: 1, gap: spacing.xs },
  statLabel: { color: colors.textDim, fontSize: font.small },
  statValue: { color: colors.text, fontSize: font.h3, fontWeight: '700' },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md },
  tile: {
    width: '47%',
    flexGrow: 1,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    padding: spacing.lg,
    gap: spacing.xs,
  },
  tileDisabled: { opacity: 0.6 },
  tileIcon: { fontSize: 28 },
  tileTitle: { color: colors.text, fontSize: font.body, fontWeight: '700' },
  tileDesc: { color: colors.textDim, fontSize: font.small },
})
