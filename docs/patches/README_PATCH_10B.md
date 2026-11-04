# Patch 10B — разрезание Telegram handlers на модули

## Что сделано

Продолжена аккуратная чистка Telegram-входного слоя после Patch 10.

Добавлены отдельные модули:

- `adapters/telegram/handlers/moderation.py` — команды модерации:
  - `/ban`
  - `/megaban`
  - `/unban`
  - `/banlist`
  - текстовые команды `бан`, `мегабан`, `разбан`
- `adapters/telegram/handlers/admin.py` — списки, help и команды владельца:
  - `/list`, `/listmsk`, `/listspb`, `/listreg`
  - `/stat`
  - `/addchat`, `/removechat`
  - `/addalias`, `/removealias`
  - `/setlink`, `/sethidden`, `/setredirect`
  - `/settopic`, `/cleartopic`
  - `/setonboardinglog`, `/clearonboardinglog`
  - `/setchief`, `/chatinfo`, `/listall`
  - `/help`, `/pleh`

Обновлён `adapters/telegram/handlers/__init__.py`:

- теперь он собирает общий `router` из нескольких подроутеров;
- порядок регистрации сохранён безопасным образом:
  1. moderation;
  2. admin;
  3. main.

Это важно, потому что текстовые команды `бан` / `мегабан` / `разбан` должны перехватываться до широкого relay-handler из `main.py`.

## Что осталось в `main.py`

В `adapters/telegram/handlers/main.py` пока остаются:

- обычная текстовая ретрансляция;
- media relay;
- reply-chain входы;
- forward-reply;
- DM `/start`, `/anon`, `/deanon`;
- DM text/media relay;
- вспомогательные функции, которые ещё нужны этим сценариям.

## Что не трогали

- `application/services/*`;
- `captcha.py`;
- `bot.py`;
- Mongo schema;
- VK/Web runtime;
- `.env`;
- `build.sh`.

## Как проверить

Минимальный чеклист:

```text
/ban reply
бан reply
/megaban reply
мегабан reply
/unban @username
разбан @username
/banlist
/list
/listmsk
/listspb
/listreg
/stat
/help в ЛС
/help в беседе от админа
/help в беседе от обычного пользователя
/pleh от владельца
/addalias testalias -> /removealias testalias
/sethidden true -> /sethidden false
/settopic 0 -> /cleartopic
обычный text relay
media relay
reply-chain
forward-reply
DM /start
DM /anon и /deanon
captcha flow
bash build.sh
```

## Проверка у ассистента

```text
python3 -m compileall .     OK
bash build.sh               OK
```

`bot.py` не запускался: у ассистента нет рабочего `.env`, Telegram token, MongoDB и тестового бота.
