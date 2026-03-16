#!/usr/bin/env python
"""
DocPulse API demo script.
Runs the full pipeline: register -> upload -> poll -> ask.
Execute with: docker compose exec api python scripts/demo.py
"""
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid


BASE_URL = "http://localhost:8000"
DEMO_EMAIL = "demo@docpulse.dev"
DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demopass123"

DEMO_TEXT = """SERVICE AGREEMENT

This agreement is entered into on March 1, 2025, between Acme Corp (the "Client")
and DevStudio SL (the "Provider").

Services: Backend API development and maintenance.
Duration: 12 months, renewable annually.
Payment: 4,800 EUR per month, invoiced on the 1st of each month.
Termination: Either party may terminate with 30 days written notice.

Signed by: John Doe (Acme Corp) and Robert M. (DevStudio SL)
"""


def _request(method, path, data=None, token=None, content_type="application/json"):
    url = f"{BASE_URL}{path}"
    body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Content-Type", content_type)
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
            return response.status, json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            data = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            data = {"detail": payload.decode(errors="replace")}
        return exc.code, data


def step(message):
    print("\n" + "-" * 50)
    print(f"  {message}")
    print("-" * 50)


def ok(message):
    print(f"  [ok] {message}")


def info(message):
    print(f"  [..] {message}")


def fail(message):
    print(f"  [x] {message}")
    sys.exit(1)


def upload_document(token):
    tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", encoding="utf-8")
    try:
        tmp.write(DEMO_TEXT)
        tmp.close()

        boundary = uuid.uuid4().hex
        with open(tmp.name, "rb") as file_handle:
            file_content = file_handle.read()

        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="service_agreement.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode() + file_content + f"\r\n--{boundary}--\r\n".encode()

        request = urllib.request.Request(f"{BASE_URL}/api/documents/", data=body, method="POST")
        request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        request.add_header("Authorization", f"Bearer {token}")

        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        fail(f"Upload failed ({exc.code}): {exc.read().decode(errors='replace')}")
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)


def main():
    print("\n" + "=" * 50)
    print("  DocPulse API - Full Pipeline Demo")
    print("=" * 50)

    step("1 / 5  Register")
    status, body = _request(
        "POST",
        "/api/auth/register/",
        {
            "email": DEMO_EMAIL,
            "username": DEMO_USERNAME,
            "password": DEMO_PASSWORD,
        },
    )
    if status == 201:
        token = body["tokens"]["access"]
        ok(f"Registered as {DEMO_EMAIL}")
    elif status == 400:
        info("User exists, trying login instead")
        status, body = _request(
            "POST",
            "/api/auth/login/",
            {
                "email": DEMO_EMAIL,
                "password": DEMO_PASSWORD,
            },
        )
        if status != 200:
            fail(f"Login failed ({status}): {body}")
        token = body["tokens"]["access"]
        ok("Logged in")
    else:
        fail(f"Register failed ({status}): {body}")

    step("2 / 5  Upload document")
    upload_body = upload_document(token)
    document_id = upload_body["id"]
    ok(f"Document uploaded - ID: {document_id}")
    info(f"Status: {upload_body['status']}")

    step("3 / 5  Processing (polling status)")
    max_wait = 120
    interval = 3
    elapsed = 0

    while elapsed < max_wait:
        time.sleep(interval)
        elapsed += interval
        _, status_body = _request("GET", f"/api/documents/{document_id}/status/", token=token)
        current_status = status_body.get("status", "unknown")
        info(f"[{elapsed:>3}s] status: {current_status}")
        if current_status == "done":
            ok("Processing complete")
            break
        if current_status == "failed":
            fail(f"Processing failed: {status_body.get('error_message', 'unknown error')}")
    else:
        fail(f"Timed out after {max_wait}s - is Ollama running?")

    step("4 / 5  Document detail")
    _, detail = _request("GET", f"/api/documents/{document_id}/", token=token)
    ok("Summary generated by Ollama:")
    print(f"\n  \"{detail.get('summary', 'N/A')}\"\n")

    extracted = detail.get("extracted_data", {})
    if extracted:
        ok("Extracted data:")
        for key, value in extracted.items():
            print(f"  - {key}: {value}")

    step("5 / 5  Q&A on document")
    question = "What is the monthly payment amount?"
    _, answer = _request(
        "POST",
        f"/api/documents/{document_id}/ask/",
        data={"question": question},
        token=token,
    )
    ok(f"Q: {question}")
    print(f"\n  A: {answer.get('answer', 'N/A')}\n")

    info("Asking the same question again to hit Redis cache")
    start = time.time()
    _request(
        "POST",
        f"/api/documents/{document_id}/ask/",
        data={"question": question},
        token=token,
    )
    cache_ms = int((time.time() - start) * 1000)
    ok(f"Cache response in {cache_ms}ms")

    print("\n" + "=" * 50)
    print("  Pipeline complete")
    print("=" * 50)
    print(f"\n  Document ID: {document_id}")
    print("  Flower:      http://localhost:5555")
    print("  MinIO:       http://localhost:9001\n")


if __name__ == "__main__":
    main()
