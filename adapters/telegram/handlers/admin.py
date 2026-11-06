"""Telegram handlers for lists, help and owner admin commands."""

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

from application.services import AdminCommandResult, AdminService
from config import settings
from database import db
from presentation.texts import DM_HELP, GROUP_HELP, GROUP_HELP_ADMIN

from .common import send_rich
from .moderation import is_moderator

router = Router()


# Добавляй сюда новые города: ("код_группы", "Прилагательное", "эмодзи")
REG_CITIES: list[tuple[str, str, str]] = [
    ("vlk", "Владивостокские", "⛵️"),
    #("krm", "Крымские", "🦅"),
    # 🔮 Здесь появятся новые города
]


def _admin_service() -> AdminService:
    """Создать сервис админ-команд и списков для текущего runtime."""
    return AdminService(
        repositories=db.get_repositories(),
        owner_user_id=settings.ADMIN_ID,
        regional_cities=REG_CITIES,
    )


def _owner_user_id(msg: Message) -> int | None:
    return msg.from_user.id if msg.from_user else None


async def _reply_admin_result(msg: Message, result: AdminCommandResult | None) -> None:
    if result and result.text:
        await msg.reply(result.text, disable_web_page_preview=result.disable_web_page_preview)


async def build_full_list_text() -> str:
    """Единый список всех коммун через AdminService."""
    return await _admin_service().render_full_list_text()


@router.message(Command("list"))
async def cmd_list(msg: Message):
    mode = "full" if msg.chat.type == "private" else "auto"
    text = await _admin_service().render_list_text(mode, current_chat_id=msg.chat.id)
    await send_rich(msg, text)


@router.message(Command("listmsk"))
async def cmd_listmsk(msg: Message):
    text = await _admin_service().render_list_text("msk")
    await send_rich(msg, text)


@router.message(Command("listspb"))
async def cmd_listspb(msg: Message):
    text = await _admin_service().render_list_text("spb")
    await send_rich(msg, text)


@router.message(Command("listreg"))
async def cmd_listreg(msg: Message):
    text = await _admin_service().render_list_text("reg")
    await send_rich(msg, text)


@router.message(Command("stat"))
async def cmd_stat(msg: Message):
    text = await _admin_service().render_stats_text()
    await msg.reply(text)


@router.message(Command("addchat"))
async def cmd_addchat(msg: Message):
    result = await _admin_service().add_chat(_owner_user_id(msg), msg.chat.id, msg.text)
    await _reply_admin_result(msg, result)


@router.message(Command("removechat"))
async def cmd_removechat(msg: Message):
    result = await _admin_service().remove_chat(_owner_user_id(msg), msg.chat.id)
    await _reply_admin_result(msg, result)


@router.message(Command("addalias"))
async def cmd_addalias(msg: Message):
    result = await _admin_service().add_alias(_owner_user_id(msg), msg.chat.id, msg.text)
    await _reply_admin_result(msg, result)


@router.message(Command("removealias"))
async def cmd_removealias(msg: Message):
    result = await _admin_service().remove_alias(_owner_user_id(msg), msg.chat.id, msg.text)
    await _reply_admin_result(msg, result)


@router.message(Command("setlink"))
async def cmd_setlink(msg: Message):
    result = await _admin_service().set_link(_owner_user_id(msg), msg.chat.id, msg.text)
    await _reply_admin_result(msg, result)


@router.message(Command("sethidden"))
async def cmd_sethidden(msg: Message):
    result = await _admin_service().set_hidden(_owner_user_id(msg), msg.chat.id, msg.text)
    await _reply_admin_result(msg, result)


@router.message(Command("setredirect"))
async def cmd_setredirect(msg: Message):
    result = await _admin_service().set_redirect(_owner_user_id(msg), msg.chat.id, msg.text)
    await _reply_admin_result(msg, result)


@router.message(Command("settopic"))
async def cmd_settopic(msg: Message):
    result = await _admin_service().set_topic(_owner_user_id(msg), msg.chat.id, msg.text)
    await _reply_admin_result(msg, result)


@router.message(Command("cleartopic"))
async def cmd_cleartopic(msg: Message):
    result = await _admin_service().clear_topic(_owner_user_id(msg), msg.chat.id)
    await _reply_admin_result(msg, result)


@router.message(Command("setchief"))
async def cmd_setchief(msg: Message):
    result = await _admin_service().set_chief(_owner_user_id(msg), msg.chat.id, msg.text)
    await _reply_admin_result(msg, result)


@router.message(Command("chatinfo"))
async def cmd_chatinfo(msg: Message):
    result = await _admin_service().chat_info(_owner_user_id(msg), msg.chat.id)
    await _reply_admin_result(msg, result)


@router.message(Command("listall"))
async def cmd_listall(msg: Message):
    result = await _admin_service().list_all_chats(_owner_user_id(msg))
    await _reply_admin_result(msg, result)


@router.message(Command("help"))
async def cmd_help(msg: Message, bot: Bot):
    # Rich Message: в справке настоящие блоки/цитаты, поэтому send_rich, а не reply.
    if msg.chat.type == "private":
        text = DM_HELP
    elif await is_moderator(bot, msg):
        # Тем, кто может банить и мутить, дополнительно показываем команды модерации.
        text = GROUP_HELP_ADMIN
    else:
        text = GROUP_HELP
    await send_rich(msg, text)


@router.message(Command("pleh"))
async def cmd_pleh(msg: Message):
    """Команды настройки бота — только для владельца."""
    text = _admin_service().settings_help_text(_owner_user_id(msg))
    if text:
        await msg.reply(text)
