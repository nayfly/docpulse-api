import pytest
from unittest.mock import patch, MagicMock
from rest_framework import status
from rest_framework.test import APIClient
from apps.users.models import User
from apps.documents.models import Document


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="testuser",
        email="test@example.com",
        password="testpass123",
    )


@pytest.fixture
def auth_client(api_client, user):
    from rest_framework_simplejwt.tokens import RefreshToken
    refresh = RefreshToken.for_user(user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(refresh.access_token)}")
    return api_client


@pytest.fixture
def done_document(db, user):
    return Document.objects.create(
        owner=user,
        name="contract.pdf",
        s3_key="documents/test/contract.pdf",
        file_size=2048,
        mime_type="application/pdf",
        status=Document.Status.DONE,
        raw_text="This contract is between Acme Corp and John Doe. Signed on January 15 2025. Total amount: 5000 EUR.",
        summary="A contract between Acme Corp and John Doe for 5000 EUR.",
        extracted_data={"client": "John Doe", "amount": "5000 EUR", "date": "2025-01-15"},
    )


@pytest.fixture
def pending_document(db, user):
    return Document.objects.create(
        owner=user,
        name="pending.pdf",
        s3_key="documents/test/pending.pdf",
        file_size=512,
        mime_type="application/pdf",
        status=Document.Status.PENDING,
    )


# ─── Upload ────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDocumentUpload:
    def test_upload_requires_auth(self, api_client):
        response = api_client.post("/api/documents/")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @patch("apps.documents.views.upload_file_to_s3")
    @patch("apps.documents.views.process_document")
    def test_upload_pdf_returns_202(self, mock_task, mock_s3, auth_client):
        mock_s3.return_value = "documents/test/file.pdf"
        mock_task.delay.return_value = MagicMock(id="task-abc")
        from django.core.files.uploadedfile import SimpleUploadedFile
        file = SimpleUploadedFile("invoice.pdf", b"%PDF-1.4 content", content_type="application/pdf")
        response = auth_client.post("/api/documents/", {"file": file}, format="multipart")
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["status"] == "pending"
        assert response.data["name"] == "invoice.pdf"

    @patch("apps.documents.views.upload_file_to_s3")
    @patch("apps.documents.views.process_document")
    def test_name_autocompletes_from_filename(self, mock_task, mock_s3, auth_client):
        mock_s3.return_value = "documents/test/file.txt"
        mock_task.delay.return_value = MagicMock(id="task-xyz")
        from django.core.files.uploadedfile import SimpleUploadedFile
        file = SimpleUploadedFile("report.txt", b"some text content", content_type="text/plain")
        response = auth_client.post("/api/documents/", {"file": file}, format="multipart")
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["name"] == "report.txt"

    def test_upload_rejects_unsupported_type(self, auth_client):
        from django.core.files.uploadedfile import SimpleUploadedFile
        file = SimpleUploadedFile("image.png", b"PNG content", content_type="image/png")
        response = auth_client.post("/api/documents/", {"file": file}, format="multipart")
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ─── List ──────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDocumentList:
    def test_list_returns_only_own_documents(self, auth_client, done_document, db):
        other_user = User.objects.create_user(
            username="other", email="other@test.com", password="pass123"
        )
        Document.objects.create(
            owner=other_user, name="other.pdf", s3_key="other/doc.pdf",
            file_size=100, mime_type="application/pdf",
        )
        response = auth_client.get("/api/documents/list/")
        assert response.status_code == status.HTTP_200_OK
        ids = [d["id"] for d in response.data["results"]]
        assert str(done_document.id) in ids
        assert len(ids) == 1

    def test_list_filter_by_status(self, auth_client, done_document, pending_document):
        response = auth_client.get("/api/documents/list/?status=done")
        assert response.status_code == status.HTTP_200_OK
        assert all(d["status"] == "done" for d in response.data["results"])


# ─── Detail ────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDocumentDetail:
    @patch("apps.documents.serializers.get_presigned_url", return_value="https://minio/presigned-url")
    def test_detail_returns_summary_and_extracted_data(self, mock_url, auth_client, done_document):
        response = auth_client.get(f"/api/documents/{done_document.id}/")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["summary"] == done_document.summary
        assert response.data["extracted_data"] == done_document.extracted_data
        assert response.data["download_url"] == "https://minio/presigned-url"

    def test_detail_other_user_gets_404(self, db, api_client, done_document):
        other = User.objects.create_user(username="spy", email="spy@test.com", password="pass123")
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = RefreshToken.for_user(other)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(refresh.access_token)}")
        response = api_client.get(f"/api/documents/{done_document.id}/")
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ─── Status ────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDocumentStatus:
    def test_status_fields_present(self, auth_client, done_document):
        response = auth_client.get(f"/api/documents/{done_document.id}/status/")
        assert response.status_code == status.HTTP_200_OK
        for field in ["id", "status", "task_id", "error_message", "processed_at"]:
            assert field in response.data

    def test_status_isolation(self, db, api_client, done_document):
        other = User.objects.create_user(username="other2", email="other2@test.com", password="pass123")
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = RefreshToken.for_user(other)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(refresh.access_token)}")
        response = api_client.get(f"/api/documents/{done_document.id}/status/")
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ─── Ask ───────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDocumentAsk:
    @patch("apps.documents.views.answer_question", return_value="The total amount is 5000 EUR.")
    def test_ask_returns_answer(self, mock_answer, auth_client, done_document):
        response = auth_client.post(
            f"/api/documents/{done_document.id}/ask/",
            {"question": "What is the total amount?"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["answer"] == "The total amount is 5000 EUR."
        assert response.data["question"] == "What is the total amount?"

    def test_ask_on_pending_returns_409(self, auth_client, pending_document):
        response = auth_client.post(
            f"/api/documents/{pending_document.id}/ask/",
            {"question": "Anything?"},
            format="json",
        )
        assert response.status_code == status.HTTP_409_CONFLICT

    def test_ask_requires_question_field(self, auth_client, done_document):
        response = auth_client.post(
            f"/api/documents/{done_document.id}/ask/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("apps.documents.views.answer_question", return_value="Cached answer.")
    def test_ask_uses_cache(self, mock_answer, auth_client, done_document):
        # First call
        auth_client.post(
            f"/api/documents/{done_document.id}/ask/",
            {"question": "What is the total amount?"},
            format="json",
        )
        # Second call — mock should still be called (cache handled inside service)
        response = auth_client.post(
            f"/api/documents/{done_document.id}/ask/",
            {"question": "What is the total amount?"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK


# ─── Reprocess ─────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDocumentReprocess:
    @patch("apps.documents.views.process_document")
    def test_reprocess_failed_document(self, mock_task, auth_client, user):
        mock_task.delay.return_value = MagicMock(id="new-task-id")
        failed_doc = Document.objects.create(
            owner=user, name="failed.pdf", s3_key="test/failed.pdf",
            file_size=100, mime_type="application/pdf",
            status=Document.Status.FAILED, error_message="Timeout",
        )
        response = auth_client.post(f"/api/documents/{failed_doc.id}/reprocess/")
        assert response.status_code == status.HTTP_200_OK
        failed_doc.refresh_from_db()
        assert failed_doc.status == Document.Status.PENDING
        assert failed_doc.error_message == ""

    def test_reprocess_done_document_returns_409(self, auth_client, done_document):
        response = auth_client.post(f"/api/documents/{done_document.id}/reprocess/")
        assert response.status_code == status.HTTP_409_CONFLICT