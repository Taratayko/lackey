# application/services

Сюда постепенно будут выноситься сервисы бизнес-логики:

- `RelayService` — пересылка обычных сообщений;
- `ReplyService` — ответы и thread/reply mapping;
- `AdminService` — админские команды;
- `StatsService` — статистика.

На патче 1 это только каркас. Runtime пока остаётся в текущих `handlers.py`, чтобы не ломать рабочее поведение.
