# Prism Backend (`prism_be`)

FastAPI API for [prism_fe](../prism_fe). JSON uses **camelCase** on the wire.

**Full stack setup (Docker + frontend):** see the [root README](../README.md).

---

## Run with Docker (recommended)

Postgres + API only — run the frontend separately.

```powershell
cd c:\Current
docker compose -f docker-compose.backend.yml up -d --build
```

- API: http://127.0.0.1:8002  
- OpenAPI: http://127.0.0.1:8002/docs  
- Health: http://127.0.0.1:8002/health  

Set `prism_fe/.env`:

```env
VITE_API_BASE_URL=http://127.0.0.1:8002/api/v1
```

---

## Run locally (SQLite, no Docker)

```powershell
cd c:\Current\prism_be
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

Then point the frontend at port **8000**:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1
```

SQLite file: `prism.db` in this folder (delete it to reset).

---

## Authentication

Login: `POST /api/v1/auth/login`

```json
{
  "email": "9876543210@gmail.com",
  "password": "9876543210",
  "institutionCode": "DEMO001"
}
```

| User source | Email | Password |
|-------------|-------|----------|
| **Seeded demo** (`SEED_DEMO=true`) | `admin@demo.com`, `tutor@demo.com`, etc. | `demo1234` |
| **Platform super user** (seed) | `superuser@prism.io` | `superuser123` — org code `SYSTEM` |
| **Created in admin UI** | `{phone}@gmail.com` | Phone number, or custom password at creation |
| **First-run `/setup`** | `{owner phone}@gmail.com` | Phone number, or custom password at setup |

Creating users (`POST /admins`, `/tutors`, `/students`) takes a **phone** and optional **password**; email is derived on the server.

---

## Database modes

| `DATABASE_URL` | Mode |
|----------------|------|
| `sqlite:///./prism.db` | Single-schema dev (default in `.env.example`) |
| `postgresql+psycopg://...` | Production-style; **one Postgres schema per organization** when multi-schema is enabled |

### Fresh database

**Option A — Demo seed (default)**  
`SEED_DEMO=true` → platform super user + `DEMO001` org on startup. No `/setup` needed.

**Option B — Custom first org**  
`SEED_DEMO=false`, empty DB → frontend redirects to `/setup` → org owner with phone-based login.  
`/api/v1/setup` is disabled after the deployment is marked initialized.

**Reset**

```powershell
# Docker Postgres
cd c:\Current
docker compose -f docker-compose.backend.yml down -v
docker compose -f docker-compose.backend.yml up -d --build

# SQLite
Remove-Item c:\Current\prism_be\prism.db
```

---

## Environment (`.env`)

Copy from [`.env.example`](.env.example):

| Variable | Default | Notes |
|----------|---------|--------|
| `DATABASE_URL` | SQLite | Use Postgres URL in Docker (set in compose) |
| `SECRET_KEY` | dev placeholder | Change in production |
| `SEED_DEMO` | `true` | Idempotent demo platform + DEMO001 tenant |
| `AUTO_BOOTSTRAP` | `false` | Legacy; use `SEED_DEMO` instead |
| `DEFAULT_ORGANIZATION_CODE` | `CSC` | Pre-fill on `/setup` |
| `CORS_ORIGINS` | localhost:5173/5174 | Must include your Vite origin |

Seed credentials are documented in `.env.example` (dev only).

---

## API overview

| Area | Prefix |
|------|--------|
| Auth | `/api/v1/auth` |
| Setup | `/api/v1/setup` |
| Platform | `/api/v1/platform` |
| Admins | `/api/v1/admins` |
| Institutions / centers | `/api/v1/institutions`, `/api/v1/centers` |
| Curriculum / students | `/api/v1/curriculum`, `/api/v1/students` |
| Assessments | `/api/v1/assessments` |
| Analytics | `/api/v1/analytics` |

---

## Project layout

```text
prism_be/
├── app/
│   ├── main.py
│   ├── api/v1/
│   ├── core/           # config, JWT, deps
│   ├── db/             # session, tenant schemas
│   ├── models/
│   ├── services/       # business logic (incl. user_credentials.py)
│   └── schemas/
├── requirements.txt
└── .env.example
```

---

## Customer deployments

Use [`docker-compose.customer.yml`](../docker-compose.customer.yml) as a template: one deployment + one Postgres database per customer. Run `/setup` once per customer DB — do not share a single database across customers.
