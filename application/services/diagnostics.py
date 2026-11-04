"""DiagnosticsService для runtime health-check бота.

Сервис собирает короткий отчёт по MongoDB, зарегистрированным чатам,
thread mapping и служебным коллекциям. Команда нужна после серии рефакторингов,
чтобы быстро отличать проблемы запуска/БД/роутеров от проблем бизнес-логики.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from presentation.formatters import escape_html


EXPECTED_COLLECTIONS: tuple[str, ...] = (
    "chats",
    "threads",
    "stats",
    "dm_users",
    "dm_sessions",
    "delivery_claims",
    "stats_daily",
)


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    """Результат diagnostic-команды."""

    text: str | None = None


class DiagnosticsService:
    """Application-сервис диагностики runtime-состояния."""

    def __init__(self, database: Any, owner_user_id: int) -> None:
        self._database = database
        self._owner_user_id = owner_user_id

    def is_owner(self, user_id: int | None) -> bool:
        return user_id == self._owner_user_id

    async def render_health_text(
        self,
        actor_user_id: int | None,
        *,
        current_chat_id: int | None = None,
        bot_username: str | None = None,
    ) -> DiagnosticResult | None:
        """Собрать HTML-отчёт для /diag и /health.

        Команда intentionally owner-only: отчёт содержит chat_id и сведения о БД.
        """
        if not self.is_owner(actor_user_id):
            return None

        lines: list[str] = ["<b>🩺 Lackey diagnostics</b>"]
        lines.append(f"time_utc: <code>{datetime.now(timezone.utc).isoformat(timespec='seconds')}</code>")
        if bot_username:
            lines.append(f"bot: @{escape_html(bot_username)}")

        mongo = getattr(self._database, "_db", None)
        if mongo is None:
            lines.append("\n<b>MongoDB</b>")
            lines.append("❌ database handle is not initialized")
            return DiagnosticResult("\n".join(lines))

        lines.append("\n<b>MongoDB</b>")
        try:
            await mongo.command("ping")
            lines.append("✅ ping ok")
        except Exception as exc:  # pragma: no cover - depends on runtime Mongo
            lines.append(f"❌ ping failed: <code>{escape_html(type(exc).__name__)}</code>")
            lines.append(escape_html(str(exc))[:500])
            return DiagnosticResult("\n".join(lines))

        collection_names = await self._safe_collection_names(mongo)
        lines.append(f"db: <code>{escape_html(getattr(mongo, 'name', 'unknown'))}</code>")
        lines.extend(await self._collections_block(mongo, collection_names))
        lines.extend(await self._chats_block(current_chat_id))
        lines.extend(await self._threads_block(mongo))
        lines.extend(await self._claims_block(mongo))
        lines.extend(await self._indexes_block(mongo))

        return DiagnosticResult("\n".join(lines))

    async def _safe_collection_names(self, mongo: Any) -> set[str]:
        try:
            return set(await mongo.list_collection_names())
        except Exception:
            return set()

    async def _collections_block(self, mongo: Any, collection_names: set[str]) -> list[str]:
        lines = ["\n<b>Collections</b>"]
        for name in EXPECTED_COLLECTIONS:
            if name not in collection_names:
                lines.append(f"⚠️ <code>{name}</code>: missing")
                continue
            try:
                count = await mongo[name].count_documents({})
                lines.append(f"✅ <code>{name}</code>: {count}")
            except Exception as exc:  # pragma: no cover - depends on runtime Mongo
                lines.append(f"❌ <code>{name}</code>: {escape_html(type(exc).__name__)}")
        return lines

    async def _chats_block(self, current_chat_id: int | None) -> list[str]:
        lines = ["\n<b>Registered chats</b>"]
        try:
            chats = await self._database.get_all_chats()
        except Exception as exc:  # pragma: no cover - depends on runtime Mongo
            return [*lines, f"❌ cannot read chats: <code>{escape_html(type(exc).__name__)}</code>"]

        lines.append(f"total: {len(chats)}")
        groups: dict[str, int] = {}
        missing_name = 0
        missing_slug = 0
        missing_group = 0
        duplicate_slugs: dict[str, int] = {}

        for chat in chats:
            group = str(chat.get("group") or "<empty>")
            groups[group] = groups.get(group, 0) + 1
            if not chat.get("name"):
                missing_name += 1
            if not chat.get("slug"):
                missing_slug += 1
            else:
                slug = str(chat.get("slug"))
                duplicate_slugs[slug] = duplicate_slugs.get(slug, 0) + 1
            if not chat.get("group"):
                missing_group += 1

        if groups:
            group_text = ", ".join(f"{escape_html(group)}={count}" for group, count in sorted(groups.items()))
            lines.append(f"groups: {group_text}")

        duplicate_count = sum(1 for count in duplicate_slugs.values() if count > 1)
        warnings: list[str] = []
        if missing_name:
            warnings.append(f"missing name={missing_name}")
        if missing_slug:
            warnings.append(f"missing slug={missing_slug}")
        if missing_group:
            warnings.append(f"missing group={missing_group}")
        if duplicate_count:
            warnings.append(f"duplicate slugs={duplicate_count}")
        lines.append("✅ schema quick check ok" if not warnings else "⚠️ " + ", ".join(warnings))

        if current_chat_id is not None:
            chat = await self._database.get_chat_by_id(current_chat_id)
            if chat:
                name = escape_html(str(chat.get("name", "<without name>")))
                lines.append(f"current chat: ✅ {name} / <code>{current_chat_id}</code>")
            else:
                lines.append(f"current chat: ⚠️ not registered / <code>{current_chat_id}</code>")

        return lines

    async def _threads_block(self, mongo: Any) -> list[str]:
        lines = ["\n<b>Threads</b>"]
        try:
            threads_count = await mongo.threads.count_documents({})
            lines.append(f"thread mappings: {threads_count}")
            latest = await mongo.threads.find_one(sort=[("_id", -1)])
            if latest:
                lines.append(
                    "latest: "
                    f"origin <code>{latest.get('origin_chat_id')}:{latest.get('origin_message_id')}</code> → "
                    f"relay <code>{latest.get('relay_chat_id')}:{latest.get('relay_message_id')}</code>"
                )
            else:
                lines.append("latest: none")
        except Exception as exc:  # pragma: no cover - depends on runtime Mongo
            lines.append(f"❌ cannot read threads: <code>{escape_html(type(exc).__name__)}</code>")
        return lines

    async def _claims_block(self, mongo: Any) -> list[str]:
        lines = ["\n<b>Delivery guard</b>"]
        try:
            for status in ("dispatching", "delivered", "partial", "uncertain"):
                count = await mongo.delivery_claims.count_documents({"status": status})
                lines.append(f"{status}: {count}")
        except Exception as exc:
            lines.append(f"⚠️ cannot read claims: {escape_html(type(exc).__name__)}")
        return lines

    async def _indexes_block(self, mongo: Any) -> list[str]:
        lines = ["\n<b>Indexes</b>"]
        try:
            chat_indexes = await mongo.chats.index_information()
            thread_indexes = await mongo.threads.index_information()
            lines.append(f"chats indexes: {len(chat_indexes)}")
            lines.append(f"threads indexes: {len(thread_indexes)}")
            guard_indexes = await mongo.delivery_claims.index_information()
            lines.append(f"delivery claim indexes: {len(guard_indexes)}")
        except Exception as exc:  # pragma: no cover - depends on runtime Mongo
            lines.append(f"⚠️ cannot read indexes: <code>{escape_html(type(exc).__name__)}</code>")
        return lines
