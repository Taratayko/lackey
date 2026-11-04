# README PATCH 01 — L2.5 skeleton / config-env-texts-formatters

## Цель

Первый безопасный шаг к L2.5-архитектуре без переписывания бизнес-логики.
Бот должен продолжить запускаться старым способом, но в проекте появляется
каркас будущего communication core под Telegram/VK/Web.

## Что сделано

- Добавлен архитектурный каркас:
  - `domain/`
  - `application/`
  - `ports/`
  - `adapters/`
  - `presentation/`
- Добавлены стартовые доменные модели:
  - `ChannelRef`
  - `IncomingMessage`
  - `Attachment`
- Добавлены заготовки портов:
  - `ChannelPort`
  - `ChatRepositoryPort`
- Добавлен слой форматирования:
  - `presentation/formatters.py`
  - безопасный `escape_html()`
  - безопасный `html_link()`
- Добавлен слой текстов:
  - `presentation/texts.py`
  - стартовый `DEFAULT_ONBOARDING_TEXT`
- Обновлён `config.py`:
  - настройки через `SettingsConfigDict`
  - `extra="ignore"` для лишних ключей в `.env`
  - `TELEGRAM_PROXY_URL`
  - настройки онбординга/капчи из `.env`
- Обновлён `bot.py`:
  - proxy больше не захардкожен в коде, берётся из `settings.telegram_proxy_url`
- Обновлён `captcha.py`:
  - timeout/approve delay/onboarding text берутся из `config.py/.env`
  - часть HTML-вывода экранируется через formatter
- Обновлён `handlers.py`:
  - `sender_mention()` экранирует имя/username
  - строки списка чатов используют безопасные HTML helpers
- Заполнен `.env.example`.
- Добавлен `.gitignore`.
- Обновлён `start_bot.bat`:
  - если есть `.venv`, запускает через `.venv\Scripts\python.exe`
  - иначе сохраняет старый fallback `py -3.13 bot.py`
- Обновлён основной `README.md`.

## Что специально не трогали

- Не переносили `RelayService`.
- Не переносили `ReplyService`.
- Не переносили Mongo в repository layer.
- Не меняли схему БД.
- Не меняли маршрутизацию сообщений.
- Не меняли порядок router-ов aiogram.
- Не подключали VK/Web.

## Ожидаемое поведение

Поведение бота должно остаться прежним.

Единственное намеренное изменение: proxy теперь можно отключить через `.env`:

```env
TELEGRAM_PROXY_URL=
```

Если ключ не задан, сохранено старое поведение:

```env
TELEGRAM_PROXY_URL=http://127.0.0.1:10801
```

## Проверка у разработчика без живого токена

```powershell
.\.venv\Scripts\python.exe -m compileall .
```

## Проверка у владельца на локальном боте

После наката патча проверить:

```text
/start или базовая команда
добавление/удаление чата
пересылка обычного текста
reply на пересланное сообщение
медиа: фото/видео/документ
бан/разбан
капча нового участника
статистика
перезапуск бота без потери состояния
```

## Перед следующим патчем

Следующий шаг: `Патч 2 — универсальные модели ChannelRef / Message / Attachment`.
Но перед ним нужен результат проверки этого патча на тестовых чатах `Тестраз` и
`Тестдва`.
