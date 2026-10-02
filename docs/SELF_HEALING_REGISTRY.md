# Self-healing registry — what watches the platform, what it repairs, and what it cannot

Owner directive 2026-10-02: *"Anything which is automated in the system should have this mechanism
already built in and the index and the registry updated with what mechanisms are in place to keep
the system working healthy. All other bugs which show the system is not healthy should be addressed
in a similar fashion — start with the cameras."*

This is the inventory. It exists because the platform had most of this machinery already and the
cameras were simply never plugged into it — which is a failure mode a list like this is supposed to
make obvious before a customer finds it.

---

## The shape every mechanism here follows

```
auto-check  →  auto-fix what is genuinely fixable  →  RE-CHECK  →  escalate only what survived
```

Established by `docs/DATA_HEALTH_MONITOR.md` and reused rather than reinvented. Four rules travel
with it, each learned from a specific failure:

| Rule | Why | Where it was learned |
|---|---|---|
| **A watchdog may not depend on a human click.** It schedules itself on every boot. | mig 241 shipped a cron as a commented-out block for someone to paste; nothing proves anyone did. mig 411's "daily 6am" sweep had no job at all. | mig 950 / 971 |
| **Re-check before escalating.** | Alerting off the first look reports problems the fix just solved; skipping the alert because a fix ran hides the ones it did not. | DATA_HEALTH_MONITOR |
| **Never claim a repair you cannot make.** | A fix that cannot address the cause reports a repair that never happened. | vision `events_stopped` (2026-10-02) |
| **A delivered-to-nobody alert is not "already alerted".** | The quietest failure was also the stickiest. | `_send_alert`, 2026-09-20 |

**Operational self-healing ≠ the auto-fix pipeline.** Re-pulling a feed, retrying a token or
re-listing devices is operational and runs unattended. Changing *code* goes through
`fix_pipeline.py`, whose Phase-1 rule is absolute: nothing it does can deploy anything.

---

## Where it surfaces

| Surface | What it answers |
|---|---|
| **Super-admin control box** (index §20) | Is the platform working? What is red, **what is not being watched at all**, did the daily check actually run? |
| **Admin attention popup** | Per-tenant items that must *clear* when the thing is fixed. |
| **Daily system check** (mig 971) | Hourly tick, per-org cadence; walks every org's lamps. Re-registers its own cron on every boot, and **carries a lamp about its own last run**, so a stopped watchman goes red about itself instead of leaving yesterday's green on screen. |

**How a module joins.** One line — `register_provider(...)` — and it gains both an attention item
and a control-box lamp, with no migration and no change to the control box
(`control_box_api._provider_specs` reads the live provider list). A module that is not in this
registry is almost certainly not registered.

---

## The register

### Cameras / Vision — *added 2026-10-02, this is the one that was missing*

| | |
|---|---|
| **Detects** | `app/modules/vision/health.py` — Google grant dead vs transient, events stopped, entrances with no counting line, cameras unassigned, store PC offline, setup never finished |
| **Cadence** | Daily, via the existing system check (detection) + `vision-health-run-due` 07:40 UTC (repair) |
| **Auto-fixes** | `retry_token` (a 5xx/timeout refresh — asking again *is* the fix) · `resync_devices` (the real repair when a camera was renamed or re-homed in the Google Home app) |
| **Escalates** | Re-authorising Google · publishing the OAuth consent screen · powering on a store PC · drawing a counting line. None may be done on somebody's behalf. |
| **Headline diagnosis** | A grant that was minted and died inside 8 days is **the consent screen still being in Testing** — Google expires test-mode refresh tokens weekly. Distinguished from a genuine revocation by the gap between `token_issued_at` and `last_error_at`, because the generic message sends people hunting a Google outage every seven days forever. |
| **Proof** | `harness_vision_health.py` — 59 checks, mutation-tested |
| **Code** | `health.py` · `attention.py` · `router.py` (`/vision/health`, `/vision/health/run-due`) · mig `1034` |

### Data freshness (commission feeds)

Auto-check after each daily email sweep; **the sweep that just ran is the re-pull**; escalates a
feed still stale afterwards, distinguishing *the report email stopped arriving* from *the file
arrives but its content is stale*. Also detects the commonest real cause — an email that arrived and
matched no import rule because the report was renamed at source. See `DATA_HEALTH_MONITOR.md`.

### Scheduled ingest (email · portal pulls · epay · dlar · vip · b2b · Google reviews · account recompute)

Each carries a **heartbeat lamp measured from its own last *successful work*, not from whether a
cron entry exists** — a registered job that produces nothing is exactly the failure mig 950 found by
accident. Self-registering crons; late is amber, properly overdue is red.

### The daily system check itself

Self-scheduling (mig 971), per-org cadence, and it watches itself.

---

## Not covered — stated plainly, because a registry that lists only successes is marketing

| Gap | Consequence | Status |
|---|---|---|
| **The analyzer reports no achieved frame rate.** `detect_fps` is a single global flag. | An overloaded store PC degrades silently — 6/s → 5.4 → 4.4 — and nothing says so. The counts just drift low. | Open. Raised 2026-08-29. |
| **CI runs no tests.** `security.yml` is the only PR workflow and every scan step is `continue-on-error` by design. | ~1,100 harness checks run on a developer's machine and nowhere else. A regression reaches `main` unopposed. | Open. |
| **`prove_deposit_recon_nav.mjs` is red on `main`.** A spent PR-scoped snapshot that now compares `main` against itself. | A permanently-red proof trains people to ignore red proofs. | Open — needs retiring or generalising. |
| **Pub/Sub push health cannot be repaired from here.** The subscription lives in the customer's own Google Cloud. | Detected and escalated; never auto-fixed. | By design, not a gap to close. |

---

## Adding the next one

1. Write the decision as a **pure function** — `(snapshot, now) → findings` — and prove it offline.
   The judgement is what fails silently, so the judgement is what gets a harness.
2. `register_provider(...)` for the lamp and the popup item. Each finding needs a deep link that
   makes *that* item disappear when the action is completed.
3. Only then consider a repair pass, and keep its action list short, idempotent and cheap. If a fix
   cannot address the cause, do not run it — escalate.
4. Self-schedule from the boot hook. Never ship a cron for a human to paste.
5. Add a row above, including an honest line in **Not covered** if something is left.
