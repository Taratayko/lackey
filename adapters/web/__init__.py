"""Web/Site adapter MVP."""
from .adapter import MemoryWebOutbox, WebChannelAdapter, WebOutboxPort
from .api import WebApiAdapter, WebIncomingHandler
from .auth import WebAuthError, validate_web_api_token
from .mapper import domain_message_to_web_dict, web_dict_to_domain, web_payload_to_domain
from .schemas import WebAttachmentPayload, WebAuthorPayload, WebIncomingMessagePayload

__all__ = [
    "MemoryWebOutbox",
    "WebChannelAdapter",
    "WebOutboxPort",
    "WebApiAdapter",
    "WebIncomingHandler",
    "WebAuthError",
    "validate_web_api_token",
    "domain_message_to_web_dict",
    "web_dict_to_domain",
    "web_payload_to_domain",
    "WebAttachmentPayload",
    "WebAuthorPayload",
    "WebIncomingMessagePayload",
]
