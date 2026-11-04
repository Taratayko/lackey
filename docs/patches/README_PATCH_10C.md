# Patch 10C — активация Telegram handler package

## Зачем нужен патч

После проверки Patch 10/10B в свежем архиве обнаружилось, что пакет
`adapters/telegram/handlers/` уже есть, но корневой `handlers.py` всё ещё
содержит старый большой runtime-код.

Из-за этого `bot.py` продолжал импортировать старый root-router, а новый
разрезанный Telegram handler package фактически не был активирован.

Patch 10C делает маленькую корректировку: корневой `handlers.py` становится
тонким compatibility shim и экспортирует `router` из
`adapters.telegram.handlers`.

## Изменённые файлы

- `handlers.py`
- `README.md`
- `docs/patches/README_PATCH_10C.md`

## Что изменилось

- `handlers.py` больше не содержит обработчики напрямую.
- Старый публичный импорт остаётся рабочим:

```python
from handlers import router
```

- Фактический router теперь берётся из:

```python
from adapters.telegram.handlers import router
```

- `bot.py` менять не нужно.

## Что не трогали

- `bot.py`;
- `captcha.py`;
- `database.py`;
- `application/services/*`;
- `adapters/telegram/handlers/admin.py`;
- `adapters/telegram/handlers/moderation.py`;
- `adapters/telegram/handlers/main.py`;
- Mongo schema;
- `.env`;
- `build.sh`.

## Как проверять

1. Синтаксис:

```bash
python -m compileall .
```

2. Сборка:

```bash
bash build.sh
```

3. Запуск бота обычным способом.

4. Минимальная runtime-проверка:

- `/help`;
- `/list`;
- текстовая команда `бан` reply на сообщение;
- обычный relay текста между тестовыми чатами;
- reply на доставленное ботом сообщение;
- media relay;
- captcha flow, если есть тестовый чат с капчей.

## Ожидаемый результат

Поведение должно остаться как после Patch 10/10B, но теперь реально работает
новый пакет `adapters.telegram.handlers`, а не старый большой root-файл.
