# Gmail Intelligence Agent

A small personal Gmail agent. Classifies with deterministic rules first, AI second. Keeps important email visible; routes low-value or uncertain email to a **Review** label (archived, never deleted). Applies its own labels alongside any folder you already made by hand — an existing "Uber" label catches Uber receipts automatically. See `CLAUDE.md` for the full spec.

> **Safety principle:** the app may organize aggressively, but it will never automatically delete an email.

## What this is (and isn't)

This is intentionally small: read the inbox, classify, apply labels. There is **no dashboard, no Google Sheets workbook, no daily digest email, no audit trail, no attachment reading, and no deadline/subscription/travel intelligence** — an earlier, much larger version of this project had all of that; it was deliberately stripped back. See `CLAUDE.md` §14 for the full list of what was removed and why.

What it does:

- Connects one Gmail account via OAuth.
- Classifies mail with a deterministic rules engine (`app/classification/`), consulting an AI provider only when the rules can't settle it.
- Applies the result as real Gmail labels (`Critical`, `Review`, `Financial`, ...) — plus, additively, any existing label whose name matches the sender (e.g. `Uber`).
- Runs a background loop that classifies new mail as it arrives (off by default).
- Reads VIPs and sender/domain rules from a single checked-in file, `config/rules.toml`, that you edit directly.

## Requirements

- Python 3.13
- Windows / macOS / Linux

## Quick start (local development)

```powershell
# 1. Create a virtual environment (Windows PowerShell)
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install the project with dev extras
pip install -e ".[dev]"

# 3. Copy the example env file (do NOT commit .env)
Copy-Item .env.example .env

# 4. Run tests
pytest

# 5. Start the app
uvicorn app.main:app --reload --port 8000
```

macOS / Linux equivalent:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
pytest
uvicorn app.main:app --reload --port 8000
```

Visit http://localhost:8000/health — you should see `{"status": "ok", ...}`.

## Connecting your Google account

Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and a real `SESSION_SECRET` in `.env`, then open http://localhost:8000/ and click **Connect Gmail**. In the Google Cloud Console, enable the **Gmail** and **People** APIs for your project.

## Trying it out

- `GET /classify/preview?limit=25` — read-only. Shows what the rules engine (and, if configured, the AI) would do to your last 25 messages. Changes nothing.
- `POST /gmail/apply?limit=10&confirm=true` — classifies and, if the write gate is open (see below), actually applies it to Gmail.
- `GET /realtime/status` / `POST /realtime/poll` — the background real-time loop's status, and a way to run one cycle by hand.

## Rules file

Add VIPs, sender rules, and domain rules by editing `config/rules.toml` directly — see the comments in that file, and `CLAUDE.md` §11.

## Layout

See [`CLAUDE.md` §4](CLAUDE.md) for the canonical repo layout.

## Safety defaults

- `DRY_RUN=true` — never modifies Gmail unless explicitly disabled.
- `GMAIL_PROCESSING_ENABLED=false` — no Gmail writes until you opt in. Both `DRY_RUN=false` and `GMAIL_PROCESSING_ENABLED=true` are required together before any write path touches Gmail (`app/gmail/apply.py:check_write_gate`).
- Gmail access is `gmail.readonly` plus `gmail.modify` (labels, archive — never send, never Trash, never a permanent delete).
- `REALTIME_ENABLED=false` by default — the background poll loop that classifies new mail is off until you turn it on. Turning it on doesn't by itself allow writes; the same gate above still applies.
- The app refuses to start in production (`APP_ENV=production`) with a still-default `SESSION_SECRET` — a failed deploy with a clear error, rather than a live app encrypting the Gmail token with a secret published in this repo.
- No AI keys are required to run the app — classification stays fully deterministic without one.

## Deployment

Deploys to Render's free plan via [`render.yaml`](render.yaml). Render wipes local disk on every redeploy; `GOOGLE_OAUTH_SEED_REFRESH_TOKEN` (shown once on the Gmail-connected confirmation page after your first connect, pasted into Render's dashboard by hand) is what keeps the connection alive across redeploys without a paid persistent disk — see `app/gmail/tokens.py`.
