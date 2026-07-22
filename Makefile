.PHONY: test test-postgres test-docker lint security verify

test:
	pytest -q

test-postgres:
	@test -n "$(POSTGRES_TEST_DATABASE_URL)" || (echo "POSTGRES_TEST_DATABASE_URL is required" >&2; exit 2)
	POSTGRES_TEST_DATABASE_URL="$(POSTGRES_TEST_DATABASE_URL)" pytest -q tests/test_postgres_concurrency.py

test-docker:
	docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
	docker compose -f docker-compose.test.yml down -v

lint:
	ruff check app tests alembic
	ruff format --check app tests alembic

security:
	ruff check app --select S
	bandit -q -r app
	pip-audit -r requirements.txt

verify: lint test
