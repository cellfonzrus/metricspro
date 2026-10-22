'use client'
import { useSyncExternalStore } from 'react'
import { applyTheme, currentTheme, serverTheme, subscribeTheme, type Theme } from '@/lib/theme'

/** The way back to the light app. It spells no colour and no attribute of its own — both come from
 *  lib/theme, so the button and the pre-paint script cannot drift apart.
 *
 *  The theme is READ during render from the document the pre-paint script already stamped, rather
 *  than set from an effect: an effect would render the server's default first and then correct it,
 *  which is a visible flicker on the one control whose job is to say what the theme is. */
export default function ThemeToggle() {
  const theme = useSyncExternalStore(subscribeTheme, currentTheme, serverTheme)
  const next: Theme = theme === 'dark' ? 'light' : 'dark'
  const label = next === 'light' ? 'Switch to the light theme' : 'Switch to the dark theme'
  return (
    <button className="mp-icon-btn" title={label} aria-label={label} onClick={() => applyTheme(next)}>
      {theme === 'dark' ? '☀️' : '🌙'}
    </button>
  )
}
