import pytest
from unittest.mock import patch
from apps.documents.models import Document
from apps.users.models import User


@pytest.fixture
def user(db):
    return User.objects.create_user(username="taskuser", email="task@test.com", password="pass123")


@pytest.fixture
def pending_document(db, user):
    return Document.objects.create(
        owner=user, name="test.pdf", s3_key="test/test.pdf",
        file_size=512, mime_type="application/pdf", status=Document.Status.PENDING,
    )


@pytest.mark.django_db
class TestProcessDocumentTask:
    @patch("apps.documents.tasks.deliver_webhook", return_value=False)
    @patch("apps.documents.tasks.analyze_document", return_value={"summary": "Test summary", "extracted_data": {"key": "value"}})
    @patch("apps.documents.tasks.extract_text", return_value="Extracted text content")
    @patch("apps.documents.tasks.download_file_from_s3", return_value=b"fake pdf bytes")
    def test_successful_processing(self, mock_dl, mock_extract, mock_analyze, mock_webhook, pending_document):
        from apps.documents.tasks import process_document
        process_document(str(pending_document.id))
        pending_document.refresh_from_db()
        assert pending_document.status == Document.Status.DONE
        assert pending_document.summary == "Test summary"

    @patch("apps.documents.tasks.download_file_from_s3", side_effect=Exception("S3 error"))
    def test_marks_failed_after_max_retries(self, mock_dl, pending_document):
        from apps.documents.tasks import process_document
        with patch.object(process_document, "retry", side_effect=process_document.MaxRetriesExceededError()):
            process_document(str(pending_document.id))
        pending_document.refresh_from_db()
        assert pending_document.status == Document.Status.FAILED
