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

### Adapter migration status (2026-08-19)
Each surface becomes an adapter: its create path calls `engine.create_request` (intimation into the
inbox) and its decision path calls `engine.sync_source_decision`; a registered `on_decide` lets the
**unified inbox** perform the effect too. Two tiers by risk:

**Tier A — clean binary approve/deny, no money side-effect → migrated & tested:**
- ✅ `timeclock_permission` (pilot)
- ✅ `shift_extension`
- ✅ `budget_override`

These three share the DM-approval + pure-status-flip shape, so the inbox and the legacy board have
byte-identical effects (proven by `harness_approvals_adapters.py`).

**Tier B — money-affecting or multi-state → migrated individually WITH REVIEW (one commit each):**

*Inbox-actionable (extract-shared-effect pattern — the legacy endpoint AND the engine `on_decide` call
ONE shared effect fn; proven byte-identical by `harness_approvals_adapters.py`):*
- ✅ `closing_expense` (`/expense/{id}/decide`) — approve/reject; approving pushes the **P&L recompute**.
  Shared: `closing.router._apply_expense_line_decision`. (approve→`approved`, deny→`rejected`.)
- ✅ `referral` (`/referrals/{id}/approve`+`/reject`) — gated **commission**; commission_pending→approved
  (amount+payout) / rejected. Shared: `referral.router._apply_referral_decision`. Segregation-of-duties
  (approver ≠ referral creator) preserved for the inbox via an `approver_predicate`.
- ✅ `remediation` (`/requests/{id}/decision`) — approve runs the one bounded playbook (→executed), deny
  →rejected. Shared: the pre-existing `remediation.router._apply_decision` (also used by the WhatsApp
  webhook). Org-level → admin-only in the inbox.

*Intimation-only (state MIRRORED into the inbox; the module board KEEPS the decision — the adapter
HARD-BLOCKS any inbox decision via `approver_predicate → False` **and** an `on_decide` that raises, so a
forced decision can never silently diverge). Chosen because these are NOT a faithful single binary
approve/deny:*
- ⚠️ `payroll_hours` (`/payroll/approvals/decide`) — **two-stage** DM→HR board, per-employee batch with
  corrections/adjustments/send-back/reset. Mirror: DM-approve opens the HR-release request, HR
  approve/send-back closes it. (`payroll_approval._intimate_payroll_decision`.)
- ⚠️ `management_incentive` (`/management-incentive/payouts/{id}/decision`) — **multi-state** ledger
  (draft→approved→paid) with three actions (approve/deny/**pay**); deny reopens to draft. Mirror on save
  + approve/deny. (`commcalc.router._intimate_mi_payout` + `mi_payout_decision`.)
- ⚠️ `ingest_guard` (`/ingest-guard/queue/{id}/decide`) — binary allow/reject, BUT 'allow' needs a
  store-code **pick** (creates the alias) and **releases cross-tenant rows** into the ledger — a
  human-only action on the guard board. Mirror on record + allow/reject.
  (`ingest_store_guard._intimate_quarantine` + `router.decide_ingest_guard_item`.)

*Still on their own boards (untouched this pass):* `payroll_chargeback` (post/waive a deduction — not
approve/deny), `hr_onboarding` + letters, `action_plan` (multi-state submit→push-back→approve→done).

Rationale for the split: a wrong approve→effect mapping on a money path causes real financial errors, so
each surface is its own reviewed commit. Where the decision cannot be faithfully reduced to a single
approve/deny (two-stage, multi-state, or needing a decision-time parameter the generic inbox can't
supply), it is intimation-only and the inbox decision is hard-blocked rather than silently mis-applied.

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
- **Chat Phase 1b (realtime): DONE** — `chat/realtime.py` broadcasts a lightweight HINT to Supabase
  Realtime's HTTP broadcast API on every send (channel topic + each member's user topic); the client
  subscribes to its user topic (`GET /chat/me` resolves it) and re-fetches over REST. Polling stays as a
  fallback (4s when the socket is down, 20s safety sweep when it is up). `harness_chat.py` 18/18.
- **Chat Phase 2 (rich messaging): DONE** — migration 880 (`chat_reactions`; threads/attachments/edit
  columns already existed on `chat_messages`). Backend: toggle reactions, threaded replies (parent
  preview surfaced in `list_messages`), file/image attachments (Supabase Storage `chat-attachments`
  bucket, membership-gated signed URLs), edit (author-only) + soft-delete (author or `chat_admin`),
  reaction/edit/delete broadcasts. Frontend: hover actions, quick-emoji reactions, reply banner,
  attach + inline image thumbnails, inline edit, delete tombstones, presence ("N here") + typing
  indicators over the per-channel topic. `harness_chat.py` 30/30.
- **Chat Phase 3 (approvals-in-chat): DONE** — no new migration (reuses mig-867 `approval_requests`,
  incl. its `chat_message_id` link column). Backend: `POST /chat/channels/{id}/approvals` raises a
  request on the unified engine (`engine.create_request`, notify=False) + posts a `kind='approval'`
  card linked by `approval_request_id`; `list_messages` surfaces the LIVE linked request so the card
  always reflects current status; `POST .../messages/{mid}/decision` decides inline by REUSING the
  approvals router's own `_caller`/`_may_decide` RBAC + `engine.decide` (zero logic duplicated, engine
  internals untouched) then broadcasts. Frontend: an approve/deny card in the thread + a "request
  approval" composer action. `harness_chat.py` 36/36.
- **Chat Phase 4 (search + org management): DONE** — migration 881 (message `search_tsv` generated
  column + GIN, plus a pg_trgm index for the ILIKE path). Backend: membership-scoped message search
  (`GET /chat/search`), public-channel browser (`GET /chat/channels/browse`) + join/leave, member
  management (`GET .../members`, `DELETE .../members/{eid}` owner-or-admin), channel rename/topic/
  archive (`PATCH /chat/channels/{id}`), and a `chat_admin`-gated retention sweep
  (`POST /chat/admin/retention {days}`). `/chat/me` now reports `is_chat_admin`. Frontend: sidebar
  search, browse-channels modal, and a members/settings panel (add/remove, rename, leave, admin
  retention). `harness_chat.py` 51/51.
- **Chat Phase 5 (voice/video + mobile push): DONE (in-repo scaffolding).** Migration 882 adds
  `storeops.chat_push_tokens`. WebRTC 1:1 calling: signaling (offer/answer/ICE/bye) rides a dedicated
  Realtime broadcast topic (`chat-call:<channel>`) client-side — no server relay — with ICE servers
  from `GET /chat/call/config` (operator-supplied `CHAT_ICE_SERVERS`, defaulting to public STUN); a
  minimal call overlay (getUserMedia + RTCPeerConnection, local/remote video, accept/hang-up) lives in
  the thread header. Push: device token registry (`POST /chat/push/(un)register`) + a server send path
  (`push.py`, FCM HTTP) wired into new-message delivery — GATED on `CHAT_FCM_SERVER_KEY`, a documented
  no-op (never a fake success) until the operator supplies credentials. Frontend push registration is
  likewise env-gated on a VAPID key + a service worker. `harness_chat.py` 57/57. **Operator infra
  required for production calls/push — see Operator TODO (TURN server, FCM/APNs, VAPID + `/sw.js`).**
- **Approvals Tier-B money surfaces: DONE (this pass)** — one reviewed commit each, extending
  `harness_approvals_adapters.py` (48/48). Inbox-actionable: `closing_expense`, `referral`,
  `remediation` (shared-effect fn + `sync_source_decision`, byte-identical to the legacy board).
  Intimation-only (mirrored + inbox-decision hard-blocked): `payroll_hours`, `management_incentive`,
  `ingest_guard` — see the Tier-B table above for why each is not a faithful single approve/deny. No new
  migration needed (all reuse mig-867 `approval_requests`).
- **Remaining approvals work:** `payroll_chargeback`, `hr_onboarding` + letters, `action_plan` — still
  on their own boards (post/waive and multi-state workflows, not binary), to be handled as separate
  reviewed commits.
- Everything else: phased per the tables above.

## Operator / Owner TODO (desktop)
- [ ] **Apply the approvals engine migration** (adds `storeops.approval_requests` + `approval_events`).
- [ ] **Apply the chat migrations** (868 = `storeops.chat_*`; 880 = `storeops.chat_reactions`; 881 =
      message full-text search column/index).
- [ ] **Chat attachments bucket:** the backend auto-creates a private `chat-attachments` Supabase
      Storage bucket on first upload (same pattern as helpdesk `ticket-attachments`); no action needed
      unless your Storage policy blocks service-role bucket creation, in which case create it manually
      (private).
- [ ] **Enable Supabase Realtime** for the chat broadcast topics (Realtime is on by default on Supabase;
      no table exposure needed for backend-driven broadcast).
- [ ] **Grant permissions:** `approvals_decide` to the roles that approve; `chat_admin` to moderators.
- [ ] **Approvals inbox scope note:** `closing_expense`, `referral`, `remediation` are now decidable
      from the unified Approvals inbox (in addition to their own boards). `remediation` requests are
      org-level (admin-only in the inbox). `referral` keeps its segregation-of-duties rule (the approver
      may not be the rep who created the referral) in the inbox too. `payroll_hours`,
      `management_incentive`, and `ingest_guard` appear in the inbox as **read-only intimations** — decide
      them on their own boards (the inbox will refuse, by design, because those carry a stage/parameter
      the generic inbox can't supply). Nothing to configure; noted so the behavior isn't a surprise.
- [ ] Decide **SLA windows + escalation targets** per approval type (tell me and I'll wire them).
- [ ] Decide **chat retention** policy (how long messages are kept). The sweep is on-demand today
      (`POST /chat/admin/retention {days}`, chat-admin only / the Members panel's Admin section); wire it
      to a scheduled job if you want it automatic.
- [ ] **Voice/video calls (Phase 5) — infra:** calls work on the same network with the built-in public
      STUN default, but cross-NAT calls need a **TURN server**. Provide one by setting `CHAT_ICE_SERVERS`
      (backend env) to a JSON array of RTCIceServer objects, e.g.
      `[{"urls":"turn:turn.example.com:3478","username":"u","credential":"p"}]`. `GET /chat/call/config`
      reports `has_turn` so you can confirm.
- [ ] **Mobile/web push (Phase 5) — credentials:** the token registry + send path ship, but delivery is
      gated. Set `CHAT_FCM_SERVER_KEY` (backend env) to enable FCM sends (`configured()` gate; no key →
      documented no-op). For **web push** also set `NEXT_PUBLIC_VAPID_PUBLIC_KEY` (frontend) and ship a
      `/sw.js` service worker that shows the notification; for **native iOS** add an APNs path alongside
      the FCM one in `backend/app/modules/chat/push.py` (structured as a follow-up there). Native mobile
      apps are out of repo scope.
