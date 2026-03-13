import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="documents.process_document",
)
def process_document(self, document_id: str):
    from apps.documents.models import Document
    from apps.documents.services import (
        download_file_from_s3,
        extract_text,
        analyze_document,
        deliver_webhook,
    )

    try:
        doc = Document.objects.get(id=document_id)
    except Document.DoesNotExist:
        logger.error(f"Document {document_id} not found — aborting task.")
        return

    doc.status = Document.Status.PROCESSING
    doc.task_id = self.request.id
    doc.save(update_fields=["status", "task_id", "updated_at"])

    try:
        file_bytes = download_file_from_s3(doc.s3_key)
        raw_text = extract_text(file_bytes, doc.mime_type)
        result = analyze_document(raw_text)

        doc.raw_text = raw_text
        doc.summary = result["summary"]
        doc.extracted_data = result["extracted_data"]
        doc.status = Document.Status.DONE
        doc.processed_at = timezone.now()
        doc.error_message = ""
        doc.save(update_fields=[
            "raw_text", "summary", "extracted_data",
            "status", "processed_at", "error_message", "updated_at"
        ])

        delivered = deliver_webhook(doc)
        if delivered:
            doc.webhook_delivered = True
            doc.save(update_fields=["webhook_delivered"])

    except Exception as exc:
        logger.exception(f"[{document_id}] Processing failed: {exc}")
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            doc.status = Document.Status.FAILED
            doc.error_message = str(exc)
            doc.save(update_fields=["status", "error_message", "updated_at"])
