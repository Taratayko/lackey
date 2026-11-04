"""Auxiliary admin info/help methods split to keep modules below 400 lines."""
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class AdminCommandResult:
    """Результат выполнения command/service операции."""

    text: str | None = None
    disable_web_page_preview: bool = False


class AdminDetails:
    """Display-oriented admin methods; inherited by AdminService."""

    async def chat_info(self, actor_user_id: int | None, chat_id: int) -> AdminCommandResult | None:
        if not self.is_owner(actor_user_id):
            return None
        chat = await self._repositories.chats.get_chat_by_id(chat_id)
        if not chat:
            return AdminCommandResult(f"❌ Чат не зарегистрирован\nchat_id: <code>{chat_id}</code>")

        aliases_str = ", ".join(chat.get("aliases", [])) or "—"
        return AdminCommandResult(
            f"<b>Инфо о чате:</b>\n"
            f"Название: {chat['name']}\n"
            f"Группа: {chat['group']}\n"
            f"Главный: {chat['is_chief']}\n"
            f"Внутренняя: {chat.get('is_internal', False)}\n"
            f"Алиасы: {aliases_str}\n"
            f"Топик: {chat.get('topic_id') or '—'}\n"
            f"Ссылка: {chat.get('link') or '—'}\n"
            f"chat_id: <code>{chat['chat_id']}</code>"
        )

    async def list_all_chats(self, actor_user_id: int | None) -> AdminCommandResult | None:
        if not self.is_owner(actor_user_id):
            return None
        chats = await self._repositories.chats.get_all_chats()
        if not chats:
            return AdminCommandResult("Нет зарегистрированных чатов")

        lines = ["<b>Все зарегистрированные чаты:</b>"]
        for chat in chats:
            chief_mark = " ★" if chat["is_chief"] else ""
            lines.append(
                f"[{chat['group'].upper()}{chief_mark}] {chat['name']} "
                f"(<code>{chat['chat_id']}</code>)"
            )
        return AdminCommandResult("\n".join(lines))

    def settings_help_text(self, actor_user_id: int | None) -> str | None:
        if not self.is_owner(actor_user_id):
            return None
        groups = " | ".join(f"<code>{group}</code>" for group in self._allowed_groups_text_tuple())
        return (
            "<b>Настройка бота:</b>\n"
            "/addchat &lt;group&gt; &lt;chief&gt; &lt;name&gt; [link] — добавить чат\n"
            f"  group: {groups}\n"
            "  chief: <code>true</code> | <code>false</code>\n"
            "/removechat — удалить текущий чат из базы\n"
            "/addalias &lt;алиас&gt; — добавить псевдоним чату\n"
            "/removealias &lt;алиас&gt; — удалить псевдоним\n"
            "/setlink &lt;url&gt; — задать ссылку\n"
            "/setchief true | false — главный чат или нет\n"
            "/sethidden true | false — скрыть из списка\n"
            "/setredirect &lt;chat_id&gt; — перенаправить входящие в другой чат (0 — сброс)\n"
            "/chatinfo — инфо о текущем чате\n"
            "/listall — все зарегистрированные чаты\n"
            "/settopic &lt;id&gt; — привязать к топику форума (0 — сброс)\n"
            "/cleartopic — убрать привязку к топику\n"
            "/diag или /health — диагностика Mongo/коллекций/чатов"
        )

    @staticmethod
    def _parse_bool(value: str) -> bool:
        return value.strip().lower() in ("true", "1", "yes", "да")

    @staticmethod
    def _settopic_usage_text() -> str:
        return (
            "Использование: <code>/settopic &lt;topic_id&gt;</code>\n"
            "0 — сброс (бот будет реагировать во всём чате)\n\n"
            "<b>Как узнать topic_id:</b>\n"
            "1. Зайди в нужный топик форума\n"
            "2. Правой кнопкой на любое сообщение → «Скопировать ссылку»\n"
            "   Ссылка: <code>https://t.me/c/1234567890/42/99</code>\n"
            "   → topic_id = <b>42</b> (второе число после /c/...)\n\n"
            "Или перешли любое сообщение из топика боту @RawDataBot — "
            "он покажет <code>message_thread_id</code>"
        )
