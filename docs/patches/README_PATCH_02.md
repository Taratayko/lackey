# Patch 02 — universal domain models

Дата: 2026-06-22

## Что сделано

- Расширен `domain/models.py` под будущий communication core.
- Добавлены универсальные модели без зависимости от `aiogram`, VK API, FastAPI и MongoDB:
  - `ChannelKind`
  - `ChannelRef`
  - `MessageRef`
  - `MessageAuthor`
  - `AttachmentKind`
  - `Attachment`
  - `MessageDirection`
  - `Message`
  - `IncomingMessage`
  - `OutgoingMessage`
- Добавлены alias-свойства для переходного периода:
  - `ChannelRef.chat_id`
  - `MessageRef.chat_id`
  - `MessageRef.message_id`
  - `Message.sender_id`
  - `Message.reply_to_message_id`
- Добавлен `Message.from_legacy(...)`, чтобы дальше аккуратно маппить старые Telegram-поля без переписывания runtime одним большим шагом.
- Обновлён публичный экспорт `domain/__init__.py`.
- Обновлён `ports/channel.py`: порт теперь описан через `ChannelRef`, `MessageRef`, `OutgoingMessage`.
- Обновлён список патчей в `README.md`.

## Что не трогали

- `handlers.py` не переписывался.
- `captcha.py` не переписывался.
- MongoDB-слой не выносился в repository layer.
- VK/Web адаптеры не подключались.
- Runtime-поведение Telegram-бота не менялось.
- `.env.example` и переменные окружения не менялись.
- `build.sh` не менялся: новые файлы добавлены внутри уже включённых директорий, новых root items нет.

## Как проверить локально

```bash
python -m compileall .
```

Дополнительно можно проверить импорт моделей:

```bash
python - <<'PY'
from domain import ChannelRef, IncomingMessage

msg = IncomingMessage.from_legacy(
    channel="telegram",
    chat_id=-1001,
    message_id=42,
    sender_id=777,
    text="test",
)
print(msg.channel, msg.chat_id, msg.message_id, msg.sender_id)
print(ChannelRef("telegram", -1001).key)
PY
```

Ожидаемо:

```text
telegram -1001 42 777
telegram:-1001
```

После этого владелец бота проверяет обычный чеклист поведения на рабочем `.env`, MongoDB и тестовом Telegram-боте.
