import type { EntityOption } from '@/components/EntityPicker'

/** THE rep-filter option list for the pickup screens: the employee roster UNION the rep names that
 *  actually filed an envelope in the window/scope.
 *
 *  OWNER BUG REPORT 2026-09-07 ("it does not hold sort by rep"). `GET /closing/pickups` matches
 *  `employees=` EXACTLY — the picker was assumed to supply real roster names — but the picker offered
 *  ONLY `storeops.employees`, and 252 of the org's 1,729 closing rows since May carry a name in no
 *  roster ('Waleed', 'Syed 117', 'Abdul K', 'arif', 'Naima', 'Asad Umar', 'Yasir', 'David', 'Venkata
 *  Penumatcha') — usually a short form of a real person. Those reps were unpickable twice over: absent
 *  from the dropdown, and unmatched by the roster spelling of the same person. A rep you can SEE in
 *  the table can now always be picked.
 *
 *  `fromData` comes from the endpoint's `employee_options`, collected server-side BEFORE the employee
 *  filter — so choosing one rep never shrinks the list you can choose from next. The MATCH rule is
 *  unchanged (still exact), so nothing silently widens.
 *
 *  Shared by /closing/pickup and /closing/billpay-pickup: the two screens run the same parameterized
 *  pickup machinery, and two copies of this list is where they would drift. */
export function repOptions(roster: any[], fromData?: string[] | null): EntityOption[] {
  const byId = new Map<string, EntityOption>()
  for (const e of roster || []) {
    const n = (e?.name || '').trim()
    if (n) byId.set(n.toLowerCase(), { id: n, label: n, sublabel: e.email || undefined })
  }
  for (const raw of fromData || []) {
    const n = (raw || '').trim()
    // The roster spelling wins when both exist — it carries the email that disambiguates two people
    // with the same first name.
    if (n && !byId.has(n.toLowerCase())) byId.set(n.toLowerCase(), { id: n, label: n, sublabel: 'from closings' })
  }
  return [...byId.values()].sort((a, b) => a.label.localeCompare(b.label))
}
