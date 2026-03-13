import hashlib
import hmac
import json
import logging
import urllib.request
from io import BytesIO

import boto3
import PyPDF2
from botocore.client import Config
from django.conf import settings

logger = logging.getLogger(__name__)


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


def extract_text_from_pdf(file_bytes: bytes) -> str:
    try:
        reader = PyPDF2.PdfReader(BytesIO(file_bytes))
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


def _ollama_chat(prompt: str, max_tokens: int = 1024) -> str:
    payload = json.dumps({
        "model": settings.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }).encode()

    req = urllib.request.Request(
        f"{settings.OLLAMA_BASE_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
        return data.get("response", "")


def analyze_document(text: str) -> dict:
    if not text.strip():
        return {"summary": "No text could be extracted.", "extracted_data": {}}

    truncated = text[:8000]

    prompt = f"""Analyze the following document and respond ONLY with a JSON object with two keys:
1. "summary": 2-4 sentence summary.
2. "extracted_data": flat JSON with key-value pairs (dates, names, amounts). Use snake_case keys.

Document:
---
{truncated}
---

Respond ONLY with the JSON object. No markdown, no explanation."""

    response = _ollama_chat(prompt)
    try:
        # Ollama a veces envuelve en ```json ... ```
        clean = response.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(clean)
        return {
            "summary": result.get("summary", ""),
            "extracted_data": result.get("extracted_data", {}),
        }
    except json.JSONDecodeError:
        return {"summary": response, "extracted_data": {}}


def answer_question(document_text: str, question: str, cache_key: str = None) -> str:
    from django.core.cache import cache

    if cache_key:
        cached = cache.get(cache_key)
        if cached:
            return cached

    truncated = document_text[:8000]

    prompt = f"""Answer the question using ONLY the document below. If the answer is not in the document, say so.

Document:
---
{truncated}
---

Question: {question}

Answer concisely."""

    answer = _ollama_chat(prompt, max_tokens=512)

    if cache_key:
        cache.set(cache_key, answer, timeout=3600)

    return answer


def sign_webhook_payload(payload: str) -> str:
    secret = settings.WEBHOOK_SECRET.encode()
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def deliver_webhook(document) -> bool:
    import json
    import urllib.request
    import urllib.error
    from django.utils import timezone

    url = document.webhook_url or (document.owner.webhook_url if document.owner else None)
    if not url:
        return False

    payload = json.dumps({
        "event": "document.processed",
        "document_id": str(document.id),
        "status": document.status,
        "timestamp": timezone.now().isoformat(),
    })
    signature = sign_webhook_payload(payload)

    req = urllib.request.Request(
        url,
        data=payload.encode(),
        headers={
            "Content-Type": "application/json",
            "X-DocPulse-Signature": signature,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status < 400
    except urllib.error.URLError as e:
        logger.warning(f"Webhook delivery failed for document {document.id}: {e}")
        return False
