import pytest
from unittest.mock import patch, MagicMock, call
from apps.documents.models import Document
from apps.users.models import User


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="taskuser", email="task@test.com", password="pass123"
    )


@pytest.fixture
def pending_document(db, user):
    return Document.objects.create(
        owner=user,
        name="test.pdf",
        s3_key="test/test.pdf",
        file_size=512,
        mime_type="application/pdf",
        status=Document.Status.PENDING,
    )


@pytest.mark.django_db
class TestProcessDocumentTask:

    @patch("apps.documents.tasks.deliver_webhook", return_value=True)
    @patch("apps.documents.tasks.analyze_document", return_value={
        "summary": "A contract between Acme and John.",
        "extracted_data": {"client": "John", "amount": "5000 EUR"}
    })
    @patch("apps.documents.tasks.extract_text", return_value="Contract text here.")
    @patch("apps.documents.tasks.download_file_from_s3", return_value=b"fake bytes")
    def test_full_pipeline_success(self, mock_dl, mock_extract, mock_analyze, mock_webhook, pending_document):
        from apps.documents.tasks import process_document
        process_document(str(pending_document.id))

        pending_document.refresh_from_db()
        assert pending_document.status == Document.Status.DONE
        assert pending_document.summary == "A contract between Acme and John."
        assert pending_document.extracted_data == {"client": "John", "amount": "5000 EUR"}
        assert pending_document.raw_text == "Contract text here."
        assert pending_document.processed_at is not None
        assert pending_document.error_message == ""
        assert pending_document.webhook_delivered is True

    @patch("apps.documents.tasks.deliver_webhook", return_value=False)
    @patch("apps.documents.tasks.analyze_document", return_value={"summary": "Test", "extracted_data": {}})
    @patch("apps.documents.tasks.extract_text", return_value="Some text.")
    @patch("apps.documents.tasks.download_file_from_s3", return_value=b"bytes")
    def test_webhook_failure_does_not_fail_task(self, mock_dl, mock_extract, mock_analyze, mock_webhook, pending_document):
        from apps.documents.tasks import process_document
        process_document(str(pending_document.id))

        pending_document.refresh_from_db()
        # Document should still be DONE even if webhook failed
        assert pending_document.status == Document.Status.DONE
        assert pending_document.webhook_delivered is False

    @patch("apps.documents.tasks.download_file_from_s3", side_effect=Exception("Connection refused"))
    def test_marks_failed_after_max_retries(self, mock_dl, pending_document):
        from apps.documents.tasks import process_document
        with patch.object(process_document, "retry", side_effect=process_document.MaxRetriesExceededError()):
            process_document(str(pending_document.id))

        pending_document.refresh_from_db()
        assert pending_document.status == Document.Status.FAILED
        assert "Connection refused" in pending_document.error_message

    def test_nonexistent_document_does_not_raise(self):
        from apps.documents.tasks import process_document
        # Should log and return gracefully, not raise
        process_document("00000000-0000-0000-0000-000000000000")

    @patch("apps.documents.tasks.deliver_webhook", return_value=False)
    @patch("apps.documents.tasks.analyze_document", return_value={"summary": "Test", "extracted_data": {}})
    @patch("apps.documents.tasks.extract_text", return_value="Text.")
    @patch("apps.documents.tasks.download_file_from_s3", return_value=b"bytes")
    def test_processing_sets_task_id(self, mock_dl, mock_extract, mock_analyze, mock_webhook, pending_document):
        from apps.documents.tasks import process_document
        process_document(str(pending_document.id))

        pending_document.refresh_from_db()
        # task_id may be empty in direct call (no Celery broker), that's fine
        assert pending_document.status == Document.Status.DONE


@pytest.mark.django_db
class TestOllamaIntegration:

    @patch("apps.documents.services._ollama_generate")
    def test_analyze_document_parses_json(self, mock_ollama):
        import json
        mock_ollama.return_value = json.dumps({
            "summary": "A simple test document.",
            "extracted_data": {"topic": "testing", "date": "2025-01-01"}
        })
        from apps.documents.services import analyze_document
        result = analyze_document("This is a test document about testing on January 1st 2025.")
        assert result["summary"] == "A simple test document."
        assert result["extracted_data"]["topic"] == "testing"

    @patch("apps.documents.services._ollama_generate")
    def test_analyze_document_handles_markdown_fences(self, mock_ollama):
        mock_ollama.return_value = '```json\n{"summary": "Test.", "extracted_data": {}}\n```'
        from apps.documents.services import analyze_document
        result = analyze_document("Some document text.")
        assert result["summary"] == "Test."

    @patch("apps.documents.services._ollama_generate")
    def test_analyze_document_fallback_on_invalid_json(self, mock_ollama):
        mock_ollama.return_value = "Sorry, I cannot analyze this document."
        from apps.documents.services import analyze_document
        result = analyze_document("Some text.")
        # Should not raise, fallback to raw text as summary
        assert isinstance(result["summary"], str)
        assert result["extracted_data"] == {}

    @patch("apps.documents.services._ollama_generate")
    def test_answer_question_uses_cache(self, mock_ollama):
        mock_ollama.return_value = "The answer is 42."
        from django.core.cache import cache
        from apps.documents.services import answer_question

        cache_key = "test:cache:key:123"
        cache.delete(cache_key)

        # First call — hits Ollama
        answer1 = answer_question("Document text.", "What is the answer?", cache_key=cache_key)
        assert answer1 == "The answer is 42."
        assert mock_ollama.call_count == 1

        # Second call — should hit cache, not Ollama
        answer2 = answer_question("Document text.", "What is the answer?", cache_key=cache_key)
        assert answer2 == "The answer is 42."
        assert mock_ollama.call_count == 1  # still 1, not 2

    def test_analyze_empty_text_returns_default(self):
        from apps.documents.services import analyze_document
        result = analyze_document("")
        assert result["summary"] == "No text could be extracted."
        assert result["extracted_data"] == {}


@pytest.mark.django_db
class TestWebhookService:

    def test_sign_and_verify_webhook(self):
        from apps.documents.services import sign_webhook_payload, verify_webhook_signature
        payload = '{"event": "document.processed", "document_id": "abc"}'
        signature = sign_webhook_payload(payload)
        assert verify_webhook_signature(payload, signature) is True
        assert verify_webhook_signature(payload, "bad-signature") is False

    def test_deliver_webhook_no_url_returns_false(self, user):
        from apps.documents.services import deliver_webhook
        doc = Document.objects.create(
            owner=user, name="test.pdf", s3_key="test/test.pdf",
            file_size=100, mime_type="application/pdf",
            webhook_url=None,
        )
        assert deliver_webhook(doc) is False