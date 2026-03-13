from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.documents.models import Document
from apps.documents.serializers import DocumentDetailSerializer, DocumentUploadSerializer
from apps.users.models import User


@pytest.mark.django_db
def test_upload_serializer_rejects_file_too_large():
    file_obj = SimpleUploadedFile(
        "large.txt",
        b"a" * (10 * 1024 * 1024 + 1),
        content_type="text/plain",
    )

    serializer = DocumentUploadSerializer(data={"file": file_obj})

    assert serializer.is_valid() is False
    assert "file" in serializer.errors


@pytest.mark.django_db
@patch("apps.documents.serializers.get_presigned_url", side_effect=Exception("boom"))
def test_detail_serializer_returns_none_when_presign_fails(_mock_url):
    user = User.objects.create_user(
        username="seruser",
        email="seruser@test.com",
        password="pass12345",
    )
    document = Document.objects.create(
        owner=user,
        name="invoice.pdf",
        s3_key="documents/test/invoice.pdf",
        file_size=100,
        mime_type="application/pdf",
    )

    serializer = DocumentDetailSerializer(document)

    assert serializer.data["download_url"] is None
