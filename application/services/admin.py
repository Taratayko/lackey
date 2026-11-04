"""AdminService для списков, статистики и служебных команд владельца.

Сервис выносит из handlers.py бизнес-логику команд управления чатами:
регистрацию, алиасы, ссылки, скрытие, редирект, топики,
chatinfo/listall, а также формирование /list и /stat.

aiogram остаётся только во входном слое handlers.py: там разбирается Message и
отправляется готовый текст пользователю.
"""
from __future__ import annotations

from .admin_details import AdminCommandResult, AdminDetails
from typing import Literal

from ports.repositories import RepositoryBundle
from presentation.formatters import escape_html, escape_html_attr

ListMode = Literal["auto", "full", "msk", "spb", "reg"]

# (код_группы, прилагательное, эмодзи) для основных городов.
MSK_SECTION = ("msk", "Московские", "🍓")
SPB_SECTION = ("spb", "Питерские", "\U0001F344\u200d\U0001F7EB")  # 🍄‍🟫

LIST_TITLE = "к КоммуСети подключены:"
BRAILLE_BLANK = "\u2800"  # «невидимый» отступ перед заголовком секции

# Группы, которые /addchat принимает, даже когда их нет в реестре REG_CITIES.
# В старом handlers.py список групп /addchat был зашит отдельно от реестра:
# krm принимался, хотя Крымские пока закомментированы и в /list не показываются.
ADDCHAT_EXTRA_GROUPS: tuple[str, ...] = ("krm",)


class AdminService(AdminDetails):
    """Application-слой для списков, статистики и owner admin-команд."""

    def __init__(
        self,
        repositories: RepositoryBundle,
        owner_user_id: int,
        regional_cities: list[tuple[str, str, str]] | tuple[tuple[str, str, str], ...],
    ) -> None:
        self._repositories = repositories
        self._owner_user_id = owner_user_id
        self._regional_cities = list(regional_cities)

    def is_owner(self, user_id: int | None) -> bool:
        """True, если пользователь является владельцем бота."""
        return user_id == self._owner_user_id

    # ──────────────────────────────────────────────────────────────────────
    # /list, /listmsk, /listspb, /listreg
    #
    # Текст списка — это HTML для Rich Message (Bot API 10.1+, sendRichMessage),
    # а не обычный parse_mode=HTML: только там работает настоящий <h2>.
    # Отправлять результат нужно через send_rich() из handlers/common.py.
    # В Rich HTML перевод строки "\n" схлопывается в пробел, поэтому строки разделяются <br/>,
    # а секции оборачиваются в <p>.
    # ──────────────────────────────────────────────────────────────────────

    async def render_list_text(self, mode: ListMode, current_chat_id: int | None = None) -> str:
        """Собрать Rich-HTML списка коммун для выбранного режима."""
        if mode == "full":
            return await self.render_full_list_text()
        if mode == "msk":
            return await self._render_city_with_reg_and_special(*MSK_SECTION)
        if mode == "spb":
            return await self._render_city_with_reg_and_special(*SPB_SECTION)
        if mode == "reg":
            return await self._render_regional_list_text()

        if current_chat_id is None:
            return await self.render_full_list_text()

        chat = await self._repositories.chats.get_chat_by_id(current_chat_id)
        group = chat["group"] if chat else None
        if group == "spb":
            return await self._render_city_with_reg_and_special(*SPB_SECTION)
        if group == "msk":
            return await self._render_city_with_reg_and_special(*MSK_SECTION)
        return await self.render_full_list_text()

    async def render_full_list_text(self) -> str:
        """Единый список всех коммун для ЛС и незарегистрированных чатов."""
        parts: list[str] = []
        for group_code, label, emoji in (MSK_SECTION, SPB_SECTION):
            chats = await self._visible_chats_by_group(group_code)
            parts.append(self._section_block(label, emoji, chats))

        for code, label, emoji in self._regional_cities:
            reg_chats = await self._visible_chats_by_group(code)
            parts.append(self._section_block(label, emoji, reg_chats))

        parts.extend(await self._special_blocks())
        return self._wrap_list_body(parts)

    async def _render_city_with_reg_and_special(self, group_code: str, label: str, emoji: str) -> str:
        parts = [self._section_block(label, emoji, await self._visible_chats_by_group(group_code))]
        for code, reg_label, reg_emoji in self._regional_cities:
            reg_chats = await self._visible_chats_by_group(code)
            parts.append(self._section_block(reg_label, reg_emoji, reg_chats))
        parts.extend(await self._special_blocks())
        return self._wrap_list_body(parts)

    async def _render_regional_list_text(self) -> str:
        parts: list[str] = []
        for code, label, emoji in self._regional_cities:
            chats = await self._visible_chats_by_group(code)
            parts.append(self._section_block(label, emoji, chats))
        parts.extend(await self._special_blocks())
        return self._wrap_list_body(parts)

    async def _visible_chats_by_group(self, group: str) -> list[dict]:
        return [c for c in await self._repositories.chats.get_chats_by_group(group) if not c.get("hidden")]

    async def _special_blocks(self) -> list[str]:
        """Спецчаты — всегда в самом низу, каждый своим блоком."""
        special = await self._visible_chats_by_group("special")
        return [f"<p>{self._special_chat_line(chat)}</p>" for chat in special]

    @staticmethod
    def _wrap_list_body(parts: list[str]) -> str:
        """Общая шапка «к КоммуСети подключены:» (настоящий H2 Rich Message) + секции."""
        return f"<h2>{LIST_TITLE}</h2>" + "<br/>".join(parts)

    @staticmethod
    def _section_header(label: str, emoji: str) -> str:
        """Заголовок секции: Braille blank для отступа + жирный текст."""
        return f"{BRAILLE_BLANK}<b>{label} коммуны {emoji}</b>"

    @staticmethod
    def _sort_internal_first(chats: list[dict]) -> list[dict]:
        """«Внутренние» коммуны (is_internal) всегда первыми; сортировка стабильна."""
        return sorted(chats, key=lambda c: not c.get("is_internal", False))

    def _section_block(self, label: str, emoji: str, chats: list[dict]) -> str:
        """Секция: заголовок + коммуны одним блоком <p>, строки через <br/>."""
        lines = [self._section_header(label, emoji)]
        lines.extend(self._chat_line(c) for c in self._sort_internal_first(chats))
        return "<p>" + "<br/>".join(lines) + "</p>"

    @staticmethod
    def _chat_line(chat: dict) -> str:
        """Строка коммуны: «• » + название-ссылка + курсивное aka + алиасы в <code>.

        Внутренние коммуны (is_internal) выделяются жирным названием.
        """
        name = escape_html(chat["name"])
        display_name = f"<b>{name}</b>" if chat.get("is_internal", False) else name
        link = chat.get("link")
        if link:
            base = f'• <a href="{escape_html_attr(link)}">{display_name}</a>'
        else:
            base = f"• {display_name}"
        aliases = chat.get("aliases", [])
        if aliases:
            aka_text = ", ".join(f"<code>{escape_html(str(a).lower())}</code>" for a in aliases)
            base += f" <i>aka</i> {aka_text}"
        return base

    @staticmethod
    def _special_chat_line(chat: dict) -> str:
        """Спецчат: целиком жирным курсивом, эмодзи 🤖 вплотную к названию."""
        name = escape_html(chat["name"])
        link = chat.get("link")
        if link:
            line = f'🤖<a href="{escape_html_attr(link)}">{name}</a>'
        else:
            line = f"🤖{name}"
        return f"<i><b>{line}</b></i>"

    # ──────────────────────────────────────────────────────────────────────
    # /stat
    # ──────────────────────────────────────────────────────────────────────

    async def render_stats_text(self) -> str:
        """Собрать текст статистики сообщений."""
        all_stats = await self._repositories.stats.get_stats_all()
        if not all_stats:
            return "Статистики пока нет 📊"

        all_chats = await self._repositories.chats.get_all_chats()
        chat_names = {c["chat_id"]: c["name"] for c in all_chats}

        totals: dict[int, int] = {}
        dm_total = 0
        for stat in all_stats:
            from_id = stat["from_chat_id"]
            to_id = stat["to_chat_id"]
            count = stat.get("total", 0)
            if from_id not in chat_names:
                dm_total += count
            totals[to_id] = totals.get(to_id, 0) + count

        sorted_totals = sorted(totals.items(), key=lambda item: item[1], reverse=True)
        lines = ["<b>📊 Статистика сообщений (за всё время):</b>", ""]
        for chat_id, count in sorted_totals:
            name = chat_names.get(chat_id, str(chat_id))
            lines.append(f"{name}: {count}")
        if dm_total:
            lines.append("")
            lines.append(f"📬 Через ЛС Лакея: {dm_total}")
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────────────
    # Owner admin-команды управления чатами
    # ──────────────────────────────────────────────────────────────────────

    async def add_chat(self, actor_user_id: int | None, chat_id: int, raw_text: str | None) -> AdminCommandResult | None:
        if not self.is_owner(actor_user_id):
            return None

        parts = (raw_text or "").split()
        if len(parts) < 4:
            return AdminCommandResult(
                "Использование:\n"
                "<code>/addchat &lt;group&gt; &lt;is_chief&gt; &lt;name&gt; [link]</code>\n\n"
                "Пример:\n"
                "<code>/addchat msk true Клубника https://t.me/klubnika</code>\n"
                "<code>/addchat vlk true Пассад https://t.me/passad_commune</code>\n"
                "<code>/addchat krm true ... — для Крыма</code>"
            )

        group = parts[1]
        is_chief_str = parts[2]
        last = parts[-1] if len(parts) >= 4 else ""
        if len(parts) >= 5 and (last.startswith("http") or last.startswith("t.me/")):
            raw_link = last
            link = raw_link if raw_link.startswith("http") else f"https://{raw_link}"
            name = " ".join(parts[3:-1])
        else:
            link = None
            name = " ".join(parts[3:])

        allowed_groups = self._allowed_groups_text_tuple()
        if group not in allowed_groups:
            return AdminCommandResult(f"❌ group должен быть: {' | '.join(allowed_groups)}")

        is_chief = self._parse_bool(is_chief_str)
        data = {
            "chat_id": chat_id,
            "name": name,
            "slug": name.lower(),
            "aliases": [],
            "group": group,
            "is_chief": is_chief,
            "topic_id": None,
            "link": link,
            "description": None,
        }

        ok = await self._repositories.chats.add_chat(data)
        if ok:
            return AdminCommandResult(f"✅ Чат <b>{name}</b> добавлен ({group}, chief={is_chief})")
        return AdminCommandResult("❌ Чат уже существует или ошибка БД")

    async def remove_chat(self, actor_user_id: int | None, chat_id: int) -> AdminCommandResult | None:
        if not self.is_owner(actor_user_id):
            return None
        ok = await self._repositories.chats.remove_chat(chat_id)
        return AdminCommandResult("✅ Чат удалён из базы" if ok else "❌ Чат не найден в базе")

    async def add_alias(self, actor_user_id: int | None, chat_id: int, raw_text: str | None) -> AdminCommandResult | None:
        if not self.is_owner(actor_user_id):
            return None
        parts = (raw_text or "").split(maxsplit=1)
        if len(parts) < 2:
            return AdminCommandResult("Использование: <code>/addalias &lt;алиас&gt;</code>")

        alias = parts[1].strip().lower()
        chat = await self._repositories.chats.get_chat_by_id(chat_id)
        if not chat:
            return AdminCommandResult("❌ Чат не зарегистрирован. Сначала /addchat")

        aliases = chat.get("aliases", [])
        if alias in aliases:
            return AdminCommandResult("❌ Такой алиас уже есть")

        aliases.append(alias)
        await self._repositories.chats.update_chat(chat_id, {"aliases": aliases})
        return AdminCommandResult(f"✅ Алиас <b>{alias}</b> добавлен")

    async def remove_alias(self, actor_user_id: int | None, chat_id: int, raw_text: str | None) -> AdminCommandResult | None:
        if not self.is_owner(actor_user_id):
            return None
        parts = (raw_text or "").split(maxsplit=1)
        if len(parts) < 2:
            return AdminCommandResult("Использование: <code>/removealias &lt;алиас&gt;</code>")

        alias = parts[1].strip().lower()
        chat = await self._repositories.chats.get_chat_by_id(chat_id)
        if not chat:
            return AdminCommandResult("❌ Чат не зарегистрирован")

        aliases = chat.get("aliases", [])
        if alias not in aliases:
            return AdminCommandResult("❌ Алиас не найден")

        aliases.remove(alias)
        await self._repositories.chats.update_chat(chat_id, {"aliases": aliases})
        return AdminCommandResult(f"✅ Алиас <b>{alias}</b> удалён")

    async def set_link(self, actor_user_id: int | None, chat_id: int, raw_text: str | None) -> AdminCommandResult | None:
        if not self.is_owner(actor_user_id):
            return None
        parts = (raw_text or "").split(maxsplit=1)
        if len(parts) < 2:
            return AdminCommandResult("Использование: <code>/setlink &lt;https://t.me/...&gt;</code>")

        link = parts[1].strip()
        ok = await self._repositories.chats.update_chat(chat_id, {"link": link})
        return AdminCommandResult(f"✅ Ссылка обновлена: {link}" if ok else "❌ Чат не найден в базе")

    async def set_hidden(self, actor_user_id: int | None, chat_id: int, raw_text: str | None) -> AdminCommandResult | None:
        if not self.is_owner(actor_user_id):
            return None
        parts = (raw_text or "").split(maxsplit=1)
        if len(parts) < 2:
            return AdminCommandResult("Использование: <code>/sethidden true|false</code>")

        hidden = self._parse_bool(parts[1])
        ok = await self._repositories.chats.update_chat(chat_id, {"hidden": hidden})
        return AdminCommandResult(f"✅ hidden = {hidden}" if ok else "❌ Чат не найден в базе")

    async def set_redirect(self, actor_user_id: int | None, chat_id: int, raw_text: str | None) -> AdminCommandResult | None:
        if not self.is_owner(actor_user_id):
            return None
        parts = (raw_text or "").split(maxsplit=1)
        if len(parts) < 2:
            return AdminCommandResult("Использование: <code>/setredirect &lt;chat_id&gt;</code>\n0 — сброс")

        try:
            redirect_to = int(parts[1].strip())
        except ValueError:
            return AdminCommandResult("❌ chat_id должен быть числом")

        redirect_to_value = redirect_to if redirect_to != 0 else None
        if redirect_to_value:
            target = await self._repositories.chats.get_chat_by_id(redirect_to_value)
            if not target:
                return AdminCommandResult("❌ Целевой чат не найден в базе. Сначала добавь его через /addchat")

        ok = await self._repositories.chats.update_chat(chat_id, {"redirect_to": redirect_to_value})
        if ok:
            label = str(redirect_to_value) if redirect_to_value else "сброшен"
            return AdminCommandResult(f"✅ Редирект → {label}")
        return AdminCommandResult("❌ Чат не найден в базе")

    async def set_topic(self, actor_user_id: int | None, chat_id: int, raw_text: str | None) -> AdminCommandResult | None:
        if not self.is_owner(actor_user_id):
            return None
        parts = (raw_text or "").split(maxsplit=1)
        if len(parts) < 2:
            return AdminCommandResult(self._settopic_usage_text())

        try:
            topic_id = int(parts[1].strip())
        except ValueError:
            return AdminCommandResult("❌ topic_id должен быть числом")

        topic_id_value = topic_id if topic_id != 0 else None
        ok = await self._repositories.chats.update_chat(chat_id, {"topic_id": topic_id_value})
        if ok:
            label = str(topic_id_value) if topic_id_value else "сброшен (весь чат)"
            return AdminCommandResult(f"✅ Топик → {label}")
        return AdminCommandResult("❌ Чат не найден в базе. Сначала /addchat")

    async def clear_topic(self, actor_user_id: int | None, chat_id: int) -> AdminCommandResult | None:
        if not self.is_owner(actor_user_id):
            return None
        ok = await self._repositories.chats.update_chat(chat_id, {"topic_id": None})
        return AdminCommandResult("✅ Топик сброшен — бот работает во всём чате" if ok else "❌ Чат не найден в базе")

    async def set_chief(self, actor_user_id: int | None, chat_id: int, raw_text: str | None) -> AdminCommandResult | None:
        if not self.is_owner(actor_user_id):
            return None
        parts = (raw_text or "").split(maxsplit=1)
        if len(parts) < 2:
            return AdminCommandResult("Использование: <code>/setchief true|false</code>")

        is_chief = self._parse_bool(parts[1])
        ok = await self._repositories.chats.update_chat(chat_id, {"is_chief": is_chief})
        return AdminCommandResult(f"✅ is_chief = {is_chief}" if ok else "❌ Чат не найден в базе")

    def _allowed_groups_text_tuple(self) -> tuple[str, ...]:
        regional = tuple(code for code, _label, _emoji in self._regional_cities)
        extra = tuple(group for group in ADDCHAT_EXTRA_GROUPS if group not in regional)
        return ("msk", "spb", *regional, *extra, "special")

