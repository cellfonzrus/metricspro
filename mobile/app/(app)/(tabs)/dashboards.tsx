import React from 'react'
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native'
import { useRouter } from 'expo-router'

import { useAuth } from '@/auth/AuthContext'
import { visibleDashboards, type DashboardDef } from '@/modules/dashboards'
import { Body, EmptyState, Screen } from '@/components/ui'
import { OfflineBanner } from '@/components/OfflineBanner'
import { colors, font, radius, spacing } from '@/theme'

// Dashboards hub. Every card routes to a native dashboard screen that reads the SAME live endpoint as
// its web counterpart. The list is the permission-filtered catalog (src/modules/dashboards.ts), so a
// store rep never sees a card for a company-wide financial they can't open.
export default function DashboardsHub() {
  const { me } = useAuth()
  const router = useRouter()
  const items = visibleDashboards(me)

  const groups: { key: string; label: string; items: DashboardDef[] }[] = [
    { key: 'personal', label: 'For you', items: items.filter((d) => d.group === 'personal') },
    { key: 'store', label: 'Your store', items: items.filter((d) => d.group === 'store') },
    { key: 'company', label: 'Company', items: items.filter((d) => d.group === 'company') },
  ].filter((g) => g.items.length > 0)

  return (
    <Screen>
      <OfflineBanner />
      {items.length === 0 ? (
        <EmptyState title="No dashboards yet" subtitle="Your role doesn't include reporting access." />
      ) : (
        <ScrollView contentContainerStyle={styles.container}>
          <Body dim>Live numbers, straight from your reports. Tap a dashboard to open it.</Body>
          {groups.map((g) => (
            <View key={g.key} style={{ gap: spacing.sm }}>
              <Text style={styles.groupLabel}>{g.label}</Text>
              {g.items.map((d) => (
                <Pressable key={d.slug} style={styles.card} onPress={() => router.push(d.route as never)}>
                  <Text style={styles.icon}>{d.icon}</Text>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.title}>{d.title}</Text>
                    <Text style={styles.sub} numberOfLines={2}>{d.subtitle}</Text>
                  </View>
                  <Text style={styles.chevron}>›</Text>
                </Pressable>
              ))}
            </View>
          ))}
        </ScrollView>
      )}
    </Screen>
  )
}

const styles = StyleSheet.create({
  container: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xxl },
  groupLabel: {
    color: colors.textDim, fontSize: font.tiny, fontWeight: '800',
    letterSpacing: 0.6, textTransform: 'uppercase',
  },
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    padding: spacing.lg,
  },
  icon: { fontSize: 26 },
  title: { color: colors.text, fontSize: font.body, fontWeight: '700' },
  sub: { color: colors.textDim, fontSize: font.small },
  chevron: { color: colors.textDim, fontSize: 28, fontWeight: '300' },
})
