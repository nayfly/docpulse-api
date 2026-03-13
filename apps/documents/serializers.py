from rest_framework import serializers
from .models import Document
from .services import get_presigned_url


class DocumentUploadSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True)
    webhook_url = serializers.URLField(required=False, allow_blank=True)

    class Meta:
        model = Document
        fields = ["id", "name", "file", "webhook_url", "status", "created_at"]
        read_only_fields = ["id", "status", "created_at"]
        extra_kwargs = {
            "name": {"required": False, "allow_blank": True},
        }

    def validate(self, data):
        if not data.get("name"):
            data["name"] = data["file"].name
        return data

    def validate_file(self, value):
        allowed_types = ["application/pdf", "text/plain", "text/csv"]
        max_size = 10 * 1024 * 1024
        if value.content_type not in allowed_types:
            raise serializers.ValidationError(
                f"Unsupported file type: {value.content_type}. Allowed: PDF, plain text, CSV."
            )
        if value.size > max_size:
            raise serializers.ValidationError("File too large. Max size is 10 MB.")
        return value


class DocumentListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ["id", "name", "status", "file_size", "mime_type", "created_at", "processed_at"]


class DocumentDetailSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            "id", "name", "status", "file_size", "mime_type",
            "summary", "extracted_data", "task_id",
            "error_message", "webhook_url", "webhook_delivered",
            "created_at", "updated_at", "processed_at",
            "download_url",
        ]

    def get_download_url(self, obj):
        try:
            return get_presigned_url(obj.s3_key)
        except Exception:
            return None


class DocumentStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ["id", "status", "task_id", "error_message", "processed_at", "updated_at"]


class AskQuestionSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=1000)
