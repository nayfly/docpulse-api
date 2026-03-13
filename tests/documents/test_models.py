import pytest

from apps.documents.models import Document
from apps.users.models import User


@pytest.mark.django_db
def test_document_str_and_is_processable():
    user = User.objects.create_user(
        username="docmodel",
        email="docmodel@test.com",
        password="pass12345",
    )
    document = Document.objects.create(
        owner=user,
        name="invoice.pdf",
        s3_key="documents/test/invoice.pdf",
        file_size=100,
        mime_type="application/pdf",
        status=Document.Status.PENDING,
    )

    assert str(document) == "invoice.pdf [pending]"
    assert document.is_processable is True


@pytest.mark.django_db
def test_document_done_is_not_processable():
    user = User.objects.create_user(
        username="docmodel2",
        email="docmodel2@test.com",
        password="pass12345",
    )
    document = Document.objects.create(
        owner=user,
        name="report.pdf",
        s3_key="documents/test/report.pdf",
        file_size=100,
        mime_type="application/pdf",
        status=Document.Status.DONE,
    )

    assert document.is_processable is False
