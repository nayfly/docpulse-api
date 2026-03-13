"""
Tests for services.py - S3, text extraction, Ollama, webhooks.
These tests mock external dependencies (boto3, urllib) to stay fast and isolated.
"""

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
import pypdf


@pytest.mark.django_db
class TestS3Services:
    @patch("apps.documents.services.boto3.client")
    def test_get_s3_client_uses_settings(self, mock_boto):
        from apps.documents.services import get_s3_client

        get_s3_client()

        mock_boto.assert_called_once()
        call_kwargs = mock_boto.call_args[1]
        assert "endpoint_url" in call_kwargs
        assert "aws_access_key_id" in call_kwargs

    @patch("apps.documents.services.get_s3_client")
    def test_ensure_bucket_creates_if_missing(self, mock_get_client):
        from apps.documents.services import ensure_bucket_exists

        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = Exception("NoSuchBucket")
        mock_get_client.return_value = mock_client

        ensure_bucket_exists()

        mock_client.create_bucket.assert_called_once()

    @patch("apps.documents.services.get_s3_client")
    def test_ensure_bucket_skips_create_if_exists(self, mock_get_client):
        from apps.documents.services import ensure_bucket_exists

        mock_client = MagicMock()
        mock_client.head_bucket.return_value = {}
        mock_get_client.return_value = mock_client

        ensure_bucket_exists()

        mock_client.create_bucket.assert_not_called()

    @patch("apps.documents.services.get_s3_client")
    @patch("apps.documents.services.ensure_bucket_exists", return_value="docpulse")
    def test_upload_file_to_s3(self, mock_bucket, mock_get_client):
        from apps.documents.services import upload_file_to_s3

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        fake_file = BytesIO(b"fake content")
        result = upload_file_to_s3(fake_file, "documents/test/file.pdf", "application/pdf")

        assert result == "documents/test/file.pdf"
        mock_client.upload_fileobj.assert_called_once()

    @patch("apps.documents.services.get_s3_client")
    def test_download_file_from_s3(self, mock_get_client):
        from apps.documents.services import download_file_from_s3

        mock_client = MagicMock()
        mock_client.get_object.return_value = {"Body": BytesIO(b"file content")}
        mock_get_client.return_value = mock_client

        result = download_file_from_s3("documents/test/file.pdf")

        assert result == b"file content"

    @patch("apps.documents.services.get_s3_client")
    def test_get_presigned_url(self, mock_get_client):
        from apps.documents.services import get_presigned_url

        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = "https://minio/presigned?token=abc"
        mock_get_client.return_value = mock_client

        url = get_presigned_url("documents/test/file.pdf", expiry=3600)

        assert url == "https://minio/presigned?token=abc"
        mock_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "docpulse", "Key": "documents/test/file.pdf"},
            ExpiresIn=3600,
        )


class TestTextExtraction:
    def test_extract_text_plain(self):
        from apps.documents.services import extract_text

        result = extract_text(b"Hello world", "text/plain")

        assert result == "Hello world"

    def test_extract_text_csv(self):
        from apps.documents.services import extract_text

        result = extract_text(b"name,age\nJohn,30", "text/csv")

        assert "name" in result
        assert "John" in result

    def test_extract_text_pdf_valid(self):
        from apps.documents.services import extract_text

        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=300, height=300)
        buf = BytesIO()
        writer.write(buf)

        result = extract_text(buf.getvalue(), "application/pdf")

        assert isinstance(result, str)

    def test_extract_text_pdf_corrupted_returns_empty(self):
        from apps.documents.services import extract_text

        result = extract_text(b"not a real pdf at all", "application/pdf")

        assert result == ""

    def test_extract_text_invalid_bytes_fallback(self):
        from apps.documents.services import extract_text

        result = extract_text(b"\xff\xfe invalid", "text/plain")

        assert isinstance(result, str)

    def test_extract_text_decode_exception_returns_empty(self):
        from apps.documents.services import extract_text

        class BadPayload:
            def decode(self, *_args, **_kwargs):
                raise UnicodeError("decode failed")

        result = extract_text(BadPayload(), "text/plain")

        assert result == ""


class TestOllamaGenerate:
    @patch("apps.documents.services.urllib.request.urlopen")
    def test_ollama_generate_success(self, mock_urlopen):
        from apps.documents.services import _ollama_generate

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"response": "Hello from Ollama"}).encode()
        mock_urlopen.return_value.__enter__ = lambda s: mock_response
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        result = _ollama_generate("Say hello")

        assert result == "Hello from Ollama"

    @patch("apps.documents.services.urllib.request.urlopen")
    def test_ollama_generate_raises_on_connection_error(self, mock_urlopen):
        import urllib.error

        from apps.documents.services import _ollama_generate

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        with pytest.raises(RuntimeError, match="LLM unavailable"):
            _ollama_generate("Some prompt")

    def test_parse_llm_json_clean(self):
        from apps.documents.services import _parse_llm_json

        raw = '{"summary": "A test doc.", "extracted_data": {"key": "value"}}'
        result = _parse_llm_json(raw)

        assert result["summary"] == "A test doc."

    def test_parse_llm_json_with_markdown_fences(self):
        from apps.documents.services import _parse_llm_json

        raw = '```json\n{"summary": "Test.", "extracted_data": {}}\n```'
        result = _parse_llm_json(raw)

        assert result["summary"] == "Test."

    def test_parse_llm_json_with_plain_fences(self):
        from apps.documents.services import _parse_llm_json

        raw = '```\n{"summary": "Test.", "extracted_data": {}}\n```'
        result = _parse_llm_json(raw)

        assert result["summary"] == "Test."

    def test_parse_llm_json_invalid_raises(self):
        from apps.documents.services import _parse_llm_json

        with pytest.raises(json.JSONDecodeError):
            _parse_llm_json("not valid json at all")


class TestAnalyzeDocument:
    @patch("apps.documents.services._ollama_generate")
    def test_returns_summary_and_extracted_data(self, mock_ollama):
        from apps.documents.services import analyze_document

        mock_ollama.return_value = json.dumps({
            "summary": "A contract between parties.",
            "extracted_data": {"amount": "5000 EUR", "date": "2025-01-01"},
        })

        result = analyze_document("Contract text here.")

        assert result["summary"] == "A contract between parties."
        assert result["extracted_data"]["amount"] == "5000 EUR"

    @patch("apps.documents.services._ollama_generate")
    def test_handles_markdown_wrapped_json(self, mock_ollama):
        from apps.documents.services import analyze_document

        mock_ollama.return_value = '```json\n{"summary": "Wrapped.", "extracted_data": {}}\n```'

        result = analyze_document("Some text.")

        assert result["summary"] == "Wrapped."

    @patch("apps.documents.services._ollama_generate")
    def test_fallback_on_unparseable_response(self, mock_ollama):
        from apps.documents.services import analyze_document

        mock_ollama.return_value = "I cannot process this document right now."

        result = analyze_document("Some text.")

        assert isinstance(result["summary"], str)
        assert result["extracted_data"] == {}

    def test_empty_text_returns_default(self):
        from apps.documents.services import analyze_document

        result = analyze_document("")

        assert result["summary"] == "No text could be extracted."
        assert result["extracted_data"] == {}

    def test_whitespace_only_returns_default(self):
        from apps.documents.services import analyze_document

        result = analyze_document("   \n\t  ")

        assert result["summary"] == "No text could be extracted."

    @patch("apps.documents.services._ollama_generate")
    def test_text_is_truncated_to_8000_chars(self, mock_ollama):
        from apps.documents.services import analyze_document

        mock_ollama.return_value = json.dumps({"summary": "ok", "extracted_data": {}})

        long_text = "x" * 20000
        analyze_document(long_text)

        prompt_used = mock_ollama.call_args[0][0]
        assert len(prompt_used) < 20000


class TestAnswerQuestion:
    @patch("apps.documents.services._ollama_generate")
    def test_returns_answer(self, mock_ollama):
        from apps.documents.services import answer_question

        mock_ollama.return_value = "The amount is 5000 EUR."

        result = answer_question("Contract for 5000 EUR.", "What is the amount?")

        assert result == "The amount is 5000 EUR."

    @patch("apps.documents.services._ollama_generate")
    def test_caches_result(self, mock_ollama):
        from django.core.cache import cache
        from apps.documents.services import answer_question

        mock_ollama.return_value = "Cached answer."

        key = "test:services:cache:unique999"
        cache.delete(key)

        answer_question("Doc text.", "Question?", cache_key=key)
        answer_question("Doc text.", "Question?", cache_key=key)

        assert mock_ollama.call_count == 1

    @patch("apps.documents.services._ollama_generate")
    def test_no_cache_key_always_calls_ollama(self, mock_ollama):
        from apps.documents.services import answer_question

        mock_ollama.return_value = "Answer."

        answer_question("Doc.", "Question?")
        answer_question("Doc.", "Question?")

        assert mock_ollama.call_count == 2


class TestWebhooks:
    def test_sign_payload_is_deterministic(self):
        from apps.documents.services import sign_webhook_payload

        payload = '{"event": "test"}'

        assert sign_webhook_payload(payload) == sign_webhook_payload(payload)

    def test_verify_valid_signature(self):
        from apps.documents.services import sign_webhook_payload, verify_webhook_signature

        payload = '{"event": "document.processed"}'
        sig = sign_webhook_payload(payload)

        assert verify_webhook_signature(payload, sig) is True

    def test_verify_invalid_signature(self):
        from apps.documents.services import verify_webhook_signature

        assert verify_webhook_signature('{"event": "test"}', "badsig") is False

    def test_verify_tampered_payload(self):
        from apps.documents.services import sign_webhook_payload, verify_webhook_signature

        payload = '{"event": "document.processed", "document_id": "abc"}'
        sig = sign_webhook_payload(payload)
        tampered = '{"event": "document.processed", "document_id": "xyz"}'

        assert verify_webhook_signature(tampered, sig) is False

    @pytest.mark.django_db
    @patch("apps.documents.services.urllib.request.urlopen")
    def test_deliver_webhook_success(self, mock_urlopen):
        from apps.documents.models import Document
        from apps.documents.services import deliver_webhook
        from apps.users.models import User

        user = User.objects.create_user(username="wh", email="wh@test.com", password="pass12345")
        doc = Document.objects.create(
            owner=user,
            name="test.pdf",
            s3_key="test/test.pdf",
            file_size=100,
            mime_type="application/pdf",
            webhook_url="https://example.com/webhook",
            status=Document.Status.DONE,
            summary="A test document.",
        )

        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value.__enter__ = lambda s: mock_response
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        result = deliver_webhook(doc)

        assert result is True

    @pytest.mark.django_db
    @patch("apps.documents.services.urllib.request.urlopen")
    def test_deliver_webhook_http_error_returns_false(self, mock_urlopen):
        import urllib.error

        from apps.documents.models import Document
        from apps.documents.services import deliver_webhook
        from apps.users.models import User

        user = User.objects.create_user(username="wh2", email="wh2@test.com", password="pass12345")
        doc = Document.objects.create(
            owner=user,
            name="test.pdf",
            s3_key="test/test.pdf",
            file_size=100,
            mime_type="application/pdf",
            webhook_url="https://example.com/webhook",
        )
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        result = deliver_webhook(doc)

        assert result is False

    @pytest.mark.django_db
    def test_deliver_webhook_no_url_returns_false(self):
        from apps.documents.models import Document
        from apps.documents.services import deliver_webhook
        from apps.users.models import User

        user = User.objects.create_user(username="wh3", email="wh3@test.com", password="pass12345")
        doc = Document.objects.create(
            owner=user,
            name="test.pdf",
            s3_key="test/test.pdf",
            file_size=100,
            mime_type="application/pdf",
            webhook_url=None,
        )

        result = deliver_webhook(doc)

        assert result is False

    @pytest.mark.django_db
    @patch("apps.documents.services.urllib.request.urlopen")
    def test_deliver_webhook_includes_signature_header(self, mock_urlopen):
        from apps.documents.models import Document
        from apps.documents.services import deliver_webhook
        from apps.users.models import User

        user = User.objects.create_user(username="wh4", email="wh4@test.com", password="pass12345")
        doc = Document.objects.create(
            owner=user,
            name="test.pdf",
            s3_key="test/test.pdf",
            file_size=100,
            mime_type="application/pdf",
            webhook_url="https://example.com/webhook",
            status=Document.Status.DONE,
            summary="Test.",
        )

        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value.__enter__ = lambda s: mock_response
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        deliver_webhook(doc)

        request_obj = mock_urlopen.call_args[0][0]
        assert "X-docpulse-signature" in request_obj.headers
        assert "X-docpulse-event" in request_obj.headers

    @pytest.mark.django_db
    @patch("apps.documents.services.urllib.request.urlopen")
    def test_deliver_webhook_uses_owner_webhook_when_document_missing_one(self, mock_urlopen):
        from apps.documents.models import Document
        from apps.documents.services import deliver_webhook
        from apps.users.models import User

        user = User.objects.create_user(
            username="ownerwh",
            email="ownerwh@test.com",
            password="pass12345",
            webhook_url="https://example.com/owner-webhook",
        )
        doc = Document.objects.create(
            owner=user,
            name="test.pdf",
            s3_key="test/test.pdf",
            file_size=100,
            mime_type="application/pdf",
            webhook_url=None,
            status=Document.Status.DONE,
        )

        mock_response = MagicMock()
        mock_response.status = 204
        mock_urlopen.return_value.__enter__ = lambda s: mock_response
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        result = deliver_webhook(doc)

        assert result is True
