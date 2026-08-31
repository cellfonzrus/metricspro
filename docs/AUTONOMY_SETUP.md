# Autonomous-session setup

**Purpose.** This lists the environment variables and access to define in a Claude Code environment so the
agent can work **autonomously** — read live data, act on the running app, and (optionally) apply database
changes directly — instead of round-tripping every diagnostic ("which days are empty?", "did it import?",
"is the price 0?") back to a human.

> **Never paste a secret into the chat.** Define these as **environment variables in the environment's
> config** (Claude Code → environment settings). The agent reads them from the process env, exactly like the
> backend does. The chat transcript is not a safe place for credentials.

---

## Merge policy (in effect: **Option B**)

The agent follows **Option B** for its own PRs:

- **Backend / data / infra / docs fixes** → the agent **merges automatically once CI is green** (Vercel +
  the `lineage-guard` check).
- **User-facing UI changes** (anything a tenant sees — pages, components, layout, copy) → the agent
  **opens the PR and asks first**, linking the Vercel preview, and merges only on explicit approval.

This keeps the plumbing flowing without babysitting, while the things worth eyeballing stay under review.
To change this, edit this section and tell the agent.

---

## Tier 1 — unlocks real autonomy

### 1. Read-only database (diagnostics)
The single highest-leverage grant. Lets the agent self-serve per-day coverage, freshness, "did this file
import", stored values (e.g. confirming a `$`-parse bug by seeing `ext_price = 0`), and the
`commcalc.email_processed` history.

| Variable | Value |
|---|---|
| `DATABASE_URL` | A **read-only** Postgres connection string to Supabase (a role with `SELECT` only — *not* the service role). |

Create the read-only role once (Supabase → SQL Editor), then put its connection string in `DATABASE_URL`:

```sql
-- Read-only role for the autonomous agent. SELECT only, across the app's schemas.
create role claude_ro login password 'CHANGE_ME_STRONG';
grant usage on schema public, commcalc, core, notify, pos, storeops to claude_ro;
grant select on all tables in schema public, commcalc, core, notify, pos, storeops to claude_ro;
-- new tables created later are covered automatically:
alter default privileges in schema public   grant select on tables to claude_ro;
alter default privileges in schema commcalc  grant select on tables to claude_ro;
alter default privileges in schema core      grant select on tables to claude_ro;
alter default privileges in schema notify     grant select on tables to claude_ro;
alter default privileges in schema pos        grant select on tables to claude_ro;
alter default privileges in schema storeops   grant select on tables to claude_ro;
```

Connection string (Supabase pooler; use the **Session** or **Transaction** pooler host from the dashboard):

```
DATABASE_URL=postgresql://claude_ro:CHANGE_ME_STRONG@<PROJECT-REF>.pooler.supabase.com:6543/postgres?sslmode=require
```

> ⚠️ **Network policy.** Outbound in the environment goes through an HTTPS proxy; the raw Postgres port
> (5432/6543) may be **blocked** unless the environment's network policy allows the Supabase host. If a
> direct connection won't establish, use the **HTTPS route** in Tier 1 §2 instead (it rides the proxy).

### 2. Supabase over HTTPS (proxy-friendly, read)
An alternative or companion to §1 that always works through the proxy (PostgREST is HTTPS).

| Variable | Value |
|---|---|
| `SUPABASE_URL` | `https://<PROJECT-REF>.supabase.co` |
| `SUPABASE_ANON_KEY` | The project's **anon** (read) key, or a restricted read key. |

### 3. Backend API (so the agent can ACT, not just read)
Lets the agent trigger **Run now**, read the **processed list** and **freshness** endpoints, re-sweep, and
verify a fix end-to-end — the clicks a human otherwise does.

| Variable | Value |
|---|---|
| `API_BASE_URL` | The deployed FastAPI base URL (Railway). |
| `API_AUTH_TOKEN` | A long-lived **admin token scoped to the tenant** (LuxeLink), used as `Authorization: Bearer …`. |

---

## The direct-write option (full autonomy on schema + data)

Add this when you want the agent to **apply database changes directly on Supabase** — run migrations and
data fixes itself — instead of handing you SQL to paste. This is the powerful option; it is **write access
to production**, so read the safeguards.

| Variable | Value |
|---|---|
| `DATABASE_WRITE_URL` | A **write-capable** Postgres connection string (a dedicated role with DDL + DML on the app schemas), **or** |
| `SUPABASE_SERVICE_ROLE_KEY` | The Supabase **service-role** key (full access over HTTPS/PostgREST). |

A dedicated write role (preferred over the blanket service role — scoped and revocable):

```sql
create role claude_rw login password 'CHANGE_ME_STRONG';
grant usage, create on schema public, commcalc, core, notify, pos, storeops to claude_rw;
grant all on all tables    in schema public, commcalc, core, notify, pos, storeops to claude_rw;
grant all on all sequences in schema public, commcalc, core, notify, pos, storeops to claude_rw;
alter default privileges in schema commcalc grant all on tables to claude_rw;   -- repeat per schema as needed
```

```
DATABASE_WRITE_URL=postgresql://claude_rw:CHANGE_ME_STRONG@<PROJECT-REF>.pooler.supabase.com:5432/postgres?sslmode=require
```

**Safeguards the agent follows when write access is present:**
1. **Every schema change is a file first.** DDL is written as a numbered, idempotent migration in
   `database/migrations/NNN_*.sql` (with a `-- REVERT:` line) and committed — so it is reviewable and
   repeatable — *then* applied. No unfiled, ad-hoc DDL against production.
2. **Idempotent + guarded.** `create … if not exists`, `add column if not exists`, `on conflict …`, and
   presence-probes — matching the repo's existing migration style — so a re-run is a no-op.
3. **Data writes name their scope and are reversible/bounded.** No unscoped `delete`/`update`; always
   `where org_id = …` (multi-tenant rule) and a stated row-count expectation.
4. **Read before write.** The agent inspects the target (counts, sample rows) before a destructive change,
   and reports what it changed.
5. **Under Option B**, applying a migration is a "data/infra fix" → it may proceed once its PR is green;
   but a **destructive** or **irreversible** change (dropping a column/table, a broad data rewrite) is
   surfaced for explicit approval regardless.

---

## Tier 2 — nice to have

| Variable | Unlocks |
|---|---|
| `INGEST_IMAP_HOST` / `INGEST_IMAP_USER` / `INGEST_IMAP_PASSWORD` | Read-only view of the **b2b ingestion mailbox** — only needed if the Backend API (Tier 1 §3) is *not* provided; the API's Test-connection/processed endpoints already expose what's in the mailbox. Sensitive — skip if §3 covers you. |
| `RAILWAY_TOKEN` | **Backend deploy visibility.** The agent already sees Vercel (frontend) via PR checks but has no Railway (backend) deploy signal; this lets it confirm a backend fix is actually live before reporting done. |

---

## Already working — no action needed
- **GitHub**: push, PR, and the `lineage-guard` CI check are all wired; branch/PR flow is established.
- **Repo / code exploration**: the agent has full repo access.

---

## Quick reference: what each capability removes as a human step

| You had to… | With… | The agent does it |
|---|---|---|
| Run per-day / freshness SQL and paste results | `DATABASE_URL` or `SUPABASE_URL`+key | Queries directly |
| Click **Run now** / read the processed list | `API_BASE_URL` + `API_AUTH_TOKEN` | Triggers and reads it |
| Paste a migration into the SQL Editor | `DATABASE_WRITE_URL` / service key | Files it, then applies it |
| Confirm a backend deploy is live | `RAILWAY_TOKEN` | Checks the deploy |
