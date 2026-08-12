# Docker — Backend + Postgres

Run **PostgreSQL** and the **Prism API** in Docker. Run **prism_fe** locally with Vite.

```text
┌─────────────────────┐     ┌──────────────────────────────┐
│  prism_fe (local)   │────▶│  Docker: backend :8002       │
│  npm run dev :5174  │     │  Docker: postgres :5432      │
└─────────────────────┘     └──────────────────────────────┘
```

Overview and login tables: [README](../README.md).

---

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- Node.js 18+ (frontend, separate `prism_fe` clone)

---

## Start

```powershell
cd prism_be
docker compose -f docker-compose.backend.yml up -d --build
```

Optional env file:

```powershell
copy .env.docker.example .env.docker
docker compose -f docker-compose.backend.yml --env-file .env.docker up -d --build
```

Check:

```powershell
docker compose -f docker-compose.backend.yml ps
Invoke-RestMethod http://127.0.0.1:8002/health
```

---

## Frontend

Clone and run the frontend repo separately:

```powershell
git clone https://github.com/gkishore1002/prism_fe.git
cd prism_fe
copy .env.example .env
npm install
npm run dev
```

Ensure `prism_fe/.env`:

```env
VITE_API_BASE_URL=http://127.0.0.1:8002/api/v1
```

Open http://localhost:5174/login

---

## Demo logins (`SEED_DEMO=true`, default)

| Role | Org code | Email | Password |
|------|----------|-------|----------|
| Platform super user | `SYSTEM` | `superuser@prism.io` | `superuser123` |
| Org admin | `DEMO001` | `admin@demo.com` | `demo1234` |
| Tutor | `DEMO001` | `tutor@demo.com` | `demo1234` |
| Student | `DEMO001` | `student@demo.com` | `demo1234` |

These are **legacy email** seed accounts. Users you create in the admin UI use **`{phone}@gmail.com`** and password = phone (unless overridden).

---

## Custom first organization

1. Set `SEED_DEMO=false` in `.env.docker` (or compose environment).
2. Reset DB (below).
3. Open http://localhost:5174/setup — enter org + owner **phone**.
4. Login with `{phone}@gmail.com`, password (phone or custom), and org code.

---

## Reset database

Wipes all organizations, users, and data.

```powershell
cd prism_be
docker compose -f docker-compose.backend.yml down -v
docker compose -f docker-compose.backend.yml up -d --build
```

With custom env:

```powershell
docker compose -f docker-compose.backend.yml --env-file .env.docker down -v
docker compose -f docker-compose.backend.yml --env-file .env.docker up -d --build
```

Then `/setup` (if `SEED_DEMO=false`) or wait for demo seed (if `SEED_DEMO=true`).

---

## Daily commands

| Action | Command |
|--------|---------|
| Start | `docker compose -f docker-compose.backend.yml up -d` |
| Stop | `docker compose -f docker-compose.backend.yml down` |
| Logs | `docker compose -f docker-compose.backend.yml logs -f backend` |
| Rebuild API | `docker compose -f docker-compose.backend.yml up -d --build backend` |

---

## Postgres connection

| Setting | Default |
|---------|---------|
| Host | `localhost` |
| Port | `5432` |
| Database | `prism` |
| User | `prism` |
| Password | `prism123` |

```powershell
docker compose -f docker-compose.backend.yml exec postgres psql -U prism -d prism
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Port 8002 in use | Stop local uvicorn or set `BACKEND_PORT` in `.env.docker` |
| Port 5432 in use | Set `POSTGRES_PORT=5433` in `.env.docker` |
| CORS errors | Add your Vite origin to `CORS_ORIGINS`, then `up -d --force-recreate backend` |
| Setup page unavailable | DB already initialized — login with existing owner or `down -v` to reset |
| Postgres not ready | `docker compose -f docker-compose.backend.yml logs postgres` |
