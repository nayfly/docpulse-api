.PHONY: up down build restart logs logs-worker test test-fast test-services migrate createsuperuser demo shell clean setup

# Docker

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose up --build -d

restart:
	docker compose restart api worker

logs:
	docker compose logs -f api worker

logs-worker:
	docker compose logs -f worker

# Django

migrate:
	docker compose exec api python manage.py migrate

createsuperuser:
	docker compose exec api python manage.py createsuperuser

shell:
	docker compose exec api python manage.py shell

# Tests

test:
	docker compose exec api pytest --cov=apps --cov-report=term-missing

test-fast:
	docker compose exec api pytest -x -q

test-services:
	docker compose exec api pytest tests/documents/test_services.py -v

# Demo

demo:
	@echo ""
	@echo "DocPulse API - Live Demo"
	@echo ""
	@docker compose exec api python scripts/demo.py

# Cleanup

clean:
	docker compose down -v
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

# Setup from scratch

setup: build migrate
	@echo ""
	@echo "DocPulse ready at http://localhost:8000"
	@echo "MinIO console: http://localhost:9001  (minioadmin / minioadmin)"
	@echo "Flower:        http://localhost:5555"
	@echo ""
