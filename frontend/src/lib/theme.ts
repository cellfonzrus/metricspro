// THE ONE HOME for the app's theme (index §CORE-15). The attribute, its two values, where the choice
// is kept and how it is applied all live here — nothing else in the app spells 'data-theme' or the
// storage key, so the pre-paint script, the toggle and the lock can never disagree about any of them.
//
// `data-theme` on `:root` is the convention this repo already uses (components/RunbookDoc.tsx);
// globals.css reads it. The default is DARK (owner 2026-10-21); 'light' is the way back.
export const THEME_ATTR = 'data-theme'
export const THEME_KEY = 'mp-theme'
export const THEMES = ['dark', 'light'] as const
export type Theme = (typeof THEMES)[number]
export const DEFAULT_THEME: Theme = 'dark'

export function isTheme(v: unknown): v is Theme {
  return typeof v === 'string' && (THEMES as readonly string[]).includes(v)
}

/** What the browser should render right now: the person's stored choice, else the default. */
export function storedTheme(): Theme {
  try {
    const v = window.localStorage.getItem(THEME_KEY)
    return isTheme(v) ? v : DEFAULT_THEME
  } catch {
    // A browser that refuses storage (private window, blocked site data) still gets a theme.
    return DEFAULT_THEME
  }
}

/** The theme as the DOCUMENT currently has it — which is what the pre-paint script already applied,
 *  so a reader never has to re-derive it from storage and never disagrees with what is on screen. */
export function currentTheme(): Theme {
  const v = document.documentElement.getAttribute(THEME_ATTR)
  return isTheme(v) ? v : DEFAULT_THEME
}

// A tiny store so a component can READ the theme during render (useSyncExternalStore) rather than
// setting state from an effect, which paints once with the wrong answer and is lint debt besides.
const listeners = new Set<() => void>()
export function subscribeTheme(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn) } }
/** On the server there is no document; the document is sent carrying the default, so that is the
 *  honest server answer and hydration matches. */
export function serverTheme(): Theme { return DEFAULT_THEME }

export function applyTheme(t: Theme) {
  document.documentElement.setAttribute(THEME_ATTR, t)
  try { window.localStorage.setItem(THEME_KEY, t) } catch { /* a refused write is not a broken page */ }
  listeners.forEach(fn => fn())
}

/** Runs before the first paint, inlined in the document head — see app/layout.tsx. A theme applied
 *  by an effect would paint the default first, which on this app is a white flash before black. */
export const PRE_PAINT = `(function(){try{var t=localStorage.getItem('${THEME_KEY}');` +
  `document.documentElement.setAttribute('${THEME_ATTR}',t==='light'||t==='dark'?t:'${DEFAULT_THEME}')}` +
  `catch(e){document.documentElement.setAttribute('${THEME_ATTR}','${DEFAULT_THEME}')}})()`
