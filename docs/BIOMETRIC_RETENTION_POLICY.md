# Biometric Data (Face Descriptor) Collection, Retention & Destruction Policy

**Issued by:** **IT Solutions of LI Inc**, which develops and operates the MetricsPro platform and
is the party in possession of any biometric data held through it.
**Applies to:** the StoreOps time-clock face-recognition feature, and to each company that uses it
through MetricsPro. As of the effective date those tenants are **Cellfonz R Us** (the first tenant),
**Luxelink Wireless LLC**, and **Vzone**. Each is a separate employer, is a CUSTOMER of the platform
rather than its operator, and adopts this schedule as its own written policy for its own employees;
where this document says "the Company," a reader should understand it to mean the employing company
whose employee's data is in question. A company added to the platform later adopts this policy on the
date it enables the feature.

> **On the two roles.** Biometric law reaches both the party that POSSESSES the data (IT Solutions of
> LI Inc, as operator) and the EMPLOYER that collects it from its own staff (the tenant). This
> document is the operator's written schedule and the template each employing tenant adopts as its
> own — it is not a claim that the operator employs anyone's staff. The public version is published
> at the MetricsPro website's Biometric Data Policy page, which is what satisfies the
> "make available to the public" requirement.
**Effective:** 2026-08-09. **Next review:** on any change to the retention schedule, the collection
method, or applicable law — and in any event within 12 months.
**Status of face recognition today:** OFF for every company on the platform (migration 420,
2026-08-09). This policy governs the descriptors already on file from before that change, and any
future re-enablement.

> **No employee action is required, and none is being requested, by the adoption of this policy.**
> Publishing this schedule does not itself notify, email, prompt, or change what any employee sees.
> The consent process described in §3 applies only when a company re-enables face recognition; until
> then it is a statement of what will happen, not something in motion.

This document is the written retention schedule required by biometric privacy law (most notably the
Illinois Biometric Information Privacy Act, 740 ILCS 14/1 et seq., "BIPA") and is intended to be read
on its own — without reference to source code — by counsel, an auditor, or a regulator. Section 6 maps
each commitment made here to the exact code that enforces it, for engineering reference only.

This is a policy document, not legal advice. It should be reviewed by an employment/privacy attorney,
particularly for any tenant with employees in Illinois, Texas, Washington, or any other jurisdiction
with a biometric privacy statute.

---

## 1. What is collected

- **A face descriptor**: a 128-number mathematical vector produced by client-side face-recognition
  software (face-api.js) from a photograph captured at kiosk clock-in. This vector is a *geometric
  summary* of facial features — it cannot be used to reconstruct a photograph of the person, and it is
  not, itself, a "photo" or "image."
- **The Company does not store photographs for the purpose of face matching.** A separate, always-on
  "selfie" photo is stored at every clock-in/out event as a timekeeping and safety record (fraud
  prevention, "who actually punched this clock"); that photo is a distinct, non-biometric record kept
  under the Company's ordinary timekeeping retention practice and is out of scope for this policy.
- The descriptor is stored per employee, one row per person, in a dedicated database table
  (`storeops.face_descriptors`) that is not shared with, or readable by, any other part of the product.

## 2. Why it is collected

The sole purpose is **employee identity verification at clock-in**, to prevent "buddy punching" (one
employee clocking in for another) at kiosk time clocks. It is not used for surveillance, marketing,
analytics, performance evaluation, or any purpose other than the clock-in match itself, and it is never
shared with a third party or used to train any external system (see §5, "Never shared").

A non-biometric alternative — a selfie-only clock-in with no face matching — is always available and is,
as of 2026-08-09, the **only** active method for every tenant (§3).

## 3. Consent

No face descriptor is captured without the employee's prior written consent, which discloses: that
biometric data is being collected, the specific purpose (clock-in identity verification), and this
retention schedule. Consent is recorded per employee with a status (`signed` / `declined`) and a
timestamp, and a declined or withdrawn consent takes precedence over every other setting — an employee
who has declined is never face-matched, regardless of any tenant-level configuration.

Where face recognition is re-enabled for a tenant going forward, every employee with no consent record
on file is treated, at the moment of re-enablement, as newly needing that disclosure and a fresh consent
decision before their template is used again — this is a deliberate, dated, auditable event, not a
silent carry-over.

## 4. Retention schedule — when a descriptor is destroyed

The Company destroys each employee's face descriptor at the **earliest** of the following events
("whichever occurs first," per 740 ILCS 14/15(a)):

| # | Trigger | Timing |
|---|---|---|
| 1 | **Purpose satisfied** — the employee's employment ends | **90 calendar days** after the employee's last day of employment (tenant-configurable; see §4a) |
| 2 | **Employee request** | **Immediately**, on request (see §5) |
| 3 | **Tenant opts to purge on disable** | **Immediately**, if/when a tenant both (a) turns face recognition off and (b) has opted into "purge on disable" (see §4b) |
| 4 | **Statutory backstop** | **1,095 days (3 years)** after the employee's last interaction with their own biometric template (enrollment, re-enrollment, or a clock-in that was actually verified by face match) — applies regardless of whether a termination date is ever recorded |

**#4 is an absolute ceiling and is never configurable by any tenant.** It is what BIPA requires when an
employment relationship's end is never formally recorded, or is recorded later than the statute's own
outer bound would allow.

### 4a. Why 90 days, not 12 months

An industry-common figure for this kind of retention window is 12 months. That is not the correct
reading here: the purpose for which the descriptor was collected — verifying a *currently employed*
person at clock-in — is fully satisfied on that person's last working day. Under the "whichever occurs
first" standard, the purpose-satisfied trigger controls the moment it is reached, regardless of any
longer calendar period a business might otherwise prefer. Ninety days is short enough to be clearly
purpose-bound (no plausible business need for face-clock-in identity verification exists past that
point) while long enough to cover the practical realities of a rehire within the same quarter or a
disputed/late-finalized last punch. Each tenant may configure a different figure to fit its own HR
process, from a minimum of 1 day up to the statutory ceiling of 1,095 days (§4, trigger 4) — the figure
is never silently widened by the Company.

### 4b. Disabling face recognition does not, by itself, destroy existing descriptors

When a tenant turns face recognition off, its already-enrolled descriptors are, by default, **kept**
(not destroyed) — this lets the tenant turn the feature back on later without asking every employee to
re-enroll. A tenant that instead wants the stronger posture of destroying every descriptor the moment
the feature goes off may opt into that behavior; when it does, the purge happens immediately and applies
to every enrolled descriptor for that tenant, without waiting for any individual employee's date-based
window.

## 5. The right to demand destruction

Under BIPA and comparable statutes, an employee (or a former employee) may demand that their biometric
data be destroyed. Any such request — made in writing, by email, verbally to HR and then documented, or
through any other channel — is honored **immediately**, independent of the schedule in §4: HR/an admin
processes the request through the retention tooling, which deletes the stored descriptor and records the
request (date, channel, requester) in the same audit trail described in §7. This path never waits for a
scheduled sweep and is not conditioned on the employee's employment status.

## 6. How destruction is carried out

Destruction is a hard delete of the stored descriptor row — not a soft-delete flag, not an
archive/cold-storage move. Once destroyed, the vector cannot be recovered by the Company, by the
employee, or by any support process; re-enabling face recognition for that person afterward requires a
fresh enrollment (a new photo, producing a new descriptor).

Two paths carry out the same schedule, using the same evaluation logic:

- **On demand.** An administrator can, at any time, run a **preview** that lists exactly which
  descriptors are currently due and under which of the four triggers — and then apply it. Destruction
  is always preceded by that preview; nothing is destroyed by a single unconfirmed click.
- **On a daily schedule.** An automated sweep evaluates every enrolled descriptor, for every company,
  against the §4 schedule and destroys whatever is due. **This sweep is built and is enabled by the
  platform operator; as of the effective date of this policy the daily schedule has not yet been
  switched on**, and destruction is therefore carried out by an administrator on demand. This
  document will be updated on the date the automated schedule begins. No descriptor is currently due
  under any of the four triggers, so nothing is pending destruction at the time of writing.

*Engineering reference (not part of the policy commitment itself): the schedule is implemented in
`backend/app/modules/storeops/face_retention.py`, the per-tenant configuration in migration
`database/migrations/422_storeops_face_retention.sql` (`storeops.tenants.face_retention_days`,
clamped to `[1, 1095]`; `storeops.tenants.face_recognition_purge_on_disable`), the scheduled sweep at
`POST /storeops/timeclock/face-retention/run-due` (invoked by the platform's existing daily job
scheduler), the on-demand preview/apply at `POST /storeops/timeclock/face-retention/run`, and the
employee-request path at `POST /storeops/employees/{id}/face-retention/request-deletion`. Every read and
write in this module is scoped to the tenant that owns the data — no cross-tenant query exists anywhere
in the retention job.*

## 7. How destruction is evidenced

Every destruction — however triggered — is recorded in a permanent audit log
(`storeops.face_retention_log`) **before** the underlying descriptor is gone from operational use, and
the log entry survives the descriptor's deletion. Each entry records: which employee, which trigger
fired (purpose-satisfied / employee-request / tenant-purge-on-disable / statutory-backstop), the dates
that decided it (termination date and/or last-interaction date, as applicable), who or what performed
the destruction (a named administrator, or the automated scheduled job), and when. **The audit log never
stores the biometric descriptor itself** — only these metadata fields — so the evidentiary record
carries no biometric data of its own and creates no new BIPA exposure by existing.

This log is what lets the Company answer, years later, "did you destroy this person's biometric data,
and when" — a question BIPA's private right of action makes a real and foreseeable one — with a
specific, dated, auditable answer rather than an assertion.

## 8. What is never done with this data

- Never shared with, sold to, or disclosed to any third party.
- Never used for any purpose beyond the clock-in identity verification named in the consent disclosure
  (§3) — not analytics, not performance scoring, not marketing, not law enforcement absent valid legal
  process.
- Never fed to an external vendor or service for training or any other purpose.
- Never logged, echoed, or displayed anywhere outside the enrollment/verification flow itself — support
  tooling, error logs, and this policy's own audit trail (§7) never contain the vector.

## 9. Questions or requests

An employee who wants to know what biometric data is held about them, or who wants to exercise the
destruction right described in §5, should contact their HR representative or store manager, who will
process the request through the HR system per §5–7 above. Employees of **Cellfonz R Us** may also
direct questions to the Company at the contact details on file with HR.

---

**Issued by:** Cellfonz R Us, operator of the MetricsPro platform, on behalf of itself and each
company that adopts this schedule (§ header).
**Effective date:** 2026-08-09 · **Last updated:** 2026-08-09, alongside the deletion job that
enforces it · **Next review:** within 12 months, or immediately upon any change to the retention
schedule, the collection method, or applicable law.

*Adopting company (complete on adoption):* ______________________________
*Authorized signature / title:* ______________________________  *Date:* ____________

*This policy is a statement of the Company's practice; it is not legal advice. It should be reviewed
by an employment/privacy attorney before being relied upon, particularly for any company with
employees in Illinois, Texas, Washington, or another jurisdiction with a biometric privacy statute.*
