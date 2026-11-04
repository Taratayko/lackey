# Patch 12 — VK adapter MVP

## Цель

Добавить первый изолированный слой VK-адаптера под будущую схему:

```text
VK Bots Long Poll / Callback
→ VK mapper
→ IncomingMessage
→ application services
→ ChannelPort
→ VkChannelAdapter
```

Патч **не подключает VK к runtime** и не меняет запуск текущего Telegram-бота.
Это подготовительный слой, чтобы следующий патч мог подключать VK уже без
переписывания core-моделей и портов.

## Что добавлено

- `adapters/vk/client.py`
  - минимальный `VkApiClient` без новых зависимостей;
  - `messages_send(...)` для отправки текста;
  - `groups_get_long_poll_server(...)`;
  - `long_poll_check(...)`.

- `adapters/vk/mapper.py`
  - `vk_message_update_to_domain(...)`;
  - конвертация `message_new` в `IncomingMessage`;
  - базовая конвертация VK attachments в доменные `Attachment`.

- `adapters/vk/adapter.py`
  - `VkChannelAdapter` как реализация `ChannelPort`;
  - MVP поддерживает отправку текста;
  - медиа, moderation и join-request capabilities пока явно не реализованы.

- `adapters/vk/longpoll.py`
  - skeleton `VkLongPollRunner` для будущего запуска VK Bots Long Poll;
  - runner не подключён к `bot.py`.

- `adapters/vk/__init__.py`
  - публичные экспорты VK adapter package.

- `config.py` / `.env.example`
  - добавлены выключенные по умолчанию VK-настройки:
    - `VK_ENABLED=false`;
    - `VK_GROUP_ID`;
    - `VK_GROUP_TOKEN`;
    - `VK_API_VERSION`;
    - `VK_LONGPOLL_WAIT`.

- `README.md`
  - добавлены VK env-переменные;
  - добавлена строка Patch 12 в список патчей.

## Что не трогали

- `bot.py`;
- Telegram runtime;
- `handlers.py`;
- `adapters/telegram/handlers/*`;
- reply / relay / media relay;
- captcha runtime;
- moderation runtime;
- Mongo schema;
- `requirements.txt`.

## Почему VK пока не запускается

Сейчас в Telegram-слое ещё чинится/проверяется reply-пересылка. Поэтому Patch 12
сделан безопасно: VK-код лежит рядом, проходит синтаксическую проверку, но не
может сломать текущий Telegram-бот.

Следующий VK-патч должен отдельно решить:

```text
как запускать Telegram polling и VK Long Poll вместе;
как маппить VK peer_id на существующие коммуны;
какие политики relay разрешены между Telegram/VK/Web;
как хранить cross-channel thread mapping.
```

## Проверка

Минимально:

```bash
python -m compileall .
bash build.sh
```

Живая проверка текущего бота:

```text
/start
/help
/diag
обычный relay
media relay
reply, когда его дочинят
captcha flow
```

VK-команды и VK-runtime пока проверять не надо: они не подключены.
