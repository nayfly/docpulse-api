# DocPulse API

**Async Document Intelligence API** — upload documents, get AI-powered summaries, extract structured data, and query documents via natural language.

Built with Django 5, DRF, Celery, Redis, MinIO, and Claude (Anthropic).

---

## Architecture

```
Client → POST /api/documents/ → Django API → MinIO (store file)
                                           → PostgreSQL (create record)
                                           → Redis (enqueue task)
                                              ↓
                                           Celery Worker
                                              → MinIO (download file)
                                              → Extract text (PyPDF2)
                                              → Claude API (analyze)
                                              → PostgreSQL (save results)
                                              → Webhook (notify client)
```

## Stack

| Layer | Tech |
|-------|------|
| Framework | Django 5 + DRF |
| Auth | JWT (simplejwt) |
| Async tasks | Celery + Redis |
| Database | PostgreSQL |
| File storage | MinIO (S3-compatible) |
| AI | Anthropic Claude |
| Monitoring | Flower |
| Containers | Docker + docker-compose |
| Tests | pytest-django |

---

## Quick Start

```bash
git clone https://github.com/nayfly/docpulse-api
cd docpulse-api
cp .env.example .env
# Add your ANTHROPIC_API_KEY to .env

docker compose up --build
docker compose exec api python manage.py migrate
```

Services:
- API: http://localhost:8000
- MinIO Console: http://localhost:9001
- Flower: http://localhost:5555

---

## API Reference

### Auth
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/register/` | Register + get JWT tokens |
| POST | `/api/auth/login/` | Login + get JWT tokens |
| POST | `/api/auth/logout/` | Blacklist refresh token |
| POST | `/api/auth/refresh/` | Refresh access token |

### Documents
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/documents/` | Upload document (202 Accepted) |
| GET | `/api/documents/list/` | List user's documents |
| GET | `/api/documents/{id}/` | Full detail + presigned download URL |
| GET | `/api/documents/{id}/status/` | Lightweight polling |
| POST | `/api/documents/{id}/ask/` | Q&A on document (Redis cached) |
| POST | `/api/documents/{id}/reprocess/` | Requeue failed document |

---

## Example

```bash
# Register
curl -X POST http://localhost:8000/api/auth/register/ \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "username": "you", "password": "securepass"}'

# Upload
curl -X POST http://localhost:8000/api/documents/ \
  -H "Authorization: Bearer <token>" \
  -F "file=@contract.pdf"

# Poll status
curl http://localhost:8000/api/documents/<id>/status/ \
  -H "Authorization: Bearer <token>"

# Ask a question
curl -X POST http://localhost:8000/api/documents/<id>/ask/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the payment terms?"}'
```

---

## Tests

```bash
docker compose exec api pytest --cov=apps --cov-report=term-missing
```

---

## Key Design Decisions

- **Fail-closed retries**: Tasks retry 3x with 60s delay; on exhaustion → `failed` (never stuck in `processing`)
- **Thin views, fat services**: Business logic in `services.py`, views and tasks stay thin and testable
- **Redis Q&A cache**: Same question on same document hits cache, not Claude API
- **Presigned URLs**: Files never served through Django — MinIO generates short-lived URLs
- **Signed webhooks**: HMAC-SHA256 signature for payload verification
