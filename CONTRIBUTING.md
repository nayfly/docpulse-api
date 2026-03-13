# Contributing & Local Development

## Prerequisites

- Docker + Docker Compose
- Ollama installed locally with `llama3.1:8b` pulled (`ollama pull llama3.1:8b`)
- Python 3.12+ (for running tests outside Docker)

---

## Setup

```bash
git clone https://github.com/nayfly/docpulse-api
cd docpulse-api
cp .env.example .env
# Edit .env - no API keys needed, uses local Ollama
docker compose up --build
docker compose exec api python manage.py migrate
```

---

## Running Tests

```bash
# All tests with coverage
docker compose exec api pytest --cov=apps --cov-report=term-missing

# Specific module
docker compose exec api pytest tests/documents/test_services.py -v

# Single test
docker compose exec api pytest tests/documents/test_views.py::TestDocumentAsk::test_ask_returns_answer -v
```

---

## Project Structure

```text
docpulse-api/
├── config/               # Django settings, Celery config, URLs
├── apps/
│   ├── users/            # Custom User model, JWT auth endpoints
│   └── documents/
│       ├── models.py     # Document with state machine
│       ├── services.py   # All business logic (S3, LLM, webhooks)
│       ├── tasks.py      # Celery async task
│       ├── views.py      # DRF views - thin, delegate to services
│       └── serializers.py
└── tests/
    ├── documents/
    │   ├── test_views.py    # Endpoint integration tests
    │   ├── test_tasks.py    # Celery task + Ollama unit tests
    │   └── test_services.py # Services layer unit tests
    └── users/
        └── test_views.py    # Auth endpoint tests
```

---

## Architecture Decisions

**Why services.py?**
All business logic lives in `services.py`. Views and tasks are intentionally thin - they orchestrate but don't implement. This makes unit testing trivial (mock services, not HTTP).

**Why Celery + Redis instead of Django Q or background threads?**
Celery gives retry logic, task state tracking, concurrency control, and Flower monitoring out of the box. For a document processing pipeline that can take 30+ seconds, that's non-negotiable.

**Why MinIO instead of local filesystem?**
S3-compatible API means zero code changes to deploy to AWS S3 or any cloud storage. `boto3` works identically against MinIO locally and S3 in production.

**Why Ollama locally instead of a hosted LLM?**
Zero cost during development, no API keys required, works offline. The `services.py` layer abstracts the LLM call - swapping to Anthropic or OpenAI is a 10-line change.

---

## Services

| Service | URL | Credentials |
|---------|-----|-------------|
| API | http://localhost:8000 | - |
| Django Admin | http://localhost:8000/admin | superuser |
| MinIO Console | http://localhost:9001 | minioadmin / minioadmin |
| Flower (Celery) | http://localhost:5555 | - |
| PostgreSQL | localhost:5432 | docpulse / docpulse |
| Redis | localhost:6379 | - |

---

## Environment Variables

See `.env.example` for all required variables. The defaults work out of the box with `docker compose up`.
