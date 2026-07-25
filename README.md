# Tg Web3 Digest

A digest generation project that collects sources, formats updates, and delivers summaries.

## Features

- Telegram bot command handling and operational notifications.
- Persistent storage for state, logs, or domain data.

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
