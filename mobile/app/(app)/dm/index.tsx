import React from 'react'
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native'
import { useRouter } from 'expo-router'

import { useAuth } from '@/auth/AuthContext'
import { atLeast, scopeOf } from '@/lib/scope'
import { Body, EmptyState, Screen } from '@/components/ui'
import { colors, font, radius, spacing } from '@/theme'

// DM tools hub — the district-manager work: sign off each store's daily closing, and run a store-visit
// inspection checklist. Gated to market/company scope (DMs + admins).
const TOOLS = [
  { slug: 'verify', route: '/dm/verify', icon: '✅', title: 'DM Verify', sub: "Review and sign off each store's daily closing." },
  { slug: 'checklist', route: '/dm/checklist', icon: '📋', title: 'DM Checklist', sub: 'Run a store-visit inspection: check in, inspect, submit.' },
]

export default function DmHub() {
  const { me } = useAuth()
  const router = useRouter()
  if (!atLeast(scopeOf(me), 'market')) {
    return <Screen><EmptyState title="Not available" subtitle="DM tools are for district managers and admins." /></Screen>
  }
  return (
    <Screen>
      <ScrollView contentContainerStyle={styles.c}>
        <Body dim>District-manager tools.</Body>
        {TOOLS.map((t) => (
          <Pressable key={t.slug} style={styles.card} onPress={() => router.push(t.route as never)}>
            <Text style={styles.icon}>{t.icon}</Text>
            <View style={{ flex: 1 }}>
              <Text style={styles.title}>{t.title}</Text>
              <Text style={styles.sub} numberOfLines={2}>{t.sub}</Text>
            </View>
            <Text style={styles.chevron}>›</Text>
          </Pressable>
        ))}
      </ScrollView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  c: { padding: spacing.lg, gap: spacing.md, paddingBottom: spacing.xxl },
  card: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.md,
    backgroundColor: colors.surface, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border, padding: spacing.lg,
  },
  icon: { fontSize: 26 },
  title: { color: colors.text, fontSize: font.body, fontWeight: '700' },
  sub: { color: colors.textDim, fontSize: font.small },
  chevron: { color: colors.textDim, fontSize: 28, fontWeight: '300' },
})
