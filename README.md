# MetricsPro Platform

Commission Intelligence & Business Operations Suite for Boost Mobile Retailers.

## Architecture

```
frontend/    Next.js 14 + TypeScript + Tailwind  →  Vercel
backend/     Python FastAPI                       →  Railway
database/    PostgreSQL migrations                →  Supabase Pro
```

## Modules

| Module | Path | Status |
|--------|------|--------|
| CommCalc (Commission Intelligence) | `/commcalc` | ✅ Built |
| StoreOps (Scheduling & HR) | `/storeops` | ✅ Built |
| Assets (Phone Lending) | `/assets` | 🔲 Template ready |

## Quick Start

### 1. Database Setup (Supabase)

Run migrations in order in Supabase SQL Editor:
```
database/migrations/001_core.sql
database/migrations/002_commcalc.sql
database/migrations/003_storeops.sql
```

### 2. Backend (FastAPI on Railway)

```bash
cd backend
cp .env.example .env   # fill in Supabase keys
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

API docs at: http://localhost:8000/docs

### 3. Frontend (Next.js on Vercel)

```bash
cd frontend
cp .env.example .env.local   # fill in keys
npm install
npm run dev
```

Open: http://localhost:3000

## Deployment

### Vercel (Frontend)
1. Push to GitHub
2. Connect repo to Vercel
3. Set environment variables in Vercel dashboard
4. Deploys automatically on every push to main

### Railway (Backend)
1. Connect GitHub repo to Railway
2. Set root directory to `backend/`
3. Set environment variables
4. Railway auto-detects Dockerfile and deploys

### Environment Variables

**Frontend (.env.local):**
```
NEXT_PUBLIC_SUPABASE_URL=https://[project].supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
NEXT_PUBLIC_API_URL=https://your-api.railway.app
```

**Backend (.env):**
```
SUPABASE_URL=https://[project].supabase.co
SUPABASE_KEY=your-anon-key
SUPABASE_SERVICE_KEY=your-service-role-key
APP_ENV=production
```

## Adding a New Module

1. Copy `database/migrations/004_module_template.sql` → `005_[name].sql`
2. Replace `assets` with your module name
3. Run migration in Supabase
4. Copy `backend/app/modules/_template/` → `backend/app/modules/[name]/`
5. Mount router in `backend/app/main.py`
6. Create `frontend/src/app/(platform)/[name]/` pages
7. Add to nav in `frontend/src/app/(platform)/layout.tsx`

**Zero risk to existing modules** — each module has its own schema.

## Monthly Workflow

1. Download files from EPay portal
2. Go to **Upload Files** → upload all 7 file types for the period
3. Go to **Commission Rates** → verify rates → Save Settings  
4. Go to **Dashboard** → click **Run Calculation**
5. Review **All Reports** → Rep Breakdown
6. Check **Flags** for any compliance issues
7. Review **Gross Profit** → By Store for P&L
8. Export payroll from **StoreOps → Payroll**
