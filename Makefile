SHELL := /bin/bash
VENV ?= .venv
PYTHON ?= $(VENV)/bin/python
PIP ?= $(VENV)/bin/pip

.PHONY: install api worker web test lint typecheck migrate migration bootstrap

install:
	$(PIP) install -e '.[dev]'
	cd apps/web && npm ci

api:
	PYTHONPATH=apps/api $(VENV)/bin/uvicorn clipos.main:app --host 0.0.0.0 --port 8000 --reload

worker:
	PYTHONPATH=apps/api $(PYTHON) -m clipos.worker

web:
	cd apps/web && npm run dev -- --hostname 0.0.0.0 --port 3000

test:
	PYTHONPATH=apps/api $(VENV)/bin/pytest

lint:
	PYTHONPATH=apps/api $(VENV)/bin/ruff format --check apps/api
	PYTHONPATH=apps/api $(VENV)/bin/ruff check apps/api
	cd apps/web && npm run lint

typecheck:
	PYTHONPATH=apps/api $(VENV)/bin/mypy apps/api/clipos
	cd apps/web && npm run typecheck

migrate:
	PYTHONPATH=apps/api $(VENV)/bin/alembic upgrade head

migration:
	PYTHONPATH=apps/api $(VENV)/bin/alembic revision --autogenerate -m "$(m)"

bootstrap:
	PYTHONPATH=apps/api $(PYTHON) scripts/bootstrap_admin.py $(ARGS)
