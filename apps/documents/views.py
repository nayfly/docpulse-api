import hashlib
import logging
import uuid

from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from .models import Document
from .permissions import IsDocumentOwner
from .serializers import (
    AskQuestionSerializer,
    DocumentDetailSerializer,
    DocumentListSerializer,
    DocumentStatusSerializer,
    DocumentUploadSerializer,
)
from .services import upload_file_to_s3
from .tasks import process_document

logger = logging.getLogger(__name__)


class UploadThrottle(UserRateThrottle):
    scope = "document_upload"


class AskThrottle(UserRateThrottle):
    scope = "document_ask"


class DocumentUploadView(APIView):
    throttle_classes = [UploadThrottle]

    def post(self, request):
        serializer = DocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        file = serializer.validated_data["file"]
        s3_key = f"documents/{request.user.id}/{uuid.uuid4()}/{file.name}"

        upload_file_to_s3(file, s3_key, file.content_type)

        doc = Document.objects.create(
            owner=request.user,
            name=file.name,
            s3_key=s3_key,
            file_size=file.size,
            mime_type=file.content_type,
            webhook_url=serializer.validated_data.get("webhook_url") or request.user.webhook_url,
        )

        task = process_document.delay(str(doc.id))
        doc.task_id = task.id
        doc.save(update_fields=["task_id"])

        return Response(DocumentUploadSerializer(doc).data, status=status.HTTP_202_ACCEPTED)


class DocumentListView(ListAPIView):
    serializer_class = DocumentListSerializer

    def get_queryset(self):
        qs = Document.objects.filter(owner=self.request.user)
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs


class DocumentDetailView(RetrieveAPIView):
    serializer_class = DocumentDetailSerializer

    def get_queryset(self):
        return Document.objects.filter(owner=self.request.user)

    def get_permissions(self):
        return [IsDocumentOwner()]


class DocumentStatusView(RetrieveAPIView):
    serializer_class = DocumentStatusSerializer

    def get_queryset(self):
        return Document.objects.filter(owner=self.request.user)


class DocumentAskView(APIView):
    throttle_classes = [AskThrottle]

    def post(self, request, pk):
        try:
            doc = Document.objects.get(pk=pk, owner=request.user)
        except Document.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if doc.status != Document.Status.DONE:
            return Response(
                {"detail": f"Document is not ready. Current status: {doc.status}"},
                status=status.HTTP_409_CONFLICT,
            )

        if not doc.raw_text:
            return Response({"detail": "No text content available."}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        serializer = AskQuestionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        question = serializer.validated_data["question"]

        cache_key = f"docpulse:qa:{hashlib.md5(f'{doc.id}{question}'.encode()).hexdigest()}"

        from .services import answer_question
        answer = answer_question(doc.raw_text, question, cache_key=cache_key)

        return Response({"question": question, "answer": answer})


class DocumentReprocessView(APIView):
    def post(self, request, pk):
        try:
            doc = Document.objects.get(pk=pk, owner=request.user)
        except Document.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if not doc.is_processable:
            return Response(
                {"detail": f"Cannot reprocess a document with status '{doc.status}'."},
                status=status.HTTP_409_CONFLICT,
            )

        doc.status = Document.Status.PENDING
        doc.error_message = ""
        doc.save(update_fields=["status", "error_message", "updated_at"])

        task = process_document.delay(str(doc.id))
        doc.task_id = task.id
        doc.save(update_fields=["task_id"])

        return Response({"detail": "Reprocessing started.", "task_id": task.id})
