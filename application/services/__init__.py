"""Публичный API application-сервисов."""
from .admin import AdminCommandResult, AdminService
from .diagnostics import DiagnosticResult, DiagnosticsService
from .relay import RelayResult, RelayService, RelayTextRequest
from .reply import ReplyRelayRequest, ReplyRelayResult, ReplyService

__all__ = [
    "AdminCommandResult",
    "AdminService",
    "DiagnosticResult",
    "DiagnosticsService",
    "RelayResult",
    "RelayService",
    "RelayTextRequest",
    "ReplyRelayRequest",
    "ReplyRelayResult",
    "ReplyService",
]
