# MetricsPro — Backup, Restore & Disaster Recovery

Security Controls Spec §5, item 14. Defines recovery objectives, the restore procedure for our actual
stack, and a test cadence so "we have backups" becomes "we have *tested* backups." Pairs with
`docs/INCIDENT_RESPONSE_PLAN.md` (which references this for the recovery phase).

---

## 1. Where state actually lives

- **Supabase Postgres** — the ONLY durable state (all tenant data, auth, audit logs). This is the sole
  thing that must be backed up and restored.
- **Backend (Railway)** — stateless; rebuilt from git on deploy. No data to restore.
- **Frontend (Vercel)** — stateless; rebuilt from git on deploy.
- **Secrets** — held in Railway/Vercel/Supabase env, not in the database. Keep an offline copy of the
  critical ones (`FIELD_ENCRYPTION_KEY` especially — without it, encrypted SSN/bank columns are
  unrecoverable even from a good DB backup).

So **DR ≈ database restore + redeploy from git**. There is no application-tier state to reconstruct.

---

## 2. Recovery objectives (targets)

| Objective | Target | Depends on |
|---|---|---|
| **RPO** (max data loss) | **≤ 5 min** with Supabase PITR; **≤ 24 h** on daily-backup-only | the Supabase plan's backup tier |
| **RTO** (time to restore) | **≤ 2 h** to a working system | restore duration + redeploy |

**Action required to hit RPO ≤ 5 min:** confirm Supabase **Point-in-Time Recovery (PITR)** is enabled
on the project. Daily-backup-only leaves up to 24 h of exposure. Tracked in
`docs/SECURITY_DAILY_QUESTIONS.md`.

---

## 3. Backups — what should be running

- **Supabase automated backups** — daily on Pro; **PITR** (continuous WAL) on the add-on. Verify both
  the schedule and retention window in the Supabase dashboard (Database → Backups).
- **Offline secret escrow** — `FIELD_ENCRYPTION_KEY` / `FIELD_ENCRYPTION_KEYS`, `SUPABASE_SERVICE_KEY`,
  `NOTIFY_RUN_SECRET` stored in a password manager / secrets vault, NOT only in Railway. A DB restore
  without the encryption key leaves SSN/bank unreadable.
- **Schema in git** — `database/migrations/*.sql` is the schema of record; a fresh project can be
  rebuilt by replaying migrations if ever needed.

---

## 4. Restore procedure

### 4a. Point-in-time / snapshot restore (data loss or corruption)
1. In Supabase (Database → Backups), pick the snapshot or PITR timestamp **just before** the incident.
2. Restore into a **new project/branch first** (never overwrite the live DB blind) and validate:
   row counts on key tables, a Customer-360 lookup, a recent closing.
3. Repoint the backend: update `SUPABASE_URL` / `SUPABASE_*_KEY` in Railway to the restored project,
   or promote the restored DB per Supabase's flow.
4. Confirm `FIELD_ENCRYPTION_KEY` matches the restored data's key era (decrypt a known SSN/bank field);
   if keys rotated, ensure `FIELD_ENCRYPTION_KEYS` includes the older key.
5. Redeploy backend (Railway) and frontend (Vercel) from the intended git ref.
6. Run the post-restore checks in §5, then dial enforcement back up (see IRP §3).

### 4b. Full project loss (rebuild from scratch)
1. Create a new Supabase project.
2. Replay `database/migrations/*.sql` in order.
3. Restore data from the latest backup export.
4. Set all env/secrets (from escrow), redeploy both tiers, run §5 checks.

---

## 5. Post-restore validation checklist
- [ ] Auth works (sign in) and `/health` is green.
- [ ] Row counts on `commcalc.raw_sales`, `pos.customers`, `core.*` audit tables look sane vs. expected.
- [ ] A **decrypt check**: a known SSN/bank field reads back in clear (proves the key matches).
- [ ] A Customer-360 lookup returns sections; a recent daily closing loads.
- [ ] Access log is writing new rows; the prune job (`prune_audit_logs`) still schedules.
- [ ] Enforcement env flags are set as intended (see the security posture line at boot).

---

## 6. Test cadence
- **Quarterly restore drill:** restore the latest backup into a throwaway project, run §5, record the
  measured RPO (data-loss window) and RTO (wall-clock to validated). File anything that missed target.
- **After any schema-heavy release:** confirm the migration set replays cleanly on an empty project.
- Log each drill's date + result in `docs/SECURITY_DAILY_QUESTIONS.md` so "tested" is provable.

> Until the first drill is done, treat restore as **unverified** — that's the current state and the
> single most important gap this document exists to close.
