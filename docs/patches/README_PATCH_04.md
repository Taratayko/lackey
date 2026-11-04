# Patch 04 — Telegram sender/media adapter

## Цель

Начать вынос Telegram-specific отправки из `handlers.py` в отдельный адаптер.

Этот патч не переносит бизнес-логику ретрансляции в сервисы. Он только убирает
часть прямых aiogram-вызовов из слоя handlers и готовит место, куда дальше будут
ходить `RelayService`, `ReplyService`, `ModerationService` и `CaptchaService`.

## Добавленные/изменённые файлы

```text
adapters/telegram/sender.py
adapters/telegram/__init__.py
handlers.py
README.md
docs/patches/README_PATCH_04.md
```

## Что изменено

- Добавлен `adapters/telegram/sender.py`.
- В Telegram-адаптер вынесены helper-функции:
  - `send_in_topic(...)` — отправка HTML-текста в чат/топик;
  - `send_media_to_chat(...)` — отправка фото/видео/документов/аудио/voice/sticker/animation/video_note;
  - `set_delivered_reaction(...)` — реакция доставки `✍`;
  - `delete_message(...)`;
  - `ban_chat_member(...)`;
  - `unban_chat_member(...)`.
- Добавлен класс `TelegramChannelAdapter`, который реализует текущий `ChannelPort` через aiogram `Bot`.
- `handlers.py` теперь импортирует sender/media helper-функции из `adapters.telegram`, а не держит их внутри себя.
- Логика fallback при ошибке `message to be replied not found` сохранена: если reply-сообщение недоступно, адаптер повторяет отправку без reply.
- Основной `README.md` обновлён строкой про Patch 04.

## Что не менялось

- `handlers.py` всё ещё остаётся главным runtime-слоем aiogram.
- `captcha.py` пока не переведён на Telegram-адаптер.
- Прямая отправка через `bot.send_message(...)` в сложных участках handlers пока не выпиливалась полностью.
- Mongo-схема и repository layer не менялись.
- VK/Web adapter runtime не подключался.
- Новые `.env`-ключи не добавлялись.

## Почему это безопасно

Большая часть поведения сохранена 1-в-1: функции отправки просто переехали из
`handlers.py` в `adapters/telegram/sender.py`. Сигнатуры legacy helper-функций
оставлены прежними, поэтому существующие вызовы в handlers не требуют массового
переписывания.

`TelegramChannelAdapter` пока является подготовительным классом для следующих
патчей. Текущий бот не обязан создавать его при запуске, поэтому runtime-риск
минимальный.

## Локальные проверки

```bash
python -m compileall .
bash build.sh
```

## Ручной чеклист поведения

После наката патча владелец бота проверяет обычное поведение:

```text
/start или базовая команда
добавление/удаление чата
пересылка обычного текста
reply на пересланное сообщение
медиа: фото/видео/документ
стикер/видеозаметка без caption
бан/разбан
капча нового участника
статистика
перезапуск бота без потери состояния
```

## Следующий патч

Patch 05 — начать вынос `RelayService` для обычных сообщений, чтобы бизнес-логика
ретрансляции постепенно уходила из `handlers.py` в application layer.
