## Меню `(つ✧ω✧)つ` — «В гости» / «Вписаться» (04.11.2026)

- Постоянная Reply Keyboard и inline-меню по ТЗ `docs/lakey-tz.md`: `Вписаться`, `В гости`, `Написать`, `Список коммун`, `Поддержка`.
- Сценарии «В гости» и «Вписаться» шлют заявку в коммуну через общий DeliveryPipeline (reply-цепочки, anti-duplicate, `/anon`).
- Состояние шагов — Mongo `menu_flows` с TTL 20 минут. Принятые решения и открытые вопросы: `docs/patches/README_MENU_20261104.md`.

## Патч A — SiteFeedback (21.10.2026)

- `/communesite` (`/communeSite`) в ЛС: отзывы по разделам сайта, предложения новых страниц, свои обращения.
- `/sitefeedback` и `/sfnote` только для `ADMIN_ID` в ЛС: очередь, фильтры, поиск по Telegram ID / известным username, решения и уведомления.
- Отдельные коллекции Mongo `site_feedback_*`, автосоздание индексов; `/start communesite` подготовлен для будущего анонса.
- **Рассылка B не запускается**. Сначала интеграция C и сквозные тесты A+C, затем отдельное решение о публикации.
- Все авторы видны администрации по Telegram ID; `/anon` в ретрансляции не скрывает автора предложений.
- Подробнее: `docs/patches/README_SITE_FEEDBACK_A_20261021.md`.

## Stage 4 — forward через reply, тесты, обслуживание (2026-10-20)

- Reply с явной командой `НазваниеКоммуны!` пересылает исходное сообщение другого участника либо бота; `НазваниеКоммуны! комментарий` добавляет подпись к заголовку. Обычные reply-цепочки без такой команды остаются прежними.
- **Важно:** если бот не администратор группы и Privacy Mode включён, Telegram может вообще не отправить боту update для reply на чужое сообщение. Сделайте бота администратором либо отключите Privacy Mode в @BotFather и пере-добавьте бота в группу.
- При невозможности forward защищённого контента бот показывает понятную ошибку; новое вложение в reply с командой forward намеренно не принимается.
- Удалены растущие локальные `_reply_locks`; дубль защищается Mongo `delivery_claims` Stage 3. В Windows-скрипте больше нет требования прав администратора.
- `TELEGRAM_PROXY_URL` теперь реально используется **если указан**; если старое значение указывает на несуществующий локальный прокси, установите `TELEGRAM_PROXY_URL=` в `.env`.

Полное описание, откат и сценарии проверки: `docs/patches/README_STAGE4_FORWARD_REPLY_20261020.md`.

## Stage 3 — MongoDB durability and duplicate protection (2026-10-17)

- New atomic `delivery_claims` guard protects text/media/forward/reply/album from duplicate source updates, including across bot restarts. It uses MongoDB `_id` uniqueness before Telegram API calls. If MongoDB cannot claim, the message is not sent (fail closed).
- Claims that are `dispatching` (crash during delivery) or `uncertain` (network error before message ID was returned) are **never automatically retried**: sending again could duplicate a message. Admin review is required. No automatic TTL; claims are small but persistent.
- `threads` now carry platform-qualified fields (`origin_channel`, `relay_channel`, `*_external_chat_id`) and new compound lookup indexes, while old Telegram scalar fields and Stage 2 array aliases remain readable.
- Stats keep the permanent `total`, but no longer append to unlimited `timestamps`; compact `stats_daily` counters are used for recent activity. Old timestamp arrays are retained for backwards-readable history. Recent daily counts include the entire boundary day and can overcount by less than a day.
- `/diag` shows new collections and claim statuses. The live runtime still supports **Telegram** only; the new metadata prepares storage for future VK/Web work, not a VK/Web launch.

See `docs/patches/README_STAGE3_MONGO_20261017.md` for install, verification, limitations, and rollback. The *forward-another-message-via-reply* workflow is separate and was **not** changed in Stage 3.

# COMM (Лакей)

Telegram-бот — почтальон и менеджер коммунарских/анархических беседок.
Связывает беседы между собой: ретрансляция сообщений, reply-цепочки, анонимные
ЛС-сессии и мультигородские справочники `/list`.

## Структура файлов

```
COMM/
├── bot.py           # Точка входа
├── config.py        # Настройки (читает из .env)
├── database.py      # Слой MongoDB (motor)
├── handlers.py      # Compatibility shim: экспортирует router из adapters.telegram.handlers
├── presentation/     # Тексты и HTML-форматтеры
├── domain/           # Доменные модели будущего communication core
├── ports/            # Контракты будущих адаптеров/репозиториев
├── adapters/         # Реализации Telegram/VK/Web/Mongo
│   └── telegram/handlers/ # Telegram router layer для aiogram-хэндлеров
├── application/      # Сервисы бизнес-логики
├── docs/patches/     # README по каждому патчу
├── requirements.txt
├── start_bot.bat     # Запуск на Windows (ascii-арт + автозапрос прав админа)
├── .env.example      # Пример настроек без секретов
└── .env              # Локальные секреты, не хранить в git
```

## Установка

```bash
pip install -r requirements.txt
# создайте .env по образцу ниже
python bot.py
```

На Windows можно запустить `start_bot.bat` — права администратора не нужны;
скрипт запускает бота из его каталога.

## Переменные окружения (.env)

| Переменная  | Описание                               |
|-------------|-----------------------------------------|
| BOT_TOKEN   | Токен от @BotFather                     |
| MONGODB_URI | URI подключения к MongoDB               |
| DB_NAME     | Имя базы данных (по умолчанию `tgbot`)  |
| ADMIN_ID    | Telegram ID владельца бота              |
| TELEGRAM_PROXY_URL | URL proxy для Telegram, пусто = без proxy |
| VK_ENABLED | Включать VK runtime позже; сейчас по умолчанию `false` |
| VK_GROUP_ID | ID VK-группы для будущего Bots Long Poll |
| VK_GROUP_TOKEN | Токен VK-группы, не хранить в git |
| VK_API_VERSION | Версия VK API для adapter MVP |
| VK_LONGPOLL_WAIT | Wait-секунды для будущего VK Long Poll runner |
| WEB_ENABLED | Включать будущий Web/Site runtime; сейчас по умолчанию `false` |
| WEB_API_TOKEN | Token для будущего site/API слоя, не хранить в git |
| WEB_PUBLIC_BASE_URL | Публичный URL будущего сайта коммун |


## Патчи рефакторинга

- `docs/patches/README_PATCH_01.md` — L2.5 skeleton: env/config, тексты, форматтеры, стартовые заготовки `domain`/`ports`/`adapters`/`application`.
- `docs/patches/README_PATCH_01A.md` — build.sh и ASCII-only hotfix запуска на Windows.
- `docs/patches/README_PATCH_02.md` — универсальные модели `ChannelRef` / `Message` / `Attachment` под Telegram/VK/Web.
- `docs/patches/README_PATCH_03.md` — repository layer над Mongo: контракты портов и Mongo-адаптеры без изменения runtime-поведения.
- `docs/patches/README_PATCH_04.md` — Telegram sender/media adapter: отправка текста, медиа и реакций вынесена из handlers в адаптер.
- `docs/patches/README_PATCH_05.md` — RelayService: обычная текстовая ретрансляция из чатов и ЛС вынесена в application-слой.
- `docs/patches/README_PATCH_06.md` — ReplyService: reply-цепочки вынесены в application-слой через repository layer и Telegram channel adapter.
- `docs/patches/README_PATCH_07.md` — ModerationService: бан, мегабан, разбан, banlist и cleanup сообщений вынесены в application-слой (позже модуль модерации удалён из бота).
- `docs/patches/README_PATCH_08.md` — CaptchaService: онбординг, captcha-сессии, approve/decline и onboarding-log вынесены в application-слой (позже модуль капчи полностью удалён из бота).
- `docs/patches/README_PATCH_09.md` — AdminService: списки, статистика и команды владельца для управления чатами вынесены в application-слой.
- `docs/patches/README_PATCH_10.md` — Telegram handler package: корневой `handlers.py` стал compatibility shim, фактический router перенесён в `adapters/telegram/handlers/`.
- `docs/patches/README_PATCH_10B.md` — Telegram handlers split: moderation/admin/help/list вынесены в отдельные подроутеры, `main.py` оставлен для relay/DM/reply входов.
- `docs/patches/README_PATCH_10C.md` — Telegram handler package activation: корневой `handlers.py` стал реальным compatibility shim и включает пакет `adapters.telegram.handlers`.
- `docs/patches/README_PATCH_10D.md` — Telegram handlers split: DM/личные сообщения вынесены в отдельный подроутер, общие Telegram helpers вынесены в `common.py`.
- `docs/patches/README_PATCH_10E.md` — hotfix DM/reply: ЛС-router больше не перехватывает reply-цепочки, их снова обрабатывает ReplyService.
- `docs/patches/README_PATCH_11.md` — DiagnosticsService: owner-only команды `/diag` и `/health` для проверки Mongo, коллекций, чатов, thread mapping и индексов.
- `docs/patches/README_PATCH_12.md` — VK adapter MVP: изолированный VK API client, mapper, ChannelPort adapter и Long Poll runner skeleton без подключения к runtime.
- `docs/patches/README_PATCH_13.md` — Web/Site adapter MVP: framework-agnostic DTO/mapper/API boundary и WebChannelAdapter без запуска web runtime.
- `docs/patches/README_STAGE1_SECURITY_20261015.md` — security fix, HTML, redirect и безопасная постобработка Mongo.
- `docs/patches/README_STAGE2_UNIFIED_DELIVERY_20261017.md` — единая доставка текста/медиа/forward/reply, топики, альбомы и multi-ID mapping.
- `docs/patches/README_STAGE3_MONGO_20261017.md` — атомарные delivery claims, Mongo indexes, статистика по дням.
- `docs/patches/README_STAGE4_FORWARD_REPLY_20261020.md` — reply-forward, Privacy Mode, тесты и рефакторинг.

## Группы чатов

| Группа    | Описание                                          |
|-----------|----------------------------------------------------|
| `msk`     | Московские беседы                                   |
| `spb`     | Питерские беседы                                    |
| `vlk`     | Владивостокские беседы ⛵️                            |
| `krm`     | Крымские беседы 🦅                                   |
| `special` | Спецчаты (получают/отвечают, сами не отправляют)    |

Региональные города (всё кроме `msk`/`spb`/`special`) хранятся в едином
реестре `REG_CITIES` в `adapters/telegram/handlers/admin.py` — это текущий источник правды для Telegram admin/list слоя
для `/list`, `/listreg`, полного списка в ЛС и проверки `group` в `/addchat`.
Чтобы добавить новый регион, достаточно дописать туда тройку
`("код_группы", "Прилагательное", "эмодзи")` — он автоматически появится
во всех списках.

```python
REG_CITIES: list[tuple[str, str, str]] = [
    ("vlk", "Владивостокские", "⛵️"),
    ("krm", "Крымские", "🦅"),
    # 🔮 Здесь появятся новые города
]
```

## Правила маршрутизации

- **Специальные** не могут *отправлять*, но могут *получать* и *отвечать*
- **Неглавные** (`is_chief=false`) могут писать в **неглавные** и **специальные**, но **не в главные** (включая redirect)
- **Главные** могут писать в любые чаты; **специальные** могут получать и отвечать, но не начинать новую отправку
- Reply-цепочки работают бесконечно в обе стороны
- Чат можно привязать к конкретному топику форума (`/settopic`) — тогда бот
  реагирует только в нём, а не во всём чате

## Добавление чатов

Добавьте бота в нужный чат, затем отправьте (от имени `ADMIN_ID`):

```
/addchat msk true Клубника https://t.me/klubnika
/addchat msk false Твари https://t.me/tvari
/addalias Бестиарий
/addalias Лешие
/addchat spb true Флажок https://t.me/flagok
/addchat vlk true Пассад https://t.me/passad_commune
/addchat krm false Маяк https://t.me/mayak
/addchat special false Локей https://t.me/lokei
```

## Пользовательские команды

| Команда      | Описание                                          |
|--------------|----------------------------------------------------|
| `/start`     | Приветствие в ЛС (новым — список всех коммун)      |
| `/list`      | Список коммун группы текущего чата (в ЛС — полный) |
| `/listmsk`   | Московские + региональные + специальные            |
| `/listspb`   | Питерские + региональные + специальные              |
| `/listreg`   | Только региональные (vlk, krm, ...) + специальные   |
| `/stat`      | Статистика сообщений                                |
| `/anon`      | Писать анонимно (только в ЛС)                       |
| `/deanon`    | Писать от своего имени (только в ЛС)                |
| `/help`      | Справка — текст зависит от контекста (см. ниже)     |

## Отправка сообщения

Формат маршрутизации в зарегистрированном чате или ЛС — **название + `!`**:

```
Твари! привет
Могильный Платон! текст сообщения
```

- Фото/видео с подписью `Твари! текст` передаются с подписью.
- `Твари!` на фото/видео передаёт медиа без дополнительного текста.
- Reply на чужое сообщение с текстом `Твари!` пересылает его в Твари:
  приходит отдельный заголовок и пересланный оригинал; **reply работает на обоих**.
- Reply стикером или видеокружочком на бот-сообщение отправляет в другую
  беседу заголовок + медиа; отвечать можно на любой элемент.
- Альбом из нескольких фото/видео отправляется целиком, если в его подписи
  указана цель. Все доставленные части связываются с одним диалогом.
- Если чат привязан к топику `/settopic`, новые команды из других топиков
  игнорируются. Уже созданные reply-цепочки используют сохранённые топики.

После успешной доставки бот пытается поставить реакцию ✍; запись mapping,
статистика и реакция не должны вызывать дублирующую отправку.

## Admin-команды (только ADMIN_ID)

| Команда                                   | Описание                              |
|---------------------------------------------|------------------------------------------|
| `/addchat <group> <chief> <name> [link]`    | Зарегистрировать текущий чат               |
| `/removechat`                               | Удалить текущий чат из БД                  |
| `/addalias <алиас>`                         | Добавить псевдоним (aka)                   |
| `/removealias <алиас>`                      | Удалить псевдоним                          |
| `/setlink <url>`                            | Задать ссылку для `/list`                  |
| `/setchief true\|false`                     | Пометить/снять «главный чат»               |
| `/sethidden true\|false`                    | Скрыть/показать чат в `/list`              |
| `/setredirect <chat_id>`                    | Перенаправить входящие в другой чат (0 — сброс) |
| `/settopic <topic_id>`                      | Привязать чат к топику форума (0 — сброс)  |
| `/cleartopic`                               | Снять привязку к топику                    |
| `/chatinfo`                                 | Инфо о текущем чате из БД                  |
| `/listall`                                  | Все зарегистрированные чаты                |
| `/pleh`                                     | Полный список служебных команд (только владельцу) |

## Стек

Python 3.13 · aiogram 3 · MongoDB (motor) · pydantic-settings · rich (для
заставки при запуске)

### Patch C Remote — Communars Feedback (два сервера)

Синхронизация через **исходящий HTTPS от Lackey к сайту**, отдельный worker
`site_feedback_sync.py` и MySQL-копия обращений на сайте. Входящие порты
Lackey / SSH-туннели не нужны. Настройка и SQL-миграция — в
`docs/patches/README_STAGEC_COMMUNARS_INTEGRATION_20261021.md`.
Массовая рассылка (B) запрещена до успешных проверок A+C.
