# Phase 0 — Foundation (plain English)

## What was built

The empty project folder is now a real Python web application. Nothing fancy yet — think of it as pouring the foundation of a house before any rooms are built. Specifically, this phase produced:

- A Python 3.13 project you can install and run locally.
- A tiny web server (using a framework called **FastAPI** — that's just Python code that answers web requests) with two pages:
  - `/health` — a status page the app itself and future hosting will use to check "is the app alive?"
  - `/` — a plain welcome page that says the app is running.
- A **config loader** that reads settings from environment variables (things like which timezone to use, whether to touch Gmail, which AI provider to prefer). All safety switches are turned to the safest setting by default.
- A **logging system** that writes structured log lines and automatically hides anything that looks like a secret or an email body.
- A **test suite** (13 automated checks that pass right now) verifying the app boots, the config defaults are safe, and the log system does not leak secrets.
- Empty folders for every future component (Gmail access, classification, AI, dashboard, digest, learning, audit, etc.) so future phases have a place to live.
- Documentation folders and this explainer file.

## Why this matters

You cannot safely add Gmail access, AI, or a dashboard without a stable base first. This phase gives us:

1. A repeatable way to install and run the app.
2. Safety defaults that make it *impossible* for Phase 0 to touch your Gmail (see below).
3. A test runner so we can catch mistakes early.
4. A hosting-friendly shape (works on Render later without rework).

## What happens when it runs

When you start the app locally (see "How to test" below), a web server listens on your computer at `http://localhost:8000`. Visiting `/health` returns a small JSON status object saying the app is fine. That is the entire behavior of Phase 0.

## What it can and can't change in Gmail

**Zero. Nothing. Not one thing.** Phase 0 has never seen your Gmail, has no permission to see your Gmail, and has no code that talks to Google. Two safety switches inside the app currently sit in these positions:

- `DRY_RUN = true` — even when Gmail code exists in later phases, "dry run" means "look but don't touch".
- `GMAIL_PROCESSING_ENABLED = false` — Gmail work is completely turned off.

Both of those are just extra belts-and-braces. The real reason Phase 0 can't touch Gmail is that no Gmail code has been written yet.

## What you should test

You do not need to be a developer to run these steps. Open PowerShell in the project folder and run, one at a time:

1. **Create a Python sandbox for the project** (a "virtual environment"):
   ```powershell
   py -3.13 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. **Install the app and its testing tools**:
   ```powershell
   pip install -e ".[dev]"
   ```

3. **Run the automated tests**:
   ```powershell
   pytest
   ```
   Expected: something like `13 passed`.

4. **Start the web server**:
   ```powershell
   uvicorn app.main:app --reload --port 8000
   ```

5. **Open two links in your browser**:
   - http://localhost:8000/ — should show a small "Gmail Intelligence Agent" welcome page.
   - http://localhost:8000/health — should show a JSON block with `"status": "ok"`, `"dry_run": true`, `"gmail_processing_enabled": false`, `"phase": 0`.

If all of that works, Phase 0 is verified.

## What could go wrong

- **"py -3.13 is not recognized"** — Python 3.13 is not installed. Install it from https://www.python.org/downloads/ (pick the 3.13 installer, check "Add Python to PATH").
- **`pip install` errors** — network hiccup; try again. If a specific package fails, tell me the error text.
- **`ImportError: ... Unknown timezone 'America/New_York'`** — that would only happen if the `tzdata` dependency didn't install. Re-run step 2.
- **Port 8000 already in use** — start uvicorn on a different port: `--port 8001`.

## How to undo it

Phase 0 doesn't modify anything outside its own folder. To fully undo:

1. Stop the server (Ctrl+C in the PowerShell window).
2. Delete the `.venv` folder (it's the Python sandbox — safe to remove).
3. Optionally delete every file in the project folder except `CLAUDE.md`. That returns you to where we started this session.

Nothing outside the project folder is touched: no Windows settings, no Gmail, no browser data, no scheduled tasks.

## What success looks like

- `pytest` reports all tests passing.
- The health page returns JSON with the two safety flags shown as `true` (dry_run) and `false` (gmail_processing_enabled).
- The welcome page opens without errors.

## Any short definitions you might want

- **Virtual environment (.venv)** — a private Python sandbox that keeps this project's packages separate from other Python projects on your machine.
- **FastAPI** — a Python library for building small web servers. That's how `/health` and `/` respond to your browser.
- **pytest** — the tool that runs the automated tests.
- **Environment variable** — a setting that lives outside the code file (like which timezone to use). Kept in a `.env` file locally, kept in Render's dashboard in production. **Never committed to git.**

## Next phase

**Phase 1 — Gmail read-only.** This is where the app will ask Google for permission to *read* (not modify) your Gmail. You will click through a Google consent screen. The consent screen will show exactly which permissions the app is requesting. Phase 1 still won't change any email. I'll produce a Phase 1 explainer the same way as this one when it's ready.
