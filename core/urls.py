
from django.urls import path
from .views import TankListView, CalculateBlendView, ChatView, ChatHistoryView, TerminalManagementView, TerminalUploadView
from . import vault_views

urlpatterns = [
    path("tanks/", TankListView.as_view(), name="tank-list"),
    path("calculate/", CalculateBlendView.as_view(), name="calculate-blend"),
    path("chat/", ChatView.as_view(), name="chat"),
    path("chat/history/", ChatHistoryView.as_view(), name="chat-history-list"),
    path("chat/history/<int:session_id>/", ChatHistoryView.as_view(), name="chat-history-detail"),
    path("terminals/", TerminalManagementView.as_view(), name="terminal-list"),
    path("terminals/<int:terminal_id>/", TerminalManagementView.as_view(), name="terminal-detail"),
    path("terminals/<int:terminal_id>/upload/", TerminalUploadView.as_view(), name="terminal-upload"),
    path('vault/download/', vault_views.vault_download, name='vault_download'),
    path('vault/upload/', vault_views.vault_upload, name='vault_upload'),
    path('vault/status/', vault_views.vault_status, name='vault_status'),
]
