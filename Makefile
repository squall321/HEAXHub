.PHONY: help install backend frontend worker beat dev migrate seed test test-integration test-e2e test-all typecheck lint format build clean docker-up docker-down

help:
	@echo "HEAXHub Makefile targets"
	@echo "  make install          - install backend + frontend dependencies"
	@echo "  make dev              - run backend + frontend + worker in parallel"
	@echo "  make backend          - run FastAPI dev server"
	@echo "  make frontend         - run Vite dev server"
	@echo "  make worker           - run Celery worker"
	@echo "  make beat             - run Celery beat (scheduler)"
	@echo "  make migrate          - apply DB migrations"
	@echo "  make seed             - create initial admin user"
	@echo "  make test             - run backend + frontend unit tests"
	@echo "  make test-integration - run backend integration suite (-m integration)"
	@echo "  make test-e2e         - run frontend Playwright E2E suite"
	@echo "  make test-all         - run test + test-integration + test-e2e"
	@echo "  make typecheck        - ruff + mypy (backend) + tsc (frontend)"
	@echo "  make lint             - run ruff + biome lint"
	@echo "  make format           - apply formatters"
	@echo "  make build            - build frontend for production"
	@echo "  make docker-up        - dev shortcut: start postgres + redis + mailhog on Apptainer ports (5732/6479/8125/8126)"
	@echo "  make docker-down      - dev shortcut: stop docker services"
	@echo "  make clean            - remove caches and build artifacts"

install:
	cd backend && pip install -e ".[dev]"
	cd frontend && pnpm install

# Backend FastAPI dev server. Port matches APP_PORT in .env (default 4040)
# which the vite dev server proxies to (see frontend/vite.config.ts).
backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 4040 --env-file ../.env

frontend:
	cd frontend && pnpm dev

# Use .venv/bin/celery explicitly for both worker and beat so they share the
# same Python env (avoids picking up a system-wide celery without app deps).
worker:
	cd backend && .venv/bin/celery -A app.workers.celery_app worker --loglevel=info

beat:
	cd backend && .venv/bin/celery -A app.workers.celery_app beat --loglevel=info

dev:
	@echo "Starting full dev stack — open 4 terminals or use a process manager"
	@echo "Terminal 1: make backend"
	@echo "Terminal 2: make frontend"
	@echo "Terminal 3: make worker"
	@echo "Terminal 4: make beat"

migrate:
	cd backend && alembic upgrade head

# NOTE: `scripts` here resolves to backend/scripts/ (because of `cd backend`),
# NOT the top-level /scripts/ directory. Don't confuse the two — top-level
# /scripts holds shell ops scripts; backend/scripts holds Python entrypoints
# like create_admin.py.
seed:
	cd backend && .venv/bin/python -m scripts.create_admin

test:
	cd backend && pytest -v
	cd frontend && pnpm test

test-integration:
	cd backend && pytest -m integration -v

test-e2e:
	cd frontend && pnpm e2e

test-all: test test-integration test-e2e

typecheck:
	cd backend && ruff check app && mypy app || true
	cd frontend && pnpm typecheck

lint:
	cd backend && ruff check app
	cd frontend && pnpm lint

format:
	cd backend && ruff format app && ruff check --fix app
	cd frontend && pnpm format

build:
	cd frontend && pnpm build

docker-up:
	docker compose -f deploy/dev-host/docker-compose.yml up -d

docker-down:
	docker compose -f deploy/dev-host/docker-compose.yml down

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf backend/.pytest_cache backend/.ruff_cache backend/.mypy_cache
	rm -rf frontend/dist frontend/node_modules/.vite
