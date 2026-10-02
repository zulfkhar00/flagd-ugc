# Creator Telegram destinations

Keep the original creator in the existing bot conversation. Maya, Sofia, Ethan and
Daniel each have a private group with `@flagd_ugc_bot` added. The bot's membership
and group permissions were verified on September 30, 2026. Actual chat IDs are
saved locally in `data/creator-delivery/telegram-chats.json`, outside Git.

Set `TELEGRAM_CREATOR_CHAT_IDS` in Dokploy to a JSON object mapping these IDs:

| Creator ID | Creator | Destination |
| --- | --- | --- |
| `creator-01` | Original creator / Emily | Current bot conversation |
| `creator-02` | Maya | flagd ugc — Maya |
| `creator-03` | Sofia | flagd ugc — Sofia |
| `creator-04` | Ethan | flagd ugc — Ethan |
| `creator-05` | Daniel | flagd ugc — Daniel |

Paste the single line in `data/creator-delivery/dokploy-env.txt` into Dokploy's
environment settings. The JSON mapping file is also available for review.
The existing `TELEGRAM_CHAT_ID` remains the original creator's fallback when her
entry is absent. A missing destination for another creator raises a configuration
error, and duplicate destination IDs are rejected.

`TelegramClient.for_creator(settings, creator_id, database)` resolves the
destination once for the client's videos, summaries and failure alerts. Each
creator's batch/delivery state must be isolated when the multi-creator generation
runner is added. The current production runner still creates only the original
creator's batch; adding these destinations does not activate the other creators.
