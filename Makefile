.PHONY: up down seed eval run migrate lint test fmt clean

up:
	docker compose up -d --build
	@echo "Waiting for postgres to become healthy..."
	@sleep 8
	docker compose exec api alembic upgrade head
	@echo "API ready at http://localhost:8000/docs"

down:
	docker compose down -v

migrate:
	docker compose exec api alembic upgrade head

seed:
	docker compose exec api python -m milaan.adapters.synthetic.generate --seed 42 --records 5000 --out data/synthetic

eval:
	docker compose exec api python -m milaan.eval.run --data-dir data/synthetic
	docker compose exec api python -m milaan.eval.gate metrics.json --mode deterministic_only

run:
	@echo "UI dev server: cd frontend && npm run dev  (localhost:5173)"
	@echo "API already up at localhost:8000/docs"

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy --strict src/
	uv run lint-imports

fmt:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest -m "not slow" --cov --cov-fail-under=80
	uv run pytest -m hypothesis
	uv run pytest -m security

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
