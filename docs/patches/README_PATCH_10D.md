# Patch 10D — вынос DM handlers

## Что сделано

- Добавлен `adapters/telegram/handlers/dm.py`.
- В отдельный Telegram-router вынесены личные сообщения боту:
  - `/start`;
  - `/anon`;
  - `/deanon`;
  - текстовые сообщения из ЛС;
  - медиа-сообщения из ЛС.
- Добавлен `adapters/telegram/handlers/common.py` с общими helper-функциями Telegram-router слоя:
  - `sender_mention`;
  - `has_media`;
  - `extract_target_and_text`;
  - `extract_target_only`;
  - `resolve_target`;
  - `resolve_target_only`;
  - `resolve_chat_and_text`;
  - `is_real_reply`.
- `adapters/telegram/handlers/main.py` стал меньше и теперь отвечает за group relay / media relay / reply / forward-reply.
- `adapters/telegram/handlers/__init__.py` подключает новый `dm.router` до широкого `main.router`.

## Что не трогали

- `bot.py`.
- Корневой `handlers.py` compatibility shim.
- `captcha.py` и captcha-router.
- `AdminService`, `ModerationService`, `RelayService`, `ReplyService`.
- Mongo schema и `database.py`.
- VK/Web runtime.
- `.env`, `build.sh`, зависимости.

## Как проверить

1. Запустить статические проверки:

```bash
python -m compileall .
bash build.sh
```

2. Проверить личку с ботом:

```text
/start
/anon
/deanon
НазваниеКоммуны, привет из ЛС
НазваниеКоммуны, + фото/документ/стикер
```

3. Проверить, что не сломались групповые сценарии:

```text
/help
/list
обычный group relay
media relay из группы
reply на доставленное сообщение
бан / мегабан / разбан
captcha flow
```

## Ожидаемый результат

Поведение бота не меняется. Меняется только структура Telegram-router слоя:
личные сообщения больше не лежат в большом `main.py`, а живут в отдельном
`dm.py`.
