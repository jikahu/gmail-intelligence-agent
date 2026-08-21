# Gmail Intelligence Agent

A small personal Gmail agent. Classifies with deterministic rules first, AI second. Keeps important email visible; routes low-value or uncertain email to a **Review** label (archived, never deleted). Applies its own labels alongside any folder you already made by hand — an existing "Uber" label catches Uber receipts automatically. See `CLAUDE.md` for the full spec.

> **Safety principle:** the app may organize aggressively, but it will never automatically delete an email.

## What this is (and isn't)

This is intentionally small: read the inbox, classify, apply labels. There is **no dashboard, no Google Sheets workbook, no daily digest email, no audit trail, no attachment reading, and no deadline/subscription/travel intelligence** — an earlier, much larger version of this project had all of that; it was deliberately stripped back. See `CLAUDE.md` §14 for the full list of what was removed and why.

What it does:

- Connects one Gmail account via OAuth.
- Classifies mail with a deterministic rules engine (`app/classification/`), consulting an AI provider only when the rules can't settle it.
- Applies the result as real Gmail labels (`Critical`, `Review`, `Financial`, ...) — plus, additively, any existing label whose name matches the sender (e.g. `Uber`).
- Classifies new mail as it arrives — `python -m app.scheduling` runs one check-and-apply cycle and exits; `.github/workflows/realtime-poll.yml` runs that directly on a GitHub Actions runner every 10 minutes. There's no hosted server and no in-process background loop.
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
- `GET /realtime/status` / `POST /realtime/poll` — what the last few scheduled polls did, and a way to trigger one cycle by hand at any time.

## Rules file

Add VIPs, sender rules, and domain rules by editing `config/rules.toml` directly — see the comments in that file, and `CLAUDE.md` §11.

## Layout

See [`CLAUDE.md` §4](CLAUDE.md) for the canonical repo layout.

## Safety defaults

- `DRY_RUN=true` — never modifies Gmail unless explicitly disabled.
- `GMAIL_PROCESSING_ENABLED=false` — no Gmail writes until you opt in. Both `DRY_RUN=false` and `GMAIL_PROCESSING_ENABLED=true` are required together before any write path touches Gmail (`app/gmail/apply.py:check_write_gate`).
- Gmail access is `gmail.readonly` plus `gmail.modify` (labels, archive — never send, never Trash, never a permanent delete).
- There's no on/off switch for real-time processing — `POST /realtime/poll` just does nothing new if nothing's called it. Whether anything happens automatically depends entirely on whether something outside the app is calling that endpoint on a schedule (see Deployment below).
- The app refuses to start in production (`APP_ENV=production`) with a still-default `SESSION_SECRET` — a failed deploy with a clear error, rather than a live app encrypting the Gmail token with a secret published in this repo.
- No AI keys are required to run the app — classification stays fully deterministic without one.

## Deployment

There is no hosted server. `.github/workflows/realtime-poll.yml` runs `python -m app.scheduling` directly on a GitHub Actions runner every 10 minutes — checkout, install, run one poll cycle, exit. Nothing stays running between ticks, so there's nothing to keep warm and nothing that goes to sleep.

**One-time setup:**

1. Run the app locally (`uvicorn app.main:app --reload --port 8000`) and connect Gmail via `/` → **Connect Gmail**, same as local development above.
2. Its callback page shows a `GOOGLE_OAUTH_SEED_REFRESH_TOKEN` value. In this repo's Settings → Secrets and variables → Actions, add these repo secrets:
   - `ANTHROPIC_API_KEY`
   - `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
   - `GOOGLE_OAUTH_SEED_REFRESH_TOKEN` (from step 2)
   - `SESSION_SECRET` (generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`)
3. Under Settings → Actions → General → Workflow permissions, select **Read and write permissions** — the workflow commits the moved Gmail history cursor (`oauth_tokens/realtime_cursor.json`, not sensitive — just a number) back to the repo after each run so the next run knows where it left off. See `app/scheduling/state.py`.

That's it — the schedule in `realtime-poll.yml` (`*/10 * * * *`) takes it from there. Trigger a run by hand any time with `gh workflow run realtime-poll.yml` or the Actions tab's **Run workflow** button.
