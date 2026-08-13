import React from 'react'
import { Text } from 'react-native'
import { Tabs } from 'expo-router'

import { useAuth } from '@/auth/AuthContext'
import { MODULES } from '@/modules/registry'
import { colors, font } from '@/theme'

// Tab bar. All tabs are declared statically (expo-router needs file routes) but each module tab is
// hidden with `href: null` when the signed-in user can't see it — so the bar reflects the registry +
// the user's permissions. Home and Settings are always present.
function icon(glyph: string) {
  return ({ focused }: { focused: boolean }) => (
    <Text style={{ fontSize: 20, opacity: focused ? 1 : 0.55 }}>{glyph}</Text>
  )
}

export default function TabsLayout() {
  const { me } = useAuth()
  const can = (key: string) => {
    const m = MODULES.find((x) => x.key === key)
    return m ? m.visible(me) : false
  }

  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: colors.surface },
        headerTintColor: colors.text,
        headerTitleStyle: { color: colors.text, fontWeight: '700' },
        tabBarStyle: { backgroundColor: colors.surface, borderTopColor: colors.border },
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.textDim,
        tabBarLabelStyle: { fontSize: font.tiny, fontWeight: '600' },
      }}
    >
      <Tabs.Screen name="index" options={{ title: 'Home', tabBarIcon: icon('🏠') }} />
      <Tabs.Screen
        name="timeclock"
        options={{ title: 'Time Clock', tabBarIcon: icon('⏱️'), href: can('timeclock') ? undefined : null }}
      />
      <Tabs.Screen
        name="pos"
        options={{ title: 'POS', tabBarIcon: icon('🛒'), href: can('pos') ? undefined : null }}
      />
      <Tabs.Screen
        name="crm"
        options={{ title: 'CRM', tabBarIcon: icon('📇'), href: can('crm') ? undefined : null }}
      />
      <Tabs.Screen name="settings" options={{ title: 'Settings', tabBarIcon: icon('⚙️') }} />
    </Tabs>
  )
}
