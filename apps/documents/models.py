import uuid
from django.db import models
from django.conf import settings


class Document(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="documents")
    name = models.CharField(max_length=255)
    s3_key = models.CharField(max_length=512)
    file_size = models.PositiveIntegerField(help_text="Size in bytes")
    mime_type = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    summary = models.TextField(blank=True)
    extracted_data = models.JSONField(default=dict, blank=True)
    raw_text = models.TextField(blank=True)
    task_id = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)
    webhook_url = models.URLField(blank=True, null=True)
    webhook_delivered = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} [{self.status}]"

    @property
    def is_processable(self):
        return self.status in (self.Status.PENDING, self.Status.FAILED)
