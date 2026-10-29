'use client'
// THE ONE HOME for "how does a person pick a date in this product?" (owner ask 2026-10-29).
//
// OWNER ASK, verbatim: *"for payroll hours approval the calendor should open up instead of enetring it
// manuallu"*.
//
// THE CLASS, not the instance. The instance is the Hours Approval From/To pair. The class is that
// `<input type="date">` — all 168 of them in this tree — does carry a native calendar, but in Chromium
// it opens only from the small glyph at the right-hand edge of the field. A click anywhere else lands a
// caret in the mm/dd/yyyy segments and the person types the date, digit by digit, which is exactly what
// he was doing. So nothing was missing: typing WAS the control's default behaviour, everywhere.
//
// WHAT THIS CHANGES. The calendar opens when the field is clicked or focused, over the whole field
// rather than one glyph. Nothing else: this is the same native input, so the value is still an ISO
// `YYYY-MM-DD` string, min/max/required/disabled still work, and every caller's `value` and `onChange`
// are untouched. A page adopting it is a one-line change and nothing downstream notices.
//
// TYPING IS NEVER REMOVED, deliberately. `showPicker()` is an ADDITION. A keyboard user tabbing through
// a form, a screen-reader user who is never shown a calendar at all, and anyone who finds it faster to
// type four digits than to page a calendar back to June all still work exactly as before — so no
// `readOnly`, and no `preventDefault` on the click path, either of which would trade his problem for
// theirs.
//
// WHY `showPicker` IS GUARDED THREE WAYS. It is young API (Chrome 99, Safari 16, Firefox 101) and it
// THROWS rather than no-ops in three real situations: the browser does not implement it; the call is not
// inside a user gesture; the input is hidden or disabled. An exception escaping a React event handler
// unmounts the tree — so an older browser would get a blank payroll screen in exchange for a
// convenience. Hence: existence check, try/catch, and nothing in the catch (the native glyph and typing
// both still work, so there is no degraded state to report and nothing a user could act on).
//
// Locked by `frontend/prove_date_field.mjs`, which also RATCHETS: a NEW bare `<input type="date">` fails
// the build, and `frontend/date_field_pending.txt` — the call sites not yet converted — may only shrink.
import { useCallback } from 'react'

export type DateFieldProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'>

/** Open the native calendar for this input. Never throws — see the header for the three cases. */
function openPicker(el: HTMLInputElement | null) {
  if (!el || el.disabled || el.readOnly) return
  // `showPicker` is absent on older engines; the cast is to the one method the DOM lib may not know.
  const withPicker = el as HTMLInputElement & { showPicker?: () => void }
  if (typeof withPicker.showPicker !== 'function') return
  try {
    withPicker.showPicker()
  } catch {
    // Unsupported, not user-activated, or the field is not visible. The glyph and typing both still
    // work, so there is nothing to tell the user and nothing to fall back to.
  }
}

/**
 * A date input whose calendar opens on a click, anywhere in the field.
 *
 * A drop-in for `<input type="date" …>`: every other prop, including `value`, `onChange`, `style`,
 * `min`, `max`, `disabled` and `aria-*`, is forwarded untouched.
 */
export default function DateField({ onClick, onFocus, ...rest }: DateFieldProps) {
  // The caller's own handler runs FIRST and is never replaced — a page that opens a drawer on click
  // keeps doing so.
  const handleClick = useCallback((e: React.MouseEvent<HTMLInputElement>) => {
    onClick?.(e)
    openPicker(e.currentTarget)
  }, [onClick])

  // Focus covers the keyboard: tabbing into the field offers the calendar the same way clicking does.
  // Chromium ignores a showPicker call from a focus event that no gesture preceded, which is correct —
  // it means a programmatic focus on page load cannot pop a calendar nobody asked for.
  const handleFocus = useCallback((e: React.FocusEvent<HTMLInputElement>) => {
    onFocus?.(e)
    openPicker(e.currentTarget)
  }, [onFocus])

  return <input type="date" onClick={handleClick} onFocus={handleFocus} {...rest} />
}
