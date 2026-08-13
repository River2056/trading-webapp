SHELL := /bin/bash

.PHONY: install dev backend frontend test lint typecheck build verify

install:
	uv sync --extra dev
	cd frontend && npm ci

dev:
	@trap 'kill 0' EXIT; \
	uv run uvicorn backend.app.main:app --reload & \
	cd frontend && npm run dev

backend:
	uv run uvicorn backend.app.main:app --reload

frontend:
	cd frontend && npm run dev

test:
	uv run pytest
	cd frontend && npm test
	cd frontend && npm run test:e2e

lint:
	uv run ruff check .
	cd frontend && npm run lint

typecheck:
	uv run mypy
	cd frontend && npm run typecheck

build:
	uv build
	cd frontend && npm run build

verify: test lint typecheck build
