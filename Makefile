.PHONY: up down dev-backend dev-frontend test lint

up:            ## full stack
	docker compose up -d --build

down:
	docker compose down

dev-backend:   ## hot-reload API on :8420 (needs local python env)
	cd backend && uvicorn hearth.main:create_app --factory --reload --port 8420

dev-frontend:  ## vite dev server on :5173, proxies /api to :8420
	cd frontend && npm run dev

test:
	cd backend && pytest -q

lint:
	cd backend && ruff check hearth
