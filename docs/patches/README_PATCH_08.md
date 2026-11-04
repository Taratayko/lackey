# Patch 08 — CaptchaService

## Что сделано

Вынесена основная логика капчи/онбординга из `captcha.py` в application-слой:

- добавлен `application/services/captcha.py`;
- добавлены DTO:
  - `CaptchaSettings`;
  - `CaptchaJoinRequest`;
  - `CaptchaAnswerRequest`;
  - `CaptchaCommandResult`;
- `captcha.py` оставлен тонким aiogram-router слоем;
- обработка `chat_join_request` теперь идёт через `CaptchaService`;
- проверка активной captcha-сессии теперь идёт через `CaptchaService`;
- ответ пользователя в ЛС, сохранение анкеты и отложенный approve теперь идут через `CaptchaService`;
- таймер истечения заявки и decline теперь идут через `CaptchaService`;
- onboarding-log после успешного approve теперь идёт через `CaptchaService`;
- команды `/captcha_add`, `/captcha_remove`, `/captcha_list`, `/captcha_timeout` переведены на repository layer вместо прямого доступа к `db._db`.

## Что изменилось в адаптере Telegram

`TelegramChannelAdapter` получил методы, нужные капче:

- `send_photo_from_path(...)` — отправить локальную картинку `hello.png`;
- `get_channel_info(...)` — получить title/link чата для welcome/onboarding-log;
- `approve_join_request(...)`;
- `decline_join_request(...)`.

`send_text(...)` получил необязательный параметр `disable_web_page_preview`, чтобы сохранить поведение onboarding-log без предпросмотра ссылок.

## Что изменилось в домене/портах

- добавлена доменная модель `ChannelInfo`;
- `ChannelPort` расширен capability-методами для onboarding/join-request.

## Что не трогали

- `handlers.py`;
- обычный relay;
- media relay;
- reply-цепочки;
- moderation service;
- admin-команды чатов (`addchat/removechat/link/stats/redirect/hidden`);
- Mongo schema;
- VK/Web runtime;
- `.env`;
- `build.sh`.

## Как проверять после наката

1. Запустить бота через `start_bot.bat`.
2. Проверить, что бот стартует и polling работает.
3. В тестовой беседе включить онбординг:

   ```text
   /captcha_add
   ```

4. Подать заявку новым тестовым пользователем в чат с включённым `chat_join_request`.
5. Проверить, что бот пишет пользователю в ЛС с вопросом “кто ты и что ищешь”.
6. Ответить пользователем текстом в ЛС.
7. Проверить, что бот отправляет `hello.png` + onboarding text.
8. Дождаться задержки approve и проверить, что заявка одобрена.
9. Проверить welcome-сообщение в ЛС.
10. Проверить onboarding-log в чате, если он включён.
11. Проверить `/captcha_list`.
12. Проверить `/captcha_timeout`.
13. Проверить `/captcha_remove`.
14. Проверить обычный текстовый relay, media relay, reply-цепочки и moderation-команды, чтобы подтвердить, что соседние слои не сломались.
15. Выполнить:

   ```bash
   bash build.sh
   ```

## Локальная проверка у ассистента

Выполнено без запуска `bot.py`, потому что нет рабочего `.env`, Telegram token и локальной MongoDB владельца:

```text
python3 -m compileall .     OK
bash build.sh               OK
```
