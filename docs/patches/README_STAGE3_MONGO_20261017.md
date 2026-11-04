# Lackey — Stage 3: MongoDB, platform IDs, deduplication

**Source:** `Lackey_stage2_20261017.zip` with Stage 1 and Stage 2 preserved.
**Release:** 2026-10-17.
**Scope:** persistence, indexes, delivery idempotency and statistics only.

## Changes

1. `delivery_claims` is a durable Mongo collection. Before **any** outbound Telegram API call, an atomic `update_one(..., $setOnInsert, upsert=True)` claims a SHA-256 key derived from the source platform, chat ID, message IDs, and the **requested** destination (before redirect). Mongo `_id` guarantees only one worker claims the same delivery. The key ignores text and topic changes, so replay after a redirect/topic update is also blocked.
2. The delivery claim is finalized as `delivered`, `partial`, or `uncertain`. An exception without a returned message ID cannot prove whether Telegram delivered the message; the claim stays blocked. After a crash, `dispatching` likewise stays blocked. **No automatic retry or TTL**, by design.
3. When the bot fails to reach Mongo before claiming, it **does not** call Telegram. When Mongo fails **after** sending, it does not resend; the existing Stage 1 receipt/error policy remains.
4. Explicit `TelegramMigrateToChat` rejections (with `migrate_to_chat_id`) remove the old pre-send claim and retain the Stage 1 migration retry path. There was no successful delivery on that Telegram error.
5. `threads` gains `origin_channel`, `relay_channel`, and `origin_external_chat_id`/`relay_external_chat_id` string fields. For Telegram, legacy `chat_id` and `message_id` numeric values are preserved. Reads with channel=`telegram` include records where the channel is absent (pre-Stage-3); reads for VK/Web never match legacy Telegram documents. Array aliases from Stage 2 remain supported.
6. Compound indexes on chat ID + single/array message ID for origin and relay; platform-qualified array alias indexes for future adapters. No unique index on `threads.origin_*`, because an original may legitimately have different destination mappings.
7. Stop pushing new timestamps into `stats.timestamps`. Keep legacy records unchanged; new compact `stats_daily` documents count by UTC calendar day. `stats.total` continues incrementing. `get_stats_recent()` merges legacy exact timestamps and new daily counters; at the rolling-window boundary the daily section is approximate to one day.
8. `/diag` checks `delivery_claims` and `stats_daily`, and reports claim statuses. No live VK/Web channel runtime is enabled by this patch.

## Install (no DB script required)

1. Stop running Lackey instances and **back up MongoDB** (including `chats`, `threads`, `stats`, `dm_sessions`, `dm_users`) plus the current project. Stop any duplicate instances of the bot; only one polling worker should run for the same token.
2. Extract the full archive over the Stage-2 project. Keep your existing `.env`. **Do not delete the Mongo database, collections or `.env`**.
3. Run from project root:
   ```bash
   python -m unittest discover -s tests -v
   python -m compileall -q .
   ```
4. Start the bot as before (`python bot.py`, or existing launcher). `Database.connect()` creates additive indexes. The first startup may take a little longer if `threads` is large; watch the logs. If index creation fails, the bot does not continue without persistence protection.
5. As the owner, use `/diag` (or `/health`): verify Mongo ping, collections `delivery_claims` and `stats_daily`, and counts. A fresh DB should have `dispatching: 0` after test sends finish. Existing collections and thread count should remain intact.

## Live verification

- **Group text:** from A send `B! этап 3 текст`. It should arrive once in B. Repeat with a new Telegram message: it **should also** arrive (new message ID means a new delivery). Do not use identical text alone as the deduplication test.
- **Reply:** reply from B to the bot's delivered text, then reply in A to the bot's answer. Both should work as in Stage 2, including the original topic.
- **Forward/media:** send a single photograph, a sticker and an album from A to B; reply to forwarded header and item to ensure multi-ID mappings are preserved.
- **DM:** test `/anon` + direct message and reply, ensuring names remain hidden.
- **Mongo:** check `delivery_claims` documents after successful sends (status=`delivered`); `threads` newest records now have `*_channel`; `stats_daily` gets one daily counter per route and date; no new element is added to old `stats.timestamps` arrays.
- **Duplicate updates:** the built-in tests simulate concurrent processing and replay across a new `DeliveryPipeline`; a human sending the same text twice is **not** a duplicate. Telegram does not expose a normal UI for replaying a past `update_id`.

Optional **read-only** inspection from a `mongosh` session already connected to the bot database:

```javascript
db.threads.findOne({}, {sort: {_id:-1}})
db.threads.getIndexes().map(x => x.name)
db.delivery_claims.aggregate([{$group: {_id:"$status", count:{$sum:1}}}])
db.delivery_claims.find({}, {status:1, created_at:1, updated_at:1}).sort({created_at:-1}).limit(5)
db.stats_daily.find().sort({day:-1}).limit(5)
```

## Investigating claim failures

A small transient `dispatching` count during sending is normal. Records remaining `dispatching` after a crash, or any `uncertain` record, require an operator to inspect the Telegram destination **before** deciding to manually retry. Do not blindly delete claims: doing so can produce duplicate deliveries. `/diag` shows only counts; detailed lookups require Mongo. A successful `delivered` claim also stays in Mongo indefinitely, intentionally preventing replay; keep an eye on collection size as usage grows.

## Rollback

Stop bot, restore code from `Lackey_stage2_20261017.zip`, preserve `.env`, restart. **No Mongo rollback/migration** is required: Stage 2 ignores the new platform fields, `delivery_claims`, and `stats_daily`. Stage 2 does not benefit from the new deduplication guard and will resume appending `timestamps`, and its recent stats view will not include Stage-3 daily events. If exact statistics and guard behavior matter, revert by restoring the backed-up database **only after** weighing the loss of messages created after the backup.

## Boundaries

- There is no way to guarantee Telegram exactly-once effects after arbitrary network failures; this is *at-most-one automated attempt per logical delivery key*, not an atomic transaction with Telegram.
- Current Telegram chat registration (`chats`) remains numeric and Telegram-only; only the thread schema and claims are platform-qualified. No VK/Web live routing is claimed.
- Live MongoDB/Motor and Telegram integration tests require the deployment environment; tests in `tests/test_stage3_persistence.py` use atomic Mongo mimics.
- The user-reported ability to **forward another person's message via reply** is not handled in Stage 3 and needs its own scenario-specific patch.
