# Patch 03 — слой Mongo-репозиториев

## Цель

Добавить слой репозиториев поверх текущего доступа к MongoDB, пока без изменения runtime-поведения бота.

Это подготовительный шаг перед последующим выносом application-сервисов:
`RelayService`, `ReplyService`, `ModerationService`, `CaptchaService` и `AdminService`.

## Добавленные/изменённые файлы

```text
ports/repositories.py
ports/__init__.py
adapters/mongo/repositories.py
adapters/mongo/__init__.py
database.py
README.md
docs/patches/README_PATCH_03.md
```

## Что изменено

- Расширены контракты репозиториев в `ports/repositories.py`:
  - `ChatRepositoryPort`
  - `ThreadRepositoryPort`
  - `StatsRepositoryPort`
  - `DmRepositoryPort`
  - `BanRepositoryPort`
  - `MessageRepositoryPort`
  - `CaptchaRepositoryPort`
  - `RepositoryBundle`
- Добавлены Mongo-реализации в `adapters/mongo/repositories.py`.
- Добавлены публичные экспорты в `ports/__init__.py` и `adapters/mongo/__init__.py`.
- В `database.py` добавлен небольшой lazy hook `Database.get_repositories()`.
- Добавлен README этого патча и обновлён список патчей в основном `README.md`.

## Что не менялось

- Логика handlers не переносилась.
- Миграция схемы Mongo не добавлялась.
- Runtime-поведение Telegram/VK/Web не менялось.
- `handlers.py` пока продолжает использовать `database.db` напрямую.
- `captcha.py` пока продолжает использовать существующие helper-функции напрямую.
- Новые ключи в `.env` не добавлялись.

## Почему это безопасно

Новые классы репозиториев оборачивают существующие методы `Database` и текущие Mongo-коллекции. Пока следующие патчи явно не начнут прокидывать эти репозитории в сервисы, поведение работающего бота должно остаться прежним.

`Database.get_repositories()` ленивый: Mongo-адаптер импортируется только при вызове метода, поэтому обычный запуск бота не должен измениться.

## Локальные проверки

```bash
python -m compileall .
bash build.sh
```

## Ручной чеклист поведения

После наката патча владелец бота всё равно проверяет обычное runtime-поведение:

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

## Следующий патч

Patch 04 — начать вынос Telegram sender/media adapter, при этом Telegram остаётся единственным активным runtime-транспортом.
