"""
Конфигурация бота.

Секреты и параметры запуска читаются из переменных окружения или .env.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Telegram
    BOT_TOKEN: str
    ADMIN_ID: int
    TELEGRAM_PROXY_URL: str | None = None

    # VK adapter MVP. По умолчанию выключен и не подключается к runtime.
    VK_ENABLED: bool = False
    VK_GROUP_ID: int | None = None
    VK_GROUP_TOKEN: str | None = None
    VK_API_VERSION: str = "5.199"
    VK_LONGPOLL_WAIT: int = 25

    # Web/Site adapter MVP. По умолчанию выключен и не подключается к runtime.
    WEB_ENABLED: bool = False
    WEB_API_TOKEN: str | None = None
    WEB_PUBLIC_BASE_URL: str | None = None

    # Communars SiteFeedback: bot-side only until web integration (Patch C).
    SITE_FEEDBACK_BASE_URL: str = "https://communars.itcheckup.pro"

    # Cross-server sync: outbound HTTPS only, disabled unless explicitly configured.
    SITE_FEEDBACK_SYNC_URL: str = ""
    SITE_FEEDBACK_SYNC_TOKEN: str = ""
    SITE_FEEDBACK_SYNC_INTERVAL: int = 20

    # MongoDB
    MONGODB_URI: str = "mongodb://localhost:27017"
    DB_NAME: str = "tgbot"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def telegram_proxy_url(self) -> str | None:
        """Вернуть URL proxy или None, если proxy отключён пустым значением."""
        if self.TELEGRAM_PROXY_URL is None:
            return None
        value = self.TELEGRAM_PROXY_URL.strip()
        return value or None


settings = Settings()
