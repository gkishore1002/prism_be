# Prism Backend (prism_be)

FastAPI CRUD backend for the [prism_fe](../prism_fe) Prism frontend.

## Quick start

```bash
cd c:\Current\prism_be
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m app.seed
uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

## Demo credentials

| Email | Role | Password | Institution code |
|-------|------|----------|------------------|
| arjun@brightpath.edu | student | demo123 | BRIGHTPATH |
| priya@brightpath.edu | tutor | demo123 | BRIGHTPATH |
| rajesh@brightpath.edu | admin | demo123 | BRIGHTPATH |
| demo@prism.app | multi-role picker | demo123 | BRIGHTPATH |

## API modules (no AI)

| Module | Prefix | CRUD |
|--------|--------|------|
| Auth | `/api/v1/auth` | login, select-role, me, logout |
| Institutions | `/api/v1/institutions`, `/api/v1/centers` | read + create/update/delete centers |
| Curriculum | `/api/v1/curriculum` | boards, grades, subjects, topics (read, create, update, delete) |
| Students | `/api/v1/students` | list, get, create, update, delete |
| Batches | `/api/v1/batches` | list, get, create, update, delete, assign students |
| Questions | `/api/v1/questions` | full CRUD, bulk paper import |
| Question papers | `/api/v1/question-papers` | list, get, create, bulk, custom, delete |
| Search | `/api/v1/search` | students, topics, questions, papers |
| Assessments | `/api/v1/assessments` | CRUD, submit, attendance |
| Study plans | `/api/v1/study-plans` | CRUD, toggle day done |
| Notifications | `/api/v1/notifications` | list, create, mark read, delete one, clear all |

## FE integration

Add to `prism_fe`:

```
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

All request/response JSON uses **camelCase** field names (e.g. `institutionId`, `durationMinutes`, `questionIds`) to match the TypeScript types. Snake_case is also accepted on input.

## Project layout

```
prism_be/
├── app/
│   ├── main.py          # FastAPI entry
│   ├── seed.py          # Demo data
│   ├── api/v1/          # Route handlers
│   ├── core/            # Config, JWT, deps
│   ├── db/              # SQLAlchemy session
│   ├── models/          # DB tables
│   └── schemas/         # Pydantic (mirrors FE types)
├── requirements.txt
└── .env.example
```

## Database

SQLite by default (`prism.db`). Set `DATABASE_URL` in `.env` for PostgreSQL.

### Fresh database (no seed)

Delete `prism.db` and restart uvicorn. On first startup the API **auto-bootstraps**:

- Institution code: `BRIGHTPATH` (configurable via `BOOTSTRAP_INST_CODE` in `.env`)
- Default HQ center (for student assignment — not used at login)
- Admin / tutor / student logins with password `demo123`

Login uses **institution code**, not center ID.

Manual bootstrap: `python -m app.bootstrap --inst-code MYACAD ...`
