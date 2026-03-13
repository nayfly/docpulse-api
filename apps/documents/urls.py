from django.urls import path
from .views import (
    DocumentUploadView,
    DocumentListView,
    DocumentDetailView,
    DocumentStatusView,
    DocumentAskView,
    DocumentReprocessView,
)

urlpatterns = [
    path("documents/", DocumentUploadView.as_view(), name="document-upload"),
    path("documents/list/", DocumentListView.as_view(), name="document-list"),
    path("documents/<uuid:pk>/", DocumentDetailView.as_view(), name="document-detail"),
    path("documents/<uuid:pk>/status/", DocumentStatusView.as_view(), name="document-status"),
    path("documents/<uuid:pk>/ask/", DocumentAskView.as_view(), name="document-ask"),
    path("documents/<uuid:pk>/reprocess/", DocumentReprocessView.as_view(), name="document-reprocess"),
]
