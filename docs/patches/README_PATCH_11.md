# Patch 11 — DiagnosticsService / health-check

## Что сделано

Добавлен безопасный diagnostic layer для быстрой проверки runtime-состояния бота после серии рефакторингов Telegram handlers.

Новые команды владельца:

- `/diag`
- `/health`

Команды доступны только `ADMIN_ID` и возвращают краткий HTML-отчёт:

- текущий UTC timestamp;
- Telegram username бота, если `bot.get_me()` доступен;
- MongoDB `ping`;
- имя базы;
- наличие и размер ожидаемых коллекций;
- количество зарегистрированных чатов и разбивку по группам;
- быстрые предупреждения по схеме чатов: пустые `name` / `slug` / `group`, дубли slug;
- зарегистрирован ли текущий чат;
- количество thread mappings и последний mapping;
- количество индексов `chats` и `threads`.

## Изменённые/новые файлы

- `application/services/diagnostics.py`
- `application/services/__init__.py`
- `adapters/telegram/handlers/diagnostics.py`
- `adapters/telegram/handlers/__init__.py`
- `application/services/admin.py`
- `README.md`
- `docs/patches/README_PATCH_11.md`

## Что не трогали

- reply / forward-reply логику;
- `main.py` relay/media handlers;
- DM handlers;
- moderation/admin/captcha runtime;
- Mongo schema;
- `bot.py`;
- `.env`;
- `build.sh`.

## Как проверять

Перед запуском:

```powershell
python -m compileall .
bash build.sh
```

В Telegram от владельца бота:

```text
/diag
/health
/pleh
```

Ожидаемо:

- `/diag` и `/health` отвечают отчётом;
- MongoDB `ping ok`;
- коллекции отображаются с количеством документов или `missing`, если коллекция ещё не создавалась;
- `/pleh` показывает новую строку про `/diag` / `/health`;
- обычный relay/reply/media/captcha не изменились этим патчем.

Если команду вызывает не владелец, бот молчит.
