.PHONY: up down logs ps seed rebuild test backend-test frontend-dev backfill-dry backfill backfill-invalidate help

help:
	@echo "up           - build + start full stack (db, seeder, backend, frontend)"
	@echo "down         - stop stack (data volume preserved)"
	@echo "logs         - tail all logs"
	@echo "ps           - service status"
	@echo "seed         - re-run the seeding/harmonization job"
	@echo "test         - run backend test suite"
	@echo "backend-dev  - run backend locally on SQLite (no docker)"

up:
	docker compose up --build -d
	@echo "Frontend: http://localhost:8080  API: http://localhost:8000/docs"

down:
	docker compose down

logs:
	docker compose logs -f --tail=120

ps:
	docker compose ps

seed:
	docker compose run --rm seeder

test:
	cd backend && python -m pytest tests -q

backfill-dry:
	docker compose exec backend python scripts/backfill_firms.py --dry-run

backfill:
	docker compose exec backend python scripts/backfill_firms.py --fresh

backfill-resume:
	docker compose exec backend python scripts/backfill_firms.py

backfill-invalidate:
	docker compose exec backend python scripts/backfill_firms.py --invalidate-only

backend-dev:
	cd backend && EMBEREBEYE_DATABASE_URL=sqlite:///./embereye_dev.db python -m app.seed_run && \
	EMBEREBEYE_DATABASE_URL=sqlite:///./embereye_dev.db uvicorn app.main:app --reload --port 8000
