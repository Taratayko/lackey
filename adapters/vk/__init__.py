"""VK adapter package.

Patch 12 добавляет изолированный VK MVP-слой, но не подключает его к runtime
Telegram-бота. Реальный запуск VK Long Poll будет отдельным патчем после
проверки текущего Telegram core.
"""
from .adapter import VkChannelAdapter
from .client import VkApiClient, VkApiError, VkLongPollServer
from .longpoll import VkLongPollRunner
from .mapper import vk_message_update_to_domain

__all__ = [
    "VkApiClient",
    "VkApiError",
    "VkLongPollServer",
    "VkChannelAdapter",
    "VkLongPollRunner",
    "vk_message_update_to_domain",
]
