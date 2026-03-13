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
def document(db, user):
    return Document.objects.create(
        owner=user,
        name="test.pdf",
        s3_key="documents/test/test.pdf",
        file_size=1024,
        mime_type="application/pdf",
        status=Document.Status.DONE,
        raw_text="This is a test document about Python and Django.",
        summary="A test document.",
        extracted_data={"topic": "Python"},
    )


@pytest.mark.django_db
class TestDocumentUpload:
    def test_upload_requires_auth(self, api_client):
        response = api_client.post("/api/documents/")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @patch("apps.documents.views.upload_file_to_s3")
    @patch("apps.documents.views.process_document")
    def test_upload_success(self, mock_task, mock_s3, auth_client):
        mock_s3.return_value = "documents/test/file.pdf"
        mock_task.delay.return_value = MagicMock(id="task-123")
        from django.core.files.uploadedfile import SimpleUploadedFile
        file = SimpleUploadedFile("test.pdf", b"%PDF-1.4 content", content_type="application/pdf")
        response = auth_client.post("/api/documents/", {"file": file}, format="multipart")
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["status"] == "pending"


@pytest.mark.django_db
class TestDocumentStatus:
    def test_status_returns_correct_fields(self, auth_client, document):
        response = auth_client.get(f"/api/documents/{document.id}/status/")
        assert response.status_code == status.HTTP_200_OK
        assert "status" in response.data

    def test_cannot_access_other_users_document(self, db, api_client, document):
        other = User.objects.create_user(username="other", email="other@test.com", password="pass123")
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = RefreshToken.for_user(other)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(refresh.access_token)}")
        response = api_client.get(f"/api/documents/{document.id}/status/")
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestDocumentAsk:
    @patch("apps.documents.views.answer_question")
    def test_ask_returns_answer(self, mock_answer, auth_client, document):
        mock_answer.return_value = "The document is about Python and Django."
        response = auth_client.post(
            f"/api/documents/{document.id}/ask/",
            {"question": "What is this about?"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert "answer" in response.data

    def test_ask_fails_if_not_done(self, auth_client, user):
        pending_doc = Document.objects.create(
            owner=user, name="pending.pdf", s3_key="test/pending.pdf",
            file_size=100, mime_type="application/pdf", status=Document.Status.PENDING,
        )
        response = auth_client.post(
            f"/api/documents/{pending_doc.id}/ask/",
            {"question": "What is this?"},
            format="json",
        )
        assert response.status_code == status.HTTP_409_CONFLICT
