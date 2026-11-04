# Patch 13 — Web/Site adapter MVP

Дата: 2026-09-20

## Что сделано

Добавлен изолированный MVP-слой для будущего сайта коммун / web-адаптера:

- `adapters/web/schemas.py` — DTO для входящих сообщений сайта:
  - `WebAuthorPayload`;
  - `WebAttachmentPayload`;
  - `WebIncomingMessagePayload`.
- `adapters/web/mapper.py` — преобразование web JSON/DTO в доменные модели:
  - `web_dict_to_domain(...)`;
  - `web_payload_to_domain(...)`;
  - `domain_message_to_web_dict(...)`.
- `adapters/web/auth.py` — минимальная token-auth заготовка:
  - Bearer token;
  - `X-Lackey-Token`;
  - no-op, если токен не задан.
- `adapters/web/adapter.py` — `WebChannelAdapter` под `ChannelPort` и `MemoryWebOutbox` для smoke/unit-тестов.
- `adapters/web/api.py` — framework-agnostic REST boundary для будущего `POST /api/messages`.
- `adapters/web/__init__.py` — публичные экспорты web-адаптера.
- `config.py` / `.env.example` — добавлены web-настройки:
  - `WEB_ENABLED=false`;
  - `WEB_API_TOKEN`;
  - `WEB_PUBLIC_BASE_URL`.

## Что специально не трогали

- `bot.py`.
- Telegram runtime.
- Reply / forward-reply.
- Relay / media relay.
- Captcha runtime.
- Moderation runtime.
- VK runtime.
- Mongo schema.
- `requirements.txt`.

В этом патче **не запускается web-сервер** и не добавляются FastAPI/aiohttp зависимости. Это только безопасный adapter/API skeleton, чтобы позже подключить сайт коммун без смешивания web-framework с application core.

## Как проверить

Минимально:

```bash
python -m compileall .
bash build.sh
```

Живая регрессия Telegram:

```text
бот стартует
/help
/diag
обычный relay
media relay
captcha flow
```

Web runtime пока не проверять: он ещё не подключён к запуску.
