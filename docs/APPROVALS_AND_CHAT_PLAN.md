# Unified Approvals Engine + Internal Chat

Owner directive 2026-08-19. Two linked platform features:

1. **Unified Approvals Engine** — ONE system every module routes its "needs approval / needs intimation"
   requests through, replacing the ~20 bespoke approval flows scattered across modules today.
2. **Internal Chat** — a Slack/WhatsApp-class messaging system, phased to full parity, that also carries
   approvals (approve/deny a request from inside a conversation).

Owner decisions (2026-08-19): **rebuild approvals on one engine** (not just aggregate); chat to **full
parity, multi-phase**.

> **Two audiences:** the architecture/phasing (for the build) and an **Operator / Owner TODO** at the
> end (apply migrations, set secrets, enable the module).

---

## Part A — Unified Approvals Engine

### The problem it replaces
Every module grew its own approval surface with its own table, endpoint, notification (or none), and UI.
Inventory of what exists today (all become *adapters* onto the engine):

| Module | Surface (decision endpoint) |
|---|---|
| storeops | `/timeclock/permissions/{id}/decision`, `/shift-extensions/{id}/decision`, `/budget-overrides/{id}/decision`, `/payroll-chargebacks/{id}/decision`, `/action-plans/{id}/approve`, `/payroll/approvals/decide` |
| closing | `/expense/{id}/decide`, `/ops-chargebacks/decide`, `/expense/approve` |
| commcalc | `/ingest-guard/queue/{id}/decide`, `/management-incentive/payouts/{id}/decision` |
| remediation | `/requests/{id}/decision` |
| referral | `/referrals/{id}/approve` |
| hr | `/onboarding/employee/{id}/approve`, letters `/queue/{id}/approve` |

Problems: no single inbox, inconsistent RBAC, most send **no notification** (the "DM has no notifications
to approve Ali" bug — fixed tactically for timeclock, but the class remains), no shared SLA/escalation,
no audit uniformity.

### The engine (one source of truth)
Two new tables in the **storeops** schema (already PostgREST-exposed, service-role-only behind FastAPI —
same placement as the helpdesk module). Org-scoped per the migration-728 tenant-scoped-FK convention.

`storeops.approval_requests`
- `id, org_id`
- `type` — e.g. `timeclock_permission`, `shift_extension`, `payroll_hours`, `budget_override`,
  `closing_expense`, `special_order_fulfillment`, `remediation`, `referral`, `hr_onboarding`, …
- `source_table, source_id` — link back to the module's own record (adapters); **UNIQUE
  (org_id, type, source_table, source_id)** makes creation idempotent.
- `title, summary, payload jsonb` — human summary + type-specific render/decision data.
- `requested_by, requested_by_name, store_code, market` — origin + scoping.
- `assignee_kind ('dm'|'market'|'admin'|'role'|'user'), assignee_employee_id, assignee_email` — who decides.
- `status ('pending'|'approved'|'denied'|'cancelled'|'expired')`, `priority ('normal'|'high'|'urgent')`.
- `decision, decided_by, decided_by_name, decided_at, decision_note`.
- `due_at, escalated, escalated_at` — SLA + escalation.
- `chat_message_id` — link to the chat card, when posted (Part B integration).
- `created_at, updated_at`, `UNIQUE (org_id, id)`.

`storeops.approval_events` — immutable audit: `id, org_id, request_id, actor, event_type, detail jsonb, created_at`.

### The registry (how modules plug in)
Mirrors the existing `core.import_health.register_provider` decorator pattern. Each approval **type**
registers:
- `on_decide(request, decision, actor, note) -> None` — performs the module's REAL effect (e.g. stamping
  held timeclock hours, extending a clock-out, releasing a payroll batch). This is where the existing,
  tested per-module logic moves to / is called from — so the engine owns the *lifecycle* while the module
  keeps owning the *effect*.
- `renderer(request) -> dict` (optional) — a normalized card for the inbox + chat.
- `approver_predicate(caller_ctx, request) -> bool` — may this caller decide this request (on top of the
  engine's default scope check).

`approvals/engine.py`: `create_request(...)` (idempotent upsert), `decide(request_id, decision, actor,
note)` (guards pending → dispatches `on_decide` → stamps status + writes an `approval_events` row →
notifies + updates the chat card), `expire_due()` (SLA sweep), `escalate_overdue()`.

### Endpoints (`/api/v1/approvals`)
- `GET /approvals?status=&type=&scope=` — the caller's inbox, store/market-scoped via the same
  `scope_keyset` machinery every storeops read uses.
- `GET /approvals/summary` — counts by status/type for the nav badge.
- `GET /approvals/{id}` — detail (payload + events).
- `POST /approvals/{id}/decision` — `{decision:'approve'|'deny', note?}`; RBAC-gated to eligible approvers.
- `POST /approvals` — create a generic/manual request (also the door chat uses).

### Notification
Reuses the fire-and-forget email path built for timeclock (`_notify_permission_approvers` →
generalized to `notify_approvers(request)`), plus the in-app inbox badge, plus (Part B) a chat card in
the approver's DM/channel. One notification policy for every type.

### Migration strategy (incremental, low-regression)
1. **Engine + inbox + one pilot** (this PR): build the tables/engine/endpoints and migrate **timeclock
   permissions** as the reference adapter — its create path also writes an `approval_request`; its decision
   flows through `approvals.decide`. Ship the inbox UI.
2. **Adapter per surface**, one at a time, each behind its own commit + harness: shift extensions →
   budget overrides → payroll → closing expenses → remediation → referral → HR → commcalc. Each module's
   existing endpoint becomes a thin wrapper that calls `approvals.decide` (back-compat preserved).
3. **Deprecate** the bespoke tables only after every reader is on the engine (a later cleanup).

### RBAC
A new `approvals_view` / `approvals_decide` permission family, but defaults derive from existing scope:
a store/market approver sees their span; admins see all. Deciding requires being an eligible approver for
the request's scope (engine default) AND the type's `approver_predicate`.

---

## Part B — Internal Chat (full parity, phased)

A Slack/WhatsApp-class messenger. Storage + business logic in **storeops** schema behind FastAPI (service
role); realtime via **Supabase Realtime** (already in the stack — supabase-js client, CSP allows
`wss://*.supabase.co`).

### Data model (`storeops.chat_*`)
- `chat_channels` — `id, org_id, kind ('channel'|'dm'|'group'), name, topic, is_private, created_by,
  created_at`. A DM is a 2-member channel; a group DM is >2.
- `chat_members` — `channel_id, org_id, employee_id, role ('owner'|'member'), last_read_at, muted,
  joined_at`. Unread = messages after `last_read_at`.
- `chat_messages` — `id, org_id, channel_id, sender_employee_id, body, kind ('text'|'system'|'approval'),
  reply_to_id (threads), attachments jsonb, edited_at, deleted_at, created_at`.
- `chat_reactions` — `message_id, org_id, employee_id, emoji`.
- `chat_reads` / `last_read_at` on membership — read receipts + unread counts.
- Approval integration: a message with `kind='approval'` carries `approval_request_id`; approve/deny in
  the thread calls `approvals.decide`, and the card updates in place.

### Realtime transport
The app never talks to the DB directly (everything is service-role behind FastAPI), so chat uses
**Supabase Realtime Broadcast** driven from the backend: on message insert the API broadcasts to a
per-channel topic; clients subscribe with their authenticated socket. Presence (online/typing) rides the
same channel's presence API. (A REST short-poll fallback exists for the first cut and when sockets drop.)

### Phases
- **Phase 1 — core messaging (foundation):** channels + 1:1 DMs, send/fetch, unread counts, @mentions,
  the members model, and the REST layer. Realtime broadcast wired; typing/presence stubbed.
- **Phase 2 — rich messaging:** reactions, threaded replies, file/image attachments (reuse the
  ticket-attachments bucket pattern), edit/delete, presence + typing indicators.
- **Phase 3 — approvals-in-chat:** post an `approval_request` as a card into a channel/DM; approve/deny
  inline; the engine's notifications route to chat.
- **Phase 4 — search + org management:** full-text message search, channel browser, group management,
  admin retention/controls.
- **Phase 5 — voice/video + mobile push:** calls (WebRTC), and push notifications to the phone.

### RBAC / scope
Membership is the gate: you see only channels you're a member of; DMs are implicitly private. Org admins
can be given a `chat_admin` permission for moderation/retention. Everything is org-scoped.

---

## Cross-cutting
- Both features live in `storeops.*` (exposed, service-role-only) — no new PostgREST schema to provision.
- One notification policy (email + in-app badge + chat card) shared by approvals and chat mentions.
- Nav: a top-level **Approvals** inbox (with a pending badge) and **Chat**.

---

## Status
- **Plan:** written (this doc).
- **Approvals Phase 1 (engine + inbox + timeclock pilot): DONE** — migration 867, `approvals/` module
  (engine + registry + `/approvals` API), timeclock adapter (shared effect fn), `/approvals` inbox page +
  nav. `harness_approvals_engine.py` 18/18.
- **Chat Phase 1 (core messaging foundation): DONE** — migration 868 (`chat_channels/members/messages`),
  `chat/` module (channels, DMs/groups, messages, membership, unread/read, directory), `/chat` two-pane
  page (polling) + nav. `harness_chat.py` 14/14.
- **Next:** Approvals adapters for the remaining ~19 surfaces (one per commit); Chat Phase 1b (Supabase
  Realtime broadcast to replace polling) then Phase 2 (reactions/threads/attachments/presence).
- Everything else: phased per the tables above.

## Operator / Owner TODO (desktop)
- [ ] **Apply the approvals engine migration** (adds `storeops.approval_requests` + `approval_events`).
- [ ] **Apply the chat migration** (adds `storeops.chat_*`).
- [ ] **Enable Supabase Realtime** for the chat broadcast topics (Realtime is on by default on Supabase;
      no table exposure needed for backend-driven broadcast).
- [ ] **Grant permissions:** `approvals_decide` to the roles that approve; `chat_admin` to moderators.
- [ ] Decide **SLA windows + escalation targets** per approval type (tell me and I'll wire them).
- [ ] Decide **chat retention** policy (how long messages are kept).
