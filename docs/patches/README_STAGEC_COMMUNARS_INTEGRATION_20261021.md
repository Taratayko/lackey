# Lackey × Communars — Patch C (разные серверы, исходящий HTTPS)

## Важное

Это **замена прежнего Patch C**, который обращался к `127.0.0.1` с PHP-сервера. Прежний Patch C **не устанавливать**.

- MongoDB + Lackey находятся на сервере Telegram-бота.
- PHP + MySQL находятся на сервере сайта `communars.itcheckup.pro`.
- **Все соединения между серверами инициирует Lackey** по HTTPS к уже существующему сайту. SSH-туннель, входящий порт бота и публичная MongoDB не нужны.
- Новая синхронизация — **отдельный процесс** `site_feedback_sync.py`. `bot.py` работает как раньше.
- **B (анонс всем коммунам) не запускаем** до проверки A + C и разрешения владельца.

## Как работает

1. Lackey через MongoDB считывает страницы, обращения и известные Telegram usernames; передаёт на сайт HTTPS POST `/api/site-feedback/sync/v1/snapshot/`.
2. Сайт хранит синхронизированную копию в **локальном MySQL**. Личная админка `/admin/site-feedback/` читает только локальную базу.
3. Администратор принимает решение в веб-форме: в MySQL создаётся запись в очереди со статусом `pending`. Это **ещё не изменение в Telegram**.
4. Lackey делает исходящий GET `/api/site-feedback/sync/v1/actions/`, применяет решение в MongoDB, пытается уведомить автора и POST `/api/site-feedback/sync/v1/ack/`.
5. Следующая синхронизация обновляет страницу и показывает `applied` или `conflict`.

Особенности: idempotency по `site:<action_id>` в истории Mongo; сравнение исходного статуса; сравнение `revision` при обновлении копии; повтор после сбоя HTTP; HTTPS/TLS-проверка. При недоступности сайта Telegram продолжает работать. При недоступности бота действия ожидают в MySQL. Отправка уведомления в Telegram — best effort (после аварии в узком промежутке между изменением Mongo и уведомлением сообщение может не прийти; состояние обращения при этом сохранится).

## 1. Сайт: развернуть архив

1. Сделать backup текущего сайта + MySQL. Сохранить существующий `.env` **вне** каталога перед заменой файлов.
2. Развернуть **Communars_Site_Patch_C_Remote_20261021.zip** вместо предыдущего C, убедившись, что не затронуты секреты и другие подпроекты.
3. Применить только новую миграцию через штатный механизм:

   ```bash
   php tools/migrate.php status
   php tools/migrate.php up
   php tools/migrate.php status
   ```

   **Миграция:** `020_communars_site_feedback_sync.sql` — четыре новые таблицы `communars_feedback_items`, `communars_feedback_pages`, `communars_feedback_aliases`, `communars_feedback_actions`. Старые таблицы не меняются. Если сервер не отмечает предыдущие миграции в `schema_migrations`, сначала разобраться со статусом (НЕ запускать слепо старые миграции). Автоматической миграции не будет.

4. Создать *один общий секрет* длиной минимум 32 случайных символа, например `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'` на доверенной машине. Сохранить его приватно и прописать один и тот же на двух серверах.
5. В **сайтовом** `.env`:

   ```env
   COMMUNARS_FEEDBACK_ENABLED=1
   COMMUNARS_FEEDBACK_ADMIN_USER=moderator
   COMMUNARS_FEEDBACK_ADMIN_PASSWORD_HASH=<password_hash из PHP>
   COMMUNARS_FEEDBACK_SYNC_TOKEN=<длинный_общий_секрет>
   COMMUNARS_FEEDBACK_COOKIE_SECURE=1
   APP_ENV=production
   ```

   Получить bcrypt хеш пароля (без пароля в командной строке): `php -r '$p=readline("Password: "); echo password_hash($p, PASSWORD_BCRYPT), PHP_EOL;'` (если команда запускается в терминале, где ввод скрывать важно, используйте локальный генератор или защищённый ввод).

6. Сайт должен быть доступен по HTTPS и PHP должен видеть `$_SERVER['HTTPS'] = 'on'` при HTTPS-запросе. Для FastCGI нужно передавать `HTTP_AUTHORIZATION` (заголовок Bearer). В Nginx PHP location типично `fastcgi_param HTTP_AUTHORIZATION $http_authorization;`, при этом **не доверяйте X-Forwarded-Proto из Интернета**. Прототипная `/admin/` не является авторизацией для реальных отзывов: новая панель использует отдельный Basic Auth + CSRF.

## 2. Бот: обновить архив, затем включить worker

1. Сделать backup рабочей версии Patch A и `.env`, обновить код из **Lackey_Patch_C_Remote_20261021.zip**.
2. Сохранить существующие `BOT_TOKEN`, `ADMIN_ID`, `MONGODB_URI`, `DB_NAME` и прочие параметры. Добавить в **ботовом** `.env`:

   ```env
   SITE_FEEDBACK_SYNC_URL=https://communars.itcheckup.pro/api/site-feedback/sync/v1
   SITE_FEEDBACK_SYNC_TOKEN=<тот_же_общий_секрет>
   SITE_FEEDBACK_SYNC_INTERVAL=20
   ```

3. Проверить:

   ```bash
   python -m unittest discover -s tests -q
   python -m compileall -q .
   ```

4. Проверить HTTPS-доступ с сервера бота, не передавая токен в историю shell (читать из своего секретного файла/.env):

   ```bash
   curl -fsS https://communars.itcheckup.pro/api/site-feedback/pages/
   # Авторизованный запрос /health/ должен вернуть {"ok":true,"protocol":2}.
   ```

5. Перезапустить существующий `bot.py` своим штатным способом. **Отдельно** запустить синхронизатор в его окружении:

   ```bash
   python site_feedback_sync.py
   ```

   После успешного ручного smoke — установить свой systemd unit по образцу `deploy/lackey-site-feedback-sync.service.example`. Исправить в unit `User=`, `WorkingDirectory=` и путь к Python под реальную установку. Не запускать второй экземпляр worker без необходимости.

## 3. Совместное тестирование A + C

1. Проверить старую пересылку Telegram, в том числе reply из ЛС. Она не должна зависеть от сайта.
2. В ЛС: `/communesite` → отзыв → «Мои предложения». Получить реальное обращение в MongoDB бота.
3. Дождаться ближайшей синхронизации (обычно до интервала + времени запроса). Открыть `https://communars.itcheckup.pro/admin/site-feedback/` и авторизоваться.
4. Найти обращение по Telegram ID, потом по `@username`. После смены username проверить старый сохранённый alias.
5. Изменить статус с `new` на `in_review`. Сразу после отправки формы действие должно отображаться как **pending**. После цикла worker — **applied**, а статус в MongoDB и на сайте совпадают. У автора должно появиться уведомление (если Telegram доступен).
6. Проверить двойное нажатие формы: не должно появиться двух решений. Проверить конфликт статусов: если статус уже изменён в самом Telegram, устаревшее решение сайта получает `conflict` и не перезаписывает MongoDB.
7. Временно остановить только worker: бот продолжает общаться, сайт продолжает показывать кеш; решение остаётся pending. Возобновить worker — очередь применяется без дублей.
8. Попробовать открыть приватный `/api/site-feedback/sync/v1/actions/` без Bearer: ответ должен быть 401. Публичный `/api/site-feedback/pages/` не должен содержать отзывы/имена.

**B (массовое объявление)** строго после подтверждённых результатов этих проверок. Никакая рассылка ни в один архив не добавлена.

## Откат

Остановить **только** `site_feedback_sync.py`; вернуть предыдущее состояние кода сайта и бота, восстановить файлы `.env` (оставив оригинальные секреты). Четыре новые MySQL-таблицы не удалять без резервной копии и явной команды — в них могут остаться ожидающие решения. Монго-коллекции A не трогать. Старый localhost-bridge не запускать.

## Проверки в этой сборке

- Python unit-тесты и `compileall`.
- Проверка синтаксиса PHP.
- HTTP smoke защищённых endpoints, Basic Auth и публичного реестра.
- PHP проверка SQL-контракта очереди с тестовым PDO.
- **Не выполнялся живой тест MySQL**: в проверочном контейнере отсутствует `pdo_mysql`, реальных серверов и учётных данных здесь нет.
