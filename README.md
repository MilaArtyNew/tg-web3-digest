# Tg Web3 Digest

Telegram Web3 digest bot that collects Web3/news sources, builds digest messages, and sends them to Telegram. It is focused on source aggregation and digest delivery, not trading execution.

## Features

- Collects Web3/news inputs for digest creation.
- Formats and sends Telegram digest outputs.
- Documents deployment, scheduling, and source/API configuration.

## Architecture

- **Repository:** `MilaArtyNew/tg-web3-digest`
- **Primary stack:** Python, systemd, Railway
- **Entrypoints and scripts:**
  - `main.py`
- **Notable dependencies:** `APScheduler`, `pytz`, `telethon`

## Configuration

Configure the service with environment variables. Do not commit real secrets to the repository.

- `TG_API_HASH` — required or optional runtime configuration. See deployment environment for the actual value.
- `TG_API_ID` — required or optional runtime configuration. See deployment environment for the actual value.
- `SOURCES_CONFIG` — optional path to `sources.json` for grouping digest output into Smart/Core/Other blocks. Defaults to `sources.json` next to `tg_digest_sender.py`.
- `MAX_ITEMS_PER_BLOCK` — optional per-block item cap for Smart/Core/Other. Defaults to `6`.
- `TG_MESSAGE_CHAR_LIMIT` — optional Telegram safety cap. Defaults to `3900` chars to stay below Telegram's 4096-char message limit.
- `RAW_DIGEST_ENABLED` — optional fallback switch for the old raw Telegram sender. Defaults to `false`; set to `true` only for temporary raw-feed fallback.
- `MESSAGES_API_MAX_LIMIT` — optional upper cap for `/messages` raw payload. Defaults to `1500` so Hermes weekly reading can fetch a wider 7-day candidate pool before local scoring.

### API for Hermes LLM digest

Protected endpoint:

- `GET /messages?start=<iso>&end=<iso>&limit=160`

`limit` is capped by `MESSAGES_API_MAX_LIMIT` (default `1500`).

It returns bounded raw Telegram messages with `channel`, `msg_id`, `date_utc`, `text`, and best-effort `source_url`. Hermes cron jobs use this payload to generate aggregated TG Web3 digests and weekly reading lists instead of sending truncated raw post snippets.

### Digest blocks

The sender groups output into three blocks:

1. `Smart` — channels listed in `sources.json` under `smart`.
2. `Core` — channels listed in `sources.json` under `core`.
3. `Other` — every other subscribed/read channel.

## Setup

```bash
git clone https://github.com/MilaArtyNew/tg-web3-digest
cd tg-web3-digest
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running Locally

```bash
python main.py
```

## Bot Commands

No interactive Telegram commands were detected automatically. If this service sends alerts only, document the operational controls here when they are added.

If a command requires extra input and the argument is missing, the bot should ask a follow-up question instead of failing silently.

## Deployment Notes

- Keep secrets in the deployment platform environment variables, not in Git.
- Use the default branch as the source of truth for deployments.
- Check logs after every deployment and verify the `/status` or health endpoint when available.
- If the project uses a scheduler, verify timezone assumptions and idempotency before enabling it in production.

## Operational Notes

- Review logs after startup for missing environment variables or API authentication errors.
- Keep command names in English and document every user-facing command in this README.
- For Telegram bots, `/help` should list the same commands documented here.
- Inline buttons should edit the original message with the final status rather than sending duplicate messages.

## Troubleshooting

- **Bot does not respond:** verify the bot token, webhook/polling mode, and chat permissions.
- **Missing data:** check API keys, rate limits, and upstream service status.
- **Deployment starts but exits:** inspect platform logs for missing environment variables or import errors.
- **Commands differ from README:** update the command list here and in the bot command menu at the same time.

## Security

- Never commit `.env` files, API keys, private keys, Telegram tokens, or session strings.
- Use `.env.example` for placeholders only.
- Rotate any credential that was accidentally committed.
