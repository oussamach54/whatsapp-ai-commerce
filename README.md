# WhatsApp AI Commerce

A production-oriented foundation for a cash-on-delivery e-commerce platform in which customers will communicate through WhatsApp and business owners will manage commerce operations through an admin dashboard.

## Architecture

- `backend/`: Python 3.13 FastAPI API, SQLAlchemy 2.x, Pydantic Settings, Alembic, and pytest.
- `frontend/`: React, TypeScript, Vite, and Tailwind CSS admin dashboard shell.
- `infra/docker/`: Docker build configuration for the backend.
- `docs/`: Documentation reserved for subsequent implementation steps.
- `docker-compose.yml`: Local PostgreSQL 17 and the backend service.

No business models, business logic, AI agent, OpenAI integration, or WhatsApp integration are implemented in this step.

## Prerequisites

- Python 3.13
- Node.js 20 or newer and npm
- Docker Desktop with Docker Compose v2

## Environment variables

Copy the root example before using Docker Compose:

```powershell
Copy-Item .env.example .env
```

Set a strong local value for `DATABASE_PASSWORD` in `.env`. Compose and the backend share `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_PORT`, `BACKEND_PORT`, and `ENVIRONMENT`. The backend constructs its SQLAlchemy URL from these individual settings, so passwords containing URL-reserved characters are supported.

For a directly run backend, copy `backend/.env.example` to `backend/.env` and set the database values to match the root `.env`.

The frontend optionally reads `VITE_API_BASE_URL`; see `frontend/.env.example`.

## Start PostgreSQL

```powershell
docker compose up -d postgres
docker compose ps
```

The database is PostgreSQL 17. Its data is stored in the named `postgres_data` Docker volume.

## Alembic convention

When a future SQLAlchemy model is added, import it from `app.models.__init__`. Alembic imports that registry before reading `Base.metadata`, allowing autogeneration to discover model tables.

Domain status and channel values are native PostgreSQL enums. Future additive values require an Alembic migration using PostgreSQL's `ALTER TYPE ... ADD VALUE` before application code uses them. Monetary database fields use `NUMERIC(12,2)`, which preserves Decimal precision for Moroccan dirham pricing.

## Run the backend

Create and activate a Python 3.13 virtual environment, then install dependencies:

```powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

The health endpoint is available at `http://localhost:8000/health`.

Alternatively, start the database and backend with Docker Compose:

```powershell
docker compose up --build
```

## Run the frontend

```powershell
cd frontend
npm install
npm run dev
```

Vite prints the local URL, normally `http://localhost:5173`.

## Run tests

With the backend virtual environment activated:

```powershell
cd backend
pytest
```

Database model tests require a PostgreSQL database that has been migrated with Alembic. Use a dedicated database and set `TEST_DATABASE_HOST`, `TEST_DATABASE_PORT`, `TEST_DATABASE_NAME`, `TEST_DATABASE_USER`, and `TEST_DATABASE_PASSWORD` before running pytest. The tests use a transaction and roll it back after each test; they never create schema objects outside Alembic.

## Current status

Step 1 is limited to the monorepo foundation: a FastAPI health route, PostgreSQL/SQLAlchemy/Alembic configuration with no business models or migrations, a minimal dashboard shell, and Docker Compose infrastructure for PostgreSQL and the backend. Future services n8n, OpenAI, and WhatsApp Business Cloud API are intentionally absent.
