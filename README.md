# Zoiko Rooms

A long-term room-share marketplace (30+ night stays only). Renters browse and apply for verified private rooms; hosts list rooms they own; a super admin reviews identity documents and manages the platform.

- **Frontend** — Next.js 16 (App Router, Turbopack), TypeScript, Tailwind CSS. Lives in `frontend/`.
- **Backend** — FastAPI, SQLAlchemy, PostgreSQL, Alembic migrations. Lives in `backend/`.

There are two independent sign-in areas:
- `/login`, `/register`, `/` — **Admin / Super Admin** dashboard
- `/account/login`, `/account/register`, `/account` — **Renter / Host** area

## Prerequisites

- Node.js 20+
- Python 3.11+
- Docker (for PostgreSQL) — or a local Postgres instance

## 1. Start the database

```bash
cd backend
docker compose up -d
```

This starts Postgres on `localhost:5432` (user/password/db: `zoiko`).

## 2. Run the backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
copy .env.example .env          # Windows: copy, macOS/Linux: cp
alembic upgrade head
python -m uvicorn app.main:app --reload --port 8000
```

The API is now at `http://localhost:8000` (interactive docs at `/docs`).

## 3. Run the frontend

```bash
cd frontend
npm install
copy .env.local.example .env.local   # Windows: copy, macOS/Linux: cp
npm run dev
```

The app is now at `http://localhost:3000`.

`.env.local` must point at the backend:
```
NEXT_PUBLIC_API_URL=http://localhost:8000
API_URL=http://localhost:8000
```

## Useful commands

| Command | Where | What it does |
|---|---|---|
| `npm run dev` | `frontend/` | Start the frontend dev server |
| `npm run build` | `frontend/` | Production build |
| `npm run lint` | `frontend/` | Lint the frontend |
| `npx tsc --noEmit` | `frontend/` | Type-check the frontend |
| `uvicorn app.main:app --reload` | `backend/` | Start the backend dev server |
| `alembic upgrade head` | `backend/` | Apply database migrations |
| `alembic current` | `backend/` | Show the current migration |
| `python seed.py` | `backend/` | Seed sample data (optional) |

## Project layout

```
frontend/
  src/                  Frontend (App Router)
    app/(dashboard)/    Admin dashboard pages
    app/account/        Renter/host pages
    components/admin/   Admin UI
    components/user/    Renter/host UI
    lib/                API clients, types, shared helpers

backend/
  app/api/routes/       FastAPI routers (admin_* and user_*)
  app/models/           SQLAlchemy models
  app/schemas/          Pydantic request/response models
  app/crud/             Database access logic
  alembic/versions/     Migrations
```
