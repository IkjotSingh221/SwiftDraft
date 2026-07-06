.PHONY: dev api web test up down eval

dev:
	@echo "Starting API + web dev servers (Ctrl+C to stop both)"
	@$(MAKE) -j2 api web

api:
	uv run uvicorn draftforge.api.app:app --reload --host 0.0.0.0 --port 8000

web:
	cd frontend && npm run dev

test:
	uv run pytest
	cd frontend && npm run test -- --run

up:
	docker compose up -d

down:
	docker compose down

eval:
	uv run python -m draftforge.evals
