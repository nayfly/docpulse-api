"""
Business logic layer — keeps views and tasks thin.
"""
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from io import BytesIO

import boto3
import pypdf
from botocore.client import Config
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


# ─── Storage ───────────────────────────────────────────────────────────────────

def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.AWS_S3_ENDPOINT_URL,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
    )


def ensure_bucket_exists():
    client = get_s3_client()
    bucket = settings.AWS_STORAGE_BUCKET_NAME
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)
    return bucket


def upload_file_to_s3(file_obj, s3_key: str, content_type: str) -> str:
    client = get_s3_client()
    bucket = ensure_bucket_exists()
    client.upload_fileobj(
        file_obj,
        bucket,
        s3_key,
        ExtraArgs={"ContentType": content_type},
    )
    return s3_key


def download_file_from_s3(s3_key: str) -> bytes:
    client = get_s3_client()
    bucket = settings.AWS_STORAGE_BUCKET_NAME
    response = client.get_object(Bucket=bucket, Key=s3_key)
    return response["Body"].read()


def get_presigned_url(s3_key: str, expiry: int = 3600) -> str:
    client = get_s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.AWS_STORAGE_BUCKET_NAME, "Key": s3_key},
        ExpiresIn=expiry,
    )


# ─── Text Extraction ───────────────────────────────────────────────────────────

def extract_text_from_pdf(file_bytes: bytes) -> str:
    try:
        reader = pypdf.PdfReader(BytesIO(file_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages).strip()
    except Exception as e:
        logger.error(f"PDF extraction failed: {e}")
        return ""


def extract_text(file_bytes: bytes, mime_type: str) -> str:
    if "pdf" in mime_type:
        return extract_text_from_pdf(file_bytes)
    try:
        return file_bytes.decode("utf-8", errors="replace")
    except Exception:
        return ""


# ─── Ollama LLM ────────────────────────────────────────────────────────────────

def _ollama_generate(prompt: str, max_tokens: int = 1024) -> str:
    """
    Calls the local Ollama instance via HTTP.
    Accessible from Docker containers via host.docker.internal.
    """
    payload = json.dumps({
        "model": settings.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "temperature": 0.1,  # low temp = more deterministic JSON
        },
    }).encode()

    req = urllib.request.Request(
        f"{settings.OLLAMA_BASE_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            return data.get("response", "").strip()
    except urllib.error.URLError as e:
        logger.error(f"Ollama request failed: {e}")
        raise RuntimeError(f"LLM unavailable: {e}") from e


def _parse_llm_json(raw: str) -> dict:
    """
    Safely parses JSON from LLM output.
    Handles cases where the model wraps response in ```json ... ```.
    """
    clean = raw.strip()
    # Strip markdown code fences if present
    if clean.startswith("```"):
        clean = clean.split("```")[1]
        if clean.startswith("json"):
            clean = clean[4:]
    clean = clean.strip().rstrip("```").strip()
    return json.loads(clean)


def analyze_document(text: str) -> dict:
    """
    Calls Ollama to produce a summary and extract structured data.
    Returns {"summary": str, "extracted_data": dict}
    """
    if not text.strip():
        return {"summary": "No text could be extracted.", "extracted_data": {}}

    truncated = text[:8000]

    prompt = f"""You are a document analysis assistant. Analyze the document below and respond ONLY with a valid JSON object containing exactly two keys:

1. "summary": a clear 2-4 sentence summary of the document.
2. "extracted_data": a flat JSON object with the most relevant key-value pairs (dates, names, amounts, references). Use snake_case keys.

Document:
---
{truncated}
---

Respond ONLY with the JSON object. No markdown, no explanation, no preamble."""

    raw = _ollama_generate(prompt, max_tokens=1024)
    logger.debug(f"Ollama raw response: {raw[:200]}")

    try:
        result = _parse_llm_json(raw)
        return {
            "summary": result.get("summary", ""),
            "extracted_data": result.get("extracted_data", {}),
        }
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning(f"Failed to parse LLM JSON response: {e}. Raw: {raw[:200]}")
        # Fallback: treat raw output as summary
        return {"summary": raw[:500], "extracted_data": {}}


def answer_question(document_text: str, question: str, cache_key: str = None) -> str:
    """
    Answers a question about a document using Ollama.
    Caches result in Redis to avoid duplicate LLM calls.
    """
    if cache_key:
        cached = cache.get(cache_key)
        if cached:
            logger.debug(f"Cache hit for key {cache_key}")
            return cached

    truncated = document_text[:8000]

    prompt = f"""You are a document Q&A assistant. Answer the question using ONLY the document below.
If the answer is not found in the document, say "The document does not contain that information."

Document:
---
{truncated}
---

Question: {question}

Answer concisely and directly. No preamble."""

    answer = _ollama_generate(prompt, max_tokens=512)

    if cache_key:
        cache.set(cache_key, answer, timeout=3600)

    return answer


# ─── Webhooks ──────────────────────────────────────────────────────────────────

def sign_webhook_payload(payload: str) -> str:
    secret = settings.WEBHOOK_SECRET.encode()
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def verify_webhook_signature(payload: str, signature: str) -> bool:
    expected = sign_webhook_payload(payload)
    return hmac.compare_digest(expected, signature)


def deliver_webhook(document) -> bool:
    """
    POSTs a signed webhook payload to the document's or owner's webhook URL.
    Returns True on successful delivery (HTTP < 400).
    """
    url = document.webhook_url or (
        document.owner.webhook_url if document.owner else None
    )
    if not url:
        return False

    payload = json.dumps({
        "event": "document.processed",
        "document_id": str(document.id),
        "document_name": document.name,
        "status": document.status,
        "summary": document.summary[:200] if document.summary else "",
        "timestamp": timezone.now().isoformat(),
    })
    signature = sign_webhook_payload(payload)

    req = urllib.request.Request(
        url,
        data=payload.encode(),
        headers={
            "Content-Type": "application/json",
            "X-DocPulse-Signature": signature,
            "X-DocPulse-Event": "document.processed",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            success = resp.status < 400
            logger.info(f"Webhook delivered to {url}: HTTP {resp.status}")
            return success
    except urllib.error.URLError as e:
        logger.warning(f"Webhook delivery failed for document {document.id}: {e}")
        return False
