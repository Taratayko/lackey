# README PATCH 01A — build manifest check / ASCII start script

## Цель

Закрыть маленький технический шаг после Патча 1 перед дальнейшим L2.5-рефакторингом:
проверить наличие manifest-сборщика `build.sh` и убрать проблему запуска `.bat` из-за
кириллицы в `cmd.exe`.

## Что сделано

- Проверен `build.sh` в корне проекта:
  - скрипт уже присутствует;
  - использует root manifest;
  - не должен включать `.env`, `.venv`, `.idea`, `__pycache__`, архивы, логи и временные файлы;
  - неизвестные новые элементы в корне должны останавливать сборку.
- Обновлён `start_bot.bat`:
  - файл сделан ASCII-only;
  - убраны русские комментарии и echo-строки;
  - сохранён запуск с правами администратора;
  - сохранён запуск через `.venv\Scripts\python.exe`, если `.venv` есть;
  - сохранён fallback `py -3.13 bot.py`.

## Что специально не трогали

- Не меняли Python-код.
- Не меняли бизнес-логику handlers/captcha.
- Не меняли `.env.example`.
- Не меняли MongoDB и схему данных.
- Не меняли Telegram polling/router-логику.
- Не подключали VK/Web.

## Как проверить локально

### 1. Проверить сборку архива

Из Git Bash / bash:

```bash
bash build.sh
```

Ожидаемо: появляется архив вида:

```text
commune_mesh_lackey_YYYYMMDD_HHMMSS.zip
```

В архиве не должно быть:

```text
.env
.venv/
.idea/
__pycache__/
*.log
старых *.zip
```

### 2. Проверить запуск батника

На Windows запустить:

```text
start_bot.bat
```

Ожидаемо: больше нет строк вида:

```text
'ратора' is not recognized as an internal or external command
'апку' is not recognized as an internal or external command
'Папка:' is not recognized as an internal or external command
```

### 3. Проверить синтаксис Python

```powershell
.\.venv\Scripts\python.exe -m compileall .
```

## Чеклист поведения у владельца

После запуска бота проверить минимально:

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

Перед Патчем 2 нужен fresh archive текущего состояния после наката этого hotfix и проверки.
