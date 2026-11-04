import { api, apiUpload } from './client'

// ── My HR (phone) ────────────────────────────────────────────────────────────────────────────────
// ONLY the `/hr/onboarding/me*` family. That matters, so it is stated rather than assumed:
// `GET /hr/onboarding/employee/{employee_id}` is the HR-SIDE read of the same record, it takes no
// `authorization` at all, and it returns the untrimmed payload including sensitive-field plumbing.
// A phone screen built on it would let any signed-in person read somebody else's record by knowing
// an employee id. The `/me` routes resolve the person FROM THE TOKEN (`_me_from_token`) and the
// server trims the payload for a portal (`_onboarding_bundle`), so there is no id to pass and
// nothing to get wrong. That trim is also why the sensitive values never arrive — see
// `lib/hr-intake` for the third state that creates.
const BASE = '/api/v1/hr/onboarding/me'
const enc = encodeURIComponent

export type MyOnboarding = {
  ok?: boolean
  ready?: boolean
  employee_id?: string
  has_profile?: boolean
  first_name?: string
  work_state?: string | null
  needs_work_state?: boolean
  workflow_status?: string
  intake_fields?: {
    key: string; label?: string; section?: string; field_type?: string; options?: unknown
    required?: boolean; sensitive?: boolean; help_text?: string
  }[]
  intake_values?: Record<string, unknown>
  intake_submitted?: boolean
  categories?: {
    key: string; label?: string
    tasks?: {
      id: string; label?: string; description?: string; status?: string
      requires_upload?: boolean; requires_signature?: boolean; has_document?: boolean
      documents?: { id?: string; name?: string; uploaded_at?: string; employee_can_delete?: boolean }[]
      template_name?: string; template_url?: string
      returned_reason?: string; missing_fields?: unknown
      is_mandatory?: boolean; work_auth?: boolean
    }[]
  }[]
  progress?: unknown
  mandatory_progress?: unknown
  states?: unknown
  work_auth_pending?: boolean
  work_auth_notice?: string
  tenant_config?: Record<string, unknown>
  [k: string]: unknown
}

export function getMyOnboarding() {
  return api.get<MyOnboarding>(BASE)
}

/** Choosing the work state is what makes the state-specific tax forms appear at all, so the screen
 *  asks for it first when `needs_work_state` is set. */
export function setMyWorkState(work_state: string) {
  return api.post<{ ok?: boolean; work_state?: string }>(`${BASE}/state`, { work_state })
}

/** Save the intake answers. Only the fields the TENANT declared are ever sent, and a sensitive one
 *  is sent only when the person typed it this time — there is nothing stored to resend. */
export function saveMyIntake(values: Record<string, string>) {
  return api.post<Record<string, unknown>>(`${BASE}/intake`, values)
}

export function myDocumentUrl(task_id: string, file_id: string) {
  return api.get<{ url?: string; [k: string]: unknown }>(
    `${BASE}/task/${enc(task_id)}/document/${enc(file_id)}`)
}

/** Delete a file the rep uploaded. The server enforces the rule; the screen offers the action only
 *  where the server's own `employee_can_delete` flag says so (see `lib/hr-intake`). */
export function deleteMyDocument(task_id: string, file_id: string) {
  return api.del<Record<string, unknown>>(
    `${BASE}/task/${enc(task_id)}/document/${enc(file_id)}`)
}

/** Upload a document against one checklist task. Multipart: the route takes `task_id` as a form
 *  field and `file` as the upload, and refuses a task whose `owner_role` is not `employee`. */
export function uploadMyDocument(
  task_id: string,
  file: { uri: string; name: string; mimeType?: string },
) {
  const form = new FormData()
  form.append('task_id', task_id)
  // React Native's FormData takes this {uri, name, type} shape for a local file.
  form.append('file', {
    uri: file.uri,
    name: file.name,
    type: file.mimeType || 'application/octet-stream',
  } as unknown as Blob)
  return apiUpload<Record<string, unknown>>(`${BASE}/upload`, form)
}
