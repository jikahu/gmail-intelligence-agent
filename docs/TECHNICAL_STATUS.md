# Technical Status

## Current shape

A single-account Gmail classification agent. No dashboard, no Sheets workbook, no digest, no audit trail — see `CLAUDE.md` §14 for the full list of what was removed from an earlier, much larger version of this project and why. What exists now:

- **OAuth + Gmail read/write** (`app/gmail/`). Scopes: `openid`, `userinfo.email`, `gmail.readonly`, `contacts.readonly`, `contacts.other.readonly`, `gmail.modify`. `gmail.labels` (needed for label color-coding) is defined but deliberately excluded from `ACTIVE_SCOPES` — see `app/gmail/scopes.py`'s docstring for why (it broke live token refresh in production once, and re-adding it needs a Google Cloud Console change first).
- **Deterministic classification** (`app/classification/`) — 17 taxonomy labels, no `AI/` prefix (dropped; see below). Protection rules, priority, Review routing all unchanged in behavior from the larger version, minus anything that depended on removed modules (attachment text extraction, deadline extraction).
- **Existing-label ("vendor folder") matching** (`app/gmail/vendor_labels.py`) — new. Recognizes a Gmail label the user already made by hand (e.g. "Uber") and applies it alongside the taxonomy label, based on sender domain/name matching against the mailbox's existing label list.
- **AI second opinion** (`app/ai/`) — unchanged in shape; only consulted when the deterministic rules can't settle a message.
- **Local rules file** (`app/rules/`, `config/rules.toml`) — replaces the old Google Sheets control workbook. VIPs, sender rules, domain rules, read-only from the app's side, user-edited directly.
- **Real-time processing** (`app/scheduling/`) — same poll-the-history-feed design as before, but the "last seen" cursor now lives in a local JSON file (`app/scheduling/state.py`) instead of a Sheets `Settings` row, there's no audit-log write alongside each apply, and there's no in-process background loop anymore either — see below.
- **Manual apply + preview** (`app/main.py`, `app/gmail/write_service.py`) — `GET /classify/preview` and `POST /gmail/apply`, unchanged in shape from before.

## What changed, and why

This project originally had 16 phases: a Command Center web dashboard, a Google Sheets control workbook, a midnight digest email, an audit trail with Undo Last Run, attachment text extraction, a deadline/money/subscription/travel/duplicate-detection intelligence layer, business-day follow-up timers, a 250-email acceptance dry-run gate, and a 12-month historical cleanup sweep, on top of the classification engine and Gmail write path. At the user's request, all of that was removed to leave a small, single-purpose agent — see `CLAUDE.md` §1 and §14.

Mechanical changes as part of that:

- `Label` enum values dropped their `AI/` prefix (`AI/Critical` → `Critical`). `Label.WAITING_FOR_REPLY` was removed — it was only ever set by the now-deleted follow-up module. AI-suggested labels and manual-rule `classify_as` actions both still tolerate a stray `AI/` prefix defensively (`app/ai/schemas.py`, `app/classification/engine.py:_label_from_action`), in case an old habit or a copied-over rule still uses it.
- `app/gmail/apply.py:check_write_gate()` dropped its third condition (a passed 250-email acceptance run recorded in the Sheets workbook) — it now only checks `DRY_RUN` and `GMAIL_PROCESSING_ENABLED`, since the acceptance-run module is gone and there's no dashboard to view its report anyway.
- The golden-dataset regression test (`tests/golden_dataset/`, scored by `app/classification/golden.py`) moved from `app/acceptance/golden.py` to `app/classification/golden.py` — it never depended on Sheets or the live acceptance run, only on the classification engine, so it survived the cut essentially unchanged.
- `app/main.py` was cut from ~40 routes to about a dozen: health, index, OAuth, `/gmail/preview`, `/classify/preview`, `/gmail/apply`, `/gmail/labels/sync-colors`, `/realtime/status`, `/realtime/poll`.
- `app/config/settings.py` dropped every dashboard/digest/Sheets-specific field (`digest_timezone`, `digest_hour`, `digest_scheduler_enabled`, `dashboard_authorized_emails`, `dashboard_session_max_age_hours`, `dashboard_login_redirect_uri`, `sheets_workbook_id`, `review_confidence_threshold` — the last was declared but never actually wired to anything).
- The in-process real-time background loop (`RealTimePoller.start()`/`.stop()`/an asyncio task ticking every `REALTIME_POLL_INTERVAL_SECONDS`) was removed, along with the `REALTIME_ENABLED` / `REALTIME_POLL_INTERVAL_SECONDS` settings. `POST /realtime/poll` still runs one cycle exactly as before; what changed is who calls it. On Render's free plan, a loop living inside the process just stops the moment the process sleeps (after 15 minutes with no traffic) — so instead, `.github/workflows/realtime-poll.yml` calls that endpoint from GitHub Actions every 10 minutes, which both drives the polling and keeps the service from ever fully sleeping. `app/scheduling/service.py:RealTimePoller` now only tracks the outcome of each call for `GET /realtime/status`; it owns no timer and no task.

## Known gaps / deliberate non-goals

- **No manual Trash.** The dashboard's confirm-then-Trash button is gone along with the dashboard, and nothing replaced it. The app currently has no code path that moves a message to Gmail's Trash at all, automatic or manual.
- **No attachment content in classification.** `Important-Document` detection still fires on subject wording or a document-shaped attachment (by mime type/filename), but no longer looks at what a PDF actually says — the text-extraction layer that used to feed that is gone.
- **`gmail.labels` scope still excluded** from `ACTIVE_SCOPES` (see above) — label color-coding (`POST /gmail/labels/sync-colors`) will keep 403ing until that Google Cloud Console step happens.
- **The real-time history cursor doesn't survive a Render redeploy** (local file, ephemeral filesystem, no seed mechanism like the OAuth token has). A redeploy just means the next poll re-bootstraps from "now" — a brief coverage gap, not a correctness issue.
- **Real-time processing now depends on an external trigger actually running.** If `.github/workflows/realtime-poll.yml` is disabled, deleted, or GitHub Actions is unavailable, nothing calls `/realtime/poll` automatically anymore — there's no in-process fallback. `GET /realtime/status`'s `last_run_at` is the way to notice this has quietly stopped.

## Testing

`pytest` from the repo root. The golden-dataset suite (`tests/golden_dataset/`) is the permanent regression guard on classification behavior — its non-negotiable number is a zero protected-email false-Review rate.
