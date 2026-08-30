# Data-health monitor — auto-check, auto re-pull, escalate

Owner design (2026-08-28): *"What can be done to check data update by the platform itself before the users
complain — an auto check and an auto fix initiated based on the investigation."*

The platform now watches its own data freshness and self-heals, so a stalled feed is caught and (usually)
fixed before anyone looks at a stale report.

## The loop
1. **Auto-check** — after every daily email sweep (`/email-sweep/run-due`), the platform evaluates each feed's
   freshness: when it last ingested and the latest transaction date it carries. A daily mailbox sweeps once a
   day, so this runs ~once/day/tenant with no extra cron.
2. **Auto-fix** — the sweep that just ran **is** the re-pull. If a scheduled tick missed, or a matching gap was
   healed by a newer rule, the fresh sweep brings the feed current on its own.
3. **Escalate only if still behind** — a feed still stale *after* that re-pull is a genuine problem, so the
   platform sends a **once-a-day-deduped alert** (via the existing connector-alert path, `_send_alert` with a
   `ref` key) naming the feed, its latest data date, its last ingest, how many days behind, and the likely
   cause — distinguishing **"the report email stopped arriving"** (last ingest itself old) from **"the file
   arrives but its content is stale"** (last ingest recent, data old). The alert says exactly what to do.

## Surfaces
- **Executive MTD banner** (shipped earlier) warns end users on the report itself when a feed is behind.
- **Integrations → Data health** panel: live per-feed 🟢/🔴 status + latest data date, and a
  **Re-check & re-pull now** button (`POST /commcalc/data-freshness/run-now`) that runs the same
  re-pull + check on demand.

## Endpoints / functions
- `GET /commcalc/ingest-freshness` — the live per-feed report (`_data_freshness_report`).
- `POST /commcalc/data-freshness/run-now` — manual re-pull (`_run_email_sweep_all`) + monitor.
- `_data_freshness_monitor(client, org_id)` — the auto-check + escalation core; hooked into
  `email_run_due` after the sweep loop. Best-effort, never affects the sweep.

## Design notes
- **No new migration, no new cron, no new alert channel** — it piggybacks the daily sweep, the connector-alert
  path, and the freshness helpers. Reuse over rebuild.
- **Never noisy**: alerts dedupe per feed per day; a healthy tenant sees nothing and gets no mail.
- **Never fragile**: every step is best-effort and degrades to a no-op; the monitor can never break or delay
  the sweep it rides on.
- Feeds covered: Activation Details (activation basis), Bill Payment Transactions, and the Sales feed
  (`raw_sales`). Adding a feed is one line in `_data_freshness_report`.

## Unrouted-report detection (owner 2026-08-29)

The most common real cause of a frozen feed isn't "the email stopped arriving" — it's **"the email arrived
but no import rule matched it"**, usually because a report was **renamed at the source** (e.g. b2b recreating
"Sales Transaction Details" as "My Sales Transaction Details Legacy New"). Previously such an attachment was
fetched and silently dropped, and the feed just went stale.

Now:
- **The sweep records every unmatched DATA-file attachment.** `email_sweep.fetch_new_attachments` takes an
  optional `unrouted` list and appends `{message_id, name, from, subject, date}` for any `.xlsx/.xls/.csv`
  that matched no pattern.
- **It's surfaced, not swallowed.** `_run_email_sweep` persists the filenames to
  `email_sweep_config.last_unrouted` (guarded by `_table_has_column`; migration `929`) and fires a
  once-a-day-deduped connector alert (`ref = unrouted:{org}:{account}:{date}`). `_data_freshness_report`
  returns them as `unrouted[]`, and the Executive-MTD freshness banner lists them with a fix link.
- **Built-in fallback patterns** (`_BUILTIN_FEED_FALLBACK_PATTERNS`) are appended after the tenant's explicit
  rules and the custom-report auto-patterns (explicit always wins), so a renamed core report still matches:
  `*sales*transaction*details*` → `daily_sales`, `*activation*details*`, `*bill*payment*transaction*`. The
  `daily_sales` one closes the actual gap — it is a built-in upload type with no custom auto-pattern.
